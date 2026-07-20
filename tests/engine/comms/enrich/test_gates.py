"""Storage gates, GC, backup rotation."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.engine.comms.enrich.config import EnrichConfig
from core.engine.comms.enrich.gates import (
    StorageGateError,
    backup_comms,
    check_storage_gates,
    enforce_storage_gates,
    gc_superseded,
)

from ._helpers import make_comms_db, msg


def test_growth_ceiling_refuses(tmp_path):
    db = make_comms_db(tmp_path / "comms.db", [msg("m1", "hi", ts="2026-03-09T10:00:00")])
    cfg = EnrichConfig(max_comms_db_bytes=1, min_disk_free_bytes=0)  # impossible ceiling
    with pytest.raises(StorageGateError, match="ceiling"):
        enforce_storage_gates(db, cfg, projected_new_entities=1000)


def test_disk_free_floor_refuses(tmp_path):
    db = make_comms_db(tmp_path / "comms.db")
    cfg = EnrichConfig(max_comms_db_bytes=10**12, min_disk_free_bytes=10**18)  # impossible floor
    status = check_storage_gates(db, cfg)
    assert not status.ok and "disk free" in status.reason


def test_gates_pass_under_normal_limits(tmp_path):
    db = make_comms_db(tmp_path / "comms.db")
    cfg = EnrichConfig(max_comms_db_bytes=10**12, min_disk_free_bytes=0)
    assert enforce_storage_gates(db, cfg).ok


def test_gc_prunes_only_old_superseded(tmp_path):
    db = make_comms_db(tmp_path / "comms.db")
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=40)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    rows = [
        ("e_old_sup", "superseded", old),     # prune
        ("e_new_sup", "superseded", recent),  # keep (within TTL)
        ("e_active", "active", old),          # keep (never GC active)
    ]
    for eid, status, created in rows:
        conn.execute(
            "INSERT INTO message_entities(id, entity_type, value, fields_json,"
            " confidence, source_ids, batch_key, extractor_version, model,"
            " created_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "topic", "x", "{}", 0.9, "[]", "b", "extract@1", "m", created, status))
    conn.commit()
    cfg = EnrichConfig(superseded_ttl_days=30)
    pruned = gc_superseded(conn, cfg, now=now)
    assert pruned == 1
    remaining = {r[0] for r in conn.execute("SELECT id FROM message_entities")}
    assert remaining == {"e_new_sup", "e_active"}
    conn.close()


def test_backup_and_rotation(tmp_path):
    db = make_comms_db(tmp_path / "comms.db", [msg("m1", "hi", ts="2026-03-09T10:00:00")])
    backup_dir = tmp_path / "backups"
    cfg = EnrichConfig(backup_dir=str(backup_dir), backup_keep=2)
    made = []
    for i in range(4):
        ts = datetime(2026, 7, 20, 3, 15, i, tzinfo=timezone.utc)
        made.append(backup_comms(db, cfg, now=ts))
    kept = sorted(backup_dir.glob("comms-*.db"))
    assert len(kept) == 2  # rotated to newest 2
    # newest two survive
    assert kept[-1].name == "comms-20260720-031503.db"
    # a backup is a valid, queryable DB
    c = sqlite3.connect(kept[-1])
    assert c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    c.close()


def test_backup_skips_when_dir_unreachable(tmp_path):
    db = make_comms_db(tmp_path / "comms.db")
    cfg = EnrichConfig(backup_dir="/nonexistent-volume-xyz/backups")
    assert backup_comms(db, cfg) is None  # skip-with-None, no crash
