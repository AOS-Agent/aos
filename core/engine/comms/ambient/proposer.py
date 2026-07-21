"""Commitments -> work inbox — the knowing->doing seam (north star).

Phase 5. Things the operator PROMISED (commitment entities >= surface, extracted
from OUTBOUND messages — direction is ground truth for "who was speaking", the
model's free-text `who` is null on ~2/3 of live rows) flow into the work
system's INBOX as proposals the operator triages. Not tasks — inbox, so trust
starts at the proposal level (locked: agent proposes, operator disposes).

Things OTHERS promised the operator (INBOUND commitments) are NOT proposed here;
they surface in the ambient digest's "owed to you" line (digest.py).

DEDUP + NO-REPROPOSAL, both via the entity's own columns (the frozen schema's
`ontology_type`/`ontology_id`, the documented Phase-5 lift seam):
  * one inbox item per entity id — a proposed entity is stamped
    ontology_type='work_inbox', ontology_id=<inbox id>, and the selector only
    takes rows with ontology_id IS NULL, so it is never proposed twice.
  * never re-propose a dismissed one — if the operator deletes the inbox item,
    the stamp is NOT removed, so the entity stays out of selection forever.

PACING: the backlog is large (hundreds of historical commitments). A per-run cap
(newest-first) drains it gradually instead of flooding the inbox in one night.

COMMS.DB SAFETY: the backfill engine may hold write locks. The only write here is
a tiny single-row UPDATE stamp per proposal, each committed immediately with a
busy timeout — never a long-held lock. dry_run (the default) writes NOTHING to
either the work system or comms.db.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.engine.comms.ambient import digest  # noqa: E402

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

SURFACE_MIN = 0.80
DEFAULT_MAX_PER_RUN = 20
_STAMP_TYPE = "work_inbox"


def _default_add_inbox() -> Callable[[str], str]:
    """Return a callable text -> inbox_id backed by the live work engine.

    Loaded lazily/by path so this module imports without the work package on
    sys.path, and so tests can inject a stub instead of touching a real DB.
    """
    work_dir = _REPO_ROOT / "core" / "engine" / "work"
    if str(work_dir) not in sys.path:
        sys.path.insert(0, str(work_dir))
    import backend as work  # noqa: PLC0415

    def add(text: str) -> str:
        item = work.add_inbox(text, source="ambient-commitment")
        return item.get("id") if isinstance(item, dict) else str(item)

    return add


def _select_candidates(conn: sqlite3.Connection, *, surface_min: float,
                       limit: int) -> list[dict]:
    """Operator's own open commitments not yet proposed, newest-first."""
    sql = (
        "SELECT e.id, e.value, e.fields_json, e.source_ids, e.person_id, "
        "m.timestamp AS ts, m.channel AS channel "
        "FROM message_entities e "
        "JOIN messages m ON m.id = json_extract(e.source_ids,'$[0]') "
        "WHERE e.entity_type='commitment' AND e.status='active' "
        "AND e.confidence >= ? AND m.direction='outbound' "
        "AND e.ontology_id IS NULL "
        "ORDER BY m.timestamp DESC LIMIT ?")
    out = []
    for r in conn.execute(sql, (surface_min, limit)):
        try:
            fields = json.loads(r["fields_json"] or "{}")
        except Exception:
            fields = {}
        try:
            src = json.loads(r["source_ids"] or "[]")
        except Exception:
            src = []
        # Durability gate (operator feedback 2026-07-21): momentary logistics
        # ("leave in 5 min", "right back") must never become inbox proposals.
        if not digest._is_durable(fields.get("due"), r["ts"]):
            continue
        out.append({"entity_id": r["id"], "what": fields.get("what") or r["value"],
                    "due": fields.get("due"), "source_ids": src,
                    "ts": r["ts"], "channel": r["channel"]})
    return out


def _inbox_text(cand: dict) -> str:
    """Operator-facing inbox line carrying provenance (source_refs travel)."""
    what = (cand["what"] or "commitment").strip()
    due = f" (due {cand['due']})" if cand.get("due") else ""
    src = cand["source_ids"][0] if cand.get("source_ids") else "?"
    date = (cand.get("ts") or "")[:10]
    return f"You committed: {what}{due} [comms {date} · src {src}]"


def propose_commitments(comms_db: Path = COMMS_DB, *, surface_min: float = SURFACE_MIN,
                        max_per_run: int = DEFAULT_MAX_PER_RUN, dry_run: bool = True,
                        add_inbox: Optional[Callable[[str], str]] = None) -> dict:
    """Propose operator commitments to the work inbox.

    dry_run=True (default) selects and renders candidates but writes NOTHING —
    returns what WOULD be created. dry_run=False creates one inbox item per
    candidate and stamps the entity so it is never re-proposed.
    """
    if not comms_db.exists():
        return {"dry_run": dry_run, "candidates": 0, "created": 0, "items": []}

    # Read-only pass to select. The write pass (stamps) opens its own short-lived
    # writable connection only when committing.
    ro = sqlite3.connect(f"file:{comms_db}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA busy_timeout=3000")
    try:
        cands = _select_candidates(ro, surface_min=surface_min, limit=max_per_run)
    finally:
        ro.close()

    items = [{"entity_id": c["entity_id"], "text": _inbox_text(c),
              "source_ids": c["source_ids"]} for c in cands]

    if dry_run:
        return {"dry_run": True, "candidates": len(cands), "created": 0,
                "items": items}

    adder = add_inbox or _default_add_inbox()
    created = 0
    wc = sqlite3.connect(comms_db)
    wc.execute("PRAGMA busy_timeout=30000")
    try:
        for it in items:
            try:
                inbox_id = adder(it["text"])
            except Exception:
                continue  # work write failed — leave unstamped, retry next run
            if not inbox_id:
                continue
            # Tiny, immediately-committed stamp — never a long-held lock.
            wc.execute(
                "UPDATE message_entities SET ontology_type=?, ontology_id=? "
                "WHERE id=? AND ontology_id IS NULL",
                (_STAMP_TYPE, str(inbox_id), it["entity_id"]))
            wc.commit()
            created += 1
    finally:
        wc.close()

    return {"dry_run": False, "candidates": len(cands), "created": created,
            "items": items}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Propose operator commitments to work inbox")
    ap.add_argument("--commit", action="store_true", help="actually create inbox items")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN)
    args = ap.parse_args()
    res = propose_commitments(dry_run=not args.commit, max_per_run=args.max)
    print(json.dumps(res, indent=2, ensure_ascii=False))
