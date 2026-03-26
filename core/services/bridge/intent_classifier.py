"""Intent classifier — detects common intents from natural language messages.

Runs before Claude dispatch. If a message matches a known intent with high
confidence, the bridge handles it directly (fast, zero tokens).
If no match, returns None and the message goes to Claude as normal.

Bridge v2 quick commands spec:
- "add task: X" / "add task X"  → work add "X"      → "Added: X"
- "done: X" / "mark X done"    → work done "X"      → "Done: X (N remaining)"
- "tasks" / "what's on my plate"→ work list           → formatted task list
- "search vault for X"          → qmd query "X"       → top 3-5 results
- Everything else → Claude. If ambiguous, goes to Claude.
"""

import logging
import re
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

AOS_DIR = Path.home() / "aos"
QMD_BIN = Path.home() / ".bun" / "bin" / "qmd"

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
            "re:is the (dashboard|bridge|listen|transcriber|whatsapp)\\s*(running|up|working|broken|down)",
            "re:check (the )?(dashboard|bridge|listen|transcriber|whatsapp|services|system)",
            "re:is .+ (broken|down|dead|crashed)",
            "anything down",
            "anything broken",
            "anything crashed",
        ],
        "handler": "handle_health_check",
    },
    "add_task": {
        "patterns": [
            "re:^add task:? .+",
            "re:^new task:? .+",
            "re:^create task:? .+",
            "re:^task:? .{3,}",
        ],
        "handler": "handle_add_task",
    },
    "done_task": {
        "patterns": [
            # Project-scoped IDs: aos#3, chief#1, t#5
            "re:mark \\w+#\\d+(\\.\\d+)? done",
            "re:done:? \\w+#\\d+(\\.\\d+)?",
            "re:complete \\w+#\\d+(\\.\\d+)?",
            "re:finish \\w+#\\d+(\\.\\d+)?",
            "re:\\w+#\\d+(\\.\\d+)? done",
            # Legacy unscoped IDs: t1, t2
            "re:mark t\\d+ done",
            "re:done:? t\\d+",
            "re:complete t\\d+",
            "re:finish t\\d+",
            "re:t\\d+ done",
            # Fuzzy title match: done: "fix the login page"
            're:^done:? ".+"',
            "re:^mark .+ done$",
        ],
        "handler": "handle_done_task",
    },
    "inbox_capture": {
        "patterns": [
            "re:^inbox .+",
            "re:^capture .+",
            "re:^jot down .+",
            "re:^note:? .+",
            "re:^remember .+",
        ],
        "handler": "handle_inbox",
    },
    "list_tasks": {
        "patterns": [
            "tasks",
            "my tasks",
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
    "vault_search": {
        "patterns": [
            "re:^search vault (for )?(.+)",
            "re:^search:? .+",
            "re:^find in vault .+",
            "re:^vault search .+",
            "re:^recall .+",
        ],
        "handler": "handle_vault_search",
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
    "messages": {
        "patterns": [
            "re:^/messages?$",
            "re:^/msg$",
            "messages",
            "unanswered messages",
            "who messaged me",
            "any messages",
            "any new messages",
            "unread messages",
        ],
        "handler": "handle_messages",
    },
    "reply": {
        "patterns": [
            "re:^/reply .+",
            "re:^reply to .+",
            "re:^tell .+ that .+",
            "re:^message .+ (saying|that) .+",
            "re:^send .+ a message .+",
        ],
        "handler": "handle_reply",
    },
    # greeting intent removed — greetings go through Claude for natural
    # personality response instead of canned text.
    "trust_check": {
        "patterns": [
            "re:^/trust\\b",
            "re:^trust level .+",
            "re:^what('s| is) .+ trust level",
        ],
        "handler": "handle_trust",
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
    # Exception: /messages, /msg are handled as quick commands
    if text.startswith("/"):
        if not re.match(r"^/(messages?|msg)$", text.lower().strip()):
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
        ("Transcriber", "http://127.0.0.1:7601/health"),
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
            ["/opt/homebrew/bin/python3", str(AOS_DIR / "core" / "work" / "cli.py"), "json"],
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

    # Strip trigger phrases to get the title (colon-separated or space-separated)
    for prefix in ("create task: ", "create task ", "new task: ", "new task ",
                    "add task: ", "add task ", "task: ", "task "):
        if text_lower.startswith(prefix):
            title = text[len(prefix):].strip()
            break
    else:
        title = text.strip()

    if not title:
        return "Please provide a task title. Example: add task: Fix the login page"

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", str(AOS_DIR / "core" / "work" / "cli.py"), "add", title],
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
    text_lower = text.lower().strip()

    # Try project-scoped IDs first: aos#3, chief#1.2, t#5
    match = re.search(r"\b(\w+#\d+(?:\.\d+)?)\b", text_lower)
    if match:
        task_id = match.group(1)
    else:
        # Legacy unscoped IDs: t1, t2
        match = re.search(r"\bt(\d+)\b", text_lower)
        if match:
            task_id = f"t{match.group(1)}"
        else:
            # Fuzzy title match: done: "fix the login page" or mark fix the login done
            # Strip known prefixes to extract the title
            for prefix in ("done: ", "done ", "complete ", "finish ", "mark "):
                if text_lower.startswith(prefix):
                    title = text[len(prefix):].strip()
                    # Remove trailing "done" if present (from "mark X done")
                    title = re.sub(r'\s+done$', '', title).strip().strip('"')
                    break
            else:
                title = None

            if title:
                task_id = title  # work CLI handles fuzzy resolution
            else:
                return 'Specify a task. Examples: done aos#3, done t2, done: "fix login"'

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", str(AOS_DIR / "core" / "work" / "cli.py"), "done", task_id],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            # Get remaining task count
            count_result = subprocess.run(
                ["/opt/homebrew/bin/python3", str(AOS_DIR / "core" / "work" / "cli.py"), "json"],
                capture_output=True, text=True, timeout=10,
            )
            remaining = "?"
            if count_result.returncode == 0:
                import json as _json
                data = _json.loads(count_result.stdout)
                tasks = data.get("tasks", [])
                remaining = len([t for t in tasks if t.get("status") in ("active", "todo")])
            return f"✅ {output} ({remaining} tasks remaining)"
        else:
            err = result.stdout.strip() or result.stderr.strip()
            return f"Could not complete task: {err[:200]}"
    except Exception as e:
        return f"Error completing task: {e}"


def handle_inbox(text: str) -> str:
    """Capture text to the v2 work inbox."""
    text_lower = text.lower().strip()

    for prefix in ("remember ", "note: ", "note ", "jot down ", "capture ", "inbox "):
        if text_lower.startswith(prefix):
            content = text[len(prefix):].strip()
            break
    else:
        content = text.strip()

    if not content:
        return "Please provide something to capture. Example: inbox look into Redis"

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", str(AOS_DIR / "core" / "work" / "cli.py"), "inbox", content],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            return f"Captured: {output}"
        else:
            return f"Could not capture: {result.stderr[:200]}"
    except Exception as e:
        return f"Error capturing: {e}"


def handle_vault_search(text: str) -> str:
    """Search the vault via QMD and return top results."""
    text_lower = text.lower().strip()

    # Extract search query from various patterns
    for prefix in ("search vault for ", "search vault ", "find in vault ",
                    "vault search ", "search: ", "search ", "recall "):
        if text_lower.startswith(prefix):
            query = text[len(prefix):].strip().strip('"')
            break
    else:
        query = text.strip()

    if not query:
        return "What should I search for? Example: search vault for bridge architecture"

    if not QMD_BIN.exists():
        return "⚠️ QMD not installed. Search unavailable."

    try:
        qmd_env = {
            **__import__("os").environ,
            "PATH": f"{QMD_BIN.parent}:{__import__('os').environ.get('PATH', '')}",
        }
        # Use fast BM25 keyword search for quick commands (no model loading)
        # Full hybrid search (query) loads 3 models and can take 30s+ on cold start
        result = subprocess.run(
            [str(QMD_BIN), "search", query, "-n", "5"],
            capture_output=True, text=True, timeout=10,
            env=qmd_env,
        )
        if result.returncode != 0:
            return f"⚠️ Search failed: {result.stderr[:200]}"

        output = result.stdout.strip()
        if not output:
            return f"No results for \"{query}\""

        # Parse QMD output into a clean Telegram-friendly format
        lines = ["<b>🔍 Vault Search</b>"]
        lines.append(f"Query: <i>{query}</i>\n")

        # QMD outputs results with paths and scores — format them
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            # QMD format: score path — snippet
            # Just pass through — it's already human-readable
            lines.append(f"  • {line[:200]}")

        if len(lines) <= 2:
            return f"No results for \"{query}\""

        return "\n".join(lines)

    except subprocess.TimeoutExpired:
        return "⚠️ Search timed out. Try a simpler query."
    except Exception as e:
        return f"⚠️ Search error: {e}"


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


def handle_messages(text: str) -> str:
    """Show unanswered messages from triage state."""
    import json
    from datetime import datetime

    triage_file = Path.home() / ".aos" / "work" / "triage-state.json"
    if not triage_file.exists():
        return "No message tracking data yet."

    try:
        state = json.loads(triage_file.read_text())
    except Exception:
        return "⚠️ Could not read triage state."

    unanswered = state.get("unanswered", {})
    if not unanswered:
        return "✅ No unanswered messages. You're all caught up."

    def _time_ago(iso_ts: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            seconds = int((now - dt).total_seconds())
            if seconds < 60:
                return "just now"
            if seconds < 3600:
                return f"{seconds // 60}m ago"
            if seconds < 86400:
                return f"{seconds // 3600}h ago"
            days = seconds // 86400
            if days < 7:
                return f"{days}d ago"
            return f"{days // 7}w ago"
        except Exception:
            return "recently"

    entries = sorted(unanswered.values(), key=lambda e: e.get("received_at", ""))

    lines = ["<b>Unanswered Messages</b>\n"]
    for entry in entries:
        name = entry.get("person_name", "Unknown")
        channel = entry.get("channel", "?")
        ago = _time_ago(entry.get("received_at", ""))
        preview = entry.get("text_preview", "")
        lines.append(f"💬 {name} ({channel}) — {ago}")
        if preview:
            lines.append(f"  {preview[:80]}")
        lines.append("")

    lines.append(f"Reply in app or from here: /reply {{name}} {{message}}")
    return "\n".join(lines)


def handle_reply(text: str) -> str:
    """Send a message to a contact via the comms bus.

    Formats:
        /reply Ahmed On my way
        tell mom I'll be late
        message Faisal saying the shipment is ready
    """
    import re as _re

    # Parse: /reply {name} {message}
    match = _re.match(r'^/reply\s+(\S+)\s+(.+)$', text, _re.IGNORECASE)
    if not match:
        # Try: tell {name} that {message}
        match = _re.match(r'^(?:tell|message|send)\s+(.+?)\s+(?:that|saying|a message)\s+(.+)$', text, _re.IGNORECASE)
    if not match:
        return "Usage: /reply {name} {message}\nExample: /reply Ahmed On my way"

    name = match.group(1).strip()
    message = match.group(2).strip()

    if not name or not message:
        return "Usage: /reply {name} {message}"

    # Resolve contact
    import sys
    people_service = str(Path.home() / ".aos" / "services" / "people")
    if people_service not in sys.path:
        sys.path.insert(0, people_service)

    try:
        import resolver
        result = resolver.resolve_contact(name)
    except Exception as e:
        return f"⚠️ Could not resolve contact '{name}': {e}"

    if not result or not result.get("resolved"):
        candidates = result.get("candidates", []) if result else []
        if candidates:
            names = ", ".join(c.get("name", "?") for c in candidates[:5])
            return f"⚠️ '{name}' is ambiguous. Did you mean: {names}?"
        return f"⚠️ Could not find '{name}' in contacts."

    contact = result["contact"]
    person_name = contact.get("name", name)
    channel = result.get("channel", "unknown")

    # Get the right identifier for the channel
    if channel == "whatsapp":
        recipient = contact.get("wa_jid") or (contact.get("phones", [None])[0] if contact.get("phones") else None)
    elif channel == "imessage":
        recipient = (contact.get("phones", [None])[0] if contact.get("phones") else None) or (contact.get("emails", [None])[0] if contact.get("emails") else None)
    else:
        recipient = contact.get("phones", [None])[0] if contact.get("phones") else None

    if not recipient:
        return f"⚠️ No phone/email found for {person_name} on {channel}."

    # Send via comms bus
    try:
        aos_root = str(Path.home() / "aos")
        if aos_root not in sys.path:
            sys.path.insert(0, aos_root)
        from core.comms.bus import MessageBus
        from core.comms.registry import load_adapters

        adapters = load_adapters()
        bus = MessageBus(adapters)
        success = bus.send(recipient=recipient, text=message, channel=channel)
    except Exception as e:
        return f"⚠️ Send failed: {e}"

    if success:
        return f"✅ Sent to {person_name} via {channel}:\n\n\"{message}\""
    else:
        return f"⚠️ Failed to send to {person_name} via {channel}. Check the bridge."


def handle_greeting(text: str) -> str:
    """Respond to greetings instantly — no Claude needed."""
    import random
    from datetime import datetime

    hour = datetime.now().hour
    if hour < 12:
        time_greeting = "Good morning"
    elif hour < 17:
        time_greeting = "Good afternoon"
    else:
        time_greeting = "Good evening"

    text_lower = text.lower().strip()
    if "salam" in text_lower or "asalam" in text_lower:
        return f"Wa alaikum assalam. {time_greeting}. How can I help?"

    return f"{time_greeting}. How can I help?"


def handle_trust(text: str) -> str:
    """Handle /trust commands — show or set comms trust levels.

    /trust           → summary of Level 1+ people
    /trust Ahmed     → show Ahmed's trust level + stats
    /trust_set Ahmed 2 → override Ahmed's level to 2
    """
    import sqlite3
    import sys
    import yaml
    from pathlib import Path

    people_db_path = Path.home() / "vault" / "people" / "people.db"
    trust_path = Path.home() / ".aos" / "config" / "trust.yaml"

    try:
        trust_config = yaml.safe_load(trust_path.read_text()) or {}
    except Exception:
        trust_config = {}

    per_person = trust_config.get("comms", {}).get("per_person", {})

    parts = text.strip().split(maxsplit=2)
    command = parts[0].lower() if parts else "/trust"

    # /trust_set <name> <level>
    if command == "/trust_set" and len(parts) >= 3:
        name_query = parts[1]
        try:
            new_level = int(parts[2])
        except ValueError:
            return "Usage: /trust_set <name> <level 0-3>"
        if new_level not in (0, 1, 2, 3):
            return "Level must be 0-3"

        conn = sqlite3.connect(str(people_db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, canonical_name FROM people WHERE canonical_name LIKE ? COLLATE NOCASE LIMIT 1",
            (f"%{name_query}%",),
        ).fetchone()
        conn.close()

        if not row:
            return f"No person found matching '{name_query}'"

        pid = row["id"]
        name = row["canonical_name"]
        old_entry = per_person.get(pid, {})
        old_level = old_entry.get("level", 0) if isinstance(old_entry, dict) else 0

        # Update
        import time
        comms = trust_config.setdefault("comms", {})
        pp = comms.setdefault("per_person", {})
        pp[pid] = {"level": new_level, "updated_at": time.time()}
        with open(trust_path, "w") as f:
            yaml.dump(trust_config, f, default_flow_style=False, allow_unicode=True)

        return f"✅ {name}: Level {old_level} → Level {new_level}"

    # /trust <name> — show one person
    if len(parts) >= 2 and command == "/trust":
        name_query = " ".join(parts[1:])
        conn = sqlite3.connect(str(people_db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, canonical_name, importance FROM people "
            "WHERE canonical_name LIKE ? COLLATE NOCASE LIMIT 1",
            (f"%{name_query}%",),
        ).fetchone()

        if not row:
            conn.close()
            return f"No person found matching '{name_query}'"

        pid = row["id"]
        name = row["canonical_name"]
        entry = per_person.get(pid, {})
        level = entry.get("level", 0) if isinstance(entry, dict) else 0
        level_names = {0: "OBSERVE", 1: "SURFACE", 2: "DRAFT", 3: "ACT"}

        # Get stats
        rs = conn.execute(
            "SELECT msg_count_30d, trajectory, interaction_count_90d "
            "FROM relationship_state WHERE person_id = ?", (pid,)
        ).fetchone()

        fb = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN operator_action IN ('accepted','edited') THEN 1 ELSE 0 END) as pos "
            "FROM surface_feedback WHERE person_id = ?", (pid,)
        ).fetchone()

        conn.close()

        lines = [f"<b>{name}</b>"]
        lines.append(f"Trust: Level {level} ({level_names.get(level, '?')})")
        lines.append(f"Importance: {row['importance']}")
        if rs:
            lines.append(f"Messages (30d): {rs['msg_count_30d'] or 0}")
            lines.append(f"Interactions (90d): {rs['interaction_count_90d'] or 0}")
            lines.append(f"Trajectory: {rs['trajectory'] or 'unknown'}")
        if fb and fb["total"] > 0:
            rate = (fb["pos"] / fb["total"]) * 100
            lines.append(f"Acceptance rate: {rate:.0f}% ({fb['total']} actions)")

        return "\n".join(lines)

    # /trust — summary
    conn = sqlite3.connect(str(people_db_path))
    conn.row_factory = sqlite3.Row

    lines = ["<b>Trust Levels</b>"]
    for level in (3, 2, 1):
        level_names = {1: "SURFACE", 2: "DRAFT", 3: "ACT"}
        people_at_level = []
        for pid, entry in per_person.items():
            plevel = entry.get("level", 0) if isinstance(entry, dict) else 0
            if plevel == level:
                row = conn.execute(
                    "SELECT canonical_name FROM people WHERE id = ?", (pid,)
                ).fetchone()
                if row:
                    people_at_level.append(row["canonical_name"])
        if people_at_level:
            lines.append(f"\nLevel {level} ({level_names[level]}):")
            for name in people_at_level:
                lines.append(f"  • {name}")

    conn.close()

    if len(lines) == 1:
        lines.append("\nNo one above Level 0 yet.")
        lines.append("Run the graduation evaluator to check for promotions.")

    return "\n".join(lines)


# ── Dispatcher ────────────────────────────────────────

# Map handler names to functions
HANDLERS = {
    "handle_health_check": handle_health_check,
    "handle_list_tasks": handle_list_tasks,
    "handle_add_task": handle_add_task,
    "handle_done_task": handle_done_task,
    "handle_inbox": handle_inbox,
    "handle_vault_search": handle_vault_search,
    "handle_goals": handle_goals,
    "handle_friction": handle_friction,
    "handle_sessions": handle_sessions,
    "handle_weekly_digest": handle_weekly_digest,
    "handle_messages": handle_messages,
    "handle_reply": handle_reply,
    "handle_greeting": handle_greeting,
    "handle_trust": handle_trust,
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
