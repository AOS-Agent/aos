"""Agent activity tracker — SQLite-backed audit log, conversation history, and session tracking."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".aos" / "data" / "dashboard" / "activity.db"


@contextmanager
def _db():
    """Context manager for DB connections — guarantees close on exception."""
    conn = _get_db()
    try:
        yield conn
    finally:
        conn.close()


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            parent_agent TEXT,
            status TEXT DEFAULT 'running',
            summary TEXT,
            duration_ms INTEGER,
            session_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'telegram',
            user_key TEXT NOT NULL,
            agent TEXT,
            topic_name TEXT,
            message TEXT NOT NULL,
            response TEXT,
            duration_ms INTEGER,
            message_type TEXT DEFAULT 'text'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            working_dir TEXT,
            agent_name TEXT,
            status TEXT DEFAULT 'running',
            files_modified TEXT DEFAULT '[]',
            tools_used TEXT DEFAULT '{}',
            total_tools INTEGER DEFAULT 0,
            last_activity TEXT
        )
    """)
    # Add session_id column to activity if missing (migration)
    try:
        conn.execute("SELECT session_id FROM activity LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE activity ADD COLUMN session_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_agent ON activity(agent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_session ON activity(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_agent ON conversations(agent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC)")
    conn.commit()
    return conn


def log_activity(agent: str, action: str, parent_agent: str = None,
                 status: str = "completed", summary: str = None,
                 duration_ms: int = None, session_id: str = None) -> int:
    """Log an agent activity. Returns the activity ID."""
    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO activity (timestamp, agent, action, parent_agent, status, summary, duration_ms, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), agent, action,
         parent_agent, status, summary, duration_ms, session_id)
    )
    conn.commit()
    activity_id = cur.lastrowid
    conn.close()
    return activity_id


def update_activity(activity_id: int, status: str, summary: str = None,
                    duration_ms: int = None):
    """Update an activity's status."""
    conn = _get_db()
    conn.execute(
        """UPDATE activity SET status = ?, summary = COALESCE(?, summary),
           duration_ms = COALESCE(?, duration_ms) WHERE id = ?""",
        (status, summary, duration_ms, activity_id)
    )
    conn.commit()
    conn.close()


def get_recent(limit: int = 50) -> list[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM activity ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent_stats() -> list[dict]:
    """Get activity counts per agent."""
    conn = _get_db()
    rows = conn.execute("""
        SELECT agent,
               COUNT(*) as total,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
               SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
               MAX(timestamp) as last_active
        FROM activity GROUP BY agent ORDER BY last_active DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Conversation logging ──────────────────────────────────


def log_conversation(channel: str, user_key: str, agent: str | None,
                     topic_name: str | None, message: str,
                     response: str | None = None, duration_ms: int | None = None,
                     message_type: str = "text") -> int:
    """Log a conversation exchange. Returns the conversation ID."""
    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO conversations
           (timestamp, channel, user_key, agent, topic_name, message, response, duration_ms, message_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), channel, user_key,
         agent, topic_name, message, response, duration_ms, message_type)
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def update_conversation(conv_id: int, response: str, duration_ms: int | None = None):
    """Update a conversation with the response."""
    conn = _get_db()
    conn.execute(
        """UPDATE conversations SET response = ?, duration_ms = COALESCE(?, duration_ms)
           WHERE id = ?""",
        (response, duration_ms, conv_id)
    )
    conn.commit()
    conn.close()


def get_conversations(limit: int = 50, agent: str | None = None) -> list[dict]:
    """Get recent conversations, optionally filtered by agent."""
    conn = _get_db()
    if agent:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE agent = ? ORDER BY timestamp DESC LIMIT ?",
            (agent, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation_stats() -> dict:
    """Get conversation statistics."""
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    today = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE timestamp >= date('now')"
    ).fetchone()[0]
    by_agent = conn.execute("""
        SELECT agent, COUNT(*) as count
        FROM conversations WHERE agent IS NOT NULL
        GROUP BY agent ORDER BY count DESC
    """).fetchall()
    conn.close()
    return {
        "total": total,
        "today": today,
        "by_agent": [dict(r) for r in by_agent],
    }


# ── Session tracking ──────────────────────────────────────

# Tools worth logging individually to the activity feed
_INTERESTING_TOOLS = {"Write", "Edit", "Agent", "Bash"}
# Bash commands too trivial to log
_TRIVIAL_BASH = ("ls", "cat", "echo", "pwd", "which", "head", "tail", "wc",
                 "true", "false", "test ", "[ ", "printf")


def should_log_to_feed(tool_name: str, tool_input: dict) -> bool:
    """Decide if a tool use is interesting enough for the activity feed."""
    if tool_name not in _INTERESTING_TOOLS:
        return False
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        if any(cmd.startswith(t) for t in _TRIVIAL_BASH):
            return False
        # Skip simple git info commands
        if cmd in ("git status", "git diff", "git log", "git branch"):
            return False
    return True


def upsert_session(session_id: str, tool_name: str = None,
                   tool_input: dict = None, working_dir: str = None) -> bool:
    """Create or update a session. Returns True if this is a new session."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()

    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?",
                       (session_id,)).fetchone()
    is_new = row is None

    if is_new:
        conn.execute(
            """INSERT INTO sessions (session_id, started_at, working_dir,
               status, files_modified, tools_used, total_tools, last_activity)
               VALUES (?, ?, ?, 'running', '[]', '{}', 0, ?)""",
            (session_id, now, working_dir, now)
        )
        conn.commit()
        # Re-fetch to work with it
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?",
                           (session_id,)).fetchone()

    if tool_name:
        # Update tool counts
        tools = json.loads(row["tools_used"])
        tools[tool_name] = tools.get(tool_name, 0) + 1
        total = row["total_tools"] + 1

        # Track modified files
        files = json.loads(row["files_modified"])
        if tool_name in ("Write", "Edit"):
            fpath = tool_input.get("file_path", "") if tool_input else ""
            if fpath and fpath not in files:
                files.append(fpath)

        conn.execute(
            """UPDATE sessions SET tools_used = ?, total_tools = ?,
               files_modified = ?, last_activity = ? WHERE session_id = ?""",
            (json.dumps(tools), total, json.dumps(files), now, session_id)
        )

    conn.commit()
    conn.close()
    return is_new


