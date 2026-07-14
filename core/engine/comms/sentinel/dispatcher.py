"""Dispatcher — sends the approved draft via message-person CLI.

Marks the trigger row as sent on success, failed on error.
"""

from __future__ import annotations

import logging
import subprocess
import sqlite3
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
MESSAGE_PERSON = Path.home() / "aos" / "core" / "bin" / "cli" / "message-person"
# Fallback to dev workspace
if not MESSAGE_PERSON.exists():
    MESSAGE_PERSON = Path.home() / "project" / "aos" / "core" / "bin" / "cli" / "message-person"


def _update_status(trigger_id: str, status: str, **fields):
    conn = sqlite3.connect(str(COMMS_DB))
    cols = ["status = ?"]
    vals: list = [status]
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(v)
    vals.append(trigger_id)
    conn.execute(f"UPDATE agent_triggers SET {', '.join(cols)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def send_draft(trigger_id: str, person_canonical: str,
               channel: str, body: str,
               handle: Optional[str] = None,
               dry_run: bool = False) -> tuple[bool, str]:
    """Invoke message-person to send. Returns (success, info).

    If `handle` is provided (e.g. 'sam@example.com' or '+12025550143'),
    we use direct addressing (--email/--phone/--jid) which bypasses
    people.db resolution. This is more reliable for outbound triggers.
    """
    if not body.strip():
        return False, "empty body"
    if not MESSAGE_PERSON.exists():
        return False, f"message-person CLI not found at {MESSAGE_PERSON}"

    cmd = [str(MESSAGE_PERSON), "--channel", channel, "--text", body]

    # Prefer direct handle addressing when available
    if handle:
        if "@" in handle:
            cmd += ["--email", handle]
        elif handle.startswith("+") or handle.replace(" ", "").isdigit():
            cmd += ["--phone", handle]
        else:
            cmd += ["--to", handle]
    else:
        cmd += ["--to", person_canonical]
    if dry_run:
        cmd.append("--dry-run")

    log.info("Dispatcher invoking: %s", " ".join(cmd[:4]) + " --text <body>")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env={"SENTINEL_SOURCE": "sentinel", **__import__("os").environ},
        )
        if result.returncode == 0:
            now = int(time.time())
            _update_status(trigger_id, "sent", sent_at=now)
            return True, result.stdout.strip()
        else:
            _update_status(trigger_id, "failed",
                           error=f"message-person rc={result.returncode}: {result.stderr.strip()[:200]}")
            return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        _update_status(trigger_id, "failed", error="message-person timeout")
        return False, "timeout"
    except Exception as e:
        _update_status(trigger_id, "failed", error=str(e)[:200])
        return False, str(e)
