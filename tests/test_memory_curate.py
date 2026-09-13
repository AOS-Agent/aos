"""Tests for core/bin/crons/memory-curate — the weekly memory curation pass.

Covers the gather step (vault log entries + closed tasks, both read-only,
both run against temp fixtures — never the operator's real vault or work
DB), the pure prompt builder, proposal parsing, and the merge-write into
memory-proposals.yaml — with the agent dispatch always stubbed. No test in
this file may shell out to `claude`; dispatch_advisor is either not called
at all (gather/prompt/parse/write tests) or monkeypatched (main() tests).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date, timedelta
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CRON_PATH = REPO / "core" / "bin" / "crons" / "memory-curate"


def _load():
    # memory-curate has no .py suffix (it's an executable cron script, like
    # weekly-digest) — spec_from_file_location can't infer a loader for that,
    # so hand it one explicitly (see tests/test_weekly_digest.py).
    loader = SourceFileLoader("memory_curate_under_test", str(CRON_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def m():
    return _load()


def _write(path: Path, frontmatter: dict, body: str = "Some body text.\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{fm}\n---\n{body}")


# ── gather_log_entries ──────────────────────────────────────────────────────

def test_gather_log_entries_picks_up_daily_session_meeting(m, tmp_path):
    vault = tmp_path / "vault"
    today = date(2026, 9, 13)

    _write(
        vault / "log" / "2026-09-10.md",
        {"title": "Thursday, September 10", "type": "daily", "date": "2026-09-10", "tags": ["daily"]},
        "# Thursday\n\nDid a thing.\n",
    )
    _write(
        vault / "log" / "sessions" / "2026-09-11-abc123.md",
        {"title": "Session about widgets", "type": "session-export", "date": "2026-09-11"},
        "Discussed the widget architecture decision.\n",
    )
    _write(
        vault / "log" / "meetings" / "2026-09-12-partner-call.md",
        {"title": "Partner call", "type": "session", "date": "2026-09-12", "project": "widgets"},
        "Agreed on the Q4 timeline.\n",
    )

    entries = m.gather_log_entries(vault_dir=vault, days=7, today=today)

    sources = {e["source"] for e in entries}
    assert sources == {
        "log/2026-09-10.md",
        "log/sessions/2026-09-11-abc123.md",
        "log/meetings/2026-09-12-partner-call.md",
    }
    # Newest first
    assert [e["date"] for e in entries] == ["2026-09-12", "2026-09-11", "2026-09-10"]
    daily = next(e for e in entries if e["source"] == "log/2026-09-10.md")
    assert daily["type"] == "daily"
    assert daily["title"] == "Thursday, September 10"
    assert "Did a thing" in daily["excerpt"]


def test_gather_log_entries_excludes_out_of_window_and_wrong_type(m, tmp_path):
    vault = tmp_path / "vault"
    today = date(2026, 9, 13)

    # Too old — 10 days back, outside the 7-day window.
    _write(vault / "log" / "2026-09-03.md", {"title": "Old daily", "type": "daily", "date": "2026-09-03"})
    # Wrong type at the top level — a friction report, not one of the three kinds asked for.
    _write(
        vault / "log" / "friction" / "2026-09-12-friction.md",
        {"title": "Friction report", "type": "friction", "date": "2026-09-12"},
    )
    # vault-maintenance log — also out of scope.
    _write(
        vault / "log" / "2026-09-12-maintenance.md",
        {"title": "Vault maintenance", "type": "maintenance", "date": "2026-09-12"},
    )
    # A weekly review — out of scope.
    _write(vault / "log" / "2026-W36.md", {"title": "Weekly review", "type": "review", "date": "2026-09-06"})

    entries = m.gather_log_entries(vault_dir=vault, days=7, today=today)
    assert entries == []


def test_gather_log_entries_missing_vault_returns_empty(m, tmp_path):
    entries = m.gather_log_entries(vault_dir=tmp_path / "does-not-exist", days=7)
    assert entries == []


def test_gather_log_entries_uses_module_default_when_no_arg(m, tmp_path):
    """Mirrors the weekly-digest test convention: overriding the module-level
    constant after load must be honored by a no-arg call."""
    vault = tmp_path / "vault"
    _write(
        vault / "log" / "2026-09-10.md",
        {"title": "Daily", "type": "daily", "date": "2026-09-10"},
    )
    m.VAULT_DIR = vault
    entries = m.gather_log_entries(today=date(2026, 9, 13))
    assert len(entries) == 1


# ── gather_closed_tasks ──────────────────────────────────────────────────────

def _make_tasks_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            project_id TEXT,
            completed_at TEXT,
            description TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO tasks (id, title, status, project_id, completed_at, description) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_gather_closed_tasks_filters_status_and_window(m, tmp_path):
    db_path = tmp_path / "work.db"
    today = date(2026, 9, 13)
    _make_tasks_db(
        db_path,
        [
            ("aos#1", "Done in window", "done", "aos", "2026-09-11T10:00:00", "did the thing"),
            ("aos#2", "Cancelled in window", "cancelled", "aos", "2026-09-12T09:00:00", None),
            ("aos#3", "Done too long ago", "done", "aos", "2026-08-01T09:00:00", None),
            ("aos#4", "Still active", "active", "aos", None, None),
            ("aos#5", "Todo, never closed", "todo", None, None, None),
        ],
    )

    tasks = m.gather_closed_tasks(db_path=db_path, days=7, today=today)
    ids = {t["id"] for t in tasks}
    assert ids == {"aos#1", "aos#2"}
    # Newest completed first
    assert [t["id"] for t in tasks] == ["aos#2", "aos#1"]
    assert tasks[1]["notes"] == "did the thing"


def test_gather_closed_tasks_missing_db_returns_empty(m, tmp_path):
    assert m.gather_closed_tasks(db_path=tmp_path / "nope.db", days=7) == []


def test_gather_closed_tasks_missing_table_returns_empty(m, tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE unrelated (id TEXT)")
    conn.commit()
    conn.close()
    assert m.gather_closed_tasks(db_path=db_path, days=7) == []


# ── build_prompt (pure) ──────────────────────────────────────────────────────

def test_build_prompt_includes_entries_and_contract(m):
    log_entries = [
        {"source": "log/2026-09-10.md", "type": "daily", "date": "2026-09-10", "title": "T", "tags": ["x"], "excerpt": "hi"}
    ]
    closed_tasks = [{"id": "aos#1", "title": "Ship it", "status": "done", "project": "aos", "completed_at": "2026-09-11T00:00:00"}]
    prompt = m.build_prompt(log_entries, closed_tasks, today=date(2026, 9, 13))

    assert "log/2026-09-10.md" in prompt
    assert "aos#1: Ship it [aos]" in prompt
    assert "Do NOT write, edit, or create anything" in prompt
    assert "~/vault/knowledge/" in prompt
    assert "SCHEMA.md" in prompt


def test_build_prompt_handles_empty_inputs(m):
    prompt = m.build_prompt([], [], today=date(2026, 9, 13))
    assert "(none in window)" in prompt


# ── parse_proposals ──────────────────────────────────────────────────────────

def test_parse_proposals_plain_list(m):
    raw = """
