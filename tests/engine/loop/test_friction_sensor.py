"""Friction sensor — gate enforcement, extraction, quota abort, budget cap.
No real LLM calls: judge is monkeypatched throughout."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.engine.loop import judge as judge_mod  # noqa: E402
from core.engine.loop import llm  # noqa: E402
from core.engine.loop import signals as sig
from core.engine.loop.sensors import friction  # noqa: E402

_SIGNALS_SCHEMA = """
CREATE TABLE signals (
    id TEXT PRIMARY KEY, sensor TEXT NOT NULL, signal_type TEXT NOT NULL,
    payload_json TEXT NOT NULL, source_refs TEXT NOT NULL,
    tainted INTEGER NOT NULL DEFAULT 0, project_key TEXT, created_at TEXT NOT NULL
);
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "qareen.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SIGNALS_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(sig, "_db_path", lambda: db)

    marker = tmp_path / "judge-gate-pass.json"
    marker.write_text(json.dumps({"judge_version": judge_mod.version_hash()}))
    monkeypatch.setattr(friction, "PASS_MARKER", marker)

    projects = tmp_path / "projects"
    projects.mkdir()
    return {"db": db, "marker": marker, "projects": projects, "tmp": tmp_path}


def _write_session(projects: Path, name: str, messages):
    d = projects / "proj-a"
    d.mkdir(exist_ok=True)
    f = d / f"{name}.jsonl"
    lines = []
    for m in messages:
        lines.append(json.dumps({"type": "user", "message": {"content": m}}))
    f.write_text("\n".join(lines))
    return f


def _run(projects):
    return asyncio.run(friction.run(projects_dir=projects))


def _signals(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM signals").fetchall()
    conn.close()
    return rows


def test_no_marker_raises(env, monkeypatch):
    monkeypatch.setattr(friction, "PASS_MARKER", env["tmp"] / "missing.json")
    with pytest.raises(friction.GateNotPassed):
        asyncio.run(friction.run(projects_dir=env["projects"]))


def test_stale_marker_raises(env):
    env["marker"].write_text(json.dumps({"judge_version": "000000000000"}))
    with pytest.raises(friction.GateNotPassed):
        asyncio.run(friction.run(projects_dir=env["projects"]))


def test_friction_written_none_skipped(env, monkeypatch):
    async def fake_judge(text, prev=None):
        if "broken" in text:
            return {"machine_text": False, "label": "frustration"}
        return {"machine_text": False, "label": "none"}

    monkeypatch.setattr(friction.judge_mod, "judge", fake_judge)
    _write_session(env["projects"], "abc12345", ["its broken AGAIN", "looks good"])
    summary = _run(env["projects"])
    rows = _signals(env["db"])
    assert summary["judged"] == 2 and summary["signals"] == 1
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "friction_frustration"
    assert rows[0]["tainted"] == 0
    assert json.loads(rows[0]["source_refs"]) == ["session:abc12345:0"]


def test_noise_prefixes_never_judged(env, monkeypatch):
    calls = []

    async def fake_judge(text, prev=None):
        calls.append(text)
        return {"machine_text": False, "label": "none"}

    monkeypatch.setattr(friction.judge_mod, "judge", fake_judge)
    _write_session(env["projects"], "abc12345", [
        "<task-notification>x</task-notification>",
        "<command-name>/gm</command-name>",
        "real human words",
    ])
    _run(env["projects"])
    assert calls == ["real human words"]


def test_429_aborts_politely(env, monkeypatch):
    async def limit_judge(text, prev=None):
        raise llm.LLMError("claude CLI rc=1: api_error_status:429 session limit")

    monkeypatch.setattr(friction.judge_mod, "judge", limit_judge)
    _write_session(env["projects"], "abc12345", ["msg one", "msg two"])
    summary = _run(env["projects"])
    assert summary["aborted_on_limit"] is True
    assert summary["signals"] == 0


def test_budget_cap(env, monkeypatch):
    async def fake_judge(text, prev=None):
        return {"machine_text": False, "label": "none"}

    monkeypatch.setattr(friction.judge_mod, "judge", fake_judge)
    monkeypatch.setattr(friction, "MAX_MESSAGES_PER_RUN", 3)
    _write_session(env["projects"], "abc12345", [f"m{i}" for i in range(10)])
    summary = _run(env["projects"])
    assert summary["judged"] == 3
