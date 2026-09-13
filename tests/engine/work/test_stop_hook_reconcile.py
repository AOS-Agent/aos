"""Stop hook regression test: reconcile.py must actually attribute work (aos#236.2).

core/engine/work/reconcile.py is wired to Claude Code's Stop hook
(~/.claude/settings.json). Dangling-wires audit (2026-09-13) found
~/.aos/logs/reconcile.log silent since 2026-03-26 despite the hook firing
every session.

Root cause: the hook read `hook_input.get("tool_use_results", [])` to find
which files were touched this turn. That key has never existed in Claude
Code's Stop hook payload (verified against the installed CLI binary's
string table -- `transcript_path` and `stop_hook_active` are real fields,
`tool_use_results` appears nowhere). So `tool_results` was always `[]`,
`files_modified` was always empty, and both branches of main() fell through
to a bare `return` with no `_log()` call ever reached -- a swallowed no-op,
not a crash, which is why nothing ever showed up in the log or in tracebacks.

The fix parses the real transcript (`transcript_path`, a JSONL file of the
session's message history) for `tool_use` content blocks instead.

This test drives the real hook entrypoint (main()) with a sandboxed HOME, an
active task set via `work start`'s live-context mechanism, and a fake
transcript containing one Write tool_use -- then asserts the attribution log
line appears. It fails against the pre-fix `tool_use_results` code (proven by
temporarily reverting logic below fails red) and passes against the fix.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

RECONCILE_PATH = (
    Path(__file__).parents[3] / "core" / "engine" / "work" / "reconcile.py"
)


def _load_reconcile_fresh():
    """Load reconcile.py under a private module name.

    LOG_FILE is a module-level constant resolved from Path.home() at import
    time, so a fresh load lets us point it at the sandbox before main() runs
    -- same technique tests/engine/work/test_session_close_threads.py uses
    for session_close.py.
    """
    spec = importlib.util.spec_from_file_location("reconcile_test", RECONCILE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_transcript(path: Path, *tool_uses: tuple[str, dict]) -> None:
    """Write a minimal transcript.jsonl with the given (tool_name, input) tool_use blocks."""
    lines = []
    for name, inp in tool_uses:
        entry = {
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": inp}
                ],
            }
        }
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n")


def test_stop_hook_attributes_files_to_active_task(work_env, monkeypatch, tmp_path):
    """An active task + a Write in the transcript must produce a log line.

    This is the exact shape the audit found broken: a session Stops with a
    task started via `work start` and files were actually touched.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    eng = work_env["engine"]

    # LiveContext.CONTEXT_FILE is a class attribute resolved from Path.home()
    # at whatever point ontology.work_utils was first imported in this
    # process -- monkeypatch it directly rather than relying on import order.
    from core.engine.work.ontology.work_utils import LiveContext
    live_ctx_file = tmp_path / ".aos" / "work" / ".live-context.json"
    monkeypatch.setattr(LiveContext, "CONTEXT_FILE", live_ctx_file)

    task = eng.add_task("Reconcile probe task")
    eng.set_live_context(task, session_id="probe-session-1")
    assert live_ctx_file.exists(), "test setup: live context was not written"

    transcript_path = tmp_path / "transcript.jsonl"
    touched = tmp_path / "touched.txt"
    _write_transcript(
        transcript_path,
        ("Write", {"file_path": str(touched), "content": "hello"}),
    )

    mod = _load_reconcile_fresh()
    log_file = tmp_path / ".aos" / "logs" / "reconcile.log"
    monkeypatch.setattr(mod, "LOG_FILE", log_file)

    hook_input = {
        "session_id": "probe-session-1",
        "transcript_path": str(transcript_path),
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    mod.main()

    assert log_file.exists(), (
        "reconcile.py wrote nothing -- this is the exact silent-since-2026-03-26 "
        "failure mode the audit found"
    )
    log_text = log_file.read_text()
    assert f"Attributed 1 files to {task['id']}" in log_text, (
        f"expected an attribution line for {task['id']!r}, got:\n{log_text}"
    )


def test_stop_hook_logs_untracked_work_with_no_active_task(work_env, monkeypatch, tmp_path):
    """No active task but files touched -> logged as untracked, not silent."""
    monkeypatch.setenv("HOME", str(tmp_path))

    from core.engine.work.ontology.work_utils import LiveContext
    live_ctx_file = tmp_path / ".aos" / "work" / ".live-context.json"
    monkeypatch.setattr(LiveContext, "CONTEXT_FILE", live_ctx_file)
    assert not live_ctx_file.exists()

    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(
        transcript_path,
        ("Edit", {"file_path": str(tmp_path / "other.txt"), "old_string": "a", "new_string": "b"}),
    )

    mod = _load_reconcile_fresh()
    log_file = tmp_path / ".aos" / "logs" / "reconcile.log"
    monkeypatch.setattr(mod, "LOG_FILE", log_file)

    hook_input = {
        "session_id": "probe-session-2",
        "transcript_path": str(transcript_path),
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    mod.main()

    assert log_file.exists()
    assert "[untracked] 1 files modified" in log_file.read_text()
