"""Heartbeat — periodic health checks, silent when clear, alerts only on new issues."""

import logging
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml
from conversation_store import record_notice_sent, record_outbound, should_send_notice

logger = logging.getLogger("aos.bridge.heartbeat")

WORKSPACE = Path.home() / "aos"

# Service identity (which services exist, their health URLs, their status) comes
# from the one registry — never a hardcoded probe. This is what stops a RETIRED
# service (listen) from being reported DOWN and a mislabeled port (:4096
# was labeled "Dashboard") from lingering.
sys.path.insert(0, str(WORKSPACE / "core" / "infra" / "lib"))
try:
    from service_registry import ManifestError, load_registry
except Exception:  # pragma: no cover — registry always ships; degrade gracefully
    load_registry = None
    ManifestError = Exception

# Alerts go through the notification router (topic -> General -> DM), so a
# broken forum still gets the message out. Loaded by file path, not by name:
# core/infra/lib/notify.py is already on this module's sys.path and would
# shadow the notify package. Kept optional — the heartbeat is the one thing
# that must still be able to speak when part of the system is broken.
def _load_router():
    import importlib.util

    path = WORKSPACE / "core" / "engine" / "notify" / "router.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("aos_notify_router", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    _router = _load_router()
except Exception:  # pragma: no cover
    _router = None

get_routing = getattr(_router, "get_routing", None)
send_notification = getattr(_router, "send_notification", None)


def _check_services() -> dict[str, dict]:
    """Probe each ACTIVE service that declares an HTTP health endpoint.
    Returns {name: {ok: bool}}. Retired/optional services are not probed —
    a retired service must never surface as DOWN."""
    results: dict[str, dict] = {}
    if load_registry is None:
        return results
    try:
        health_urls = load_registry().active_health_urls()
    except ManifestError:
        return results
    for name, url in health_urls.items():
        ok = False
        try:
            ok = httpx.get(url, timeout=3).status_code == 200
        except Exception:
            ok = False
        results[name] = {"ok": ok}
    return results

def _check_forum(bot_token: str) -> dict:
    """Is the notification group still a forum we can post into?

    A group admin can turn Topics off with one toggle, and Telegram gives no
    warning — from that moment every topic-routed notification silently lands
    in the group's main chat instead of its topic. Bots cannot turn Topics back
    on, so this is detection only: tell the operator, they flip it back.

    Returns {configured, ok, reason, detail}. reason is "" when healthy,
    "topics_off" when the toggle is off, "no_access" when the bot can no longer
    read the chat (kicked, deleted, wrong id). An unreachable API is NOT a
    problem — if we can't reach Telegram we couldn't deliver the alert anyway.
    """
    if get_routing is None:
        return {"configured": False, "ok": True, "reason": "", "detail": ""}
    try:
        group_id, _ = get_routing()
    except Exception:
        return {"configured": False, "ok": True, "reason": "", "detail": ""}
    if not group_id:
        # No forum group configured — DM-only setup, nothing to watch.
        return {"configured": False, "ok": True, "reason": "", "detail": ""}

    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{bot_token}/getChat",
            params={"chat_id": group_id},
            timeout=10,
        )
        body = resp.json()
    except Exception:
        return {"configured": True, "ok": True, "reason": "", "detail": ""}

    if not body.get("ok"):
        return {
            "configured": True,
            "ok": False,
            "reason": "no_access",
            "detail": body.get("description", "unknown error"),
        }
    if not body.get("result", {}).get("is_forum"):
        return {"configured": True, "ok": False, "reason": "topics_off", "detail": ""}
    return {"configured": True, "ok": True, "reason": "", "detail": ""}


# Startup delay to avoid race conditions with other LaunchAgents
STARTUP_DELAY_SECS = 60


def _get_work_hours() -> tuple[str, str, str]:
    """Return (timezone, active_start, active_end) from goals.yaml."""
    goals_path = WORKSPACE / "config" / "goals.yaml"
    if goals_path.exists():
        data = yaml.safe_load(goals_path.read_text())
        wh = data.get("work_hours", {})
        tz = wh.get("timezone", "America/Toronto")
        active = wh.get("active", "07:00-23:00")
        start, end = active.split("-")
        return tz, start, end
    return "America/Toronto", "07:00", "23:00"


def _is_active_hours() -> bool:
    tz_name, start_str, end_str = _get_work_hours()
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    start_h, start_m = map(int, start_str.split(":"))
    end_h, end_m = map(int, end_str.split(":"))
    current_minutes = now.hour * 60 + now.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    return start_minutes <= current_minutes < end_minutes


