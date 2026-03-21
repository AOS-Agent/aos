"""Heartbeat — periodic health checks, silent when clear, alerts only on new issues."""

import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from activity_client import log_activity as log_dashboard_activity

logger = logging.getLogger(__name__)

WORKSPACE = Path.home() / "aos"

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

    # Disk
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

    # Listen server
    listen_ok = False
    listen_detail = "DOWN"
    try:
        r = httpx.get("http://localhost:7600/jobs", timeout=3)
        if r.status_code == 200:
            listen_ok = True
            jobs = r.json()
            if isinstance(jobs, list):
                active_jobs = sum(1 for j in jobs if j.get("status") == "running")
                listen_detail = f"running ({active_jobs} active)"
            else:
                listen_detail = "running"
    except Exception:
        pass

    # Dashboard
    dashboard_ok = False
    try:
        r = httpx.get("http://localhost:4096/api/health", timeout=3)
        dashboard_ok = r.status_code == 200
    except Exception:
        pass

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
        "listen_ok": listen_ok,
        "listen_detail": listen_detail,
        "dashboard_ok": dashboard_ok,
        "bridge_ok": bridge_ok,
        "pending_tasks": pending_tasks,
    }


def _find_problems(health: dict) -> list[str]:
    """Return a list of human-readable problems. Empty list = all clear."""
    problems = []
    if health["disk_pct"] > 85:
        problems.append(f"Disk at {health['disk_pct']}% — consider cleanup")
    if health["ram_pct"] > 85:
        problems.append(f"RAM at {health['ram_pct']}% — check for runaway processes")
    if not health["listen_ok"]:
        problems.append("Listen server is DOWN")
    if not health["dashboard_ok"]:
        problems.append("Dashboard is DOWN")
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
                    summary = f"disk:{health['disk_pct']}% ram:{health['ram_pct']}% listen:{'ok' if health['listen_ok'] else 'DOWN'}"

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
