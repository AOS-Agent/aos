"""signals — writer library for the Intelligence Loop Phase 1 substrate.

Two tables in qareen.db (migration 089): `signals` (append-only observations)
and `proposals` (the only mutable entity, moved only through the guarded
status transitions below). See that migration's docstring for the council
decisions this library enforces: taint from day one, no new substrate, lazy
expiry instead of a cron.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LEGAL_TRANSITIONS = {
    "proposed": {"surfaced", "approved", "rejected"},
    "surfaced": {"approved", "rejected"},
    "approved": {"applied"},
}
_DECIDED_STATUSES = {"approved", "rejected"}


def _db_path() -> Path:
    """DB path resolution, isolated so tests can monkeypatch it."""
    return Path.home() / ".aos" / "data" / "qareen.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _short_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def append_signal(
    sensor: str,
    signal_type: str,
    payload: dict,
    source_refs: list[str],
    tainted: bool = False,
    project_key: str | None = None,
) -> str:
    """Append an observation. Returns the new signal id."""
    if not source_refs:
        raise ValueError("source_refs must be non-empty — every signal needs provenance")

    created_at = _now()
    payload_json = json.dumps(payload, sort_keys=True)
    signal_id = _short_id("sig", sensor, payload_json, created_at)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO signals
                (id, sensor, signal_type, payload_json, source_refs, tainted, project_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (signal_id, sensor, signal_type, payload_json, json.dumps(source_refs),
             int(tainted), project_key, created_at),
        )
        conn.commit()
    finally:
        conn.close()
    return signal_id


def create_proposal(
    title: str,
    diff_type: str,
    body: str,
    evidence_refs: list[str],
    project_key: str | None = None,
    ttl_days: int = 14,
) -> str:
    """Create a proposal grounded in prior signals. Returns the new proposal id."""
    if not evidence_refs:
        raise ValueError("evidence_refs must be non-empty — every proposal needs evidence")

    conn = _connect()
    try:
        placeholders = ",".join("?" for _ in evidence_refs)
        rows = conn.execute(
            f"SELECT id, tainted FROM signals WHERE id IN ({placeholders})",
            list(evidence_refs),
        ).fetchall()
        found = {r["id"] for r in rows}
        missing = set(evidence_refs) - found
        if missing:
            raise ValueError(f"evidence_refs reference unknown signals: {sorted(missing)}")
        tainted = any(r["tainted"] for r in rows)

        now = datetime.now(timezone.utc)
        created_at = now.isoformat()
        expires_at = (now + timedelta(days=ttl_days)).isoformat()

        proposal_id = _short_id("prop", title, body, created_at)
        conn.execute(
            """
            INSERT INTO proposals
                (id, title, diff_type, body, evidence_refs, tainted, project_key,
                 status, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (proposal_id, title, diff_type, body, json.dumps(evidence_refs), int(tainted),
             project_key, created_at, expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return proposal_id


def lazy_expire() -> int:
    """Flip overdue proposed/surfaced proposals to lapsed. Idempotent, crash-safe,
    callable by any reader — no cron. Returns the number of rows flipped."""
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE proposals SET status = 'lapsed' "
            "WHERE status IN ('proposed', 'surfaced') AND expires_at < ?",
            (_now(),),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_status(proposal_id: str, status: str, note: str | None = None) -> None:
    """Move a proposal through a guarded legal transition. Raises ValueError
    on an illegal transition or an unknown proposal id."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT status FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no such proposal: {proposal_id}")
        current = row["status"]
        if status not in _LEGAL_TRANSITIONS.get(current, set()):
            raise ValueError(f"illegal transition: {current} -> {status}")

        decided_at = _now() if status in _DECIDED_STATUSES else None
        conn.execute(
            "UPDATE proposals SET status = ?, decided_at = COALESCE(?, decided_at), "
            "outcome_note = COALESCE(?, outcome_note) WHERE id = ?",
            (status, decided_at, note, proposal_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_engagement(counts: dict[str, int]) -> str:
    """Append an engagement signal. Counts only, never content — every value
    must be a plain int (bools rejected too, since bool is an int subclass)."""
    for key, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"engagement counts must be int, got {key}={value!r}")
    return append_signal(
        sensor="engagement",
        signal_type="engagement_counts",
        payload=counts,
        source_refs=["engagement:self"],
    )