def end_session(session_id: str):
    """Mark a session as completed."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    conn.execute(
        "UPDATE sessions SET status = 'completed', ended_at = ? WHERE session_id = ?",
        (now, session_id)
    )
    conn.commit()
    conn.close()


def get_sessions(limit: int = 50, status: str = None,
                 agent: str = None) -> list[dict]:
    """Get recent sessions with optional filters."""
    conn = _get_db()
    query = "SELECT * FROM sessions WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if agent:
        query += " AND agent_name = ?"
        params.append(agent)
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["files_modified"] = json.loads(d.get("files_modified") or "[]")
        d["tools_used"] = json.loads(d.get("tools_used") or "{}")
        result.append(d)
    return result


def get_session(session_id: str) -> dict | None:
    """Get a single session by ID."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?",
                       (session_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["files_modified"] = json.loads(d.get("files_modified") or "[]")
    d["tools_used"] = json.loads(d.get("tools_used") or "{}")
    return d


def get_session_stats() -> dict:
    """Get session statistics."""
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    running = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE status = 'running'"
    ).fetchone()[0]
    today = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE started_at >= date('now')"
    ).fetchone()[0]
    conn.close()
    return {"total": total, "running": running, "today": today}


def get_session_activity(session_id: str, limit: int = 100) -> list[dict]:
    """Get activity entries for a specific session."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM activity WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_today_summary() -> dict:
    """Get aggregate stats for today's work."""
    conn = _get_db()
    sessions_today = conn.execute(
        "SELECT * FROM sessions WHERE started_at >= date('now') ORDER BY started_at DESC"
    ).fetchall()

    total_sessions = len(sessions_today)
    total_files = set()
    total_ops = 0
    total_seconds = 0
    commits = 0

    for s in sessions_today:
        files = json.loads(s["files_modified"] or "[]")
        total_files.update(files)
        total_ops += s["total_tools"] or 0
        if s["started_at"] and s["ended_at"]:
            try:
                start = datetime.fromisoformat(s["started_at"])
                end = datetime.fromisoformat(s["ended_at"])
                total_seconds += (end - start).total_seconds()
            except Exception:
                pass

    # Count git commits from activity
    commits = conn.execute(
        "SELECT COUNT(*) FROM activity WHERE action LIKE '%commit%' AND timestamp >= date('now')"
    ).fetchone()[0]

    conn.close()

    # Format active time
    if total_seconds < 60:
        active_time = f"{int(total_seconds)}s"
    elif total_seconds < 3600:
        active_time = f"{int(total_seconds // 60)}m"
    else:
        active_time = f"{int(total_seconds // 3600)}h {int((total_seconds % 3600) // 60)}m"

    return {
        "sessions": total_sessions,
        "files": len(total_files),
        "ops": total_ops,
        "commits": commits,
        "active_time": active_time,
    }


def get_recent_sessions_enriched(limit: int = 10) -> list[dict]:
    """Get recent sessions with key activity summaries baked in."""
    sessions = get_sessions(limit=limit)
    conn = _get_db()
    for s in sessions:
        # Get key actions for this session (commits, agents, notable commands)
        rows = conn.execute(
            """SELECT action, summary FROM activity
               WHERE session_id = ? AND agent = 'claude'
               AND action NOT IN ('Session started', 'Session ended')
               ORDER BY timestamp ASC LIMIT 8""",
            (s["session_id"],)
        ).fetchall()
        s["key_actions"] = [dict(r) for r in rows]

        # Compute duration string
        if s["started_at"] and s["ended_at"]:
            try:
                start = datetime.fromisoformat(s["started_at"])
                end = datetime.fromisoformat(s["ended_at"])
                delta = (end - start).total_seconds()
                if delta < 60:
                    s["duration_str"] = f"{int(delta)}s"
                elif delta < 3600:
                    s["duration_str"] = f"{int(delta // 60)}m"
                else:
                    s["duration_str"] = f"{int(delta // 3600)}h {int((delta % 3600) // 60)}m"
            except Exception:
                s["duration_str"] = ""
        else:
            s["duration_str"] = ""

        # Project name — prefer DB project column, fall back to working_dir
        if s.get("project") and s["project"] != "unknown":
            pass  # Already set from DB
        elif s.get("working_dir"):
            s["project"] = s["working_dir"].rstrip("/").rsplit("/", 1)[-1]
        else:
            s["project"] = "unknown"

        # File names only
        s["file_names"] = [f.rsplit("/", 1)[-1] for f in s.get("files_modified", [])]

    conn.close()
    return sessions
