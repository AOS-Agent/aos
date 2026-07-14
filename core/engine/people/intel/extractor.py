"""SignalExtractor — orchestrator for People Intelligence extraction.

Wires the adapter registry, signal store, canonical_name normalizer, and
people.db into a single end-to-end pipeline:

    people.db  ──▶  build_person_index()  ──▶  adapters.extract_all(index)
                        │                              │
                        ▼                              ▼
                normalized names           PersonSignals per source
                + identifiers                          │
                                                       ▼
                                              SignalStore.save()
                                                       │
                                                       ▼
                                               signal_store table

Usage:

    extractor = SignalExtractor()
    report = extractor.run(limit=20)            # limited test run
    report = extractor.run(person_ids=[...])    # specific persons
    report = extractor.run(adapter_names=["apple_messages", "whatsapp"])
    report = extractor.run(dry_run=True)        # no writes

    coverage = extractor.coverage_report()
    stats = extractor.stats()
    signals = extractor.get_person_signals("p_xyz123")

Side effects:
- Reads from people.db (people, person_identifiers tables) — read-only
- Reads from each adapter's underlying source DB (copied to temp first)
- Writes to signal_store table in people.db — INSERT OR REPLACE per
  (person_id, source_name) pair

Graceful failure: any adapter that raises during extract_all() has its
error captured in the run report; other adapters continue. The run is
never aborted by a single adapter failure.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalize_canonical_name
from .registry import AdapterRegistry
from .store import DEFAULT_DB_PATH, SignalStore
from .types import PersonSignals

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────

@dataclass
class RunReport:
    """Summary of a SignalExtractor.run() invocation."""
    persons_indexed: int = 0
    persons_extracted: int = 0              # distinct persons with ≥1 signal
    sources_used: list[str] = field(default_factory=list)
    sources_skipped: list[str] = field(default_factory=list)
    per_source_persons: dict[str, int] = field(default_factory=dict)  # adapter → person count
    duration_seconds: float = 0
    errors: list[dict] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "persons_indexed": self.persons_indexed,
            "persons_extracted": self.persons_extracted,
            "sources_used": list(self.sources_used),
            "sources_skipped": list(self.sources_skipped),
            "per_source_persons": dict(self.per_source_persons),
            "duration_seconds": self.duration_seconds,
            "errors": list(self.errors),
            "dry_run": self.dry_run,
        }


# ── Mapping of person_identifiers.type → person_index key ───────────

_IDENTIFIER_TYPE_MAP: dict[str, str] = {
    "phone": "phones",
    "email": "emails",
    "wa_jid": "wa_jids",
    "whatsapp": "wa_jids",          # legacy alias — merge into wa_jids
    "telegram_id": "telegram_ids",
    "telegram_username": "telegram_usernames",
}


# ── Orchestrator ─────────────────────────────────────────────────────

class SignalExtractor:
    """End-to-end orchestrator for People Intelligence extraction."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path: Path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.registry = AdapterRegistry()
        self.registry.discover()
        self.store = SignalStore(self.db_path)

    # ── Person index ──

    def build_person_index(
        self,
        person_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, dict]:
        """Build a person_index dict for adapters to match against.

        Returns:
            {
              person_id: {
                "name": <primary display form>,
                "variants": <list of lowercase matching variants>,
                "phones": [normalized phone strings],
                "emails": [lowercase emails],
                "wa_jids": [wa JIDs],
                "telegram_ids": [...],
                "telegram_usernames": [...],
              },
              ...
            }

        The canonical_name from people.db is passed through
        normalize_canonical_name() to generate matching variants. This is
        how adapters resolve weirdly-formatted names ("FabricatedExample"
        → "fabricated example") without needing their own per-adapter
        rewrites.
        """
        if not self.db_path.exists():
            logger.warning("people.db not found at %s", self.db_path)
            return {}

        conn = sqlite3.connect(str(self.db_path))
        try:
            persons = self._query_people(conn, person_ids, limit)
            if not persons:
                return {}
            self._attach_identifiers(conn, persons)
            return persons
        finally:
            conn.close()

    def _query_people(
        self,
        conn: sqlite3.Connection,
        person_ids: list[str] | None,
        limit: int | None,
    ) -> dict[str, dict]:
        """Fetch people rows and build the initial index (names only)."""
        base_sql = (
            "SELECT id, canonical_name, display_name, first_name, last_name "
            "FROM people WHERE is_archived = 0"
        )
        params: list = []
        if person_ids:
            placeholders = ",".join("?" * len(person_ids))
            base_sql += f" AND id IN ({placeholders})"
            params.extend(person_ids)

        # Deterministic ordering so --limit is repeatable.
        base_sql += " ORDER BY id"
        if limit:
            base_sql += " LIMIT ?"
            params.append(int(limit))

        persons: dict[str, dict] = {}
        for row in conn.execute(base_sql, params):
            pid, canonical, display, first, last = row
            # Pick the richest name source: display > first+last > canonical.
            display_name = (display or "").strip() or None
            fl = " ".join(t for t in ((first or "").strip(), (last or "").strip()) if t) or None
            raw_name = display_name or fl or canonical or ""

            # Normalize. If raw_name differs from canonical, also normalize
            # canonical and union the variants — gives the adapters more
            # matching surface area.
            primary_norm = normalize_canonical_name(raw_name)
            variants: list[str] = list(primary_norm.variants)

            if canonical and canonical.strip() and canonical.strip() != raw_name.strip():
                canon_norm = normalize_canonical_name(canonical)
                for v in canon_norm.variants:
                    if v not in variants:
                        variants.append(v)

            persons[pid] = {
                "name": primary_norm.primary or raw_name or "",
                "variants": variants,
                "phones": [],
                "emails": [],
                "wa_jids": [],
                "telegram_ids": [],
                "telegram_usernames": [],
            }

        return persons

    def _attach_identifiers(
        self,
        conn: sqlite3.Connection,
        persons: dict[str, dict],
    ) -> None:
        """Fetch identifiers and populate the phones/emails/wa_jids lists.

        Reads from the legacy ``person_identifiers`` table. Chunked to
        respect SQLite's variable-count limit (default 999). Mirrors the
        two-pass pattern used by core/qareen/channels/whatsapp_desktop.py
        (direct match → phone fallback).
        """
        pid_list = list(persons.keys())
        if not pid_list:
            return

        CHUNK = 500
        for i in range(0, len(pid_list), CHUNK):
            chunk = pid_list[i : i + CHUNK]
            placeholders = ",".join("?" * len(chunk))

            # Guard: fall through gracefully if the table doesn't exist
            # (e.g., fresh install where migrations haven't all run).
            try:
                rows = conn.execute(
                    f"SELECT person_id, type, value, normalized "
                    f"FROM person_identifiers WHERE person_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall()
            except sqlite3.OperationalError as e:
                logger.debug("person_identifiers query failed: %s", e)
                return

            for person_id, ident_type, value, normalized in rows:
                target_key = _IDENTIFIER_TYPE_MAP.get(ident_type)
                if not target_key:
                    continue
                # Prefer the normalized form when present; fall back to raw.
                val = (normalized or value or "").strip()
                if not val:
                    continue
                # Lowercase emails for consistent matching.
                if target_key == "emails":
                    val = val.lower()
                bucket = persons[person_id].get(target_key)
                if bucket is not None and val not in bucket:
                    bucket.append(val)

    # ── Extraction ──

    def run(
        self,
        person_ids: list[str] | None = None,
        limit: int | None = None,
        adapter_names: list[str] | None = None,
        dry_run: bool = False,
    ) -> RunReport:
        """Run the full extraction pipeline.

        Args:
            person_ids: Optional whitelist of person IDs. None → all persons.
            limit: Optional cap on persons indexed (after filtering).
            adapter_names: Optional whitelist of adapters. None → all available.
            dry_run: If True, extract signals but DON'T persist to store.

        Returns:
            A RunReport summarizing what happened.
        """
        start = time.time()

        # Ensure the signal_store table exists before we write to it.
        if not dry_run:
            try:
                self.store.init_schema()
            except Exception as e:
                logger.warning("signal_store schema init failed: %s", e)

        index = self.build_person_index(person_ids=person_ids, limit=limit)

        available = set(self.registry.available())
        all_known = set(self.registry.all_adapters())

        if adapter_names:
            requested = set(adapter_names)
            active = available & requested
            skipped = all_known - active
        else:
            active = available
            skipped = all_known - available

        report = RunReport(
            persons_indexed=len(index),
            sources_skipped=sorted(skipped),
            dry_run=dry_run,
        )

        if not index:
            logger.info("SignalExtractor: no persons indexed, nothing to extract")
            report.duration_seconds = round(time.time() - start, 2)
            return report

        # Accumulate distinct persons who got at least one signal.
        persons_with_any_signal: set[str] = set()

        for adapter_name in sorted(active):
            adapter = self.registry.get(adapter_name)
            if adapter is None:
                logger.warning("Adapter %s not available from registry", adapter_name)
                continue
            try:
                extracted = adapter.extract_all(index)
            except Exception as e:
                logger.exception("Adapter %s failed during extract_all", adapter_name)
                report.errors.append({"adapter": adapter_name, "error": str(e)})
                continue

            if not isinstance(extracted, dict):
                logger.error(
                    "Adapter %s returned non-dict: %s", adapter_name, type(extracted)
                )
                report.errors.append(
                    {"adapter": adapter_name, "error": "non-dict return"}
                )
                continue

            report.sources_used.append(adapter_name)
            report.per_source_persons[adapter_name] = len(extracted)

            for person_id, signals in extracted.items():
                if not isinstance(signals, PersonSignals):
                    logger.debug(
                        "Adapter %s returned non-PersonSignals for %s",
                        adapter_name,
                        person_id,
                    )
                    continue
                persons_with_any_signal.add(person_id)
                if not dry_run:
                    try:
                        self.store.save(person_id, adapter_name, signals)
                    except Exception as e:
                        logger.exception(
                            "Failed to persist signals for %s from %s",
                            person_id,
                            adapter_name,
                        )
                        report.errors.append(
                            {
                                "adapter": adapter_name,
                                "person_id": person_id,
                                "error": str(e),
                            }
                        )

        report.persons_extracted = len(persons_with_any_signal)
        report.duration_seconds = round(time.time() - start, 2)
        return report

    # ── Convenience delegates ──

    def coverage_report(self) -> dict:
        """Static coverage report from the registry (no extraction)."""
        return self.registry.coverage_report()

    def stats(self) -> dict:
        """Signal store statistics (row counts)."""
        try:
            return self.store.stats()
        except Exception as e:
            logger.debug("stats query failed: %s", e)
            return {"total_rows": 0, "distinct_persons": 0, "by_source": {}}

    def get_person_signals(self, person_id: str) -> PersonSignals | None:
        """Load merged signals for one person from the store."""
        try:
            return self.store.load(person_id)
        except Exception as e:
            logger.debug("load failed for %s: %s", person_id, e)
            return None
