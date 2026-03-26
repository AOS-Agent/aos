#!/usr/bin/env python3
"""Extraction lifecycle — automatic triggers for the pipeline.

Two entry points:
1. `check_and_run()` — called by reconcile. Runs extraction if:
   - Never run before (fresh install)
   - New channel detected since last run
   - Last run was > 24h ago (daily catchup)

2. `on_channel_activated(channel)` — called when a new integration
   is connected. Runs extraction for just that channel.

Both are idempotent and safe to call repeatedly.

Marker file: ~/.aos/data/.extraction-state.json
Tracks: last_run_at, channels_extracted, interaction_count
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

STATE_FILE = Path.home() / ".aos" / "data" / ".extraction-state.json"
PEOPLE_DB = Path.home() / "vault" / "people" / "people.db"


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _get_available_channels() -> list[str]:
    """Detect which local data sources are available."""
    channels = []

    # iMessage
    imessage_db = Path.home() / "Library" / "Messages" / "chat.db"
    if imessage_db.exists():
        channels.append("imessage")

    # WhatsApp local (desktop app)
    wa_db = (
        Path.home() / "Library" / "Group Containers"
        / "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"
    )
    if wa_db.exists():
        channels.append("whatsapp_local")

    # Telegram (bridge queue)
    tg_queue = Path.home() / ".aos" / "data" / "telegram-messages.jsonl"
    if tg_queue.exists():
        channels.append("telegram")

    return channels


def _notify(message: str):
    """Send Telegram notification."""
    try:
        import subprocess
        script = Path.home() / "aos" / "core" / "lib" / "notify.py"
        if script.exists():
            subprocess.run(
                [sys.executable, str(script), message],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


def check_and_run() -> dict:
    """Check if extraction should run, and run it if needed.

    Called by reconcile system or manually. Safe to call repeatedly.

    Returns:
        {"ran": bool, "reason": str, "result": dict|None}
    """
    state = _load_state()
    available = _get_available_channels()

    if not available:
        return {"ran": False, "reason": "No channels available"}

    if not PEOPLE_DB.exists():
        return {"ran": False, "reason": "People DB not bootstrapped yet"}

    # Check if we need to run
    last_run = state.get("last_run_at", 0)
    prev_channels = set(state.get("channels_extracted", []))
    new_channels = set(available) - prev_channels
    age_hours = (time.time() - last_run) / 3600 if last_run else float("inf")

    reason = None
    channels_to_extract = None

    if not last_run:
        reason = "First extraction (fresh install)"
        channels_to_extract = available
    elif new_channels:
        reason = f"New channels detected: {', '.join(new_channels)}"
        channels_to_extract = list(new_channels)
    elif age_hours > 24:
        reason = f"Daily refresh ({age_hours:.0f}h since last run)"
        channels_to_extract = available
    else:
        return {"ran": False, "reason": f"Up to date (last run {age_hours:.1f}h ago)"}

    log.info(f"Extraction triggered: {reason}")

    # Run pipeline
    try:
        from .pipeline import run_extraction
        result = run_extraction(days=365, channels=channels_to_extract)

        # Run patterns
        _run_patterns()

        # Run graduation
        _run_graduation()

        # Update state
        state["last_run_at"] = time.time()
        state["channels_extracted"] = available
        state["last_result"] = {
            "messages": result.get("total_messages", 0),
            "interactions": result.get("total_interactions", 0),
            "state_updates": result.get("state_updates", 0),
        }
        _save_state(state)

        # Notify
        msgs = result.get("total_messages", 0)
        ixs = result.get("total_interactions", 0)
        if msgs > 0:
            _notify(
                f"📊 Extraction complete: {msgs:,} messages → {ixs:,} interactions "
                f"from {', '.join(channels_to_extract)}"
            )

        return {"ran": True, "reason": reason, "result": result}

    except Exception as e:
        log.error(f"Extraction failed: {e}")
        return {"ran": False, "reason": f"Error: {e}"}


def _run_patterns():
    """Run pattern computation after extraction."""
    try:
        _aos_dev = str(Path.home() / "project" / "aos")
        _aos_root = str(Path.home() / "aos")
        for p in [_aos_dev, _aos_root]:
            if p not in sys.path:
                sys.path.insert(0, p)
        from core.comms.patterns.compute import run_compute
        run_compute()
    except Exception as e:
        log.warning(f"Pattern compute failed: {e}")


def _run_graduation():
    """Run graduation evaluator after patterns."""
    try:
        _people = str(Path.home() / ".aos" / "services" / "people")
        if _people not in sys.path:
            sys.path.insert(0, _people)
        _grad = str(Path.home() / "project" / "aos" / "core" / "comms" / "graduation")
        if _grad not in sys.path:
            sys.path.insert(0, _grad)
        from runner import run
        run()
    except Exception as e:
        log.warning(f"Graduation evaluator failed: {e}")


def on_channel_activated(channel: str) -> dict:
    """Run extraction for a newly activated channel.

    Called when an integration is first connected (e.g., WhatsApp setup complete).
    """
    log.info(f"Channel activated: {channel}")

    state = _load_state()

    try:
        from .pipeline import run_extraction
        result = run_extraction(days=365, channels=[channel])

        _run_patterns()
        _run_graduation()

        # Update state
        prev = set(state.get("channels_extracted", []))
        prev.add(channel)
        state["channels_extracted"] = list(prev)
        state["last_run_at"] = time.time()
        _save_state(state)

        msgs = result.get("total_messages", 0)
        ixs = result.get("total_interactions", 0)
        if msgs > 0:
            _notify(
                f"✅ Connected {channel}: {msgs:,} messages → {ixs:,} interactions extracted"
            )

        return {"ran": True, "channel": channel, "result": result}

    except Exception as e:
        log.error(f"Channel activation extraction failed: {e}")
        return {"ran": False, "channel": channel, "error": str(e)}


# ── CLI ──────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = check_and_run()
    print(json.dumps(result, indent=2, default=str))
