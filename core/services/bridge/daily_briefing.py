"""Daily briefing — proactive morning scan delivered via Telegram.

Runs once per day at a configured hour. Scans tasks, goals, system health,
and memory for staleness, then sends a formatted briefing to the operator.
"""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

logger = logging.getLogger(__name__)

WORKSPACE = Path.home() / "aos"


def _get_config() -> tuple[str, int, int]:
    """Return (timezone, briefing_hour, briefing_minute) from goals.yaml."""
    goals_path = WORKSPACE / "config" / "goals.yaml"
    if goals_path.exists():
        data = yaml.safe_load(goals_path.read_text())
        wh = data.get("work_hours", {})
        tz = wh.get("timezone", "America/Toronto")
        # Default: 8:00 AM
        return tz, 8, 0
    return "America/Toronto", 8, 0


def _load_yaml(path: Path) -> dict:
    if path.exists():
        return yaml.safe_load(path.read_text()) or {}
    return {}


def _build_briefing() -> str:
    """Build the daily briefing content."""
    tz_name, _, _ = _get_config()
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    lines = []
    lines.append(f"<b>Daily Briefing — {now.strftime('%A, %B %d')}</b>\n")

    # ── Goals ────────────────────────────────────────────
    goals = _load_yaml(WORKSPACE / "config" / "goals.yaml")
    objectives = goals.get("quarterly_objectives", [])
    if objectives:
        lines.append("<b>Goals</b>")
        for obj in objectives:
            name = obj.get("name", "Unnamed")
            krs = obj.get("key_results", [])
            done_count = sum(1 for kr in krs if "[DONE]" in str(kr))
            lines.append(f"  • {name} ({done_count}/{len(krs)} key results done)")
        lines.append("")

    # ── Tasks (from vault) ─────────────────────────────────
    try:
        from vault_tasks import get_tasks_by_status, get_active_tasks
        focus = get_tasks_by_status("focus")
        in_progress = get_tasks_by_status("in-progress")
        todo = get_tasks_by_status("todo")
        waiting = get_tasks_by_status("waiting")
        backlog = get_tasks_by_status("backlog")

        if focus:
            lines.append(f"<b>Focus ({len(focus)})</b>")
            for t in focus:
                domain = t.get("domain", "")
                lines.append(f"  🎯 {t['title']}" + (f" <i>({domain})</i>" if domain else ""))
            lines.append("")
        if in_progress:
            lines.append(f"<b>In Progress ({len(in_progress)})</b>")
            for t in in_progress:
                domain = t.get("domain", "")
                lines.append(f"  🔄 {t['title']}" + (f" <i>({domain})</i>" if domain else ""))
            lines.append("")
        if todo:
            lines.append(f"<b>Todo ({len(todo)})</b>")
            for t in todo[:5]:
                domain = t.get("domain", "")
                lines.append(f"  ⬜ {t['title']}" + (f" <i>({domain})</i>" if domain else ""))
            if len(todo) > 5:
                lines.append(f"  ... and {len(todo) - 5} more")
            lines.append("")
        if waiting:
            lines.append(f"<b>Waiting ({len(waiting)})</b>")
            for t in waiting:
                who = t.get("waiting_on", "?")
                lines.append(f"  ⏳ {t['title']} — on {who}")
            lines.append("")
        if backlog:
            lines.append(f"<b>Backlog</b>: {len(backlog)} items")
            lines.append("")
        if not any([focus, in_progress, todo, waiting, backlog]):
            lines.append("<b>Tasks</b>")
            lines.append("  No active tasks.")
            lines.append("")
    except Exception as e:
        logger.error(f"Failed to load vault tasks: {e}")
        lines.append("<b>Tasks</b>")
        lines.append("  Could not load vault tasks.")
        lines.append("")

    # ── Stale items ──────────────────────────────────────
    stale = []

    # Check for overdue vault tasks
    try:
        for t in get_active_tasks():
            due = t.get("due")
            if due:
                try:
                    due_date = datetime.strptime(str(due), "%Y-%m-%d").replace(tzinfo=tz)
                    if due_date < now:
                        days_late = (now - due_date).days
                        stale.append(f"{t['title']} (overdue by {days_late} days)")
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    # Check memory for recently saved items
    memory_dir = Path.home() / ".claude" / "projects" / f"-{str(WORKSPACE).replace('/', '-')}" / "memory"
    if memory_dir.exists():
        memory_files = list(memory_dir.glob("*.md"))
        memory_count = len([f for f in memory_files if f.name != "MEMORY.md"])
        lines.append(f"<b>Memory</b>")
        lines.append(f"  {memory_count} memories stored")
        lines.append("")

    if stale:
        lines.append("<b>Needs Attention</b>")
        for item in stale:
            lines.append(f"  ⚠ {item}")
        lines.append("")

    # ── Vault: Yesterday + Trends ──────────────────────────
    vault = Path.home() / "vault"
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_note = vault / "daily" / f"{yesterday}.md"

    if yesterday_note.exists():
        yd_content = yesterday_note.read_text()
        # Extract frontmatter values
        import re as _re
        energy_m = _re.search(r'^energy:\s*(\d)', yd_content, _re.MULTILINE)
        sleep_m = _re.search(r'^sleep:\s*(\d)', yd_content, _re.MULTILINE)
        yd_energy = energy_m.group(1) if energy_m else "—"
        yd_sleep = sleep_m.group(1) if sleep_m else "—"

        # Extract accomplishment from Evening Reflection
        accomplishment = ""
        if "**Accomplished**:" in yd_content:
            acc_m = _re.search(r'\*\*Accomplished\*\*:\s*(.+)', yd_content)
            if acc_m:
                accomplishment = acc_m.group(1).strip()

        lines.append("<b>Yesterday</b>")
        lines.append(f"  Energy: {yd_energy}/5 | Sleep: {yd_sleep}/5")
        if accomplishment:
            lines.append(f"  Did: {accomplishment[:80]}")
        lines.append("")

    # 3-day trends
    trend_energies = []
    trend_sleeps = []
    for d in range(1, 4):
        day_str = (now - timedelta(days=d)).strftime("%Y-%m-%d")
        day_note = vault / "daily" / f"{day_str}.md"
        if day_note.exists():
            dc = day_note.read_text()
            import re as _re
            em = _re.search(r'^energy:\s*(\d)', dc, _re.MULTILINE)
            sm = _re.search(r'^sleep:\s*(\d)', dc, _re.MULTILINE)
            if em:
                trend_energies.append(int(em.group(1)))
            if sm:
                trend_sleeps.append(int(sm.group(1)))

    if trend_energies or trend_sleeps:
        lines.append("<b>Trends (3-day)</b>")
        if trend_energies:
            avg_e = sum(trend_energies) / len(trend_energies)
            lines.append(f"  Energy: {avg_e:.1f} avg")
        if trend_sleeps:
            avg_s = sum(trend_sleeps) / len(trend_sleeps)
            lines.append(f"  Sleep: {avg_s:.1f} avg")
        lines.append("")

    # Recent sessions
    sessions_dir = vault / "sessions"
    if sessions_dir.exists():
        yesterday_sessions = sorted(sessions_dir.glob(f"{yesterday}-*.md"))
        if yesterday_sessions:
            lines.append(f"<b>Yesterday's Sessions ({len(yesterday_sessions)})</b>")
            for sf in yesterday_sessions[:3]:
                sc = sf.read_text()
                proj_m = _re.search(r'^project:\s*(.+)', sc, _re.MULTILINE)
                proj = proj_m.group(1).strip() if proj_m else "unknown"
                lines.append(f"  • {sf.stem} ({proj})")
            if len(yesterday_sessions) > 3:
                lines.append(f"  ... and {len(yesterday_sessions) - 3} more")
            lines.append("")

    # Create today's daily note if missing
    today_str = now.strftime("%Y-%m-%d")
    today_note = vault / "daily" / f"{today_str}.md"
    if not today_note.exists():
        template = vault / "templates" / "daily.md"
        if template.exists():
            content = template.read_text()
            content = content.replace("{{date}}", today_str)
            content = content.replace("{{day}}", now.strftime("%A"))
            today_note.parent.mkdir(parents=True, exist_ok=True)
            today_note.write_text(content)
            logger.info(f"Created daily note: {today_note}")

    # ── Agent Trust ──────────────────────────────────────
    trust_path = Path.home() / ".aos" / "config" / "trust.yaml"
    trust_log_dir = Path.home() / ".aos" / "logs" / "trust"
    if trust_path.exists():
        try:
            trust_data = yaml.safe_load(trust_path.read_text()) or {}
            trust_agents = trust_data.get("agents", {})
            graduation_config = trust_data.get("graduation", {})

            # Count recent trust actions (last 7 days)
            recent_actions = 0
            recent_reverts = 0
            agent_scores = {}
            for d in range(7):
                day_str = (now - timedelta(days=d)).strftime("%Y-%m-%d")
                log_file = trust_log_dir / f"{day_str}.jsonl"
                if log_file.exists():
                    import json as _json
                    for line in log_file.read_text().splitlines():
                        if not line.strip():
                            continue
                        try:
                            entry = _json.loads(line)
                            recent_actions += 1
                            if entry.get("result") == "reverted":
                                recent_reverts += 1
                            ag = entry.get("agent", "")
                            agent_scores[ag] = agent_scores.get(ag, 0) + entry.get("weight", 0)
                        except _json.JSONDecodeError:
                            pass

            # Check for graduation candidates
            grad_candidates = []
            for ag_name, ag_info in trust_agents.items():
                caps = ag_info.get("capabilities", {})
                for cap, cap_level in caps.items():
                    if cap_level >= 3:
                        continue
                    score = agent_scores.get(ag_name, 0)
                    if cap_level == 1:
                        threshold = graduation_config.get("1_to_2", {}).get("min_weighted_score", 30)
                        if score > 0:
                            pct = min(100, int(score / threshold * 100))
                            if pct >= 60:
                                grad_candidates.append(f"{ag_name}/{cap}: {pct}% to L{cap_level + 1}")

            if recent_actions > 0 or grad_candidates:
                lines.append("<b>Agent Trust</b>")
                if recent_actions > 0:
                    lines.append(f"  {recent_actions} actions this week, {recent_reverts} reverts")
                    top_agents = sorted(agent_scores.items(), key=lambda x: x[1], reverse=True)[:3]
                    for ag, sc in top_agents:
                        lines.append(f"  {ag}: {sc:+.1f} score")
                if grad_candidates:
                    for gc in grad_candidates:
                        lines.append(f"  📈 {gc}")
                lines.append("")
        except Exception as e:
            logger.debug(f"Trust digest failed: {e}")

    # ── System health ────────────────────────────────────
    import shutil
    usage = shutil.disk_usage("/")
    disk_pct = round(usage.used / usage.total * 100, 1)

    services = []
    # Bridge (we're running)
    services.append("Bridge: up")

    # Dashboard
    try:
        r = httpx.get("http://localhost:4096/api/health", timeout=3)
        services.append(f"Dashboard: {'up' if r.status_code == 200 else 'DOWN'}")
    except Exception:
        services.append("Dashboard: DOWN")

    # Listen
    try:
        r = httpx.get("http://localhost:7600/jobs", timeout=3)
        services.append(f"Listen: {'up' if r.status_code == 200 else 'DOWN'}")
    except Exception:
        services.append("Listen: DOWN")

    lines.append("<b>System</b>")
    lines.append(f"  Disk: {disk_pct}%")
    for svc in services:
        lines.append(f"  {svc}")
    lines.append("")

    # ── Agent traces (from Phoenix) ───────────────────────
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "apps" / "phoenix"))
        from agent_health import get_daily_stats, format_for_briefing
        stats = get_daily_stats(hours=24)
        lines.append(format_for_briefing(stats))
    except Exception as e:
        logger.debug(f"Phoenix stats unavailable: {e}")

    return "\n".join(lines)


