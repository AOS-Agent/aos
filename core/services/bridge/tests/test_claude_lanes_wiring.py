"""
Tests for the two bridge spawn points wired to Claude profile lanes
(aos#244.3): `persistent_session.py` (one long-lived process) and
`session_manager.py`'s two async streaming spawns (`_generate()`,
`_retry_fresh()`). Neither fits `claude_lanes.run()`'s one-shot shape — see
each module's own docstring/comments for exactly what changed and why — so
these drive the real async code paths end to end against a stub `claude`
that speaks the same NDJSON stream-json protocol the real binary does.

Runs in the bridge venv:
    ~/.aos/services/bridge/.venv/bin/python -m pytest core/services/bridge/tests -q

(Also runs fine under the main aos-python — neither module under test here
has a bridge-only dependency beyond `bridge_events`, which is stdlib-only.)

No pytest-asyncio in the bridge venv (no new dependency was added for this),
so every async path is driven with a plain `asyncio.run(...)` inside an
ordinary test function — the same pattern test_voice_long_notes.py already
uses in this suite.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BRIDGE_DIR.parent.parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

WORK_SCHEMA = (REPO_ROOT / "tests" / "fixtures" / "work_schema.sql").read_text()

# The stub speaks the real stream-json shapes parse_event() understands: one
# "system"/"init" line up front, then a "result" line — data-driven the same
# way test_claude_lanes.py's stub is (env vars say which lane(s) should
# report a limit), so the SAME binary drives every scenario below. Two
# shapes, matched on argv, because the two callers under test spawn `claude`
# two different ways:
#   - session_manager.py's `_generate()`/`_retry_fresh()` run one-shot
#     `claude -p "<message>" ...`: the message is an argv value, not stdin,
#     and the process is expected to print its one result and exit.
#   - persistent_session.py holds the process open across many messages via
#     `--input-format stream-json`: one JSON line in on stdin per message,
#     one result line out, looping until stdin closes.
_STUB_CLAUDE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
    lane = os.path.basename(cfg.rstrip("/")) if cfg else "default"

    log_path = os.environ.get("LANES_CALL_LOG")
    if log_path:
        with open(log_path, "a") as f:
            f.write(lane + "\\n")

    limited = {x for x in os.environ.get("LANES_LIMIT_LANES", "").split(",") if x}

    def emit_result():
        if lane in limited:
            result = {
                "type": "result", "session_id": "sess-" + lane,
                "result": "You've hit your weekly limit. Resets at 3pm",
                "is_error": True, "duration_ms": 1, "total_cost_usd": 0.0,
                "usage": {}, "num_turns": 1,
            }
        else:
            result = {
                "type": "result", "session_id": "sess-" + lane,
                "result": "OK from " + lane,
                "is_error": False, "duration_ms": 1, "total_cost_usd": 0.0,
                "usage": {}, "num_turns": 1,
            }
        print(json.dumps(result), flush=True)

    print(json.dumps({
        "type": "system", "subtype": "init",
        "session_id": "sess-" + lane, "model": "x", "tools": [],
    }), flush=True)

    if "--input-format" in sys.argv:
        # Persistent-session shape: one message per stdin line, looping.
        for raw in sys.stdin:
            if raw.strip():
                emit_result()
    else:
        # One-shot `-p "<message>"` shape: the message is already in argv.
        emit_result()
    """)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".aos" / "config").mkdir(parents=True)
    (home / ".aos" / "data").mkdir(parents=True)

    db_path = home / ".aos" / "data" / "work.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(WORK_SCHEMA)
    conn.commit()
    conn.close()

    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    stub = stub_dir / "claude"
    stub.write_text(_STUB_CLAUDE)
    stub.chmod(0o755)

    call_log = tmp_path / "calls.log"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("AOS_WORK_DB", str(db_path))
    monkeypatch.setenv("LANES_CALL_LOG", str(call_log))
    monkeypatch.delenv("LANES_LIMIT_LANES", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    return {"home": home, "call_log": call_log}


def _calls(sandbox) -> list[str]:
    if not sandbox["call_log"].exists():
        return []
    return [l for l in sandbox["call_log"].read_text().splitlines() if l]


def _write_lanes_yaml(home: Path, lanes: list[str]) -> None:
    (home / ".aos" / "config").mkdir(parents=True, exist_ok=True)
    (home / ".aos" / "config" / "claude-lanes.yaml").write_text(
        "lanes: [" + ", ".join(lanes) + "]\n"
    )


def _make_profile_dir(home: Path, name: str) -> Path:
    d = home / ".aos" / "claude-profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fresh_persistent_session_module():
    """persistent_session imports session_manager lazily inside send(), and
    both cache `claude_lanes` at their own module scope on first import — so
    each test gets fresh copies of both, imported only after `sandbox` has
    already patched $HOME (claude_lanes itself re-resolves Path.home() per
    call, but the sys.path bootstrap and the `claude_lanes` reference each
    module holds are only set up once, at import time)."""
    for name in ("persistent_session", "session_manager", "claude_lanes"):
        sys.modules.pop(name, None)
    import persistent_session
    return persistent_session


class TestPersistentSessionLanes:
    def test_start_applies_the_first_available_lane(self, sandbox):
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        ps_mod = _fresh_persistent_session_module()

        async def _t():
            ps = ps_mod.PersistentSession(cwd=str(sandbox["home"]))
            await ps.start()
            try:
                assert ps.lane == "default"
                assert ps.alive
            finally:
                await ps.stop()

        asyncio.run(_t())

    def test_limit_hit_terminates_and_next_start_uses_next_lane(self, sandbox, monkeypatch):
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        monkeypatch.setenv("LANES_LIMIT_LANES", "default")
        ps_mod = _fresh_persistent_session_module()

        async def _t():
            ps = ps_mod.PersistentSession(cwd=str(sandbox["home"]))
            await ps.start()
            assert ps.lane == "default"

            results = [ev async for ev in ps.send("hello")]
            assert any(getattr(ev, "is_error", False) for ev in results)
            assert not ps.alive, "the process must be terminated (and reaped) after a limit hit"

            # Next send() auto-restarts — the existing crash-recovery path —
            # and should now resolve to cld2, since default is exhausted.
            results2 = [ev async for ev in ps.send("hello again")]
            assert ps.lane == "cld2"
            assert not any(getattr(ev, "is_error", False) for ev in results2)

            await ps.stop()

        asyncio.run(_t())
        assert _calls(sandbox) == ["default", "cld2"]

        lanes_mod = sys.modules["claude_lanes"]
        state = lanes_mod.load_state()
        assert "default" in state

    def test_no_lanes_file_is_unaffected(self, sandbox):
        """No claude-lanes.yaml at all — the byte-for-byte-unchanged case."""
        ps_mod = _fresh_persistent_session_module()

        async def _t():
            ps = ps_mod.PersistentSession(cwd=str(sandbox["home"]))
            await ps.start()
            assert ps.lane == "default"
            results = [ev async for ev in ps.send("hi")]
            assert not any(getattr(ev, "is_error", False) for ev in results)
            await ps.stop()

        asyncio.run(_t())
        assert _calls(sandbox) == ["default"]


class TestSessionManagerLanes:
    def _fresh(self):
        for name in ("session_manager", "claude_lanes"):
            sys.modules.pop(name, None)
        import session_manager
        return session_manager

    def test_generate_retries_on_the_next_lane_after_a_limit_hit(self, sandbox, monkeypatch):
        """stream_claude()'s "Agent dispatch path" is the one that reaches
        the nested `_generate()`/`_retry_fresh()` closures under test here —
        the no-agent-name path goes through the persistent session instead
        (covered by TestPersistentSessionLanes). Forcing detect_dispatch()
        to always report an agent name is simpler and more robust than
        populating ~/aos/.claude/agents/*.md to make the real dispatch regex
        match, and is not itself part of what aos#244.3 changed."""
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        monkeypatch.setenv("LANES_LIMIT_LANES", "default")
        sm = self._fresh()
        monkeypatch.setattr(sm, "detect_dispatch", lambda msg: ("chief", msg))

        async def _t():
            agent_name, is_resumed, gen = await sm.stream_claude(
                "hello", "user1", cwd=str(sandbox["home"]),
            )
            return [ev async for ev in gen]

        results = asyncio.run(_t())
        final = [r for r in results if isinstance(r, sm.SessionResult)]
        assert final, "expected at least one SessionResult"
        assert not final[-1].is_error
        assert final[-1].text == "OK from cld2"
        assert _calls(sandbox) == ["default", "cld2"]

    def test_generate_succeeds_with_no_lanes_file(self, sandbox, monkeypatch):
        sm = self._fresh()
        monkeypatch.setattr(sm, "detect_dispatch", lambda msg: ("chief", msg))

        async def _t():
            agent_name, is_resumed, gen = await sm.stream_claude(
                "hello", "user2", cwd=str(sandbox["home"]),
            )
            return [ev async for ev in gen]

        results = asyncio.run(_t())
        final = [r for r in results if isinstance(r, sm.SessionResult)]
        assert final and not final[-1].is_error
        assert _calls(sandbox) == ["default"]
