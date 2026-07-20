"""Storage gates + GC + backup — land same-commit as the engine (council law).

Three defenses so enrichment can never quietly eat the disk or lose the one
irreplaceable DB:

1. Growth ceiling — the engine REFUSES to run (loudly) if comms.db plus the
   projected new entities would exceed max_comms_db_bytes, or if free disk on
   the comms.db volume is below min_disk_free_bytes.
2. GC — entities from a SUPERSEDED extractor_version are pruned after
   superseded_ttl_days. Active/current rows are never touched.
3. Backup — sqlite3 `.backup` of comms.db to the AOS-X backups dir, rotated to
   the newest `keep`. Runs before each backfill session and nightly.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


class StorageGateError(RuntimeError):
    """Raised when a storage gate refuses the run. Message is operator-facing."""


@dataclass
class GateStatus:
    db_bytes: int
    projected_bytes: int
    disk_free_bytes: int
    ok: bool
    reason: str


def check_storage_gates(comms_db: Path, cfg, *, projected_new_entities: int = 0) -> GateStatus:
    """Evaluate the growth ceiling + disk-free floor. Does NOT raise — returns a
    GateStatus so callers can log the full picture. Use `enforce=` for a raise."""
    db_bytes = comms_db.stat().st_size if comms_db.exists() else 0
    projected = db_bytes + projected_new_entities * cfg.bytes_per_entity_estimate
    try:
        free = shutil.disk_usage(comms_db.parent).free
    except OSError:
        free = 0

    reason = ""
    ok = True
    if projected > cfg.max_comms_db_bytes:
        ok = False
        reason = (f"projected comms.db {projected/1e9:.2f}GB exceeds ceiling "
                  f"{cfg.max_comms_db_bytes/1e9:.2f}GB")
    elif free < cfg.min_disk_free_bytes:
        ok = False
        reason = (f"disk free {free/1e9:.2f}GB below floor "
                  f"{cfg.min_disk_free_bytes/1e9:.2f}GB")
    return GateStatus(db_bytes, projected, free, ok, reason)


def enforce_storage_gates(comms_db: Path, cfg, *, projected_new_entities: int = 0) -> GateStatus:
    """check_storage_gates + raise StorageGateError if breached."""
    status = check_storage_gates(comms_db, cfg, projected_new_entities=projected_new_entities)
    if not status.ok:
        raise StorageGateError(
            f"STORAGE GATE REFUSED enrichment: {status.reason}. "
            f"comms.db={status.db_bytes/1e9:.2f}GB free={status.disk_free_bytes/1e9:.2f}GB"
        )
    return status


def gc_superseded(conn: sqlite3.Connection, cfg, *, now: datetime | None = None) -> int:
    """Delete entity rows with status='superseded' older than superseded_ttl_days.
    Returns the number pruned. Current/active rows are never GC'd."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=cfg.superseded_ttl_days)).isoformat()
    cur = conn.execute(
        "DELETE FROM message_entities WHERE status='superseded' AND created_at < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


def backup_comms(comms_db: Path, cfg, *, now: datetime | None = None) -> Path | None:
    """sqlite3 .backup comms.db → <backup_dir>/comms-<ts>.db, rotate to `keep`.

    Returns the backup path, or None if the backup dir isn't reachable (AOS-X
    unmounted) — a missing external drive is a skip-with-warning, not a crash.
    Uses the sqlite3 backup API (consistent snapshot of a live DB).
    """
    if not comms_db.exists():
        return None
    backup_dir = Path(cfg.backup_dir)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None  # volume not mounted

    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"comms-{stamp}.db"

    src = sqlite3.connect(comms_db)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)  # atomic consistent snapshot
        finally:
            dst.close()
    finally:
        src.close()

    _rotate(backup_dir, keep=cfg.backup_keep)
    return dest


def _rotate(backup_dir: Path, *, keep: int) -> None:
    backups = sorted(backup_dir.glob("comms-*.db"), key=lambda p: p.name, reverse=True)
    for stale in backups[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass
