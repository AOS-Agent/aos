"""Intent classifier — detects common intents from natural language messages.

Runs before Claude dispatch. If a message matches a known intent with high
confidence, the bridge handles it directly (fast, zero tokens).
If no match, returns None and the message goes to Claude as normal.
"""

import logging
import re
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

AOS_DIR = Path.home() / "aos"

# ── Intent Definitions ────────────────────────────────
# Each intent: list of patterns, handler function name
# Patterns are checked case-insensitively against the raw message text.
# A pattern can be a simple substring or a regex (prefixed with "re:").

INTENTS = {
    "health_check": {
        "patterns": [
            "is everything running",
            "is everything working",
            "is everything up",
            "are services running",
            "are services up",
            "are services working",
            "system status",
            "service status",
            "health check",
            "re:is the (dashboard|bridge|listen|phoenix|whatsapp)\\s*(running|up|working|broken|down)",
            "re:check (the )?(dashboard|bridge|listen|phoenix|whatsapp|services|system)",
            "re:is .+ (broken|down|dead|crashed)",
            "anything down",
            "anything broken",
            "anything crashed",
        ],
        "handler": "handle_health_check",
    },
    "add_task": {
        "patterns": [
            "re:^add task .+",
            "re:^new task .+",
            "re:^create task .+",
            "re:^add .{3,}",
        ],
        "handler": "handle_add_task",
    },
    "done_task": {
        "patterns": [
            "re:mark t\\d+ done",
            "re:done t\\d+",
            "re:complete t\\d+",
            "re:finish t\\d+",
            "re:t\\d+ done",
        ],
        "handler": "handle_done_task",
    },
    "inbox_capture": {
        "patterns": [
            "re:^inbox .+",
            "re:^capture .+",
            "re:^jot down .+",
        ],
        "handler": "handle_inbox",
    },
    "list_tasks": {
        "patterns": [
            "what are my tasks",
            "show my tasks",
            "show tasks",
            "list tasks",
            "what tasks do i have",
            "what's on my plate",
            "whats on my plate",
            "what do i need to do",
            "what should i work on",
            "open tasks",
            "pending tasks",
            "re:what('s| is) (left|todo|to do|remaining)",
        ],
        "handler": "handle_list_tasks",
    },
    "goals_progress": {
        "patterns": [
            "how are my goals",
            "goal progress",
            "goals progress",
            "show goals",
            "show me my goals",
            "show my goals",
            "how are goals going",
            "re:how('s| is| are) .*(goal|objective|progress)",
            "where do i stand",
            "am i on track",
        ],
        "handler": "handle_goals",
    },
    "friction_summary": {
        "patterns": [
            "any friction",
            "friction this week",
            "friction report",
            "what mistakes",
            "what am i doing wrong",
            "what keeps going wrong",
            "learning report",
            "re:what (mistakes|errors|friction|issues) (this|last) week",
        ],
        "handler": "handle_friction",
    },
    "session_stats": {
        "patterns": [
            "what did i do this week",
            "what did we do this week",
            "how many sessions",
            "session stats",
            "session count",
            "what have i been working on",
            "re:what (did|have) (i|we) (done|do|worked on)",
            "re:how (busy|active) (was|have) (i|we) been",
        ],
        "handler": "handle_sessions",
    },
    "weekly_digest": {
        "patterns": [
            "weekly summary",
            "weekly digest",
            "weekly report",
            "week in review",
            "summarize the week",
            "summarize this week",
            "how was the week",
            "how was this week",
        ],
        "handler": "handle_weekly_digest",
    },
}


def classify(text: str) -> tuple[str | None, str | None]:
    """Classify a message into an intent.

    Returns (intent_name, handler_name) or (None, None) if no match.
    Only matches if the message is short (under 100 chars) and looks like
    a question/request, not a complex instruction.
    """
    # Skip long messages — likely complex requests for Claude
    if len(text) > 120:
        return None, None

    # Skip messages that start with / — those are explicit commands
    if text.startswith("/"):
        return None, None

    # Skip messages that look like code or file paths
    if any(c in text for c in ["{", "}", "def ", "class ", "import "]):
        return None, None

    text_lower = text.lower().strip()

    for intent_name, intent_def in INTENTS.items():
        for pattern in intent_def["patterns"]:
            if pattern.startswith("re:"):
                # Regex pattern
                if re.search(pattern[3:], text_lower):
                    return intent_name, intent_def["handler"]
            else:
                # Substring match
                if pattern in text_lower:
                    return intent_name, intent_def["handler"]

    return None, None


# ── Intent Handlers ───────────────────────────────────
# Each returns an HTML string for Telegram reply.