- source: log/2026-09-10.md
  source_type: daily
  date: "2026-09-10"
  summary: A durable fact
  target_path: knowledge/references/some-fact.md
  target_type: reference
"""
    proposals = m.parse_proposals(raw)
    assert len(proposals) == 1
    assert proposals[0]["source"] == "log/2026-09-10.md"


def test_parse_proposals_strips_fenced_block(m):
    raw = "Sure, here you go:\n```yaml\n- source: a\n  summary: b\n```\n"
    proposals = m.parse_proposals(raw)
    assert proposals == [{"source": "a", "summary": "b"}]


def test_parse_proposals_empty_list(m):
    assert m.parse_proposals("[]") == []
    assert m.parse_proposals("") == []


def test_parse_proposals_garbage_returns_empty(m):
    assert m.parse_proposals("not: valid: yaml: [") == []
    assert m.parse_proposals("just a plain sentence") == []
    assert m.parse_proposals("key: value") == []  # a dict, not a list


# ── write_proposals ──────────────────────────────────────────────────────────

def test_write_proposals_shape_matches_inject_context_contract(m, tmp_path):
    out = tmp_path / "memory-proposals.yaml"
    proposals = [
        {"source": "log/2026-09-10.md", "date": "2026-09-10", "summary": "fact one"},
        {"source": "log/2026-09-11.md", "date": "2026-09-11", "summary": "fact two"},
    ]
    merged = m.write_proposals(proposals, out_path=out, generated_at="2026-09-13T06:00:00")

    assert len(merged) == 2
    # Exactly the contract inject_context.py relies on: yaml.safe_load(...) or []
    # must return a truthy, sized list.
    loaded = yaml.safe_load(out.read_text()) or []
    assert isinstance(loaded, list)
    assert len(loaded) == 2
    assert all("proposed_at" in item for item in loaded)


def test_write_proposals_merges_and_dedupes_by_source(m, tmp_path):
    out = tmp_path / "memory-proposals.yaml"
    m.write_proposals([{"source": "log/a.md", "date": "2026-09-06", "summary": "old"}], out_path=out)
    merged = m.write_proposals(
        [{"source": "log/a.md", "date": "2026-09-13", "summary": "refreshed"},
         {"source": "log/b.md", "date": "2026-09-13", "summary": "new"}],
        out_path=out,
    )
    by_source = {p["source"]: p for p in merged}
    assert len(merged) == 2
    assert by_source["log/a.md"]["summary"] == "refreshed"
    assert by_source["log/b.md"]["summary"] == "new"


def test_write_proposals_empty_list_still_writes_valid_yaml(m, tmp_path):
    out = tmp_path / "memory-proposals.yaml"
    merged = m.write_proposals([], out_path=out)
    assert merged == []
    loaded = yaml.safe_load(out.read_text()) or []
    assert loaded == []


# ── main() — agent dispatch always stubbed ───────────────────────────────────

def test_main_dry_run_never_dispatches_or_writes(m, tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    _write(vault / "log" / "2026-09-10.md", {"title": "Daily", "type": "daily", "date": "2026-09-10"})
    m.VAULT_DIR = vault
    m.WORK_DB = tmp_path / "no-such-work.db"
    out = tmp_path / "memory-proposals.yaml"
    m.PROPOSALS_FILE = out

    def _boom(*a, **k):
        raise AssertionError("dispatch_advisor must not be called in --dry-run")

    monkeypatch.setattr(m, "dispatch_advisor", _boom)

    rc = m.main(["--dry-run", "--days", "7"])
    assert rc == 0
    assert not out.exists()


def test_main_end_to_end_with_stubbed_dispatch(m, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write(
        vault / "log" / "2026-09-10.md",
        {"title": "Daily", "type": "daily", "date": "2026-09-10"},
        "Decided to use SQLite for the cache.\n",
    )
    m.VAULT_DIR = vault
    m.WORK_DB = tmp_path / "no-such-work.db"
    out = tmp_path / "memory-proposals.yaml"
    m.PROPOSALS_FILE = out

    canned = """
