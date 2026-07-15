"""
Invariant: the bridge's Telegram poll loop is actually fetching, not just the
process being alive.

python-telegram-bot's getUpdates loop can wedge while the process stays up:
launchd's KeepAlive and any process-liveness watchdog see a healthy PID, but no
updates are fetched — Telegram queues them, then drops them past its limit.
This happened live for 32h. The poll_heartbeat module in the bridge records a
timestamp on every successful getUpdates into ~/.aos/services/bridge/.last_poll.json;
this check reads that timestamp and restarts the bridge if it has gone stale
during active hours.

Scope is deliberately narrow to avoid false restarts:
  - Only acts when the bridge LaunchAgent is actually loaded (process liveness
    is KeepAlive's job, not this check's).
  - Only acts during active hours (a quiet overnight bridge legitimately polls
    on a long-poll timeout, but we don't want to churn it).
  - Only acts on a concrete stale timestamp. A missing heartbeat file (fresh
    boot, or a bridge still on pre-heartbeat code) is treated as OK, not stale.

The restart uses the canonical guarded-kickstart pattern (bootout → bootstrap →
kickstart wrapped so a drain-blocking TimeoutExpired is non-fatal), matching
SentinelPlistDriftCheck and migrations 054/056/071.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status


class BridgePollLivenessCheck(ReconcileCheck):
    name = "bridge_poll_liveness"
    description = "Bridge Telegram poll loop is fetching (not silently wedged)"

    HOME = Path.home()
    STATE_FILE = HOME / ".aos" / "services" / "bridge" / ".last_poll.json"
    GOALS_YAML = HOME / "aos" / "config" / "goals.yaml"
    PLIST_NAME = "com.aos.bridge"
    PLIST_PATH = HOME / "Library" / "LaunchAgents" / "com.aos.bridge.plist"

    # A healthy long-poll returns at least every ~10s (PTB's default timeout),
    # so 5 minutes of silence means the loop has stopped, not merely idled.
    STALE_AFTER_S = 300

    # ── Signals ──────────────────────────────────────────────────────────────

    def _service_running(self) -> bool:
        """True if the bridge LaunchAgent is loaded (has a launchd entry)."""
        uid = os.getuid()
        try:
            r = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{self.PLIST_NAME}"],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _last_poll_age(self) -> float | None:
        """Seconds since the last recorded poll, or None if unknown."""
        try:
            data = json.loads(self.STATE_FILE.read_text())
            ts = float(data.get("ts", 0))
            if ts <= 0:
                return None
            return time.time() - ts
        except Exception:
            return None

    def _is_active_hours(self) -> bool:
        """Honor goals.yaml work_hours; default 07:00-23:00 America/Toronto."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz_name, start_str, end_str = "America/Toronto", "07:00", "23:00"
        try:
            import yaml
            data = yaml.safe_load(self.GOALS_YAML.read_text())
            wh = (data or {}).get("work_hours", {})
            tz_name = wh.get("timezone", tz_name)
            start_str, end_str = wh.get("active", "07:00-23:00").split("-")
        except Exception:
            pass

        try:
            now = datetime.now(ZoneInfo(tz_name))
        except Exception:
            now = datetime.now()
        cur = now.hour * 60 + now.minute
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        return (sh * 60 + sm) <= cur < (eh * 60 + em)

    # ── Check / fix ──────────────────────────────────────────────────────────

    def check(self) -> bool:
        # Bridge not loaded → process liveness is KeepAlive's concern, not ours.
        if not self._service_running():
            return True
        # Outside active hours, a long idle is expected — don't churn.
        if not self._is_active_hours():
            return True
        age = self._last_poll_age()
        # No concrete timestamp (fresh boot / pre-heartbeat bridge) → can't call
        # it stale. Only a real, aged timestamp counts as a wedge.
        if age is None:
            return True
        return age <= self.STALE_AFTER_S

    def _guarded_kickstart(self) -> str | None:
        """bootout → bootstrap → kickstart. Returns a detail note or None.

        kickstart -k can block past the timeout while the old instance drains;
        a TimeoutExpired here must not turn the fix into an ERROR — the restart
        was issued and KeepAlive will bring the job back.
        """
        uid = os.getuid()
        domain = f"gui/{uid}"
        service = f"gui/{uid}/{self.PLIST_NAME}"

        subprocess.run(["launchctl", "bootout", service],
                       capture_output=True, timeout=10)
        time.sleep(1)
        subprocess.run(["launchctl", "bootstrap", domain, str(self.PLIST_PATH)],
                       capture_output=True, timeout=10)
        try:
            subprocess.run(["launchctl", "kickstart", "-k", service],
                           capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            return "kickstart timed out (old instance draining) — KeepAlive will restart"
        return None

    def fix(self) -> CheckResult:
        age = self._last_poll_age()
        age_str = f"{int(age)}s" if age is not None else "unknown"

        if not self.PLIST_PATH.exists():
            return CheckResult(
                self.name, Status.NOTIFY,
                f"Bridge poll stale ({age_str}) but plist missing — cannot restart",
                detail=str(self.PLIST_PATH),
                notify=True,
            )

        note = self._guarded_kickstart()
        detail = f"last poll {age_str} ago (threshold {self.STALE_AFTER_S}s)"
        if note:
            detail = f"{detail}; {note}"
        return CheckResult(
            self.name, Status.FIXED,
            "Bridge poll loop wedged — restarted the bridge",
            detail=detail,
        )