def handle_health_check(text: str) -> str:
    """Check service health endpoints."""
    services = [
        ("Dashboard", "http://127.0.0.1:4096/api/health"),
        ("Listen", "http://127.0.0.1:7600/health"),
        ("Phoenix", "http://127.0.0.1:6006"),
        ("WhatsApp", "http://127.0.0.1:7601/health"),
    ]

    # Bridge is obviously running if we're here
    results = ["🟢 <b>Bridge</b> — running"]

    for name, url in services:
        try:
            r = httpx.get(url, timeout=5)
            if r.status_code == 200:
                results.append(f"🟢 <b>{name}</b> — running")
            else:
                results.append(f"🔴 <b>{name}</b> — responded {r.status_code}")
        except Exception:
            results.append(f"🔴 <b>{name}</b> — unreachable")

    # Check if user asked about a specific service
    text_lower = text.lower()
    specific = None
    for name, _ in services:
        if name.lower() in text_lower:
            specific = name
            break

    if specific:
        line = next((r for r in results if specific in r), None)
        return line or f"⚠️ Unknown service: {specific}"

    return "<b>Service Health</b>\n" + "\n".join(results)


def handle_list_tasks(text: str) -> str:
    """List tasks from the v2 work engine."""
    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", "/Users/agentalhadi/aosv2/core/work/cli.py", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return f"<b>Tasks</b>\nCould not load tasks: {result.stderr[:200]}"

        import json as _json
        data = _json.loads(result.stdout)
        tasks = data.get("tasks", [])

        # Filter out done/cancelled
        active_tasks = [t for t in tasks if t.get("status") == "active"]
        todo_tasks = [t for t in tasks if t.get("status") == "todo"]

        if not active_tasks and not todo_tasks:
            return "<b>Tasks</b>\nNo open tasks. Use \"add task <title>\" to create one."

        priority_marker = {1: "!!", 2: "!", 3: "", 4: "~", 0: "?"}
        lines = ["<b>Tasks</b>"]

        if active_tasks:
            lines.append("\n<b>In Progress</b>")
            for t in active_tasks:
                marker = priority_marker.get(t.get("priority", 3), "")
                proj = f" <i>[{t['project']}]</i>" if t.get("project") else ""
                sessions = len(t.get("sessions", []))
                sess = f" <code>{sessions}s</code>" if sessions > 0 else ""
                prefix = f" {marker}" if marker else ""
                lines.append(f"<code>{t['id']}</code>{prefix} {t['title']}{proj}{sess}")

        if todo_tasks:
            lines.append("\n<b>Todo</b>")
            for t in todo_tasks:
                marker = priority_marker.get(t.get("priority", 3), "")
                proj = f" <i>[{t['project']}]</i>" if t.get("project") else ""
                sessions = len(t.get("sessions", []))
                sess = f" <code>{sessions}s</code>" if sessions > 0 else ""
                prefix = f" {marker}" if marker else ""
                lines.append(f"<code>{t['id']}</code>{prefix} {t['title']}{proj}{sess}")

        total = len(active_tasks) + len(todo_tasks)
        lines.append(f"\n{total} open task(s)")
        return "\n".join(lines)

    except Exception as e:
        return f"<b>Tasks</b>\nError loading tasks: {e}"


def handle_add_task(text: str) -> str:
    """Add a task to the v2 work engine."""
    text_lower = text.lower().strip()

    # Strip trigger phrases to get the title
    for prefix in ("create task ", "new task ", "add task ", "add "):
        if text_lower.startswith(prefix):
            title = text[len(prefix):].strip()
            break
    else:
        title = text.strip()

    if not title:
        return "Please provide a task title. Example: add task Fix the login page"

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", "/Users/agentalhadi/aosv2/core/work/cli.py", "add", title],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            return f"Added: {output}"
        else:
            return f"Could not add task: {result.stderr[:200]}"
    except Exception as e:
        return f"Error adding task: {e}"


def handle_done_task(text: str) -> str:
    """Mark a task done in the v2 work engine."""
    # Extract task ID (t1, t2, etc.)
    match = re.search(r"\bt(\d+)\b", text.lower())
    if not match:
        return "Please specify a task ID. Example: done t2"

    task_id = f"t{match.group(1)}"

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", "/Users/agentalhadi/aosv2/core/work/cli.py", "done", task_id],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            return f"Done: {output}"
        else:
            err = result.stdout.strip() or result.stderr.strip()
            return f"Could not complete task: {err[:200]}"
    except Exception as e:
        return f"Error completing task: {e}"


def handle_inbox(text: str) -> str:
    """Capture text to the v2 work inbox."""
    text_lower = text.lower().strip()

    for prefix in ("jot down ", "capture ", "inbox "):
        if text_lower.startswith(prefix):
            content = text[len(prefix):].strip()
            break
    else:
        content = text.strip()

    if not content:
        return "Please provide something to capture. Example: inbox look into Redis"

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", "/Users/agentalhadi/aosv2/core/work/cli.py", "inbox", content],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            return f"Captured: {output}"
        else:
            return f"Could not capture: {result.stderr[:200]}"
    except Exception as e:
        return f"Error capturing: {e}"