def _check_health(bot_token: str | None = None) -> dict:
    """Gather system health info. All checks are deterministic (no LLM)."""
    import shutil

    # Disk — use df instead of shutil.disk_usage because macOS counts
    # purgeable space as "used", giving false 85%+ readings on APFS volumes.
    disk_pct = 0
    try:
        df_result = subprocess.run(
            ["df", "-h", "/"], capture_output=True, text=True, timeout=5
        )
        # Parse "Capacity" column (e.g., "32%")
        for line in df_result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            for part in parts:
                if part.endswith("%"):
                    disk_pct = float(part.rstrip("%"))
                    break
    except Exception:
        usage = shutil.disk_usage("/")
        disk_pct = round(usage.used / usage.total * 100, 1)

    # RAM (macOS)
    ram_pct = 0
    try:
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        )
        pages = {}
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split(":")
            if len(parts) == 2:
                try:
                    pages[parts[0].strip()] = int(parts[1].strip().rstrip("."))
                except ValueError:
                    pass
        page_size = 16384  # Apple Silicon
        free = pages.get("Pages free", 0) * page_size
        active = pages.get("Pages active", 0) * page_size
        inactive = pages.get("Pages inactive", 0) * page_size
        wired = pages.get("Pages wired down", 0) * page_size
        total_used = active + wired
        total = free + active + inactive + wired
        ram_pct = round(total_used / total * 100, 1) if total else 0
    except Exception:
        ram_pct = -1

    # Memory pressure (the actionable signal; see alert note below)
    mem_free_pct = -1
    try:
        mp = subprocess.run(
            ["memory_pressure"], capture_output=True, text=True, timeout=5
        )
        for line in mp.stdout.splitlines():
            if "free percentage" in line:
                mem_free_pct = int(float(line.split(":")[-1].strip().rstrip("%")))
                break
    except Exception:
        pass

    # Active services with an HTTP health endpoint — derived from the registry,
    # not hardcoded, so retired services are never probed and never reported DOWN.
    services = _check_services()

    # Bridge (self — always true if we're running)
    bridge_ok = True

    # Pending tasks
    pending_tasks = 0
    tasks_path = WORKSPACE / "config" / "tasks.yaml"
    if tasks_path.exists():
        try:
            data = yaml.safe_load(tasks_path.read_text())
            tasks = data.get("tasks", []) if data else []
            pending_tasks = sum(1 for t in tasks if t.get("status") in ("pending", "in_progress"))
        except Exception:
            pass

    # Notification group — only checkable with a token in hand.
    forum = _check_forum(bot_token) if bot_token else {
        "configured": False, "ok": True, "reason": "", "detail": "",
    }

    return {
        "disk_pct": disk_pct,
        "ram_pct": ram_pct,
        "mem_free_pct": mem_free_pct,
        "services": services,
        "bridge_ok": bridge_ok,
        "pending_tasks": pending_tasks,
        "forum": forum,
    }


def _find_problems(health: dict) -> list[str]:
    """Return a list of phone-ready problem lines — each already emoji-led and
    plain-English (see MESSAGE_STYLE.md → "System alerts"). Empty list = all clear."""
    problems = []
    if health["disk_pct"] > 85:
        problems.append(
            f"🚨 The disk is almost full ({health['disk_pct']}%). Worth clearing some space soon."
        )
    # Raw used-% is the wrong alarm on macOS — the OS keeps RAM ~85% full by
    # design (caching/compression), so a >85% check cries wolf on any healthy
    # busy machine (operator got recurring false alerts, 2026-07-15). Alert on
    # memory PRESSURE instead: free-page percentage under 10% means the
    # compressor/swap are genuinely struggling.
    if 0 <= health.get("mem_free_pct", -1) < 10:
        problems.append(
            "🧠 Memory is under real pressure right now — the Mac may feel sluggish. "
            "Worth checking for a runaway app when you get a chance."
        )
    for name, state in health.get("services", {}).items():
        if not state.get("ok"):
            problems.append(f"🔴 {name} has stopped.")
    forum = health.get("forum", {})
    if not forum.get("ok", True):
        if forum.get("reason") == "topics_off":
            problems.append(
                "🧵 Topics got switched off in the AOS group, so my messages are "
                "landing in the main chat instead of their topics. Turn Topics "
                "back on in the group settings and they'll sort themselves again."
            )
        else:
            detail = forum.get("detail", "")
            problems.append(
                "🚫 I can't reach the AOS group any more"
                f"{f' ({detail})' if detail else ''}. Messages are coming here "
                "instead. Check I'm still in the group and still an admin."
            )
    if health["pending_tasks"] > 0:
        n = health["pending_tasks"]
        problems.append(f"📋 You've got {n} task{'s' if n != 1 else ''} waiting.")
    return problems


