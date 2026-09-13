"""morning-context's people-nudges step must tolerate the comms-intelligence
nightly being parked (migration 131, aos#240.1: enrich-comms/comms-patterns/
comms-graduation default-off).

`_get_people_nudges()` doesn't actually read anything those three jobs write
— it calls core.engine.people.intel.nudges against person_classification/
relationship_state, populated by a different pipeline entirely — but it's one
of the four readers the audit named to verify explicitly. These tests pin
that it degrades to an empty list rather than raising, whether people.db is
altogether missing or present but never populated (a fresh machine, or one
where the populating job has never run).
"""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "core" / "bin" / "crons" / "morning-context"


def _load_morning_context():
    loader = SourceFileLoader("morning_context_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_missing_people_db_returns_empty_list(tmp_path, monkeypatch, capsys):
    mc = _load_morning_context()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # people.db does not exist at all under this HOME.
    assert (tmp_path / ".aos" / "data" / "people.db").exists() is False

    result = mc._get_people_nudges()

    assert result == []
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_people_db_present_but_empty_schema_returns_empty_list_not_raise(tmp_path, monkeypatch, capsys):
    """A people.db that exists but has never been touched by the classify/
    relationship-state pipeline (intelligence_queue, person_classification,
    relationship_state all missing) must not crash morning-context."""
    mc = _load_morning_context()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    db_path = tmp_path / ".aos" / "data" / "people.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE people (id TEXT PRIMARY KEY, canonical_name TEXT)")
    conn.commit()
    conn.close()

    result = mc._get_people_nudges()

    assert result == []
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "Warning" in captured.out  # degrades loudly, not silently swallowed


def test_main_writes_context_even_when_nudges_fail(tmp_path, monkeypatch):
    """End-to-end: main() must still write morning-context.yaml with weather/
    prayer sections when the people-nudges step finds nothing to report."""
    mc = _load_morning_context()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mc, "OUTPUT_FILE", tmp_path / ".aos" / "work" / "morning-context.yaml")
    monkeypatch.setattr(mc, "_get_weather", lambda: {"summary": "test weather"})
    monkeypatch.setattr(mc, "_get_prayer_times", lambda: {"fajr": "05:00"})
    # No people.db at all.

    mc.main()

    output = mc.OUTPUT_FILE.read_text()
    assert "test weather" in output
    assert "people_today" in output