def _send_briefing(bot_token: str, chat_id: int):
    """Build and send the daily briefing via Telegram."""
    try:
        text = _build_briefing()
        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        logger.info("Daily briefing sent")
    except Exception as e:
        logger.error(f"Daily briefing failed: {e}")


def start_daily_briefing(bot_token: str, chat_id: int, hour: int = 8, minute: int = 0):
    """Start the daily briefing as a daemon thread.

    Checks every 5 minutes if it's time to send. Sends once per day at the
    configured hour (default 8:00 AM in the operator's timezone).
    """

    def _loop():
        # Persist last_sent_date to survive restarts
        state_file = WORKSPACE / "data" / "bridge" / "briefing_state.txt"
        state_file.parent.mkdir(parents=True, exist_ok=True)

        last_sent_date = None
        try:
            if state_file.exists():
                stored = state_file.read_text().strip()
                if stored:
                    from datetime import date as _date
                    last_sent_date = _date.fromisoformat(stored)
        except Exception:
            pass

        while True:
            try:
                tz_name, _, _ = _get_config()
                tz = ZoneInfo(tz_name)
                now = datetime.now(tz)

                # Send if it's past the briefing time and we haven't sent today
                if (now.hour >= hour and
                    (now.hour == hour and now.minute >= minute or now.hour > hour) and
                        last_sent_date != now.date()):
                    _send_briefing(bot_token, chat_id)
                    last_sent_date = now.date()
                    state_file.write_text(str(last_sent_date))

            except Exception as e:
                logger.error(f"Daily briefing loop error: {e}")

            # Check every 5 minutes
            threading.Event().wait(300)

    thread = threading.Thread(target=_loop, daemon=True, name="daily-briefing")
    thread.start()
    logger.info(f"Daily briefing scheduled at {hour:02d}:{minute:02d}")
    return thread
