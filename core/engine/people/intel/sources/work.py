"""Work System signal adapter.

Extracts mentions of persons from the AOS work database (qareen.db) —
tasks, projects, goals, inbox, and friction_log. Every mention becomes a
MentionSignal with a snippet of surrounding context.

The work system is project-centric, not person-centric, so signal density
is typically low. But when a person appears in a task title or project
goal, that mention tends to carry high semantic value.

Design:
  * Copy qareen.db to a temp directory before reading (lock-safe).
  * Open read-only via sqlite3 URI.
  * For each OPTIONAL table, check existence via sqlite_master and check
    which columns exist via PRAGMA table_info before building the SELECT.
  * Per-person regex is compiled once, with camelCase-split variants.
  * Stopword names and short (<4 char) names are skipped.
  * Mention contexts are capped at 20 per person.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import ClassVar

from ..types import MentionSignal, PersonSignals, SignalType
from .base import SignalAdapter

log = logging.getLogger(__name__)


# Stopwords — common English words that happen to overlap with "names" and
# would produce noise. Keep in sync with the vault adapter.
STOPWORDS: set[str] = {
    "love", "work", "home", "help", "time", "life", "good", "best",
    "real", "mind", "plan", "task", "note", "data", "code", "test",
    "demo", "user", "team", "page", "file", "line", "item", "list",
}


# Tables we scan (all optional). For each, list the candidate text columns
# in priority order — we'll intersect with the columns that actually exist.
_TABLE_TEXT_COLUMNS: dict[str, list[str]] = {
    "tasks":         ["title", "description"],
    "projects":      ["title", "description", "goal"],
    "goals":         ["title", "description"],
    "inbox":         ["content", "text", "title"],
    "friction_log":  ["description", "content", "title"],
}

# Maximum mention contexts stored per person (to bound memory/output).
_MAX_MENTION_CONTEXTS = 20

# Minimum length of the longest name variant. Anything shorter is skipped
# because it produces way too many false matches.
_MIN_NAME_LEN = 4


def _name_variants(name: str) -> list[str]:
    """Return candidate matching variants for a canonical person name.

    Handles camelCase-style canonical names by also producing a spaced
    form. E.g. "AliceSmith" → ["AliceSmith", "Alice Smith"].
    """
    name = (name or "").strip()
    if not name:
        return []
    variants = {name}
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    if spaced != name:
        variants.add(spaced)
    return list(variants)


class WorkAdapter(SignalAdapter):
    """Extract person mentions from the AOS work system database."""

    name: ClassVar[str] = "work"
    display_name: ClassVar[str] = "Work System"
    platform: ClassVar[str] = "any"
    signal_types: ClassVar[list[SignalType]] = [SignalType.MENTION]
    description: ClassVar[str] = (
        "Mentions in tasks, projects, goals from qareen.db"
    )
    requires: ClassVar[list[str]] = ["file:~/.aos/data/qareen.db"]

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.path.expanduser("~/.aos/data/qareen.db")
        self.db_path = db_path

    # ── Availability ─────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            return Path(self.db_path).exists()
        except Exception:
            return False

    # ── Extraction ───────────────────────────────────────────────────

    def extract_all(
        self, person_index: dict[str, dict]
    ) -> dict[str, PersonSignals]:
        """Extract mention signals for every person in person_index.

        Returns a dict mapping person_id → PersonSignals for every person
        with at least one mention. Missing persons are simply absent.
        """
        try:
            if not self.is_available():
                return {}

            # Build per-person regex patterns. Skip short / stopword names.
            patterns: dict[str, re.Pattern] = {}
            names_by_pid: dict[str, str] = {}
            for person_id, info in person_index.items():
                name = (info or {}).get("name", "")
                variants = _name_variants(name)
                if not variants:
                    continue
                longest = max(variants, key=len)
                if len(longest) < _MIN_NAME_LEN:
                    continue
                # Skip stopword names — compare the ORIGINAL name lowered
                # (not the split form) so "Work" is caught but "Working
                # Group" survives.
                if name.strip().lower() in STOPWORDS:
                    continue
                # Build an alternation regex over all variants.
                alt = "|".join(re.escape(v) for v in variants)
                patterns[person_id] = re.compile(
                    r"\b(?:" + alt + r")\b", re.IGNORECASE
                )
                names_by_pid[person_id] = name

            if not patterns:
                return {}

            # Copy DB to temp to avoid lock contention with live writers.
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_db = Path(tmpdir) / "qareen.db"
                try:
                    shutil.copy2(self.db_path, tmp_db)
                except Exception as e:
                    log.warning("work adapter: failed to copy db: %s", e)
                    return {}

                try:
                    conn = sqlite3.connect(
                        f"file:{tmp_db}?mode=ro", uri=True
                    )
                    conn.row_factory = sqlite3.Row
                except Exception as e:
                    log.warning("work adapter: failed to open db: %s", e)
                    return {}

                try:
                    results: dict[str, PersonSignals] = {}
                    totals: dict[str, int] = {}
                    contexts: dict[str, list[dict]] = {}

                    for table, candidate_cols in _TABLE_TEXT_COLUMNS.items():
                        self._scan_table(
                            conn=conn,
                            table=table,
                            candidate_text_cols=candidate_cols,
                            patterns=patterns,
                            totals=totals,
                            contexts=contexts,
                        )

                    # Assemble PersonSignals for every person with >= 1 mention.
                    for person_id, count in totals.items():
                        if count < 1:
                            continue
                        mention = MentionSignal(
                            source=self.name,
                            total_mentions=count,
                            mention_contexts=contexts.get(person_id, []),
                            daily_log_mentions=0,
                            session_mentions=0,
                            work_task_mentions=count,
                        )
                        results[person_id] = PersonSignals(
                            person_id=person_id,
                            person_name=names_by_pid.get(person_id, ""),
                            source_coverage=[self.name],
                            mentions=[mention],
                        )
                    return results
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except Exception as e:
            log.exception("work adapter: extract_all failed: %s", e)
            return {}

    # ── Helpers ──────────────────────────────────────────────────────

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=?",
                (table,),
            )
            return cur.fetchone() is not None
        except Exception:
            return False

    def _table_columns(
        self, conn: sqlite3.Connection, table: str
    ) -> list[str]:
        try:
            cur = conn.execute(f"PRAGMA table_info({table})")
            return [row[1] for row in cur.fetchall()]
        except Exception:
            return []

    def _scan_table(
        self,
        conn: sqlite3.Connection,
        table: str,
        candidate_text_cols: list[str],
        patterns: dict[str, re.Pattern],
        totals: dict[str, int],
        contexts: dict[str, list[dict]],
    ) -> None:
        """Scan one table for mentions, updating totals and contexts."""
        if not self._table_exists(conn, table):
            return

        cols = self._table_columns(conn, table)
        if not cols:
            return

        text_cols = [c for c in candidate_text_cols if c in cols]
        if not text_cols:
            return

        has_id = "id" in cols
        has_created = "created_at" in cols

        select_parts: list[str] = []
        if has_id:
            select_parts.append("id")
        select_parts.extend(text_cols)
        if has_created:
            select_parts.append("created_at")

        sql = f"SELECT {', '.join(select_parts)} FROM {table}"  # noqa: S608

        try:
            cur = conn.execute(sql)
            rows = cur.fetchall()
        except Exception as e:
            log.warning(
                "work adapter: failed to read %s: %s", table, e
            )
            return

        for row in rows:
            try:
                row_id = row["id"] if has_id else None
                row_date = row["created_at"] if has_created else None
                # Concatenate all text fields with a space, handling NULL.
                parts: list[str] = []
                for c in text_cols:
                    val = row[c]
                    if val is None:
                        continue
                    if not isinstance(val, str):
                        val = str(val)
                    parts.append(val)
                if not parts:
                    continue
                text = " ".join(parts)
                if not text.strip():
                    continue

                for person_id, pattern in patterns.items():
                    matches = list(pattern.finditer(text))
                    if not matches:
                        continue
                    totals[person_id] = totals.get(person_id, 0) + len(matches)

                    person_contexts = contexts.setdefault(person_id, [])
                    for m in matches:
                        if len(person_contexts) >= _MAX_MENTION_CONTEXTS:
                            break
                        start = max(0, m.start() - 50)
                        end = min(len(text), m.end() + 50)
                        snippet = text[start:end].replace("\n", " ").strip()
                        file_ref = (
                            f"{table}:{row_id}"
                            if row_id is not None
                            else table
                        )
                        person_contexts.append(
                            {
                                "file": file_ref,
                                "snippet": snippet,
                                "date": row_date,
                            }
                        )
            except Exception as e:
                log.debug(
                    "work adapter: row scan failed in %s: %s", table, e
                )
                continue
