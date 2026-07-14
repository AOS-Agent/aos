"""SignalStore — persistence layer for extracted signals.

Signals are serialized to JSON and stored in the `signal_store` table of
people.db, keyed by (person_id, source_name). On load, all rows for a
person are merged via PersonSignals.merge() into a single container.

Why per-source rows instead of one row per person:
    - Incremental updates: re-running one adapter only overwrites its row
    - Smaller writes: JSON blobs stay bounded per source
    - Source attribution preserved: we can always see which adapter produced what
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .types import (
    CommunicationSignal,
    GroupSignal,
    MentionSignal,
    MetadataSignal,
    PersonSignals,
    PhysicalPresenceSignal,
    ProfessionalSignal,
    VoiceSignal,
)

DEFAULT_DB_PATH = Path.home() / ".aos" / "data" / "people.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_store (
    person_id    TEXT NOT NULL,
    source_name  TEXT NOT NULL,
    signals_json TEXT NOT NULL,
    extracted_at INTEGER NOT NULL,
    PRIMARY KEY (person_id, source_name)
);
CREATE INDEX IF NOT EXISTS idx_signal_store_person ON signal_store(person_id);
CREATE INDEX IF NOT EXISTS idx_signal_store_source ON signal_store(source_name);
CREATE INDEX IF NOT EXISTS idx_signal_store_extracted_at ON signal_store(extracted_at);
"""


# Map signal category name → dataclass type for deserialization
_SIGNAL_CLASSES = {
    "communication": CommunicationSignal,
    "voice": VoiceSignal,
    "physical_presence": PhysicalPresenceSignal,
    "professional": ProfessionalSignal,
    "group_membership": GroupSignal,
    "mentions": MentionSignal,
    "metadata": MetadataSignal,
}


class SignalStore:
    """Persist and retrieve PersonSignals keyed by (person_id, source_name)."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # ── Schema ──

    def init_schema(self) -> None:
        """Create the signal_store table and indexes if missing."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ── Writes ──

    def save(self, person_id: str, source_name: str, signals: PersonSignals) -> None:
        """Save signals for one person × one source.

        Overwrites any existing row for the same (person_id, source_name).
        Updates extracted_at to the current epoch.
        """
        payload = _serialize(signals)
        now = int(time.time())
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT INTO signal_store (person_id, source_name, signals_json, extracted_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(person_id, source_name) DO UPDATE SET
                    signals_json = excluded.signals_json,
                    extracted_at = excluded.extracted_at
                """,
                (person_id, source_name, payload, now),
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, person_id: str, source_name: str | None = None) -> int:
        """Delete rows for a person. If source_name given, only that source."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            if source_name:
                cur = conn.execute(
                    "DELETE FROM signal_store WHERE person_id=? AND source_name=?",
                    (person_id, source_name),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM signal_store WHERE person_id=?", (person_id,)
                )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ── Reads ──

    def load(self, person_id: str) -> PersonSignals | None:
        """Load and merge all source rows for a person.

        Returns None if no rows exist.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT source_name, signals_json, extracted_at "
                "FROM signal_store WHERE person_id=?",
                (person_id,),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return None

        merged: PersonSignals | None = None
        for source_name, signals_json, extracted_at in rows:
            signals = _deserialize(person_id, signals_json)
            if extracted_at:
                from datetime import datetime, timezone
                signals.extracted_at = datetime.fromtimestamp(
                    extracted_at, tz=timezone.utc
                ).isoformat()
            if source_name not in signals.source_coverage:
                signals.source_coverage = sorted(
                    set(signals.source_coverage) | {source_name}
                )
            merged = signals if merged is None else merged.merge(signals)
        return merged

    def load_source(self, person_id: str, source_name: str) -> PersonSignals | None:
        """Load signals for one person × one source."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT signals_json FROM signal_store "
                "WHERE person_id=? AND source_name=?",
                (person_id, source_name),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return _deserialize(person_id, row[0])

    def list_persons(self) -> list[str]:
        """Return all person_ids with at least one stored signal row."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT DISTINCT person_id FROM signal_store"
            ).fetchall()
        finally:
            conn.close()
        return sorted(r[0] for r in rows)

    def stats(self) -> dict:
        """Return counts of rows per source and total."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            by_source = dict(
                conn.execute(
                    "SELECT source_name, COUNT(*) FROM signal_store GROUP BY source_name"
                ).fetchall()
            )
            total = conn.execute("SELECT COUNT(*) FROM signal_store").fetchone()[0]
            persons = conn.execute(
                "SELECT COUNT(DISTINCT person_id) FROM signal_store"
            ).fetchone()[0]
        finally:
            conn.close()
        return {
            "total_rows": total,
            "distinct_persons": persons,
            "by_source": by_source,
        }


# ── Serialization helpers ─────────────────────────────────────────────


def _serialize(signals: PersonSignals) -> str:
    """Serialize a PersonSignals to JSON.

    Only the category lists are persisted. person_id is the row key (stored
    separately). Names and timestamps are stored alongside but not depended on.
    """
    payload = {
        "person_name": signals.person_name,
        "source_coverage": list(signals.source_coverage),
        "communication": [_dc_to_dict(c) for c in signals.communication],
        "voice": [_dc_to_dict(v) for v in signals.voice],
        "physical_presence": [_dc_to_dict(p) for p in signals.physical_presence],
        "professional": [_dc_to_dict(p) for p in signals.professional],
        "group_membership": [_dc_to_dict(g) for g in signals.group_membership],
        "mentions": [_dc_to_dict(m) for m in signals.mentions],
        "metadata": [_dc_to_dict(m) for m in signals.metadata],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _deserialize(person_id: str, payload: str) -> PersonSignals:
    """Inverse of _serialize — reconstruct PersonSignals from JSON."""
    data = json.loads(payload)
    signals = PersonSignals(
        person_id=person_id,
        person_name=data.get("person_name", ""),
        source_coverage=list(data.get("source_coverage", [])),
    )
    for category, cls in _SIGNAL_CLASSES.items():
        raw_list = data.get(category, [])
        target_list = _category_attr(signals, category)
        for raw in raw_list:
            target_list.append(cls(**raw))
    return signals


def _dc_to_dict(obj) -> dict:
    """Convert a dataclass instance to a plain dict for JSON."""
    if is_dataclass(obj):
        return asdict(obj)
    # Fallback: objects should always be dataclasses, but be defensive
    return dict(obj) if hasattr(obj, "__dict__") else {}


def _category_attr(signals: PersonSignals, category: str) -> list:
    """Map JSON category name to PersonSignals attribute list."""
    return {
        "communication": signals.communication,
        "voice": signals.voice,
        "physical_presence": signals.physical_presence,
        "professional": signals.professional,
        "group_membership": signals.group_membership,
        "mentions": signals.mentions,
        "metadata": signals.metadata,
    }[category]
