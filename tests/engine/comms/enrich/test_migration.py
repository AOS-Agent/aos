"""Migration 084 contract: legacy rename, frozen shape, idempotency."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
_MIG = _REPO / "core" / "infra" / "migrations" / "084_message_entities_frozen.py"


def _load(monkeypatch, home: Path):
    monkeypatch.setenv("HOME", str(home))
    spec = importlib.util.spec_from_file_location("mig084_test", _MIG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # Re-point the module constant, since Path.home() was resolved at import.
    monkeypatch.setattr(m, "COMMS_DB", home / ".aos" / "data" / "comms.db")
    return m


def _old_shape_db(path: Path, rows: int = 5):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE messages (id TEXT PRIMARY KEY, content TEXT);
        CREATE TABLE message_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL,
            entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, confidence REAL);
    """)
    for i in range(rows):
        conn.execute("INSERT INTO message_entities(message_id, entity_type, entity_id, confidence)"
                     " VALUES (?,?,?,?)", (f"m{i}", "topic", f"t{i}", 0.9))
    conn.commit()
    conn.close()


def test_renames_old_and_creates_frozen(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db = home / ".aos" / "data" / "comms.db"
    _old_shape_db(db, rows=5)
    m = _load(monkeypatch, home)
    m.up()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM message_entities_legacy").fetchone()[0] == 5
    cols = {r[1] for r in conn.execute("PRAGMA table_info(message_entities)")}
    assert {"id", "fields_json", "source_ids", "batch_key", "extractor_version",
            "ontology_type", "status"} <= cols
    assert conn.execute("SELECT COUNT(*) FROM message_entities").fetchone()[0] == 0
    mx = {r[1] for r in conn.execute("PRAGMA table_info(message_extraction)")}
    assert {"message_id", "extractor_version", "status"} <= mx
    conn.close()


def test_idempotent_second_run(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db = home / ".aos" / "data" / "comms.db"
    _old_shape_db(db, rows=3)
    m = _load(monkeypatch, home)
    m.up()
    m.up()  # must not re-rename the now-frozen table into legacy
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM message_entities_legacy").fetchone()[0] == 3
    # frozen table still frozen (has fields_json), not clobbered back to old shape
    cols = {r[1] for r in conn.execute("PRAGMA table_info(message_entities)")}
    assert "fields_json" in cols
    conn.close()


def test_fresh_install_no_db(tmp_path, monkeypatch):
    home = tmp_path / "home"  # no comms.db at all
    m = _load(monkeypatch, home)
    m.up()  # graceful skip, no raise


def test_no_existing_message_entities(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db = home / ".aos" / "data" / "comms.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, content TEXT)")
    conn.commit(); conn.close()
    m = _load(monkeypatch, home)
    m.up()
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(message_entities)")}
    assert "fields_json" in cols
    # No legacy table created when there was nothing to preserve.
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='message_entities_legacy'").fetchone()[0] == 0
    conn.close()
