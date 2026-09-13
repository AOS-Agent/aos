"""`work <command> --help` / `-h` must never dispatch into command logic.

Bug: main() only special-cased help at the top level (``work --help``). Each
cmd_* function checked ``if not args`` to decide whether to print its own
"Usage: ..." — but argv ``["--help"]`` is truthy, so it fell straight into the
command's real logic instead:

  * ``work inbox --help``  -> no args-required guard for the "add" branch at
    all, so it captured "--help" as a real inbox item (i49, 2026-09-13).
  * ``work thread --help`` -> same shape: created a thread titled "--help".
  * ``work done --help`` / ``work cancel --help`` -> "--help" fuzzy-matched a
    task literally titled "--help" (t#7 — itself a leftover from the same bug
    class via the Telegram handler, see CHANGELOG v0.8.0) and flipped its
    status.
  * ``work handoff -h`` -> fell through to "Error: --state is required"
    (exit 1, no usage) instead of showing help.

Fix: main() now checks argv[2:] for an exact "-h"/"--help" token BEFORE
dispatching to the command function at all, and prints that command's own
usage text — so no cmd_* body ever runs, for any command.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never the real DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

import cli  # noqa: E402


def _run(monkeypatch, capsys, *argv):
    """Run cli.main() with argv = ["cli.py", *argv]; return (exit_code, stdout)."""
    monkeypatch.setattr(sys, "argv", ["cli.py", *argv])
    exit_code = 0
    try:
        cli.main()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    out = capsys.readouterr().out
    return exit_code, out


@pytest.mark.parametrize("argv", [
    ("add", "--help"),
    ("inbox", "--help"),
    ("done", "--help"),
    ("cancel", "--help"),
    ("subtask", "--help"),
    ("thread", "--help"),
    ("handoff", "-h"),
])
def test_help_prints_usage_and_never_mutates(work_env, monkeypatch, capsys, argv):
    eng = work_env["engine"]

    # Seed a task literally titled "--help" — the exact leftover shape that
    # let `done --help` / `cancel --help` fuzzy-match into a real task.
    seeded = eng.add_task("--help")
    seeded_status_before = eng.get_task(seeded["id"])["status"]

    tasks_before = len(eng.get_all_tasks())
    inbox_before = len(eng.get_inbox())
    threads_before = len(eng.get_all_threads())

    exit_code, out = _run(monkeypatch, capsys, *argv)

    assert exit_code == 0
    assert "Usage" in out

    assert len(eng.get_all_tasks()) == tasks_before
    assert len(eng.get_inbox()) == inbox_before
    assert len(eng.get_all_threads()) == threads_before
    assert eng.get_task(seeded["id"])["status"] == seeded_status_before
