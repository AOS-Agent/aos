"""Media Enrichment consumer.

Channel-agnostic pipeline that enriches media messages:
- Voice notes → download audio → transcribe via Whisper → write content
- Images/videos/documents → extract metadata (captions, filenames)

Self-sufficient: if audio isn't on disk, the consumer resolves it —
looks up the WA local DB for CDN URLs, downloads, decrypts, transcribes.
No cron, no external trigger. Everything happens within the poll cycle.

Each channel provides a media resolver. WhatsApp's downloads from CDN
and decrypts with the per-message media key. Future channels (Telegram,
iMessage, email) add their own resolver — the pipeline is the same.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from ..bus import Consumer
from ..models import Message

log = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
TRANSCRIBER_URL = "http://127.0.0.1:7602"
TRANSCRIBE_TIMEOUT = 120
VOICE_CACHE = Path.home() / ".aos" / "cache" / "voice_notes"
APPLE_EPOCH = 978307200

# WhatsApp local DB path
WA_LOCAL_DB = (
    Path.home() / "Library" / "Group Containers"
    / "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"
)
WA_CONTAINER = (
    Path.home() / "Library" / "Group Containers"
    / "group.net.whatsapp.WhatsApp.shared"
)


class MediaEnrichmentConsumer(Consumer):
    """Enriches media messages — transcription, metadata extraction."""

    name = "media_enrichment"

    def __init__(self):
        self._conn: sqlite3.Connection | None = None
        self._transcriber_checked = False
        self._transcriber_available = False
        self._wa_conn = None
        self._wa_tmp = None

    @property
    def conn(self) -> sqlite3.Connection | None:
        if self._conn is None:
            if not COMMS_DB.exists():
                return None
            self._conn = sqlite3.connect(str(COMMS_DB))
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _check_transcriber(self) -> bool:
        """Check transcriber availability (cached per poll cycle)."""
        if self._transcriber_checked:
            return self._transcriber_available
        self._transcriber_checked = True
        try:
            from urllib.request import Request, urlopen
            req = Request(f"{TRANSCRIBER_URL}/health", method="GET")
            with urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                self._transcriber_available = data.get("status") == "ready"
        except Exception:
            self._transcriber_available = False
        return self._transcriber_available

    def _get_wa_conn(self) -> sqlite3.Connection | None:
        """Get a read-only copy of the WhatsApp local DB. Cached per session."""
        if self._wa_conn is not None:
            return self._wa_conn
        if not WA_LOCAL_DB.exists():
            return None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
            tmp.close()
            shutil.copy2(str(WA_LOCAL_DB), tmp.name)
            self._wa_conn = sqlite3.connect(tmp.name)
            self._wa_conn.row_factory = sqlite3.Row
            self._wa_tmp = tmp.name
            return self._wa_conn
        except Exception:
            return None

    def _cleanup_wa_conn(self):
        """Clean up the temp WA DB copy."""
        if self._wa_conn:
            try:
                self._wa_conn.close()
            except Exception:
                pass
            self._wa_conn = None
        if self._wa_tmp and os.path.exists(self._wa_tmp):
            try:
                os.unlink(self._wa_tmp)
            except Exception:
                pass
            self._wa_tmp = None

    # --- Main processing ---

    def process(self, messages: list[Message]) -> int:
        """Enrich media messages. Returns count of messages enriched."""
        if not messages:
            return 0

        # Reset caches for this poll cycle
        self._transcriber_checked = False
        self._cleanup_wa_conn()

        enriched = 0
        for msg in messages:
            try:
                if self._enrich_message(msg):
                    enriched += 1
            except Exception as e:
                log.debug("Failed to enrich message %s: %s", msg.id, e)

        # Clean up WA DB copy
        self._cleanup_wa_conn()

        if enriched:
            log.info("Enriched %d/%d messages", enriched, len(messages))

        return enriched

    def _enrich_message(self, msg: Message) -> bool:
        """Enrich a single message. Returns True if enriched."""
        if msg.media_type == "voice" and not msg.text:
            return self._transcribe_voice(msg)
        if msg.media_type in ("image", "video", "document") and not msg.text:
            return self._extract_media_metadata(msg)
        return False

    # --- Voice transcription (self-sufficient) ---

    def _transcribe_voice(self, msg: Message) -> bool:
        """Transcribe a voice message. Resolves audio if not on disk."""
        if not self._check_transcriber():
            log.debug("Transcriber not available, skipping voice note")
            return False

        # Step 1: Find or acquire the audio file
        audio_path = self._resolve_audio(msg)
        if not audio_path:
            return False

        # Step 2: Validate
        try:
            size = os.path.getsize(audio_path)
            if size > 25 * 1024 * 1024 or size == 0:
                return False
        except OSError:
            return False

        # Step 3: Transcribe
        transcript = self._call_transcriber(audio_path)
        if not transcript:
            return False

        # Step 4: Update message
        msg.text = f"[voice] {transcript}"
        return self._update_content(msg.id, msg.text)

    def _resolve_audio(self, msg: Message) -> str | None:
        """Resolve audio file for a voice message. Downloads if needed.

        Resolution chain:
        1. media_path on the Message object (already on disk)
        2. Cached from a previous download
        3. WhatsApp: look up CDN URL + media key from local DB, download + decrypt
        4. (Future: Telegram, iMessage, email resolvers)
        """
        # 1. Direct path
        if msg.media_path and os.path.exists(msg.media_path):
            return msg.media_path

        # 2. Check cache
        VOICE_CACHE.mkdir(parents=True, exist_ok=True)
        cached = VOICE_CACHE / f"{msg.id}.opus"
        if cached.exists() and os.path.getsize(str(cached)) > 0:
            return str(cached)

        # 3. Channel-specific resolution
        if msg.channel == "whatsapp":
            return self._resolve_whatsapp_audio(msg)

        # Future: elif msg.channel == "telegram": ...
        # Future: elif msg.channel == "imessage": ...

        return None

    def _resolve_whatsapp_audio(self, msg: Message) -> str | None:
        """Download and decrypt a WhatsApp voice note from CDN.

        Uses the WhatsApp Desktop local DB (ChatStorage.sqlite) to find
        the CDN URL and media key, then downloads and decrypts.
        """
        wa_conn = self._get_wa_conn()
        if not wa_conn:
            return None

        # Find the message in WA local DB by timestamp + conversation JID
        jid = msg.metadata.get("jid", msg.conversation_id or "")
        if not jid or jid.startswith("conv_"):
            return None

        try:
            from datetime import datetime as dt
            ts = msg.timestamp if isinstance(msg.timestamp, dt) else dt.fromisoformat(str(msg.timestamp))
            apple_ts = ts.timestamp() - APPLE_EPOCH
        except Exception:
            return None

        try:
            row = wa_conn.execute("""
                SELECT mi.ZMEDIALOCALPATH, mi.ZMEDIAURL, mi.ZMEDIAKEY, mi.ZFILESIZE
                FROM ZWAMESSAGE m
                JOIN ZWACHATSESSION c ON m.ZCHATSESSION = c.Z_PK
                LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK
                WHERE m.ZMESSAGETYPE IN (3, 8)
                  AND ABS(m.ZMESSAGEDATE - ?) < 2
                  AND c.ZCONTACTJID = ?
                LIMIT 1
            """, (apple_ts, jid)).fetchone()
        except Exception:
            return None

        if not row:
            return None

        # Check if file exists locally first
        local_path = row["ZMEDIALOCALPATH"]
        if local_path:
            for prefix in ["Message/", ""]:
                full = WA_CONTAINER / prefix / local_path
                if full.exists():
                    return str(full)

        # Download from CDN
        media_url = row["ZMEDIAURL"]
        media_key = row["ZMEDIAKEY"]
        if not media_url or not media_key:
            return None

        return self._download_and_decrypt(msg.id, media_url, media_key)

    def _download_and_decrypt(self, msg_id: str, url: str, media_key_raw: bytes) -> str | None:
        """Download encrypted media from WhatsApp CDN and decrypt."""
        VOICE_CACHE.mkdir(parents=True, exist_ok=True)
        final_path = VOICE_CACHE / f"{msg_id}.opus"

        if final_path.exists() and os.path.getsize(str(final_path)) > 0:
            # Validate cached file
            with open(str(final_path), "rb") as f:
                if f.read(4) == b"OggS":
                    return str(final_path)
            # Invalid cache — re-download
            os.unlink(str(final_path))

        try:
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": "WhatsApp/2.24.0 iOS"})
            with urlopen(req, timeout=30) as resp:
                enc_data = resp.read()

            if len(enc_data) < 100:
                return None

        except Exception as e:
            log.debug("CDN download failed for %s: %s", msg_id, e)
            return None

        # Decrypt
        decrypted = self._decrypt_wa_media(enc_data, media_key_raw, "audio")
        if not decrypted:
            return None

        with open(str(final_path), "wb") as f:
            f.write(decrypted)

        log.debug("Downloaded and decrypted voice note: %s (%d bytes)", msg_id, len(decrypted))
        return str(final_path)

    @staticmethod
    def _decrypt_wa_media(enc_data: bytes, media_key_raw: bytes, media_type: str) -> bytes | None:
        """Decrypt WhatsApp media using the per-message media key.

        WhatsApp stores the media key as a protobuf blob in iOS:
            field 1 (0x0a, len 0x20): 32-byte media key
        Decryption: HKDF-SHA256(Extract+Expand) → AES-256-CBC.
        Last 10 bytes of encrypted data are MAC (stripped before decrypt).
        """
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            HKDF_INFO = {
                "audio": b"WhatsApp Audio Keys",
                "ptt": b"WhatsApp Audio Keys",
                "image": b"WhatsApp Image Keys",
                "video": b"WhatsApp Video Keys",
                "document": b"WhatsApp Document Keys",
            }
            info = HKDF_INFO.get(media_type, b"WhatsApp Audio Keys")

            # Extract 32-byte key from protobuf blob
            if (len(media_key_raw) >= 34
                    and media_key_raw[0:1] == b'\x0a'
                    and media_key_raw[1] == 0x20):
                media_key = media_key_raw[2:34]
            else:
                media_key = media_key_raw[:32]

            # HKDF Extract: PRK = HMAC-SHA256(salt=zeros, IKM=media_key)
            prk = hmac.new(b'\x00' * 32, media_key, hashlib.sha256).digest()

            # HKDF Expand: PRK → 112 bytes (IV + cipher key + MAC key + ref key)
            expanded = _hkdf_expand(prk, info, 112)
            iv = expanded[:16]
            cipher_key = expanded[16:48]

            # Strip MAC (last 10 bytes)
            if len(enc_data) < 10:
                return None
            ciphertext = enc_data[:-10]

            # AES-256-CBC
            cipher = Cipher(algorithms.AES(cipher_key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(ciphertext) + decryptor.finalize()

            # Validate: check for valid audio header
            if decrypted[:4] == b"OggS":
                return decrypted

            return None

        except ImportError:
            log.debug("cryptography package not installed, cannot decrypt WA media")
            return None
        except Exception as e:
            log.debug("Decryption failed: %s", e)
            return None

    # --- Media metadata ---

    def _extract_media_metadata(self, msg: Message) -> bool:
        """Extract and store metadata for non-voice media."""
        parts = []
        label = f"[{msg.media_type}]"

        filename = msg.metadata.get("filename", "")
        if not filename and msg.media_path:
            filename = os.path.basename(msg.media_path)

        caption = msg.metadata.get("caption", "")
        duration = msg.metadata.get("duration")

        if caption:
            parts.append(f"{label} {caption}")
        elif filename:
            parts.append(f"{label} {filename}")
        else:
            parts.append(label)

        if duration:
            parts.append(f"({duration}s)")

        content = " ".join(parts)

        if content == label and label in ("[image]", "[video]"):
            return False

        msg.text = content
        return self._update_content(msg.id, content)

    # --- Transcriber ---

    def _call_transcriber(self, audio_path: str, language_hint: str = "auto") -> str | None:
        """Call the transcriber service."""
        try:
            from urllib.request import Request, urlopen

            payload = json.dumps({
                "audio_path": audio_path,
                "mode": "fast",
                "language_hint": language_hint,
                "timestamps": False,
            }).encode()

            req = Request(
                f"{TRANSCRIBER_URL}/transcribe",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urlopen(req, timeout=TRANSCRIBE_TIMEOUT) as resp:
                result = json.loads(resp.read())
                text = result.get("text", "").strip()
                if text:
                    lang = result.get("language", "?")
                    duration = result.get("duration_audio", 0)
                    log.info("Transcribed voice (%.0fs, %s): %s...", duration, lang, text[:60])
                    return text
                return None

        except Exception as e:
            log.warning("Transcription failed for %s: %s", audio_path, e)
            return None

    # --- DB updates ---

    def _update_content(self, message_id: str, content: str) -> bool:
        """Update message content in comms.db (triggers FTS reindex)."""
        conn = self.conn
        if not conn:
            return False
        try:
            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (content, message_id),
            )
            conn.commit()
            return True
        except Exception as e:
            log.error("Failed to update comms.db for %s: %s", message_id, e)
            return False

    def on_error(self, error: Exception, message: Message | None = None) -> None:
        log.error("MediaEnrichmentConsumer error: %s", error, exc_info=True)


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand (RFC 5869) using SHA-256."""
    hash_len = 32
    n = math.ceil(length / hash_len)
    okm = b""
    prev = b""
    for i in range(1, n + 1):
        prev = hmac.new(prk, prev + info + bytes([i]), hashlib.sha256).digest()
        okm += prev
    return okm[:length]