- source: log/2026-09-10.md
  source_type: daily
  date: "2026-09-10"
  summary: Decided to use SQLite for the cache
  target_path: knowledge/decisions/2026-09-10-sqlite-cache.md
  target_type: decision
  stage: 5
"""
    calls = []

    def _stub(prompt, model="sonnet", timeout=900):
        calls.append((prompt, model))
        return canned

    monkeypatch.setattr(m, "dispatch_advisor", _stub)

    # Freeze "today" indirectly: the daily above is dated 2026-09-10, well
    # within any window measured from a real "today" in this test's run era,
    # so no date freezing is needed here — gather_log_entries(days=7) from
    # `date.today()` would only miss it once 2026-09-10 is >7 days in the
    # past. Pin days generously wide instead to keep this test robust.
    rc = m.main(["--days", "3650"])

    assert rc == 0
    assert len(calls) == 1
    assert "log/2026-09-10.md" in calls[0][0]

    loaded = yaml.safe_load(out.read_text()) or []
    assert len(loaded) == 1
    assert loaded[0]["target_path"] == "knowledge/decisions/2026-09-10-sqlite-cache.md"


def test_main_nothing_in_window_skips_dispatch(m, tmp_path, monkeypatch):
    m.VAULT_DIR = tmp_path / "empty-vault"
    m.WORK_DB = tmp_path / "no-such-work.db"
    out = tmp_path / "memory-proposals.yaml"
    m.PROPOSALS_FILE = out

    def _boom(*a, **k):
        raise AssertionError("dispatch_advisor must not be called with nothing in window")

    monkeypatch.setattr(m, "dispatch_advisor", _boom)

    rc = m.main(["--days", "7"])
    assert rc == 0
    assert not out.exists()


def test_main_dispatch_failure_reported_not_raised(m, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write(vault / "log" / "2026-09-10.md", {"title": "Daily", "type": "daily", "date": "2026-09-10"})
    m.VAULT_DIR = vault
    m.WORK_DB = tmp_path / "no-such-work.db"
    out = tmp_path / "memory-proposals.yaml"
    m.PROPOSALS_FILE = out

    def _fail(*a, **k):
        raise RuntimeError("advisor dispatch failed rc=1: boom")

    monkeypatch.setattr(m, "dispatch_advisor", _fail)

    rc = m.main(["--days", "3650"])
    assert rc == 1
    assert not out.exists()
