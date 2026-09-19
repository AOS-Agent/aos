"""
Test suite for the AOS Context Injection Hook (core/engine/work/inject_context.py).

Tests exercise the module's output contract:
  - Always produces valid JSON
  - Context rides in hookSpecificOutput.additionalContext — the only place
    Claude Code reads it. A top-level "additionalContext" key is silently
    ignored; until v0.7.14 the hook emitted exactly that and no session ever
    received its briefing, while this suite (which asserted the same wrong
    shape) stayed green.
  - Handles an empty or absent work database without crashing
  - Active tasks appear in context
  - High-priority tasks (P1/P2) are surfaced

Strategy: inject_context reads work/task state through the work backend
(``import backend as engine``), which opens a SQLite database. The backend
resolves that database from the ``AOS_WORK_DB`` environment variable first
(see backend._resolve_db_path and migration 050), so each test seeds a tiny
scratch work.db in tmp_path and points the hook at it via AOS_WORK_DB. The
hook runs as a real subprocess (stdin JSON in, stdout JSON out) so we exercise
the full pipeline without polluting this process's module state or touching the
operator's real ~/.aos/data database.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

WORK_PKG = Path(__file__).parent.parent / "core" / "engine" / "work"
INJECT_CONTEXT = WORK_PKG / "inject_context.py"


# ---------------------------------------------------------------------------
# Scratch work.db — the subset of the canonical schema the hook's read path
# touches (get_all_tasks, summary(), find_tasks_by_project_or_cwd). Mirrors
# the legacy qareen.db schema for these tables, including the migration-added
# columns on `tasks` that the live database carries.
# ---------------------------------------------------------------------------

WORK_SCHEMA = """
CREATE TABLE projects (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    path            TEXT,
    goal            TEXT,
    done_when       TEXT,
    short_id        TEXT,
    telegram_bot_key     TEXT,
    telegram_chat_key    TEXT,
    telegram_forum_topic INTEGER,
    stages          TEXT,
    current_stage   TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    modified_by     TEXT,
    modified_at     TEXT
);

CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'todo',
    priority        INTEGER NOT NULL DEFAULT 3,
    project_id      TEXT REFERENCES projects(id),
    description     TEXT,
    assigned_to     TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    due_at          TEXT,
    parent_id       TEXT REFERENCES tasks(id),
    pipeline        TEXT,
    pipeline_stage  TEXT,
    recurrence      TEXT,
    tags            TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    modified_by     TEXT,
    modified_at     TEXT,
    scheduled_at    TEXT,
    snoozed_until   TEXT,
    estimate_minutes INTEGER,
    story_points    REAL,
    actual_minutes  INTEGER,
    energy          TEXT,
    context         TEXT,
    area_id         TEXT,
    assignee_type   TEXT DEFAULT 'operator',
    recurrence_type TEXT DEFAULT 'fixed',
    template_id     TEXT,
    recurrence_index INTEGER
);

CREATE TABLE task_handoffs (
    task_id         TEXT PRIMARY KEY REFERENCES tasks(id),
    state           TEXT NOT NULL,
    next_step       TEXT NOT NULL,
    files           TEXT,
    decisions       TEXT,
    blockers        TEXT,
    session_id      TEXT,
    timestamp       TEXT
);

CREATE TABLE goals (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    weight          INTEGER DEFAULT 0,
    description     TEXT,
    project_id      TEXT REFERENCES projects(id)
);

CREATE TABLE key_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals(id),
    title           TEXT NOT NULL,
    progress        INTEGER DEFAULT 0,
    target          TEXT
);

CREATE TABLE inbox (
    id              TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    project_id      TEXT REFERENCES projects(id)
);