def _alert(bot_token: str, chat_id: int, msg: str) -> None:
    """Deliver a heartbeat alert to the alerts topic, however it can.

    The router already falls back topic -> General -> DM. The direct post below
    is the last resort for the case the router itself is missing: an alert about
    a broken system must not be lost because part of the system is broken.
    """
    if send_notification is not None:
        try:
            if send_notification(msg, topic="alerts", parse_mode=None)["delivered"]:
                return
        except Exception as e:
            logger.warning(f"Router unavailable for heartbeat alert: {e}")
    try:
        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Heartbeat alert undeliverable: {e}")


# How long a persisted notice suppresses a repeat send of the exact same
# problem text. This is the fix for aos#2324: every bridge restart used to
# re-send every still-open problem because the old dedupe (`last_reported`)
# was a plain local that died with the thread. A restart minutes later must
# not resend what a previous run already reported — but a problem that has
# genuinely stuck around for hours deserves a reminder eventually, so this is
# a window, not a permanent mute.
NOTICE_TTL_SECONDS = 6 * 60 * 60  # 6 hours


class Heartbeat:
    """One dedupe session for the life of a single bridge process.

    `last_reported` is process-local on purpose — within one run, a problem
    that clears and immediately recurs is treated as new (see
    `run_once`'s all-clear reset), which is the responsive behaviour the
    original code had. What it never had is anything that survived the
    process itself dying: that's `notice_state` in conversation_store.py,
    consulted via `should_send_notice`/`record_notice_sent` on every send, so
    a fresh instance after a restart still knows what the last one already
    told the operator.
    """

    def __init__(self, bot_token: str, chat_id: int,
                 ttl_seconds: float = NOTICE_TTL_SECONDS):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.ttl_seconds = ttl_seconds
        self.last_reported: set[str] = set()

    def run_once(self) -> list[str]:
        """Run a single heartbeat cycle. Returns the alert texts actually sent
        (empty during quiet hours, when nothing is wrong, or when everything
        currently wrong was already reported and is still within its TTL)."""
        if not _is_active_hours():
            logger.debug("Heartbeat: quiet hours, skipping")
            return []

        health = _check_health(self.bot_token)
        problems = _find_problems(health)
        svc_summary = " ".join(
            f"{n}:{'ok' if s.get('ok') else 'DOWN'}"
            for n, s in health.get("services", {}).items()
        ) or "no-http-services"
        # A health sample, not a message — it belongs in the log, not the
        # conversation store.
        logger.debug(
            f"Heartbeat: disk:{health.get('disk_pct', '?')}% "
            f"ram:{health.get('ram_pct', '?')}% {svc_summary}"
        )

        if not problems:
            # All clear — reset the in-run tracker so a recovered issue that
            # recurs later in this same process is reported promptly.
            self.last_reported.clear()
            logger.debug("Heartbeat: all clear")
            return []

        # Only consider problems not already flagged earlier in this run.
        new_problems = [p for p in problems if p not in self.last_reported]
        self.last_reported = set(problems)
        if not new_problems:
            return []

        # Cross-restart dedupe: skip anything the persisted state says was
        # already sent, recently, unchanged.
        due = [p for p in new_problems
               if should_send_notice(f"heartbeat:{p}", p, self.ttl_seconds)]
        if not due:
            return []

        # Each line is already emoji-led and human. Add a soft lead only when
        # there's more than one.
        if len(due) > 1:
            msg = "A couple of things worth knowing:\n\n" + "\n".join(due)
        else:
            msg = due[0]
        _alert(self.bot_token, self.chat_id, msg)
        record_outbound(msg, chat_id=self.chat_id, topic="alerts",
                        kind="heartbeat_alert")
        for p in due:
            record_notice_sent(f"heartbeat:{p}", p)
        logger.info(f"Heartbeat alert (new): {due}")
        return due


def start_heartbeat(bot_token: str, chat_id: int, interval_minutes: int = 30):
    """Start heartbeat as a daemon thread.

    - Delays first check by 60s to let other services start
    - Only messages when something is wrong
    - Only reports NEW problems, deduplicated both within this run and across
      restarts (`Heartbeat.run_once`)
    - Logs every check to dashboard (silent or not)
    """

    def _loop():
        hb = Heartbeat(bot_token, chat_id)

        # Wait for other services to start before first check
        threading.Event().wait(STARTUP_DELAY_SECS)

        while True:
            try:
                hb.run_once()
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

            threading.Event().wait(interval_minutes * 60)

    thread = threading.Thread(target=_loop, daemon=True, name="heartbeat")
    thread.start()
    return thread
