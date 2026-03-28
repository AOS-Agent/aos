"""Unified transcription module.

Fallback chain:
  1. YouTube captions API (free, instant — YouTube only)
  2. AOS Transcriber service at localhost:7601 (shared Whisper Large V3 Turbo)
  3. mlx-whisper local (Apple Silicon, if service is down)
  4. openai-whisper CLI (last resort)

Audio download handled by yt-dlp for all platforms.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path


TRANSCRIBER_URL = os.environ.get("TRANSCRIBER_URL", "http://127.0.0.1:7601")


def get_youtube_captions(video_id: str) -> str | None:
    """Tier 1: Pull existing YouTube captions (free, instant)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)

        lines = []
        for entry in transcript:
            start = entry.start
            minutes = int(start // 60)
            seconds = start % 60
            timestamp = f"[{minutes:02d}:{seconds:05.2f}]"
            lines.append(f"{timestamp} {entry.text}")

        return "\n".join(lines)
    except Exception as e:
        print(f"  Captions unavailable: {e}", file=sys.stderr)
        return None


def download_audio(url: str, output_dir: str) -> str | None:
    """Download audio from any supported URL via yt-dlp.

    Returns path to the audio file, or None on failure.
    """
    audio_path = os.path.join(output_dir, "audio.mp3")

    result = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3",
         "-o", audio_path, "--no-warnings", url],
        capture_output=True, text=True, timeout=120,
    )

    if result.returncode != 0:
        print(f"  yt-dlp audio download failed: {result.stderr}", file=sys.stderr)
        return None

    # yt-dlp may adjust the filename — find the actual file
    audio_files = list(Path(output_dir).glob("audio*"))
    if not audio_files:
        print("  No audio file found after download.", file=sys.stderr)
        return None

    return str(audio_files[0])


def _transcribe_service(audio_path: str) -> str | None:
    """Transcribe via the AOS Transcriber service (localhost:7601)."""
    try:
        payload = json.dumps({
            "audio_path": audio_path,
            "mode": "accurate",
            "timestamps": True,
        }).encode()

        req = urllib.request.Request(
            f"{TRANSCRIBER_URL}/transcribe",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())

        # Prefer the timestamped text if available
        if data.get("timestamped_text"):
            return data["timestamped_text"]
        return data.get("text", "")

    except (urllib.error.URLError, ConnectionRefusedError, OSError) as e:
        print(f"  Transcriber service unavailable: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Transcriber service error: {e}", file=sys.stderr)
        return None


def _transcribe_mlx(audio_path: str, model: str = "medium") -> str | None:
    """Transcribe using mlx-whisper (Apple Silicon optimized). Local fallback."""
    try:
        import mlx_whisper
    except ImportError:
        return None

    repo = f"mlx-community/whisper-{model}-mlx"
    result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=repo)

    segments = result.get("segments", [])
    if segments:
        lines = []
        for seg in segments:
            start = seg.get("start", 0)
            minutes = int(start // 60)
            seconds = start % 60
            timestamp = f"[{minutes:02d}:{seconds:05.2f}]"
            lines.append(f"{timestamp} {seg.get('text', '').strip()}")
        return "\n".join(lines)

    return result.get("text", "")


def _transcribe_openai_whisper(audio_path: str, model: str = "medium") -> str | None:
    """Transcribe using openai-whisper CLI. Last resort."""
    if subprocess.run(["which", "whisper"], capture_output=True).returncode != 0:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["whisper", audio_path, "--model", model,
             "--output_format", "txt", "--output_dir", tmpdir],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            print(f"  Whisper CLI error: {result.stderr}", file=sys.stderr)
            return None

        txt_files = list(Path(tmpdir).glob("*.txt"))
        if txt_files:
            return txt_files[0].read_text()

    return None


def transcribe_audio(audio_path: str, model: str = "medium") -> tuple[str | None, str]:
    """Transcribe an audio file using the best available method.

    Fallback chain: transcriber service -> mlx-whisper -> openai-whisper

    Returns:
        (transcript_text, source) where source is "transcriber-service" | "mlx-whisper" | "openai-whisper" | "none"
    """
    # Try the AOS transcriber service first (shared model, already warm)
    print("  Trying transcriber service...", file=sys.stderr)
    result = _transcribe_service(audio_path)
    if result:
        return result, "transcriber-service"

    # Fall back to local mlx-whisper
    print("  Falling back to local mlx-whisper...", file=sys.stderr)
    result = _transcribe_mlx(audio_path, model)
    if result:
        return result, "mlx-whisper"

    # Last resort: openai-whisper CLI
    print("  Falling back to openai-whisper...", file=sys.stderr)
    result = _transcribe_openai_whisper(audio_path, model)
    if result:
        return result, "openai-whisper"

    print("  No transcription method available.", file=sys.stderr)
    return None, "none"


def transcribe_url(url: str, platform: str = "", content_id: str = "",
                   model: str = "medium") -> tuple[str | None, str]:
    """Full transcription pipeline for a URL.

    For YouTube, tries captions first. For all platforms, falls back to
    audio download + whisper (via service or local).

    Returns:
        (transcript_text, source) where source is "captions" | "transcriber-service" | "mlx-whisper" | "openai-whisper" | "none"
    """
    # YouTube: try captions first (free, instant)
    if platform == "youtube" and content_id:
        print("  Trying YouTube captions...", file=sys.stderr)
        captions = get_youtube_captions(content_id)
        if captions:
            return captions, "captions"

    # All platforms: download audio + transcribe
    with tempfile.TemporaryDirectory() as tmpdir:
        print("  Downloading audio...", file=sys.stderr)
        audio_path = download_audio(url, tmpdir)
        if not audio_path:
            return None, "none"

        return transcribe_audio(audio_path, model)
