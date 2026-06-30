"""Trigger Detector consumer.

Runs after CommsStoreConsumer has written messages to comms.db. For each
outbound message on an enabled channel, checks for trigger phrases. If
found, inserts a row into `agent_triggers`. The Sentinel spawner daemon
picks it up from there.

IMPORTANT — connection hygiene:
- Uses short-lived connections (open → write → commit → close per call)
- Never holds a long-lived sqlite3.Connection (avoids WAL lock contention)
- Always rollback-on-error so transactions don't stick

ID strategy:
- We use msg.id directly as the agent_triggers.message_id (same id the
  CommsStoreConsumer wrote). No DB lookup needed for happy path — eliminates
  ordering dependency on whether comms_store committed first.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from ..bus import Consumer
from ..models import Message

log = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
CONFIG_PATH = Path.home() / ".aos" / "config" / "sentinel.yaml"
DB_TIMEOUT = 5.0  # seconds; never block the bus on stuck locks


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as e:
        log.warning("Could not load sentinel.yaml: %s", e)
        return {}


def _short_conn() -> sqlite3.Connection:
    """Open a short-lived autocommit connection."""
    conn = sqlite3.connect(str(COMMS_DB), timeout=DB_TIMEOUT, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class TriggerDetectorConsumer(Consumer):
    """Detect trigger phrases in outbound messages."""

    name = "trigger_detector"

    def __init__(self):
        self._detector = None
        self._config = None

    @property
    def config(self) -> dict:
        # Reload each poll so config edits take effect without restart
        return _load_config()

    @property
    def enabled(self) -> bool:
        c = self.config
        return bool(c.get("enabled", True)) and not bool(c.get("paused", False))

    @property
    def channels(self) -> set[str]:
        return set(self.config.get("channels", ["imessage"]))

    @property
    def detector(self):
        if self._detector is None:
            from core.engine.comms.triggers.detector import TriggerDetector
            phrases = self.config.get("trigger_phrases",
                                      ["consider it done", "@aos"])
            self._detector = TriggerDetector(phrases)
        return self._detector

    def process(self, messages: list[Message]) -> int:
        if not self.enabled or not messages:
            return 0

        try:
            detector = self.detector
        except Exception as e:
            log.warning("Detector unavailable: %s", e)
            return 0

        enabled_channels = self.channels
        fired = 0

        # Loop prevention: skip our own ack emoji sends
        from core.engine.comms.sentinel.ack import is_ack_message, send_ack

        for msg in messages:
            if not msg.from_me:
                continue
            if msg.channel not in enabled_channels:
                continue
            source = (msg.metadata or {}).get("source")
            if source == "sentinel":
                continue
            if is_ack_message(msg.text):
                continue
            if not msg.text or not msg.text.strip():
                continue

            match = detector.find_trigger(msg.text)
            if not match:
                continue

            ok = self._record_trigger(msg, match.phrase)
            if ok:
                fired += 1
                log.info("Trigger fired: %s (channel=%s, msg_id=%s)",
                         match.phrase, msg.channel, msg.id)
                # Fire ack synchronously — visible to operator + recipient
                try:
                    send_ack(msg.channel, msg.conversation_id)
                except Exception as e:
                    log.warning("Ack send failed (continuing): %s", e)

        return fired

    def _record_trigger(self, msg: Message, phrase: str) -> bool:
        """Insert a new trigger row using short-lived connection."""
        # Use msg.id as canonical comms.db message_id (same id comms_store uses)
        message_id = msg.id
        person_id = None  # context_builder resolves via chat.db later

        trigger_id = f"trg_{uuid.uuid4().hex[:12]}"
        now = int(time.time())

        conn = None
        try:
            conn = _short_conn()
            conn.execute("""
                INSERT INTO agent_triggers (
                    id, message_id, person_id, channel, trigger_phrase,
                    agent_name, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trigger_id, message_id, person_id, msg.channel, phrase,
                "sentinel", "detected", now,
            ))
            return True
        except sqlite3.IntegrityError:
            # UNIQUE(message_id) — already recorded
            return False
        except sqlite3.OperationalError as e:
            log.error("Trigger insert OperationalError: %s", e)
            return False
        except Exception as e:
            log.exception("Trigger insert failed: %s", e)
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def on_error(self, error: Exception, message: Message | None = None) -> None:
        log.error("trigger_detector error: %s", error, exc_info=True)