CREATE TABLE threads (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT DEFAULT 'active',
    created_at      TEXT,
    project_id      TEXT REFERENCES projects(id),
    cwd             TEXT
);
"""


def _make_work_db(db_path: Path, tasks=None, projects=None):
    """Create a scratch work.db and seed it with tasks/projects.

    Referenced projects are auto-created so project scoping renders, but the
    foreign key is not enforced (the connection leaves foreign_keys off), so
    tests need only specify the fields they assert on.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(WORK_SCHEMA)

        seeded_projects = set()

        def _ensure_project(pid: str):
            if pid and pid not in seeded_projects:
                conn.execute(
                    "INSERT INTO projects (id, title) VALUES (?, ?)",
                    (pid, pid.upper()),
                )
                seeded_projects.add(pid)

        for pid in projects or []:
            _ensure_project(pid)

        for t in tasks or []:
            pid = t.get("project")
            _ensure_project(pid)
            conn.execute(
                "INSERT INTO tasks (id, title, status, priority, project_id, "
                "created_at, tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    t["id"],
                    t["title"],
                    t.get("status", "todo"),
                    t.get("priority", 3),
                    pid,
                    t.get("created", "2026-01-01"),
                    json.dumps(t["tags"]) if t.get("tags") else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def maintenance_log_for(db_path: Path) -> Path:
    """Where run_inject_context sends the hook's maintenance log."""
    return db_path.parent / "maintenance.jsonl"


def run_inject_context(db_path: Path, hook_input: dict = None) -> dict:
    """Run inject_context.py as a subprocess against db_path, return parsed JSON."""
    if hook_input is None:
        hook_input = {"session_id": "test-session-001", "cwd": str(db_path.parent),
                      "hook_event_name": "SessionStart", "source": "startup"}

    import os
    env = dict(os.environ)
    env["AOS_WORK_DB"] = str(db_path)
    # The hook records its own size to a maintenance log. Redirect it per test:
    # a suite that appends to ~/.aos/logs/ is a suite that edits the operator's
    # machine, and the default path is the operator's machine.
    env["AOS_MAINTENANCE_LOG"] = str(maintenance_log_for(db_path))

    result = subprocess.run(
        [sys.executable, str(INJECT_CONTEXT)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, (
        f"inject_context hook exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    output_line = result.stdout.strip()
    assert output_line, f"inject_context produced no output.\nstderr: {result.stderr}"

    return json.loads(output_line)


# ===========================================================================
# Output Format
# ===========================================================================

class TestOutputFormat:

    def test_context_is_where_claude_code_reads_it(self, tmp_path):
        """The documented SessionStart shape, exactly: context nested under
        hookSpecificOutput with hookEventName naming the event, and no
        top-level additionalContext (Claude Code drops that without a word)."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path)

        output = run_inject_context(db_path)

        assert isinstance(output, dict), \
            "Output must be a JSON object (dict)"
        assert "additionalContext" not in output, (
            "a top-level additionalContext is ignored by Claude Code — the "
            "briefing never reaches the session"
        )
        hso = output.get("hookSpecificOutput")
        assert isinstance(hso, dict), \
            f"Output must carry hookSpecificOutput, got keys: {list(output.keys())}"
        assert hso.get("hookEventName") == "SessionStart"
        assert isinstance(hso.get("additionalContext"), str) and hso["additionalContext"], \
            "additionalContext must be a non-empty string"

    def test_the_briefing_is_reinjected_after_compaction(self, tmp_path):
        """SessionStart fires again with source "compact" after compaction.
        That is the path that restores the briefing, so it must inject."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "t#1", "title": "Build session linking", "status": "active",
             "priority": 2, "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path, {
            "session_id": "s1", "cwd": str(tmp_path),
            "hook_event_name": "SessionStart", "source": "compact",
        })

        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "Build session linking" in output["hookSpecificOutput"]["additionalContext"]

    def test_postcompact_stays_silent(self, tmp_path):
        """The hook is also registered on PostCompact. SessionStart(compact)
        already re-injects, so PostCompact emits nothing: no second copy of
        the briefing, and no SessionStart-shaped payload on an event whose
        name does not match it."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "t#1", "title": "Build session linking", "status": "active",
             "priority": 2, "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path, {
            "session_id": "s1", "cwd": str(tmp_path),
            "hook_event_name": "PostCompact",
        })

        assert output == {}, f"PostCompact must emit {{}}, got {output}"

    def test_the_onboarding_banner_reaches_the_session(self, tmp_path):
        """On a fresh install (no onboarding.yaml) the early-exit path carries
        the onboarding banner — through the same documented shape."""
        import os
        home = tmp_path / "home"
        home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["AOS_WORK_DB"] = str(tmp_path / "does_not_exist.db")
        env["AOS_MAINTENANCE_LOG"] = str(tmp_path / "maintenance.jsonl")

        result = subprocess.run(
            [sys.executable, str(INJECT_CONTEXT)],
            input=json.dumps({"session_id": "s1", "cwd": str(tmp_path),
                              "hook_event_name": "SessionStart", "source": "startup"}),
            capture_output=True, text=True, timeout=20, env=env,
        )

        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout.strip())
        assert "additionalContext" not in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "ONBOARDING REQUIRED" in output["hookSpecificOutput"]["additionalContext"]

    def test_works_with_no_work_db(self, tmp_path):
        """inject_context exits cleanly even when the work database does not exist."""
        db_path = tmp_path / "does_not_exist.db"
        # No database created — the hook must not crash.

        output = run_inject_context(db_path)

        # Must be valid JSON — key may or may not be present when there's no DB.
        assert isinstance(output, dict), \
            "Output must be a JSON object even with no work database"

    def test_works_with_tasks_but_none_active(self, tmp_path):
        """inject_context handles a work.db with only todo tasks (none active)."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "t#1", "title": "Backlog task", "status": "todo", "priority": 3,
             "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path)

        assert "additionalContext" in output.get("hookSpecificOutput", {}), \
            "Must produce additionalContext when tasks exist but none are active"
        # Should not crash trying to list active tasks when there are none
        context = output["hookSpecificOutput"]["additionalContext"]
        assert isinstance(context, str) and len(context) > 0, \
            "Context string must be non-empty"


# ===========================================================================
# Thread continuity (aos#223) — the SessionStart side of the "Work in <dir>"
# fix. find_thread_by_cwd used to always return None (no cwd column), so this
# "Current thread" line has never rendered for anyone since the SQLite port —
# these tests cover both the pre-existing "no thread" case (must still render
# cleanly) and the newly-reachable "thread exists" case.
# ===========================================================================

class TestThreadContinuity:

    def test_no_current_thread_renders_without_crashing(self, tmp_path):
        """No thread for this cwd yet — the hook must still produce a clean
        additionalContext with no 'Current thread' line."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path)

        output = run_inject_context(
            db_path, {"session_id": "s1", "cwd": str(tmp_path)}
        )

        assert "additionalContext" in output.get("hookSpecificOutput", {})
        assert "Current thread" not in output["hookSpecificOutput"]["additionalContext"]

    def test_an_auto_thread_for_this_cwd_does_not_render(self, tmp_path):
        """v0.7.7: an auto "Work in <dir>" thread is a record that a directory
        existed, not an exploration anyone chose to open. It used to take a line
        of every briefing; 4,639 of the live DB's 4,641 threads are these."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path)
        cwd = str(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO threads (id, title, status, created_at, cwd) "
            "VALUES ('th1', 'Work in scratch', 'exploring', '2026-09-01', ?)",
            (cwd,),
        )
        conn.commit()
        conn.close()

        output = run_inject_context(db_path, {"session_id": "s1", "cwd": cwd})

        assert "additionalContext" in output.get("hookSpecificOutput", {})
        assert "Work in scratch" not in output["hookSpecificOutput"]["additionalContext"]

    def test_an_operator_written_thread_renders(self, tmp_path):
        """The inverse, and the reason this is a filter rather than a deletion:
        a thread the operator typed is the continuity the section exists for."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path)
        cwd = str(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO threads (id, title, status, created_at, cwd) "
            "VALUES ('th1', 'Qren cutover night', 'exploring', '2026-09-01', ?)",
            (cwd,),
        )
        conn.commit()
        conn.close()

        output = run_inject_context(db_path, {"session_id": "s1", "cwd": cwd})

        assert "Qren cutover night" in output["hookSpecificOutput"]["additionalContext"]

    def test_a_closed_thread_does_not_render(self, tmp_path):
        db_path = tmp_path / "work.db"
        _make_work_db(db_path)
        cwd = str(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO threads (id, title, status, created_at, cwd) "
            "VALUES ('th1', 'Qren cutover night', 'closed', '2026-09-01', ?)",
            (cwd,),
        )
        conn.commit()
        conn.close()

        output = run_inject_context(db_path, {"session_id": "s1", "cwd": cwd})
        assert "Qren cutover night" not in output["hookSpecificOutput"]["additionalContext"]


# ===========================================================================
# Content — 2 tests
# ===========================================================================

class TestContextContent:

    def test_active_tasks_appear_in_context(self, tmp_path):
        """Tasks with status='active' are surfaced in the injected context."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "aos#7", "title": "Active feature work", "status": "active",
             "priority": 2, "project": "aos", "created": "2026-01-01"},
            {"id": "aos#8", "title": "Idle backlog item", "status": "todo",
             "priority": 4, "project": "aos", "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path)
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")

        assert "Active feature work" in context, \
            "Active task title must appear in injected context"

    def test_high_priority_tasks_are_highlighted(self, tmp_path):
        """Priority 1 and 2 todo tasks appear under a 'High priority' section."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=[
            {"id": "t#1", "title": "Urgent P1 task", "status": "todo",
             "priority": 1, "created": "2026-01-01"},
            {"id": "t#2", "title": "Important P2 task", "status": "todo",
             "priority": 2, "created": "2026-01-01"},
            {"id": "t#3", "title": "Normal priority task", "status": "todo",
             "priority": 3, "created": "2026-01-01"},
        ])

        output = run_inject_context(db_path)
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")

        assert "Urgent P1 task" in context, \
            "P1 task must appear in context"
        assert "Important P2 task" in context, \
            "P2 task must appear in context"
        assert "High priority" in context, \
            "Context must contain a 'High priority' heading section"


# ===========================================================================
# Briefing diet (v0.7.7)
#
# The SessionStart briefing is paid for on every session, in every directory:
# measured live at 4,487 chars (~1,120 tokens). Threads were the largest
# avoidable part of it — 4,639 of the live DB's 4,641 rows are auto-generated
# "Work in <dir>" records, and exactly 2 were ever promoted. A line per
# directory the operator happened to have a session in is not context, it is
# a changelog of the filesystem.
# ===========================================================================

FIXTURE_TASKS = [
    {"id": "aos#1", "title": "Build session linking", "status": "active",
     "priority": 2, "project": "aos", "created": "2026-09-01"},
    {"id": "aos#2", "title": "Write onboarding docs", "status": "todo",
     "priority": 2, "project": "aos", "created": "2026-09-02"},
    {"id": "aos#3", "title": "Ship the project layer", "status": "todo",
     "priority": 1, "project": "aos", "created": "2026-09-03"},
    {"id": "t#1", "title": "Unscoped errand", "status": "todo",
     "priority": 3, "created": "2026-09-04"},
]


def _seed_threads(db_path: Path, cwd: str, auto: int = 50) -> None:
    """`auto` generated threads plus one the operator promoted."""
    conn = sqlite3.connect(str(db_path))
    try:
        for i in range(auto):
            conn.execute(
                "INSERT INTO threads (id, title, status, created_at, cwd) "
                "VALUES (?, ?, 'exploring', ?, ?)",
                (f"th{i}", f"Work in v0.7.{i}-abc123", "2026-09-01",
                 f"{cwd}/release-{i}"),
            )
        conn.execute(
            "INSERT INTO threads (id, title, status, created_at, project_id, cwd) "
            "VALUES ('th-promoted', 'aos-app workspace layer build', 'promoted', "
            "'2026-09-10', 'aos', ?)",
            (cwd,),
        )
        conn.commit()
    finally:
        conn.close()


class TestBriefingDiet:

    def test_fifty_auto_threads_and_one_promoted_render_exactly_one(self, tmp_path):
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=FIXTURE_TASKS)
        cwd = str(tmp_path)
        _seed_threads(db_path, cwd, auto=50)

        context = run_inject_context(
            db_path, {"session_id": "s1", "cwd": cwd}
        )["hookSpecificOutput"]["additionalContext"]

        assert "aos-app workspace layer build" in context, (
            "the promoted thread is the one the operator curated — it must render"
        )
        leaked = [ln for ln in context.splitlines() if "Work in v0.7." in ln]
        assert leaked == [], f"auto threads leaked into the briefing: {leaked}"

    def test_briefing_stays_under_900_tokens(self, tmp_path):
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=FIXTURE_TASKS)
        cwd = str(tmp_path)
        _seed_threads(db_path, cwd, auto=50)

        context = run_inject_context(
            db_path, {"session_id": "s1", "cwd": cwd}
        )["hookSpecificOutput"]["additionalContext"]

        est_tokens = len(context) / 4
        assert est_tokens < 900, (
            f"briefing is ~{est_tokens:.0f} tokens ({len(context)} chars); "
            "the budget is 900 and it is paid on every session in every "
            f"directory.\n---\n{context}\n---"
        )

    def test_the_hook_records_its_own_size(self, tmp_path):
        """A budget nobody measures is a wish. The hook writes what it cost to
        the maintenance log, so the next audit reads a number instead of
        re-deriving one."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=FIXTURE_TASKS)
        cwd = str(tmp_path)
        _seed_threads(db_path, cwd, auto=50)

        context = run_inject_context(
            db_path, {"session_id": "s1", "cwd": cwd}
        )["hookSpecificOutput"]["additionalContext"]

        log = maintenance_log_for(db_path)
        assert log.exists(), "the hook did not write a maintenance log entry"
        entry = json.loads(log.read_text().strip().splitlines()[-1])
        assert entry["event"] == "briefing_rendered"
        assert entry["chars"] == len(context)
        assert entry["est_tokens"] == len(context) // 4
        assert entry["threads_rendered"] == 1
        assert entry["threads_suppressed"] == 50

    def test_the_log_is_one_line_per_session_not_a_rewrite(self, tmp_path):
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=FIXTURE_TASKS)
        cwd = str(tmp_path)

        run_inject_context(db_path, {"session_id": "s1", "cwd": cwd})
        run_inject_context(db_path, {"session_id": "s2", "cwd": cwd})

        lines = maintenance_log_for(db_path).read_text().strip().splitlines()
        assert len(lines) == 2
        assert {json.loads(ln)["session_id"] for ln in lines} == {"s1", "s2"}

    def test_a_logging_failure_never_breaks_the_hook(self, tmp_path):
        """Hooks must always emit valid JSON and exit 0 (CLAUDE.md). The log is
        a side effect; an unwritable path is not a reason to lose the briefing."""
        db_path = tmp_path / "work.db"
        _make_work_db(db_path, tasks=FIXTURE_TASKS)

        import os
        import subprocess
        env = dict(os.environ)
        env["AOS_WORK_DB"] = str(db_path)
        # A path whose parent is a FILE — mkdir and open both fail.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("")
        env["AOS_MAINTENANCE_LOG"] = str(blocker / "maintenance.jsonl")

        result = subprocess.run(
            [sys.executable, str(INJECT_CONTEXT)],
            input=json.dumps({"session_id": "s1", "cwd": str(tmp_path)}),
            capture_output=True, text=True, timeout=20, env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "additionalContext" in json.loads(result.stdout.strip())["hookSpecificOutput"]

    def test_the_ceiling_holds_when_the_briefing_is_fat(self, tmp_path):
        """The 900-token figure is a delivered ceiling, not a section budget.

        BRIEFING_BUDGET caps the sections; the header, the separator and the
        guidance block are appended after the drop loop. A project with many
        active tasks is the case that finds the difference — it grows both the
        sections (which get dropped) and the guidance id list (which does not),
        so the guidance line is bounded too.
        """
        db_path = tmp_path / "work.db"
        tasks = [
            {"id": f"aos#{i}",
             "title": f"Active piece of work number {i} with a title long "
                      f"enough to matter to a byte budget",
             "status": "active", "priority": 1 if i % 3 else 2,
             "project": "aos", "created": "2026-09-01"}
            for i in range(40)
        ]
        _make_work_db(db_path, tasks=tasks)
        cwd = str(tmp_path)
        _seed_threads(db_path, cwd, auto=50)

        context = run_inject_context(
            db_path, {"session_id": "s1", "cwd": cwd}
        )["hookSpecificOutput"]["additionalContext"]

        est_tokens = len(context) / 4
        assert est_tokens < 900, (
            f"a fat briefing delivered ~{est_tokens:.0f} tokens "
            f"({len(context)} chars) — the section budget left too little "
            "headroom for the header and guidance appended after it"
        )
        assert "more)" in context or "Active tasks in this project" not in context, (
            "with 40 active tasks the guidance id list must be truncated with a "
            "count, not printed in full"
        )
