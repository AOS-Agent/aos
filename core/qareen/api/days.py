"""Qareen API — Days routes.

Structured day data for the Days temporal navigation surface.
Returns prayer times, sessions, tasks, people, health, and reflections
for any given date.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/days", tags=["days"])

VAULT_DIR = Path.home() / "vault"
AOS_DATA = Path.home() / ".aos"
SESSIONS_DIR = VAULT_DIR / "log" / "sessions"
HEALTH_DIR = AOS_DATA / "data" / "health"
PEOPLE_DB = AOS_DATA / "data" / "people.db"
WORK_FILE = AOS_DATA / "work" / "work.yaml"
MORNING_CONTEXT = AOS_DATA / "work" / "morning-context.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split markdown into (frontmatter_dict, body)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    raw = content[3:end].strip()
    body = content[end + 3:].lstrip("\n")
    try:
        fm = yaml.safe_load(raw)
        return (fm, body) if isinstance(fm, dict) else ({}, content)
    except Exception:
        return {}, content


def _prayer_period(time_str: str, prayer_times: dict[str, str]) -> str:
    """Determine which prayer period a time falls into.

    Returns the name of the current prayer period (fajr, sunrise, duha,
    dhuhr, asr, maghrib, isha, or night).
    """
    if not prayer_times or not time_str:
        return "unknown"

    try:
        t = int(time_str.replace(":", ""))
    except (ValueError, AttributeError):
        return "unknown"

    # Build ordered boundaries
    boundaries: list[tuple[int, str]] = []
    order = ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"]
    for name in order:
        val = prayer_times.get(name, "")
        if val:
            try:
                boundaries.append((int(val.replace(":", "")), name))
            except (ValueError, AttributeError):
                pass

    if not boundaries:
        return "unknown"

    # Walk boundaries in reverse to find which period we're in
    for i in range(len(boundaries) - 1, -1, -1):
        if t >= boundaries[i][0]:
            period = boundaries[i][1]
            # Sunrise-to-Dhuhr is "duha" (morning)
            if period == "sunrise":
                return "duha"
            return period

    # Before fajr
    return "night"


def _extract_reflections(daily_log_path: Path) -> str:
    """Extract the ## Reflections section content from a daily log."""
    if not daily_log_path.is_file():
        return ""
    try:
        content = daily_log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    # Find ## Reflections section
    match = re.search(r"^## Reflections\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    text = match.group(1).strip()
    # Filter out empty/placeholder content
    if not text or text == "---":
        return ""
    return text


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_prayer_times(target_date: str) -> dict[str, str]:
    """Load prayer times from morning-context cache.

    For the current date, uses exact cached times.
    For other dates, uses cached times as approximation (off by ~1-2 min/day
    for nearby dates, more for distant ones). Good enough for period grouping.
    """
    if not MORNING_CONTEXT.is_file():
        return {}
    try:
        ctx = yaml.safe_load(MORNING_CONTEXT.read_text())
        if not isinstance(ctx, dict):
            return {}
        return ctx.get("prayer_times", {})
    except Exception:
        return {}


def _load_weather(target_date: str) -> dict[str, Any] | None:
    """Load weather from morning-context cache."""
    if not MORNING_CONTEXT.is_file():
        return None
    try:
        ctx = yaml.safe_load(MORNING_CONTEXT.read_text())
        if isinstance(ctx, dict) and str(ctx.get("date", "")) == target_date:
            return ctx.get("weather")
        return None
    except Exception:
        return None


def _load_sessions(target_date: str, prayer_times: dict[str, str]) -> list[dict[str, Any]]:
    """Load session exports for a given date."""
    if not SESSIONS_DIR.is_dir():
        return []

    sessions: list[dict[str, Any]] = []
    for path in sorted(SESSIONS_DIR.iterdir()):
        if not path.name.startswith(target_date) or path.suffix != ".md":
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fm, body = _parse_frontmatter(content)
        if not fm:
            continue

        # Extract summary from body (first paragraph after ## Summary)
        summary = ""
        sum_match = re.search(r"^## Summary\s*\n(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL)
        if sum_match:
            summary = sum_match.group(1).strip()
            # Clean up: take first meaningful lines
            lines = [l for l in summary.split("\n") if l.strip()]
            summary = "\n".join(lines[:4])

        # Extract tools and files from body
        tools: list[str] = []
        tools_match = re.search(r"\*\*Tools\*\*:\s*(.+)", body)
        if tools_match:
            tools = [t.strip() for t in tools_match.group(1).split(",")]

        files: list[str] = []
        files_match = re.search(r"^## Files\s*\n(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL)
        if files_match:
            files = [l.strip("- \n`") for l in files_match.group(1).strip().split("\n") if l.strip().startswith("-")]

        start_time = str(fm.get("time", ""))
        sessions.append({
            "id": fm.get("session_id", path.stem),
            "title": fm.get("title", path.stem),
            "project": fm.get("project", ""),
            "start_time": start_time,
            "duration_min": fm.get("duration_min", 0),
            "message_count": fm.get("message_count", 0),
            "summary": summary,
            "tools": tools[:10],
            "files": files[:8],
            "tags": fm.get("tags", []),
            "prayer_period": _prayer_period(start_time, prayer_times),
        })

    return sessions


def _load_tasks(target_date: str) -> dict[str, list[dict[str, Any]]]:
    """Load tasks relevant to a given date from work.yaml."""
    result: dict[str, list[dict[str, Any]]] = {
        "completed": [],
        "started": [],
        "active": [],
    }

    if not WORK_FILE.is_file():
        return result

    try:
        data = yaml.safe_load(WORK_FILE.read_text())
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
    except Exception:
        return result

    for task in tasks:
        if not isinstance(task, dict):
            continue

        task_info = {
            "id": task.get("id", ""),
            "title": task.get("title", ""),
            "project": task.get("project", ""),
            "priority": task.get("priority", 3),
            "status": task.get("status", ""),
        }

        # Check completed date
        completed = str(task.get("completed", ""))
        if completed.startswith(target_date):
            result["completed"].append(task_info)
            continue

        # Check started date
        started = str(task.get("started", ""))
        if started.startswith(target_date):
            result["started"].append(task_info)

        # Active tasks (no date filter — show all currently active)
        if task.get("status") == "active":
            result["active"].append(task_info)

    return result


def _load_people() -> dict[str, Any]:
    """Load people data — drifting relationships and recent interactions."""
    result: dict[str, Any] = {"drifting": [], "recent_interactions": []}

    if not PEOPLE_DB.is_file():
        return result

    try:
        conn = sqlite3.connect(f"file:{PEOPLE_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # Drifting or overdue relationships (importance 1-2)
        rows = conn.execute("""
            SELECT p.canonical_name, p.importance,
                   rs.days_since_contact, rs.avg_days_between,
                   rs.trajectory, rs.last_interaction_channel
            FROM relationship_state rs
            JOIN people p ON p.id = rs.person_id
            WHERE p.importance <= 2
              AND p.is_archived = 0
              AND rs.days_since_contact IS NOT NULL
              AND (rs.trajectory = 'drifting'
                   OR rs.days_since_contact > COALESCE(rs.avg_days_between, 999) * 1.5)
            ORDER BY p.importance ASC, rs.days_since_contact DESC
            LIMIT 5
        """).fetchall()

        for row in rows:
            result["drifting"].append({
                "name": row["canonical_name"],
                "importance": row["importance"],
                "days_since_contact": row["days_since_contact"],
                "avg_days_between": round(row["avg_days_between"], 1) if row["avg_days_between"] else None,
                "trajectory": row["trajectory"],
                "last_channel": row["last_interaction_channel"],
            })

        # Recent interactions (today/yesterday)
        rows = conn.execute("""
            SELECT p.canonical_name, i.channel, i.direction, i.msg_count,
                   i.occurred_at, i.subject
            FROM interactions i
            JOIN people p ON p.id = i.person_id
            WHERE i.occurred_at > unixepoch('now', '-2 days')
            ORDER BY i.occurred_at DESC
            LIMIT 10
        """).fetchall()

        for row in rows:
            result["recent_interactions"].append({
                "name": row["canonical_name"],
                "channel": row["channel"],
                "direction": row["direction"],
                "msg_count": row["msg_count"],
                "timestamp": row["occurred_at"],
                "subject": row["subject"],
            })

        conn.close()
    except Exception:
        logger.exception("Failed to query people.db")

    return result


def _load_health(target_date: str) -> dict[str, Any] | None:
    """Load health data for a given date."""
    health_file = HEALTH_DIR / f"{target_date}.json"
    if not health_file.is_file():
        return None
    try:
        data = json.loads(health_file.read_text())
        if isinstance(data, dict):
            return {
                "steps": data.get("steps"),
                "distance_km": round(data.get("distance", 0), 2) if data.get("distance") else None,
                "active_energy_kcal": round(data.get("active_energy", 0), 1) if data.get("active_energy") else None,
                "sleep_hours": data.get("sleep_hours"),
                "resting_hr": data.get("resting_hr"),
            }
        return None
    except Exception:
        return None


def _load_daily_log_meta(target_date: str) -> dict[str, Any]:
    """Load metadata from the compiled daily log."""
    log_path = VAULT_DIR / "log" / f"{target_date}.md"
    if not log_path.is_file():
        return {}
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        fm, _ = _parse_frontmatter(content)
        return {
            "hijri_date": fm.get("hijri_date", ""),
            "day_name": fm.get("day", ""),
            "title": fm.get("title", ""),
            "sessions_count": fm.get("sessions"),
            "tasks_completed_count": fm.get("tasks_completed"),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Period grouping + enrichment
# ---------------------------------------------------------------------------

PERIOD_ORDER = ["night", "fajr", "duha", "dhuhr", "asr", "maghrib", "isha"]
PERIOD_LABELS = {
    "night": "Before Fajr", "fajr": "Fajr", "duha": "Duha",
    "dhuhr": "Dhuhr", "asr": "Asr", "maghrib": "Maghrib", "isha": "Isha",
}


def _load_tasks_with_time(target_date: str, prayer_times: dict[str, str]) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Load tasks split into (completed_with_period, active, stale_handoffs)."""
    completed: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []

    if not WORK_FILE.is_file():
        return completed, active, stale

    try:
        data = yaml.safe_load(WORK_FILE.read_text())
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
    except Exception:
        return completed, active, stale

    now = datetime.now()
    for task in tasks:
        if not isinstance(task, dict):
            continue

        info = {
            "id": task.get("id", ""),
            "title": task.get("title", ""),
            "project": task.get("project", ""),
            "priority": task.get("priority", 3),
        }

        # Completed tasks — place in prayer period by completion time
        completed_str = str(task.get("completed", ""))
        if completed_str.startswith(target_date):
            # Extract time from datetime string (e.g., "2026-04-02T00:34:32")
            time_part = ""
            if "T" in completed_str:
                time_part = completed_str.split("T")[1][:5]  # "00:34"
            info["completed_time"] = time_part
            info["prayer_period"] = _prayer_period(time_part, prayer_times) if time_part else "unknown"
            completed.append(info)
            continue

        # Active tasks — for carry section
        if task.get("status") == "active":
            handoff = task.get("handoff", {})
            handoff_age = None
            if isinstance(handoff, dict) and handoff.get("updated"):
                try:
                    updated = datetime.fromisoformat(str(handoff["updated"]))
                    handoff_age = (now - updated).days
                except (ValueError, TypeError):
                    pass

            info["handoff_next"] = handoff.get("next_step", "") if isinstance(handoff, dict) else ""
            info["handoff_age_days"] = handoff_age
            info["sessions_count"] = len(task.get("sessions", []))
            active.append(info)

            # Stale if handoff > 3 days old
            if handoff_age is not None and handoff_age > 3:
                stale.append(info)

    return completed, active, stale


def _load_communications(target_date: str, prayer_times: dict[str, str]) -> list[dict[str, Any]]:
    """Load communications (interactions) for the date, placed in prayer periods."""
    if not PEOPLE_DB.is_file():
        return []

    comms: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{PEOPLE_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT p.canonical_name, i.channel, i.direction, i.msg_count,
                   i.occurred_at, i.subject,
                   time(i.occurred_at, 'unixepoch', 'localtime') as local_time
            FROM interactions i
            JOIN people p ON p.id = i.person_id
            WHERE date(i.occurred_at, 'unixepoch', 'localtime') = ?
            ORDER BY i.occurred_at
        """, (target_date,)).fetchall()

        for row in rows:
            local_time = row["local_time"] or ""
            comms.append({
                "name": row["canonical_name"],
                "channel": row["channel"],
                "direction": row["direction"],
                "msg_count": row["msg_count"],
                "time": local_time[:5],  # "HH:MM"
                "subject": row["subject"],
                "prayer_period": _prayer_period(local_time[:5], prayer_times) if local_time else "unknown",
            })

        conn.close()
    except Exception:
        logger.exception("Failed to load communications")

    return comms


def _build_periods(
    sessions: list[dict[str, Any]],
    completed_tasks: list[dict[str, Any]],
    communications: list[dict[str, Any]],
    prayer_times: dict[str, str],
) -> list[dict[str, Any]]:
    """Group all time-placed data into prayer period blocks."""
    # Build period buckets
    buckets: dict[str, dict[str, list]] = {
        p: {"sessions": [], "tasks": [], "comms": []} for p in PERIOD_ORDER
    }

    for s in sessions:
        period = s.get("prayer_period", "unknown")
        if period in buckets:
            buckets[period]["sessions"].append(s)

    for t in completed_tasks:
        period = t.get("prayer_period", "unknown")
        if period in buckets:
            buckets[period]["tasks"].append(t)

    for c in communications:
        period = c.get("prayer_period", "unknown")
        if period in buckets:
            buckets[period]["comms"].append(c)

    # Build period objects — only include periods with content
    periods = []
    time_lookup = {
        "fajr": prayer_times.get("fajr", ""),
        "duha": prayer_times.get("sunrise", ""),
        "dhuhr": prayer_times.get("dhuhr", ""),
        "asr": prayer_times.get("asr", ""),
        "maghrib": prayer_times.get("maghrib", ""),
        "isha": prayer_times.get("isha", ""),
        "night": "",
    }

    for period_name in PERIOD_ORDER:
        bucket = buckets[period_name]
        s_count = len(bucket["sessions"])
        t_count = len(bucket["tasks"])
        c_count = len(bucket["comms"])

        if s_count == 0 and t_count == 0 and c_count == 0:
            continue

        # Build collapsed summary
        parts = []
        if s_count:
            parts.append(f"{s_count} session{'s' if s_count != 1 else ''}")
        if t_count:
            parts.append(f"{t_count} task{'s' if t_count != 1 else ''}")
        # Add first person name from comms
        if c_count:
            first_person = bucket["comms"][0]["name"].split()[0]
            channel = bucket["comms"][0]["channel"]
            if c_count == 1:
                parts.append(f"{first_person} ({channel})")
            else:
                parts.append(f"{first_person} +{c_count - 1} ({channel})")

        periods.append({
            "name": period_name,
            "label": PERIOD_LABELS.get(period_name, period_name),
            "time": time_lookup.get(period_name, ""),
            "collapsed_summary": " · ".join(parts),
            "sessions": bucket["sessions"],
            "tasks": bucket["tasks"],
            "communications": bucket["comms"],
        })

    return periods


def _compute_flow(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute flow metrics from session data."""
    if not sessions:
        return {}

    total = len(sessions)
    durations = [s.get("duration_min", 0) or 0 for s in sessions]
    total_min = sum(durations)
    deep_work = sum(d for d in durations if d >= 30)
    longest = max(durations) if durations else 0

    # Find which period the longest session was in
    longest_period = ""
    for s in sessions:
        if (s.get("duration_min", 0) or 0) == longest:
            longest_period = s.get("prayer_period", "")
            break

    # Project distribution
    projects: dict[str, int] = {}
    for s in sessions:
        proj = s.get("project", "") or "other"
        # Clean project name
        parts = proj.split("-")
        proj_clean = parts[-1] if parts else proj
        projects[proj_clean] = projects.get(proj_clean, 0) + 1

    # Session trend: compare first half vs second half average duration
    trend = "steady"
    if total >= 4:
        mid = total // 2
        first_half_avg = sum(durations[:mid]) / mid if mid else 0
        second_half_avg = sum(durations[mid:]) / (total - mid) if (total - mid) else 0
        if second_half_avg < first_half_avg * 0.6:
            trend = "fading"
        elif second_half_avg > first_half_avg * 1.4:
            trend = "building"

    return {
        "total_sessions": total,
        "total_duration_min": total_min,
        "deep_work_min": deep_work,
        "longest_block_min": longest,
        "longest_block_period": longest_period,
        "projects": projects,
        "session_trend": trend,
    }


def _generate_headline(
    sessions: list[dict[str, Any]],
    completed_tasks: list[dict[str, Any]],
    flow: dict[str, Any],
    health: dict[str, Any] | None,
) -> str:
    """Generate a one-sentence headline for the day."""
    parts = []

    # Session count + project spread
    total = flow.get("total_sessions", 0)
    projects = flow.get("projects", {})
    if total:
        proj_names = sorted(projects.keys(), key=lambda k: projects[k], reverse=True)
        if len(proj_names) <= 2:
            parts.append(f"{total} sessions on {' and '.join(proj_names)}")
        else:
            parts.append(f"{total} sessions across {len(proj_names)} projects")

    # Task count
    t_count = len(completed_tasks)
    if t_count:
        parts.append(f"{t_count} task{'s' if t_count != 1 else ''} shipped")

    # Flow character
    trend = flow.get("session_trend", "")
    if trend == "fading":
        parts.append("energy faded through the day")
    elif trend == "building":
        parts.append("momentum built through the day")

    # Health note
    if health and health.get("steps") and health["steps"] > 0:
        steps = health["steps"]
        if steps < 2000:
            parts.append("light day on your feet")

    if not parts:
        return "A quiet day."

    headline = ". ".join(parts[:3]) + "."
    # Capitalize first letter
    return headline[0].upper() + headline[1:]


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@router.get("/{target_date}")
async def get_day(
    target_date: str = PathParam(
        ...,
        description="Date in YYYY-MM-DD format",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
) -> JSONResponse:
    """Return structured day data organized by prayer periods."""
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        day_name = dt.strftime("%A")
    except ValueError:
        return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status_code=400)

    # Load raw data
    prayer_times = _load_prayer_times(target_date)
    weather = _load_weather(target_date)
    daily_meta = _load_daily_log_meta(target_date)
    sessions = _load_sessions(target_date, prayer_times)
    completed_tasks, active_tasks, stale_handoffs = _load_tasks_with_time(target_date, prayer_times)
    communications = _load_communications(target_date, prayer_times)
    people = _load_people()
    health = _load_health(target_date)
    reflections = _extract_reflections(VAULT_DIR / "log" / f"{target_date}.md")

    # Build period-grouped structure
    periods = _build_periods(sessions, completed_tasks, communications, prayer_times)

    # Compute flow metrics
    flow = _compute_flow(sessions)

    # Generate headline
    headline = _generate_headline(sessions, completed_tasks, flow, health)

    return JSONResponse({
        "date": target_date,
        "day_name": daily_meta.get("day_name") or day_name,
        "hijri_date": daily_meta.get("hijri_date", ""),
        "headline": headline,
        "prayer_times": prayer_times,
        "weather": weather,
        "health": health,
        "periods": periods,
        "flow": flow,
        "carry": {
            "active_tasks": active_tasks,
            "stale_handoffs": stale_handoffs,
        },
        "people_drift": people.get("drifting", []),
        "reflections": reflections,
    })


@router.post("/{target_date}/reflections")
async def save_reflections(
    target_date: str = PathParam(
        ...,
        description="Date in YYYY-MM-DD format",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
    text: str = Body(..., embed=True),
) -> JSONResponse:
    """Save reflections text to the daily log's ## Reflections section."""
    # Validate date
    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return JSONResponse({"error": "Invalid date format."}, status_code=400)

    log_path = VAULT_DIR / "log" / f"{target_date}.md"

    # If daily log exists, update the Reflections section
    if log_path.is_file():
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

        # Replace existing Reflections section
        pattern = r"(^## Reflections\s*\n)(.*?)(?=^## |\Z)"
        replacement = f"## Reflections\n\n{text.strip()}\n\n"
        new_content, count = re.subn(pattern, replacement, content, count=1, flags=re.MULTILINE | re.DOTALL)

        if count == 0:
            # No Reflections section found — append one
            new_content = content.rstrip() + f"\n\n## Reflections\n\n{text.strip()}\n"

        try:
            log_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    else:
        # Create a minimal daily log with just reflections
        minimal = f"""---
title: "{target_date}"
type: daily
date: "{target_date}"
tags: [daily]
---

## Reflections

{text.strip()}
"""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_path.write_text(minimal, encoding="utf-8")
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"ok": True, "date": target_date})


@router.get("/{target_date}/year-context")
async def get_year_context(
    target_date: str = PathParam(
        ...,
        description="Any date within the desired year (YYYY-MM-DD)",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
) -> JSONResponse:
    """Return 365 days of activity data for a year view."""
    year = target_date[:4]

    days_data: dict[str, dict[str, int]] = {}

    # Scan session exports
    if SESSIONS_DIR.is_dir():
        for path in SESSIONS_DIR.iterdir():
            if path.name.startswith(year) and path.suffix == ".md":
                day_str = path.name[:10]
                if day_str not in days_data:
                    days_data[day_str] = {"sessions": 0, "tasks_completed": 0}
                days_data[day_str]["sessions"] += 1

    # Scan daily logs for task counts
    log_dir = VAULT_DIR / "log"
    if log_dir.is_dir():
        for path in log_dir.iterdir():
            if path.name.startswith(year) and path.suffix == ".md" and len(path.stem) == 10:
                day_str = path.stem
                if day_str not in days_data:
                    days_data[day_str] = {"sessions": 0, "tasks_completed": 0}
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")[:500]
                    fm, _ = _parse_frontmatter(content)
                    if fm.get("tasks_completed"):
                        days_data[day_str]["tasks_completed"] = fm["tasks_completed"]
                    if fm.get("sessions"):
                        days_data[day_str]["sessions"] = max(
                            days_data[day_str]["sessions"], fm["sessions"]
                        )
                except OSError:
                    pass

    return JSONResponse({
        "year": year,
        "days": days_data,
    })


@router.get("/{target_date}/week-context")
async def get_week_context(
    target_date: str = PathParam(
        ...,
        description="Any date within the desired week (YYYY-MM-DD)",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
) -> JSONResponse:
    """Return structured data for a week view — 7 days of activity + weekly review."""
    from datetime import timedelta

    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return JSONResponse({"error": "Invalid date format."}, status_code=400)

    # Find Monday of this week
    weekday = dt.weekday()  # Monday=0
    monday = dt - timedelta(days=weekday)

    days_list = []
    for i in range(7):
        day = monday + timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        day_name = day.strftime("%A")
        short_name = day.strftime("%a")

        # Count sessions for this day
        session_count = 0
        if SESSIONS_DIR.is_dir():
            for p in SESSIONS_DIR.iterdir():
                if p.name.startswith(day_str) and p.suffix == ".md":
                    session_count += 1

        # Check daily log
        log_path = VAULT_DIR / "log" / f"{day_str}.md"
        tasks_completed = 0
        hijri = ""
        has_log = log_path.is_file()
        if has_log:
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")[:500]
                fm, _ = _parse_frontmatter(content)
                tasks_completed = fm.get("tasks_completed", 0) or 0
                hijri = fm.get("hijri_date", "")
                session_count = max(session_count, fm.get("sessions", 0) or 0)
            except OSError:
                pass

        days_list.append({
            "date": day_str,
            "day_name": day_name,
            "short_name": short_name,
            "sessions": session_count,
            "tasks_completed": tasks_completed,
            "hijri_date": hijri,
            "has_log": has_log,
            "is_today": day_str == datetime.now().strftime("%Y-%m-%d"),
        })

    # Find weekly review file (YYYY-WNN.md)
    iso_cal = monday.isocalendar()
    week_file = VAULT_DIR / "log" / f"{iso_cal[0]}-W{iso_cal[1]:02d}.md"
    weekly_review = None
    if week_file.is_file():
        try:
            content = week_file.read_text(encoding="utf-8", errors="replace")
            fm, body = _parse_frontmatter(content)
            weekly_review = {
                "title": fm.get("title", ""),
                "sessions": fm.get("sessions", 0),
                "tasks_completed": fm.get("tasks_completed", 0),
                "body": body[:3000],  # Cap body length
            }
        except OSError:
            pass

    # Totals
    total_sessions = sum(d["sessions"] for d in days_list)
    total_tasks = sum(d["tasks_completed"] for d in days_list)

    return JSONResponse({
        "week_start": monday.strftime("%Y-%m-%d"),
        "week_end": (monday + timedelta(days=6)).strftime("%Y-%m-%d"),
        "iso_week": f"{iso_cal[0]}-W{iso_cal[1]:02d}",
        "days": days_list,
        "totals": {
            "sessions": total_sessions,
            "tasks_completed": total_tasks,
        },
        "weekly_review": weekly_review,
    })


@router.get("/{target_date}/calendar-context")
async def get_calendar_context(
    target_date: str = PathParam(
        ...,
        description="Year-month in YYYY-MM format or full date YYYY-MM-DD",
    ),
) -> JSONResponse:
    """Return lightweight per-day activity data for a month (for calendar grid).

    Accepts YYYY-MM-DD (uses that month) or can be called with YYYY-MM.
    Returns an object keyed by date with session_count and task_count.
    """
    # Extract year-month
    ym = target_date[:7]  # "2026-04"
    try:
        year, month = int(ym[:4]), int(ym[5:7])
    except (ValueError, IndexError):
        return JSONResponse({"error": "Invalid date format"}, status_code=400)

    days_data: dict[str, dict[str, Any]] = {}

    # Scan session exports for this month
    if SESSIONS_DIR.is_dir():
        for path in SESSIONS_DIR.iterdir():
            if path.name.startswith(ym) and path.suffix == ".md":
                day_str = path.name[:10]
                if day_str not in days_data:
                    days_data[day_str] = {"sessions": 0, "tasks_completed": 0, "has_log": False}
                days_data[day_str]["sessions"] += 1

    # Scan daily logs for this month
    log_dir = VAULT_DIR / "log"
    if log_dir.is_dir():
        for path in log_dir.iterdir():
            if path.name.startswith(ym) and path.suffix == ".md" and len(path.stem) == 10:
                day_str = path.stem
                if day_str not in days_data:
                    days_data[day_str] = {"sessions": 0, "tasks_completed": 0, "has_log": False}
                days_data[day_str]["has_log"] = True
                # Read frontmatter for task count
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")[:500]
                    fm, _ = _parse_frontmatter(content)
                    if fm.get("tasks_completed"):
                        days_data[day_str]["tasks_completed"] = fm["tasks_completed"]
                    if fm.get("sessions"):
                        days_data[day_str]["sessions"] = max(
                            days_data[day_str]["sessions"], fm["sessions"]
                        )
                except OSError:
                    pass

    return JSONResponse({
        "month": ym,
        "days": days_data,
    })
