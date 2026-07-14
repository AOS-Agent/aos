"""Classification feedback store — persists operator corrections.

When the operator rejects or amends a classification, the correction is
recorded in the ``classification_feedback`` table (migration 034). Recent
corrections feed back into future LLM classifier runs as few-shot
examples in the prompt — the first primitive of the Living Intelligence
Loop (Subsystem C) without shipping the full loop yet.

Also manages the ``person_classification`` table: save, load, bulk load,
delete. One active classification per person; new runs overwrite via
INSERT OR REPLACE.

No LLM, no I/O beyond SQLite. Thread-safe at the connection level —
each method opens and closes its own connection.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from .store import DEFAULT_DB_PATH
from .taxonomy import ClassificationResult, Tier

logger = logging.getLogger(__name__)


# ── Schemas (mirror migration 034) ───────────────────────────────────

_CLASSIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS person_classification (
    person_id         TEXT NOT NULL,
    tier              TEXT NOT NULL,
    context_tags_json TEXT NOT NULL DEFAULT '[]',
    reasoning         TEXT,
    model             TEXT,
    run_id            TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    PRIMARY KEY (person_id)
);
CREATE INDEX IF NOT EXISTS idx_person_classification_tier
    ON person_classification(tier);
CREATE INDEX IF NOT EXISTS idx_person_classification_run
    ON person_classification(run_id);
CREATE INDEX IF NOT EXISTS idx_person_classification_created
    ON person_classification(created_at);

CREATE TABLE IF NOT EXISTS classification_feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id     TEXT NOT NULL,
    old_tier      TEXT,
    old_tags_json TEXT,
    new_tier      TEXT,
    new_tags_json TEXT,
    notes         TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classification_feedback_person
    ON classification_feedback(person_id);
CREATE INDEX IF NOT EXISTS idx_classification_feedback_created
    ON classification_feedback(created_at);
"""


# ── Classification store ─────────────────────────────────────────────

