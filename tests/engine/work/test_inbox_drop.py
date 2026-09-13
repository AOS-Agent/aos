"""`work inbox drop <id>` — remove an inbox item via the CLI.

The engine already had a removal path (``backend.delete_inbox``, used
internally by ``promote_inbox``) but the CLI never exposed it: there was no
way to dismiss a bad inbox capture (e.g. the "--help" leftover, i49) other
than reaching into the DB directly. `cmd_inbox` treated any args other than
a recognized flag as text to capture, so `inbox drop <id>` would have
captured a new inbox item reading "drop <id>" instead of removing anything.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never the real DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

import cli  # noqa: E402


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["cli.py", *argv])
    exit_code = 0
    try:
        cli.main()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    out = capsys.readouterr().out
    return exit_code, out


def test_inbox_drop_removes_the_item(work_env, monkeypatch, capsys):
    eng = work_env["engine"]
    item = eng.add_inbox("--help")
    other = eng.add_inbox("a real capture to keep")

    exit_code, out = _run(monkeypatch, capsys, "inbox", "drop", item["id"])

    assert exit_code == 0
    ids_left = {i["id"] for i in eng.get_inbox()}
    assert item["id"] not in ids_left
    assert other["id"] in ids_left


def test_inbox_drop_unknown_id_fails_without_mutating(work_env, monkeypatch, capsys):
    eng = work_env["engine"]
    eng.add_inbox("keep me")
    before = len(eng.get_inbox())

    exit_code, out = _run(monkeypatch, capsys, "inbox", "drop", "i9999")

    assert exit_code == 1
    assert len(eng.get_inbox()) == before


def test_inbox_drop_missing_id_shows_usage(work_env, monkeypatch, capsys):
    eng = work_env["engine"]
    before = len(eng.get_inbox())

    exit_code, out = _run(monkeypatch, capsys, "inbox", "drop")

    assert exit_code == 1
    assert "Usage" in out
    assert len(eng.get_inbox()) == before