def handle_goals(text: str) -> str:
    """Show goal progress from goals.yaml."""
    import yaml

    goals_file = AOS_DIR / "config" / "goals.yaml"
    if not goals_file.exists():
        return "No goals file found."

    goals = yaml.safe_load(goals_file.read_text())
    objectives = goals.get("quarterly_objectives", [])

    lines = ["<b>Goal Progress</b>\n"]
    for obj in objectives:
        name = obj["name"]
        weight = obj.get("weight", 0)
        krs = obj.get("key_results", [])
        if not krs:
            continue
        avg = sum(kr.get("progress", 0) for kr in krs) / len(krs)
        bar = _progress_bar(avg)
        lines.append(f"{bar} <b>{name}</b> ({weight}%) — {avg:.0f}%")
        for kr in krs:
            p = kr.get("progress", 0)
            icon = "✅" if p >= 80 else "⚠️" if p < 30 else "▫️"
            lines.append(f"  {icon} {kr['text'][:55]} — {p}%")
        lines.append("")

    return "\n".join(lines)


def _progress_bar(pct: float) -> str:
    filled = int(pct / 20)
    return "▓" * filled + "░" * (5 - filled)


def handle_friction(text: str) -> str:
    """Summarize latest friction report."""
    import yaml
    reviews_dir = Path.home() / "vault" / "reviews"
    reports = sorted(reviews_dir.glob("session-friction-*.md"), reverse=True)

    if not reports:
        return "No friction reports found yet."

    latest = reports[0]
    content = latest.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm_match:
        return "Couldn't parse the latest friction report."

    fm = yaml.safe_load(fm_match.group(1))
    total = fm.get("total_frictions", 0)
    date = fm.get("date", "unknown")
    period = fm.get("period_days", "?")

    # Extract category summary
    categories = re.findall(r"\| \*\*(\w+)\*\* \| (\d+) \|", content)
    cat_lines = [f"  • {cat}: {count}" for cat, count in categories]

    lines = [
        f"<b>Friction Report — {date}</b> ({period}-day window)\n",
        f"Total: <b>{total}</b> friction instances\n",
    ]
    if cat_lines:
        lines.append("\n".join(cat_lines))

    # Check for pending auto-rules
    pending_file = AOS_DIR / "apps" / "bridge" / "data" / "bridge" / "pending_rules.json"
    if pending_file.exists():
        import json
        pending = json.loads(pending_file.read_text())
        if pending.get("status") == "pending":
            n = len(pending.get("rules", []))
            lines.append(f"\n📋 {n} auto-rule proposal(s) pending — use /approve-rules to review")

    return "\n".join(lines)


def handle_sessions(text: str) -> str:
    """Count sessions from the past 7 days."""
    import yaml
    from collections import Counter
    from datetime import datetime, timedelta

    sessions_dir = Path.home() / "vault" / "sessions"
    if not sessions_dir.exists():
        return "No session data found."

    cutoff = datetime.now() - timedelta(days=7)
    count = 0
    projects = Counter()
    days = Counter()

    for f in sessions_dir.glob("*.md"):
        try:
            content = f.read_text()
            fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1))
            date_str = fm.get("date", "")
            if not date_str:
                continue
            session_date = datetime.strptime(str(date_str), "%Y-%m-%d")
            if session_date >= cutoff:
                count += 1
                projects[fm.get("project", "unknown")] += 1
                days[session_date.strftime("%A")] += 1
        except Exception:
            continue

    if count == 0:
        return "No sessions in the past 7 days."

    lines = [f"<b>Sessions — Last 7 Days</b>\n", f"Total: <b>{count}</b>\n"]

    if projects:
        proj_str = ", ".join(f"{p} ({c})" for p, c in projects.most_common(5))
        lines.append(f"Projects: {proj_str}")

    if days:
        busiest = days.most_common(1)[0]
        lines.append(f"Busiest: {busiest[0]} ({busiest[1]} sessions)")

    return "\n".join(lines)


def handle_weekly_digest(text: str) -> str:
    """Run the weekly digest script and confirm."""
    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", str(AOS_DIR / "bin" / "weekly-digest")],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return "Weekly digest sent — check the message above."
        else:
            return f"⚠️ Digest failed: {result.stderr[:200]}"
    except Exception as e:
        return f"⚠️ Couldn't run digest: {e}"


# ── Dispatcher ────────────────────────────────────────

# Map handler names to functions
HANDLERS = {
    "handle_health_check": handle_health_check,
    "handle_list_tasks": handle_list_tasks,
    "handle_add_task": handle_add_task,
    "handle_done_task": handle_done_task,
    "handle_inbox": handle_inbox,
    "handle_goals": handle_goals,
    "handle_friction": handle_friction,
    "handle_sessions": handle_sessions,
    "handle_weekly_digest": handle_weekly_digest,
}


def dispatch(text: str) -> str | None:
    """Classify and handle a message. Returns reply text or None."""
    intent_name, handler_name = classify(text)
    if not intent_name or not handler_name:
        return None

    handler = HANDLERS.get(handler_name)
    if not handler:
        logger.warning(f"No handler for intent {intent_name}: {handler_name}")
        return None

    logger.info(f"Intent matched: {intent_name} → {handler_name}")
    try:
        return handler(text)
    except Exception as e:
        logger.error(f"Intent handler {handler_name} failed: {e}")
        return None