class ClassificationStore:
    """CRUD for person_classification + classification_feedback."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path: Path = Path(db_path) if db_path else DEFAULT_DB_PATH

    def init_schema(self) -> None:
        """Create tables if missing. Idempotent."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript(_CLASSIFICATION_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ── Classification CRUD ──

    def save(self, result: ClassificationResult) -> None:
        """Upsert a classification result for a person."""
        now = int(time.time())
        tags_json = json.dumps(result.context_tags, ensure_ascii=False)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT INTO person_classification (
                    person_id, tier, context_tags_json, reasoning,
                    model, run_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id) DO UPDATE SET
                    tier = excluded.tier,
                    context_tags_json = excluded.context_tags_json,
                    reasoning = excluded.reasoning,
                    model = excluded.model,
                    run_id = excluded.run_id,
                    created_at = excluded.created_at
                """,
                (
                    result.person_id,
                    result.tier.value,
                    tags_json,
                    result.reasoning,
                    result.model,
                    result.run_id,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def load(self, person_id: str) -> ClassificationResult | None:
        """Load the active classification for a person, or None."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                """
                SELECT person_id, tier, context_tags_json, reasoning,
                       model, run_id, created_at
                FROM person_classification
                WHERE person_id = ?
                """,
                (person_id,),
            ).fetchone()
        except sqlite3.OperationalError as e:
            logger.debug("person_classification load failed: %s", e)
            return None
        finally:
            conn.close()

        if not row:
            return None

        return self._row_to_result(row)

    def load_many(self, person_ids: list[str] | None = None) -> list[ClassificationResult]:
        """Bulk load. If person_ids is None, returns all active classifications."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            if person_ids is None:
                rows = conn.execute(
                    """
                    SELECT person_id, tier, context_tags_json, reasoning,
                           model, run_id, created_at
                    FROM person_classification
                    """
                ).fetchall()
            else:
                if not person_ids:
                    return []
                placeholders = ",".join("?" * len(person_ids))
                rows = conn.execute(
                    f"""
                    SELECT person_id, tier, context_tags_json, reasoning,
                           model, run_id, created_at
                    FROM person_classification
                    WHERE person_id IN ({placeholders})
                    """,
                    tuple(person_ids),
                ).fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("person_classification load_many failed: %s", e)
            return []
        finally:
            conn.close()

        return [self._row_to_result(r) for r in rows]

    def tier_distribution(self) -> dict[str, int]:
        """Aggregate count of classifications by tier.

        Privacy-aware: returns ONLY counts, never names or IDs.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT tier, COUNT(*) FROM person_classification GROUP BY tier"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        finally:
            conn.close()
        return {tier: count for tier, count in rows}

    def delete(self, person_id: str) -> int:
        """Delete a classification row. Returns rowcount."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.execute(
                "DELETE FROM person_classification WHERE person_id = ?",
                (person_id,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ── Tier → Importance sync ──

    # Authoritative mapping from intel tier to people.importance.
    # The intel classifier is the single source of truth for importance.
    TIER_TO_IMPORTANCE: dict[str, int] = {
        "core": 1,
        "active": 2,
        "emerging": 2,
        "channel_specific": 3,
        "fading": 3,
        "dormant": 4,
        "unknown": 4,
    }

    def sync_importance(self) -> dict[str, int]:
        """Sync person_classification.tier → people.importance.

        Reads the latest tier for every classified person and updates
        people.importance to match. Respects:
        - pinned_importance: if set, importance stays at pinned value
        - is_self: operator's own row is never auto-classified
        - lifecycle_state: deceased/archived people are not reclassified

        Returns stats dict.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        stats = {
            "promoted": 0, "demoted": 0, "unchanged": 0,
            "skipped_self": 0, "skipped_pinned": 0, "skipped_lifecycle": 0,
        }
        try:
            rows = conn.execute("""
                SELECT pc.person_id, pc.tier, p.importance, p.is_self,
                       p.pinned_importance, p.lifecycle_state
                FROM person_classification pc
                JOIN people p ON pc.person_id = p.id
            """).fetchall()

            now = int(time.time())
            for row in rows:
                if row["is_self"]:
                    stats["skipped_self"] += 1
                    continue

                if row["pinned_importance"] is not None:
                    # Pinned — ensure importance matches pin, don't auto-classify
                    if row["importance"] != row["pinned_importance"]:
                        conn.execute(
                            "UPDATE people SET importance = ?, updated_at = ? WHERE id = ?",
                            (row["pinned_importance"], now, row["person_id"]),
                        )
                    stats["skipped_pinned"] += 1
                    continue

                if row["lifecycle_state"] in ("deceased", "archived"):
                    stats["skipped_lifecycle"] += 1
                    continue

                new_imp = self.TIER_TO_IMPORTANCE.get(row["tier"], 4)
                old_imp = row["importance"] or 3

                if new_imp == old_imp:
                    stats["unchanged"] += 1
                    continue

                conn.execute(
                    "UPDATE people SET importance = ?, updated_at = ? WHERE id = ?",
                    (new_imp, now, row["person_id"]),
                )
                if new_imp < old_imp:
                    stats["promoted"] += 1
                else:
                    stats["demoted"] += 1

            conn.commit()
        finally:
            conn.close()

        logger.info(
            "sync_importance: promoted=%d demoted=%d unchanged=%d "
            "pinned=%d self=%d lifecycle=%d",
            stats["promoted"], stats["demoted"], stats["unchanged"],
            stats["skipped_pinned"], stats["skipped_self"], stats["skipped_lifecycle"],
        )
        return stats

    # ── Feedback CRUD ──

    def record_feedback(
        self,
        person_id: str,
        old: ClassificationResult | None,
        new: ClassificationResult | None,
        notes: str = "",
    ) -> None:
        """Append a correction to classification_feedback.

        At least one of old/new must be non-None. The notes field is
        operator free-text — stored as-is.
        """
        now = int(time.time())
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT INTO classification_feedback (
                    person_id, old_tier, old_tags_json,
                    new_tier, new_tags_json, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    person_id,
                    old.tier.value if old else None,
                    json.dumps(old.context_tags) if old else None,
                    new.tier.value if new else None,
                    json.dumps(new.context_tags) if new else None,
                    notes,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def recent_feedback(self, limit: int = 10) -> list[dict]:
        """Return the N most recent corrections, newest first.

        Used by LLMClassifier to build the few-shot block in the prompt.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                """
                SELECT person_id, old_tier, old_tags_json,
                       new_tier, new_tags_json, notes, created_at
                FROM classification_feedback
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("recent_feedback query failed: %s", e)
            return []
        finally:
            conn.close()

        out: list[dict] = []
        for row in rows:
            (
                person_id,
                old_tier,
                old_tags_json,
                new_tier,
                new_tags_json,
                notes,
                created_at,
            ) = row
            out.append(
                {
                    "person_id": person_id,
                    "old_tier": old_tier,
                    "old_tags": _safe_json_loads(old_tags_json) or [],
                    "new_tier": new_tier,
                    "new_tags": _safe_json_loads(new_tags_json) or [],
                    "notes": notes or "",
                    "created_at": created_at,
                }
            )
        return out

    def feedback_for_person(self, person_id: str) -> list[dict]:
        """All correction history for one person, newest first."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                """
                SELECT person_id, old_tier, old_tags_json,
                       new_tier, new_tags_json, notes, created_at
                FROM classification_feedback
                WHERE person_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (person_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
        out: list[dict] = []
        for row in rows:
            (
                pid,
                old_tier,
                old_tags_json,
                new_tier,
                new_tags_json,
                notes,
                created_at,
            ) = row
            out.append(
                {
                    "person_id": pid,
                    "old_tier": old_tier,
                    "old_tags": _safe_json_loads(old_tags_json) or [],
                    "new_tier": new_tier,
                    "new_tags": _safe_json_loads(new_tags_json) or [],
                    "notes": notes or "",
                    "created_at": created_at,
                }
            )
        return out

    # ── Internal helpers ──

    @staticmethod
    def _row_to_result(row: tuple) -> ClassificationResult:
        (
            person_id,
            tier,
            context_tags_json,
            reasoning,
            model,
            run_id,
            created_at,
        ) = row
        from datetime import datetime, timezone

        iso = (
            datetime.fromtimestamp(created_at, tz=timezone.utc).isoformat()
            if created_at
            else ""
        )
        return ClassificationResult(
            person_id=person_id,
            tier=Tier.from_str(tier),
            context_tags=_safe_json_loads(context_tags_json) or [],
            reasoning=reasoning or "",
            model=model,
            run_id=run_id or "",
            created_at=iso,
        )


def _safe_json_loads(value: str | None) -> list | dict | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
