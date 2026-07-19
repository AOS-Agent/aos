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
from activity_client import log_activity as log_dashboard_activity

logger = logging.getLogger(__name__)

WORKSPACE = Path.home() / "aos"

# Service identity (which services exist, their health URLs, their status) comes
# from the one registry — never a hardcoded probe. This is what stops a RETIRED
# service (listen) from being reported DOWN and a mislabeled port (qareen :4096
# was labeled "Dashboard") from lingering.
sys.path.insert(0, str(WORKSPACE / "core" / "infra" / "lib"))
try:
    from service_registry import ManifestError, load_registry
except Exception:  # pragma: no cover — registry always ships; degrade gracefully
    load_registry = None
    ManifestError = Exception


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


def _check_health() -> dict:
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

    return {
        "disk_pct": disk_pct,
        "ram_pct": ram_pct,
        "mem_free_pct": mem_free_pct,
        "services": services,
        "bridge_ok": bridge_ok,
        "pending_tasks": pending_tasks,
    }


def _find_problems(health: dict) -> list[str]:
    """Return a list of human-readable problems. Empty list = all clear."""
    problems = []
    if health["disk_pct"] > 85:
        problems.append(f"Disk at {health['disk_pct']}% — consider cleanup")
    # Raw used-% is the wrong alarm on macOS — the OS keeps RAM ~85% full by
    # design (caching/compression), so a >85% check cries wolf on any healthy
    # busy machine (operator got recurring false alerts, 2026-07-15). Alert on
    # memory PRESSURE instead: free-page percentage under 10% means the
    # compressor/swap are genuinely struggling.
    if 0 <= health.get("mem_free_pct", -1) < 10:
        problems.append(
            f"Memory pressure critical — {health['mem_free_pct']}% free pages "
            f"(ram used {health['ram_pct']}%) — check for runaway processes"
        )
    for name, state in health.get("services", {}).items():
        if not state.get("ok"):
            problems.append(f"{name} is DOWN")
    if health["pending_tasks"] > 0:
        problems.append(f"{health['pending_tasks']} pending task(s)")
    return problems


def start_heartbeat(bot_token: str, chat_id: int, interval_minutes: int = 30):
    """Start heartbeat as a daemon thread.

    - Delays first check by 60s to let other services start
    - Only messages when something is wrong
    - Only reports NEW problems (deduplicates across cycles)
    - Logs every check to dashboard (silent or not)
    """

    def _loop():
        # Track which problems were already reported to avoid spam
        last_reported: set[str] = set()

        # Wait for other services to start before first check
        threading.Event().wait(STARTUP_DELAY_SECS)

        while True:
            try:
                if _is_active_hours():
                    health = _check_health()
                    problems = _find_problems(health)
                    svc_summary = " ".join(
                        f"{n}:{'ok' if s.get('ok') else 'DOWN'}"
                        for n, s in health.get("services", {}).items()
                    ) or "no-http-services"
                    summary = f"disk:{health['disk_pct']}% ram:{health['ram_pct']}% {svc_summary}"

                    # Always log to dashboard
                    log_dashboard_activity("ops", "heartbeat", summary=summary)

                    if problems:
                        # Only report NEW problems (not already flagged)
                        new_problems = [p for p in problems if p not in last_reported]

                        if new_problems:
                            msg = "Alert:\n" + "\n".join(f"  — {p}" for p in new_problems)
                            httpx.post(
                                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                                json={"chat_id": chat_id, "text": msg},
                                timeout=10,
                            )
                            log_dashboard_activity("ops", "heartbeat_alert", summary=msg[:200])
                            logger.info(f"Heartbeat alert (new): {new_problems}")

                        # Update tracked problems
                        last_reported = set(problems)
                    else:
                        # All clear — reset tracker so recovered issues can re-alert
                        last_reported.clear()
                        logger.debug("Heartbeat: all clear")
                else:
                    logger.debug("Heartbeat: quiet hours, skipping")
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

            threading.Event().wait(interval_minutes * 60)

    thread = threading.Thread(target=_loop, daemon=True, name="heartbeat")
    thread.start()
    return thread
