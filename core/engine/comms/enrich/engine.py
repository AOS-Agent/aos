"""Enrichment engine — nightly + backfill entity extraction into comms.db.

Ambient Knowledge Phase 4. Pulls un-extracted messages (past the watermark for
the current extractor_version), gates spam/phish out BEFORE extraction, batches
the rest dense per person-day, runs Haiku at concurrency 3 in tracked process
groups, and checkpoints per batch so a mid-run kill loses nothing.

    nightly   incremental, bounded to nightly.max_runtime_min, off-peak, resumes.
    backfill  --backfill --max-hours N, resumable, dense, newest-first, operator-run.

Ordering: newest-first (most valuable). Checkpoint contract: for each batch, the
entities (>= store_min) and the per-message watermark rows are written in ONE
transaction. A kill between batches leaves completed batches durable and
un-started messages simply un-watermarked → re-selected next run. Idempotent:
entity ids hash over (version, type, value, source_ids), so a resumed/duplicate
batch REPLACEs identical rows rather than duplicating.

Re-run/supersede: a NEW extractor_version re-selects every message (its watermark
rows are version-scoped) and marks the prior version's entities over the same
source_ids `status='superseded'`; GC prunes them after the TTL.

CLI:
    python3 engine.py                       # nightly, bounded
    python3 engine.py --backfill --max-hours 0.5   # 30-min pilot slice
    python3 engine.py --backfill --max-hours 8     # off-peak backfill session
    python3 engine.py --gc                  # prune superseded past TTL
    python3 engine.py --backup              # snapshot comms.db to AOS-X
    python3 engine.py --stats               # coverage report, no extraction
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

# Repo-root bootstrap so `core.engine.comms.enrich.*` imports resolve whether run
# as a module or a file (matches the codebase's cron-invocation convention).
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.engine.comms.enrich.batching import build_batches
from core.engine.comms.enrich.config import EnrichConfig
from core.engine.comms.enrich.extract import LiveGroups, run_batch
from core.engine.comms.enrich.gates import (
    backup_comms,
    enforce_storage_gates,
    gc_superseded,
)
from core.engine.comms.enrich.spam import is_spam

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

_VALID_TYPES = {"topic", "commitment", "transaction", "event", "mention", "question_open"}

# Distinct exit code so the cron wrapper can tell "login expired, paused" apart
# from a clean run (0) or a hard crash (1).
AUTH_PAUSE_EXIT = 42


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entity_id(version: str, etype: str, value: str, source_ids: list[str]) -> str:
    key = json.dumps([version, etype, value, sorted(source_ids)], sort_keys=True,
                     ensure_ascii=False)
    return "ent_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


class EnrichEngine:
    def __init__(self, cfg: EnrichConfig, db_path: Path = COMMS_DB):
        self.cfg = cfg
        self.db_path = db_path

    # ── candidate selection ────────────────────────────────────────────────
    def _candidate_messages(self, conn: sqlite3.Connection, *, newest_first: bool,
                            limit: int | None) -> list[dict]:
        order = "DESC" if newest_first else "ASC"
        sql = f"""
            SELECT m.id, m.channel, m.direction, m.sender_id, m.recipient_id,
                   m.content, m.timestamp, m.person_id, m.channel_metadata
              FROM messages m
             WHERE NOT EXISTS (
                       SELECT 1 FROM message_extraction x
                        WHERE x.message_id = m.id
                          AND x.extractor_version = ?)
               AND m.content IS NOT NULL AND m.content != ''
             ORDER BY m.timestamp {order}
        """
        if limit:
            sql += " LIMIT ?"
            params = (self.cfg.extractor_version, limit)
        else:
            params = (self.cfg.extractor_version,)
        cols = ["id", "channel", "direction", "sender_id", "recipient_id",
                "content", "timestamp", "person_id", "channel_metadata"]
        return [dict(zip(cols, r)) for r in conn.execute(sql, params).fetchall()]

    # ── spam gate (before extraction) ──────────────────────────────────────
    def _gate_spam(self, conn: sqlite3.Connection, messages: list[dict]) -> tuple[list[dict], int]:
        """Split candidates into (to_extract, skipped_count). Skipped messages
        get a terminal watermark row so they are never re-attempted."""
        keep, skipped = [], 0
        now = _now_iso()
        for m in messages:
            spam, _reason = is_spam(m)
            if spam:
                conn.execute(
                    "INSERT OR REPLACE INTO message_extraction"
                    "(message_id, extractor_version, extracted_at, status)"
                    " VALUES (?,?,?, 'skipped_spam')",
                    (m["id"], self.cfg.extractor_version, now),
                )
                skipped += 1
            else:
                keep.append(m)
        conn.commit()
        return keep, skipped

    # ── checkpoint: store one batch's entities + watermark atomically ──────
    def _checkpoint(self, conn: sqlite3.Connection, batch, result: dict) -> int:
        now = _now_iso()
        stored = 0
        cur = conn.cursor()
        cur.execute("BEGIN")
        try:
            for ent in result.get("entities", []):
                etype = ent.get("type")
                conf = ent.get("confidence")
                if etype not in _VALID_TYPES or conf is None:
                    continue
                if float(conf) < self.cfg.store_min:
                    continue
                fields = ent.get("fields", {}) or {}
                source_ids = [s for s in (ent.get("source_ids") or []) if s]
                if not source_ids:
                    continue
                value = _primary_value(etype, fields)
                eid = _entity_id(self.cfg.extractor_version, etype, value, source_ids)
                # Supersede any prior-version entity over the same source set.
                cur.execute(
                    "UPDATE message_entities SET status='superseded'"
                    " WHERE source_ids=? AND extractor_version!=? AND status='active'",
                    (json.dumps(sorted(source_ids)), self.cfg.extractor_version),
                )
                cur.execute(
                    "INSERT OR REPLACE INTO message_entities"
                    "(id, entity_type, value, fields_json, confidence, source_ids,"
                    " person_id, channel, batch_key, extractor_version, model,"
                    " created_at, ontology_type, ontology_id, status)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?, NULL, NULL, 'active')",
                    (eid, etype, value, json.dumps(fields, ensure_ascii=False),
                     float(conf), json.dumps(sorted(source_ids)), batch.person_id,
                     batch.channel, batch.batch_key, self.cfg.extractor_version,
                     result.get("model") or self.cfg.model, now),
                )
                stored += 1
            # Watermark every message in the batch (extracted, even if 0 entities).
            for m in batch.messages:
                cur.execute(
                    "INSERT OR REPLACE INTO message_extraction"
                    "(message_id, extractor_version, extracted_at, status)"
                    " VALUES (?,?,?, 'extracted')",
                    (m["id"], self.cfg.extractor_version, now),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        return stored

    # ── main run ───────────────────────────────────────────────────────────
    def run(self, *, mode: str = "nightly", max_hours: float | None = None,
            limit: int | None = None, newest_first: bool | None = None,
            dry_run: bool = False) -> dict:
        if newest_first is None:
            newest_first = (self.cfg.nightly_newest_first if mode == "nightly"
                            else self.cfg.backfill_newest_first)
        if max_hours is None:
            budget_s = (self.cfg.nightly_max_runtime_min * 60 if mode == "nightly"
                        else self.cfg.backfill_default_max_hours * 3600)
        else:
            budget_s = max_hours * 3600

        # A backfill session snapshots comms.db first — the brief requires a
        # backup before each backfill run (irreplaceable DB, heavy write load).
        if mode == "backfill" and not dry_run:
            dest = backup_comms(self.db_path, self.cfg)
            print(f"[enrich] pre-backfill backup → {dest}" if dest
                  else "[enrich] pre-backfill backup skipped (AOS-X not mounted)")

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout=30000")

        # Storage gate — refuse loudly before doing any work.
        remaining = conn.execute(
            "SELECT COUNT(*) FROM messages m WHERE NOT EXISTS ("
            "SELECT 1 FROM message_extraction x WHERE x.message_id=m.id "
            "AND x.extractor_version=?)", (self.cfg.extractor_version,)
        ).fetchone()[0]
        gate = enforce_storage_gates(self.db_path, self.cfg,
                                     projected_new_entities=remaining)

        candidates = self._candidate_messages(conn, newest_first=newest_first,
                                              limit=limit)
        if dry_run:
            keep_est = sum(1 for m in candidates if not is_spam(m)[0])
            conn.close()
            return {"mode": mode, "dry_run": True, "candidates": len(candidates),
                    "would_extract": keep_est, "would_skip_spam": len(candidates) - keep_est,
                    "remaining_total": remaining, "gate_db_gb": round(gate.db_bytes/1e9, 3)}

        to_extract, skipped_spam = self._gate_spam(conn, candidates)
        batches = build_batches(to_extract, min_batch_msgs=self.cfg.min_batch_msgs,
                                max_batch_msgs=self.cfg.max_batch_msgs)
        if newest_first:
            batches.sort(key=lambda b: max((m.get("timestamp") or "" for m in b.messages),
                                           default=""), reverse=True)

        live = LiveGroups()
        restore = live.install_signal_handlers()
        t0 = time.time()
        stats = {"mode": mode, "candidates": len(candidates),
                 "skipped_spam": skipped_spam, "batches_total": len(batches),
                 "batches_done": 0, "batches_failed": 0, "messages_extracted": 0,
                 "entities_stored": 0, "by_type": {}, "cost_usd": 0.0,
                 "stopped_early": False, "auth_paused": False,
                 "remaining_total": remaining}
        try:
            self._process(conn, batches, live, budget_s, t0, stats)
        finally:
            restore()
            conn.close()
        stats["wall_s"] = round(time.time() - t0, 1)
        return stats

    def _process(self, conn, batches, live, budget_s, t0, stats):
        it = iter(batches)
        inflight = {}
        with ThreadPoolExecutor(max_workers=self.cfg.concurrency) as ex:
            def submit_next():
                try:
                    b = next(it)
                except StopIteration:
                    return False
                fut = ex.submit(run_batch, b, model=self.cfg.model,
                                timeout_s=self.cfg.call_timeout_s,
                                max_msg_chars=self.cfg.max_msg_chars, live=live)
                inflight[fut] = b
                return True

            for _ in range(self.cfg.concurrency):
                if time.time() - t0 < budget_s and not live.shutting_down:
                    submit_next()

            while inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in done:
                    batch = inflight.pop(fut)
                    result = fut.result()
                    if result.get("ok"):
                        stored = self._checkpoint(conn, batch, result)
                        stats["entities_stored"] += stored
                        stats["messages_extracted"] += batch.n
                        stats["batches_done"] += 1
                        for ent in result.get("entities", []):
                            t = ent.get("type")
                            if t:
                                stats["by_type"][t] = stats["by_type"].get(t, 0) + 1
                        if result.get("cost_usd"):
                            stats["cost_usd"] = round(stats["cost_usd"] + result["cost_usd"], 4)
                    else:
                        # Failed batch: NOT watermarked → re-selected next run.
                        stats["batches_failed"] += 1
                        if result.get("auth_failure"):
                            # Login expired: every remaining batch would fail the
                            # same way. Stop submitting, drain in-flight, and pause
                            # cleanly — completed batches are already checkpointed.
                            stats["auth_paused"] = True
                    # Refill unless budget spent OR we are pausing for auth.
                    if stats["auth_paused"]:
                        stats["stopped_early"] = True
                    elif time.time() - t0 < budget_s and not live.shutting_down:
                        submit_next()
                    else:
                        stats["stopped_early"] = True


def _primary_value(etype: str, fields: dict) -> str:
    """Denormalized `value` string for FTS/dedup, per type."""
    if etype == "transaction":
        parts = [fields.get("merchant"), fields.get("amount")]
        return " ".join(str(p) for p in parts if p) or "transaction"
    if etype == "commitment":
        return str(fields.get("what") or "commitment")
    return str(fields.get("value") or fields.get("what") or etype)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Comms enrichment engine (Phase 4)")
    ap.add_argument("--backfill", action="store_true", help="backfill mode (dense, resumable)")
    ap.add_argument("--max-hours", type=float, default=None, help="wall-time budget (hours)")
    ap.add_argument("--limit", type=int, default=None, help="cap candidate messages")
    ap.add_argument("--dry-run", action="store_true", help="report candidates, extract nothing")
    ap.add_argument("--gc", action="store_true", help="prune superseded entities past TTL and exit")
    ap.add_argument("--backup", action="store_true", help="snapshot comms.db to AOS-X and exit")
    ap.add_argument("--stats", action="store_true", help="coverage report, no extraction")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    cfg = EnrichConfig.load()
    engine = EnrichEngine(cfg)

    if args.backup:
        dest = backup_comms(COMMS_DB, cfg)
        out = {"backup": str(dest) if dest else None,
               "ok": dest is not None}
        print(json.dumps(out) if args.json else
              (f"backup → {dest}" if dest else "backup skipped (AOS-X not mounted)"))
        return 0 if dest else 1

    if args.gc:
        conn = sqlite3.connect(COMMS_DB)
        try:
            pruned = gc_superseded(conn, cfg)
        finally:
            conn.close()
        print(json.dumps({"gc_pruned": pruned}) if args.json else f"gc pruned {pruned} superseded")
        return 0

    if args.stats:
        print(json.dumps(_coverage(cfg), indent=2))
        return 0

    mode = "backfill" if args.backfill else "nightly"
    stats = engine.run(mode=mode, max_hours=args.max_hours, limit=args.limit,
                       dry_run=args.dry_run)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        _print_stats(stats)
    if stats.get("auth_paused"):
        _alert_auth_paused(stats)
        return AUTH_PAUSE_EXIT
    return 0


def _coverage(cfg: EnrichConfig) -> dict:
    conn = sqlite3.connect(COMMS_DB)
    try:
        total = conn.execute("SELECT COUNT(*) FROM messages WHERE content IS NOT NULL AND content!=''").fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(DISTINCT message_id) FROM message_extraction WHERE extractor_version=?",
            (cfg.extractor_version,)).fetchone()[0]
        ents = conn.execute("SELECT COUNT(*) FROM message_entities WHERE status='active'").fetchone()[0]
        by_type = dict(conn.execute(
            "SELECT entity_type, COUNT(*) FROM message_entities WHERE status='active' GROUP BY entity_type").fetchall())
    finally:
        conn.close()
    return {"extractor_version": cfg.extractor_version, "messages_total": total,
            "messages_extracted": done, "remaining": total - done,
            "entities_active": ents, "by_type": by_type}


def _alert_auth_paused(stats: dict) -> None:
    """Telegram the operator that the run paused on an expired login. Best-effort:
    the notify path degrades to a no-op if credentials aren't available."""
    msg = (f"⚠️ Comms backfill PAUSED — login expired.\n"
           f"Extracted {stats.get('batches_done', 0)} batches "
           f"({stats.get('entities_stored', 0)} entities) before pausing; "
           f"{stats.get('remaining_total', '?')} messages still un-extracted.\n"
           f"Run <code>claude /login</code> then re-run the backfill — it resumes "
           f"from the checkpoint.")
    try:
        _REPO = Path(__file__).resolve().parents[4]
        core_dir = _REPO / "core"
        if str(core_dir) not in sys.path:
            sys.path.insert(0, str(core_dir))
        from lib.notify import send_telegram
        send_telegram(msg)
    except Exception:
        pass


def _print_stats(s: dict) -> None:
    print(f"[{s['mode']}] {s.get('batches_done',0)}/{s.get('batches_total',0)} batches "
          f"({s.get('batches_failed',0)} failed), {s.get('messages_extracted',0)} msgs, "
          f"{s.get('entities_stored',0)} entities, spam-skipped {s.get('skipped_spam',0)}, "
          f"~${s.get('cost_usd',0):.2f}, {s.get('wall_s','?')}s"
          f"{' [stopped at budget]' if s.get('stopped_early') else ''}")
    if s.get("by_type"):
        print("  by type: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_type"].items())))
    print(f"  remaining un-extracted: {s.get('remaining_total','?')}")


if __name__ == "__main__":
    raise SystemExit(_cli())
