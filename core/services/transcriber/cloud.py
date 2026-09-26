"""Cloud transcription backend (OpenRouter).

Why this exists: the local path holds an 809M-param mlx-whisper model resident
for the life of the process. On the 16GB Mini that evicts everything else and
the box starts paging — the transcriber is usually found dead. This backend
sends the audio out instead, so the service holds no model at all and its
resident set stays in the tens of megabytes.

Model: microsoft/mai-transcribe-2 — ranked #1 on the FLEURS multilingual
benchmark, and it does code-switching natively. That last part matters here:
engine._merge_bilingual() currently runs two full Whisper passes (EN + AR) and
stitches them by per-segment quality. MAI handles mixed Arabic/English in one
pass, so "bilingual" mode costs one request instead of two model loads.

Credentials: the key is read from the login Keychain. The transcriber runs as a
GUI launchd agent, so it has Keychain access at runtime even though an SSH
session does not. No secret is stored in this repo (qren hard rule #2).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

logger = logging.getLogger("transcriber.cloud")

API_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
CLOUD_MODEL = os.environ.get("TRANSCRIBER_CLOUD_MODEL", "microsoft/mai-transcribe-2")
SOURCE_TAG = "openrouter-mai-transcribe-2"

# Keychain lookup, matching the convention already used for the other provider
# keys on this machine (service name + "api-key" account).
_KEYCHAIN_SERVICE = "openrouter-api"
_KEYCHAIN_ACCOUNT = "api-key"

# Interim fallback, used only until the Keychain entry exists. Mode 0600.
_KEY_FILE = Path.home() / ".aos" / "config" / "openrouter.key"

_TIMEOUT = float(os.environ.get("TRANSCRIBER_CLOUD_TIMEOUT", "300"))


def api_key() -> str | None:
    """Return the OpenRouter key, or None if we cannot find one.

    Keychain first — that is where this belongs. The file and env fallbacks
    exist so the service still works before the operator has added the
    Keychain entry (an SSH session cannot write to a locked login keychain).
    """
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", _KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug(f"Keychain lookup failed: {e}")

    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env.strip()

    try:
        if _KEY_FILE.exists():
            return _KEY_FILE.read_text().strip() or None
    except OSError as e:
        logger.debug(f"Key file read failed: {e}")

    return None


def available() -> bool:
    """True when a credential is present, so the caller can pick a backend."""
    return api_key() is not None


# Containers the provider accepts as-is, verified by upload on 2026-09-25.
# It rejects .m4a (iPhone voice memos) with a bare HTTP 400, and a rejection
# drops us onto the local model — which reloads the very Whisper this backend
# exists to keep out of RAM. Anything else is converted to FLAC first.
_ACCEPTED_SUFFIXES = {".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac"}


def _to_accepted_format(path: Path) -> tuple[Path, Path | None]:
    """Return (path to upload, temp file to delete or None)."""
    if path.suffix.lower() in _ACCEPTED_SUFFIXES:
        return path, None
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    fd, tmp = tempfile.mkstemp(suffix=".flac")
    os.close(fd)
    out = Path(tmp)
    try:
        subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
             "-ac", "1", "-ar", "16000", str(out)],
            check=True, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"Could not convert {path.suffix} for upload: {e}") from e
    return out, out


def _multipart(audio_path: str, fields: dict[str, str]) -> tuple[bytes, str]:
    """Build a multipart/form-data body. Kept local to avoid a requests dep."""
    boundary = f"----aos{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(audio_path)[0] or "application/octet-stream"
    name = Path(audio_path).name
    parts: list[bytes] = []

    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode()
        )

    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode()
    )
    parts.append(Path(audio_path).read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())

    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _segments_from_words(words: list[dict], max_gap: float = 0.8,
                         max_chars: int = 240) -> list[dict]:
    """Group word-level timings into segments a transcript view can show.

    Breaks on sentence-ending punctuation, on a pause longer than max_gap, or
    when a segment gets long enough to be unreadable. This is what gives the
    cloud path the same shape of output the local engine produces.
    """
    segments: list[dict] = []
    cur: list[str] = []
    start = end = 0.0
    prev_end = None

    for w in words:
        token = (w.get("word") or "").strip()
        if not token:
            continue
        w_start = float(w.get("start", 0.0))
        w_end = float(w.get("end", w_start))

        gap = None if prev_end is None else w_start - prev_end
        if cur and (gap is not None and gap > max_gap
                    or len(" ".join(cur)) >= max_chars):
            segments.append({"start": start, "end": end,
                             "text": " ".join(cur).strip()})
            cur = []

        if not cur:
            start = w_start
        cur.append(token)
        end = prev_end = w_end

        if token.endswith((".", "!", "?", "۔", "؟")):
            segments.append({"start": start, "end": end,
                             "text": " ".join(cur).strip()})
            cur = []

    if cur:
        segments.append({"start": start, "end": end,
                         "text": " ".join(cur).strip()})

    return [s for s in segments if s["text"]]


def transcribe(audio_path: str, language_hint: str = "auto",
               timestamps: bool = True) -> dict:
    """Transcribe via OpenRouter and return fields for a TranscriptionResult.

    Raises on any failure so the caller can fall back to the local engine —
    a transcription that silently returns nothing is worse than a slow one.
    """
    key = api_key()
    if not key:
        raise RuntimeError(
            "No OpenRouter key. Add it with: security add-generic-password "
            f'-U -s "{_KEYCHAIN_SERVICE}" -a "{_KEYCHAIN_ACCOUNT}" -w'
        )

    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    fields = {"model": CLOUD_MODEL}
    # verbose_json is what carries timings back; plain json is text only. MAI
    # returns the whole file as ONE coarse segment, which would collapse
    # timestamped_text to a single line, so we also ask for word-level timings
    # and rebuild sensible segments from them below.
    if timestamps:
        fields["response_format"] = "verbose_json"
        fields["timestamp_granularities[]"] = "word"
    # "auto" means let the model detect — MAI does language ID per utterance,
    # which is the whole reason we can drop the dual-pass merge.
    if language_hint and language_hint != "auto":
        fields["language"] = language_hint

    upload, tmp = _to_accepted_format(path)
    try:
        body, content_type = _multipart(str(upload), fields)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
        method="POST",
    )

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"OpenRouter HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"OpenRouter unreachable: {e.reason}") from e

    elapsed = time.monotonic() - t0

    if "error" in payload:
        raise RuntimeError(f"OpenRouter error: {payload['error']}")

    text = (payload.get("text") or "").strip()

    segments: list[dict] = []
    for seg in payload.get("segments") or []:
        seg_out = {
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": (seg.get("text") or "").strip(),
        }
        # Present only when diarization is on and the provider returns it.
        if seg.get("speaker") is not None:
            seg_out["speaker"] = seg["speaker"]
        segments.append(seg_out)

    # MAI hands back a single segment spanning the whole file. That is useless
    # for a transcript view, so rebuild from word timings when we have them.
    words = payload.get("words") or []
    if words and len(segments) <= 1:
        rebuilt = _segments_from_words(words)
        if rebuilt:
            segments = rebuilt

    # Audio duration: the API reports it under usage.seconds; fall back to the
    # last segment so duration_audio is never a lie.
    usage = payload.get("usage") or {}
    duration_audio = float(
        usage.get("seconds")
        or payload.get("duration")
        or (segments[-1]["end"] if segments else 0.0)
    )

    return {
        "text": text,
        "language": payload.get("language") or language_hint or "auto",
        # The API does not return a detection confidence. Report 0.0 rather
        # than inventing a number that downstream code might trust.
        "language_probability": 0.0,
        "segments": segments,
        "duration_audio": duration_audio,
        "duration_processing": elapsed,
        "source": SOURCE_TAG,
        "cost": usage.get("cost"),
    }
