"""Backfill — bounded sweep of comms.db history through the detect pipeline.

Initiative §2: last 90 days only (older numbers are delivered/expired —
probing them is waste and a recycled-number hazard), incremental watermark
via persisted ``last_scanned_message_id`` (the message ``rowid`` — comms.db
ids are TEXT) in the store's tracking_state key-value table, chunked 500
messages at a time, newest first.

Report-before-write: the default is a DRY RUN — the sweep runs, a report is
printed, nothing is persisted and the watermark does not move. ``--write``
persists candidates (auto-add / queue banding) and advances the watermark.

Watermark protocol: a run captures the current max message id up front
(high-water mark), processes messages with ``watermark < id <= high``
newest-first in chunks, and only on COMPLETION advances the watermark to
``high``. An interrupted run (``--max-hours`` cut, crash) leaves the
watermark untouched and re-scans next time — safe because detection dedups
on canonical number.

CLI: ``python3 -m qareen.tracking.backfill [--write] [--max-hours N]``
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import detect as _detect
from .config import TrackingConfig
from .packs import load_packs

log = logging.getLogger(__name__)

WATERMARK_KEY = "last_scanned_message_id"

DEFAULT_COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _to_message(row: sqlite3.Row) -> Dict[str, Any]:
    """Map a comms.db messages row onto the detect() message shape."""
    return {
        "message_id": row["id"],
        "channel": row["channel"],
        "sender": row["sender_id"],
        "text": row["content"] or "",
        "conversation_id": row["conversation_id"],
        "timestamp": row["timestamp"],
        "from_me": (row["direction"] or "").lower() in ("out", "sent", "outbound"),
    }


def _message_in_window(msg: Dict[str, Any], since: datetime) -> bool:
    ts = _detect._parse_ts(msg.get("timestamp"))
    if ts is None:
        return True  # unparseable timestamp — don't silently drop history
    return ts >= since


def _iter_chunks(
    conn: sqlite3.Connection,
    watermark: int,
    high: int,
    chunk_size: int,
) -> Iterator[List[sqlite3.Row]]:
    """Yield chunks of messages with watermark < rowid <= high, NEWEST first.

    Ordering keys on ``rowid``, not ``id``: comms.db message ids are TEXT
    (``wa_…``, ``gmail:…``), but the rowid is a monotonic integer, so it is
    the only safe watermark cursor. (Schemas with INTEGER PRIMARY KEY ids
    have rowid == id, so tests are unaffected.)
    """
    last_id = high + 1
    while True:
        rows = conn.execute(
            "SELECT rowid AS _wm_rowid, id, channel, direction, sender_id,"
            "       content, timestamp, conversation_id"
            " FROM messages"
            " WHERE rowid > ? AND rowid < ?"
            " ORDER BY rowid DESC LIMIT ?",
            (watermark, last_id, chunk_size),
        ).fetchall()
        if not rows:
            return
        last_id = rows[-1]["_wm_rowid"]
        yield rows


def run_backfill(
    comms_db: Path,
    store: Any = None,
    config: Optional[TrackingConfig] = None,
    packs: Optional[Dict[str, Any]] = None,
    write: bool = False,
    max_hours: Optional[float] = None,
    now: Optional[datetime] = None,
    people_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run one backfill sweep. Returns the report dict.

    ``store`` is the duck-typed tracking store: needs get_state/set_state
    for the watermark and (with write=True) add_shipment/enqueue_candidate.
    Without a store the sweep runs fully dry — detection still executes and
    the report is produced.
    """
    cfg = config or TrackingConfig()
    if packs is None:
        packs = load_packs()
    started = time.monotonic()
    now = now or datetime.now()
    since = now - timedelta(days=cfg.backfill_window_days)
    budget_s = (max_hours if max_hours is not None else cfg.backfill_default_max_hours) * 3600

    watermark = 0
    get_state = getattr(store, "get_state", None) if store is not None else None
    if callable(get_state):
        try:
            watermark = int(get_state(WATERMARK_KEY) or 0)
        except Exception:
            watermark = 0

    report: Dict[str, Any] = {
        "write": write,
        "window_days": cfg.backfill_window_days,
        "since": since.isoformat(timespec="seconds"),
        "watermark_before": watermark,
        "watermark_after": watermark,
        "scanned": 0,
        "skipped_from_me": 0,
        "skipped_privacy": 0,
        "skipped_old": 0,
        "candidates": 0,
        "by_layer": {},
        "actions": {"auto_add": 0, "queue": 0, "ignore": 0},
        "samples": [],
        "completed": True,
        "elapsed_s": 0.0,
    }

    conn = _connect_readonly(Path(comms_db))
    try:
        row = conn.execute("SELECT MAX(rowid) FROM messages").fetchone()
        high = int(row[0]) if row and row[0] is not None else 0
        report["high_water"] = high

        for chunk in _iter_chunks(conn, watermark, high, cfg.backfill_chunk_size):
            if time.monotonic() - started > budget_s:
                report["completed"] = False
                log.warning("backfill: max-hours budget exhausted, stopping early")
                break
            for row in chunk:
                msg = _to_message(row)
                if msg["from_me"]:
                    report["skipped_from_me"] += 1
                    continue
                if not _message_in_window(msg, since):
                    report["skipped_old"] += 1
                    continue
                report["scanned"] += 1
                result = _detect.detect(
                    msg, packs, store=store, config=cfg, people_db_path=people_db_path
                )
                if result.skipped_reason == "privacy":
                    report["skipped_privacy"] += 1
                    continue
                report["candidates"] += len(result.candidates)
                for cand in result.candidates:
                    report["by_layer"][cand.layer] = report["by_layer"].get(cand.layer, 0) + 1
                    if len(report["samples"]) < 10:
                        report["samples"].append(cand.to_dict())
                if write:
                    actions = _detect.persist(result, store, cfg)
                    for key, n in actions.items():
                        report["actions"][key] += n
                else:
                    for key, n in result.actions(cfg).items():
                        report["actions"][key] += n
    finally:
        conn.close()

    report["elapsed_s"] = round(time.monotonic() - started, 2)

    # Advance the watermark only on a completed WRITE run — a dry run must
    # never consume history, and an interrupted run re-scans (dedup-safe).
    if write and report["completed"]:
        set_state = getattr(store, "set_state", None) if store is not None else None
        if callable(set_state):
            try:
                set_state(WATERMARK_KEY, str(report["high_water"]))
                report["watermark_after"] = report["high_water"]
            except Exception:
                log.exception("backfill: failed to persist watermark")
    return report


def render_report(report: Dict[str, Any]) -> str:
    """Human-readable one-screen summary of a backfill run."""
    lines = [
        "backfill %s — window %dd (since %s)"
        % ("WRITE" if report["write"] else "DRY-RUN", report["window_days"], report["since"]),
        "  watermark: %s → %s%s"
        % (
            report["watermark_before"],
            report["watermark_after"],
            "" if report["completed"] else "  (INCOMPLETE — budget exhausted, not advanced)",
        ),
        "  scanned: %d  (from_me: %d, privacy: %d, outside window: %d)"
        % (
            report["scanned"],
            report["skipped_from_me"],
            report["skipped_privacy"],
            report["skipped_old"],
        ),
        "  candidates: %d  by layer: %s"
        % (report["candidates"], report["by_layer"] or "{}"),
        "  actions: auto_add=%d queue=%d ignore=%d"
        % (
            report["actions"]["auto_add"],
            report["actions"]["queue"],
            report["actions"]["ignore"],
        ),
        "  elapsed: %.1fs" % report["elapsed_s"],
    ]
    for sample in report.get("samples", []):
        lines.append(
            "    [%s] %s %s conf=%.2f"
            % (sample["layer"], sample["carrier"], sample["tracking_number"], sample["confidence"])
        )
    if not report["write"]:
        lines.append("  (dry run — re-run with --write to persist and advance the watermark)")
    return "\n".join(lines)


def _open_default_store() -> Any:
    """Best-effort open of the real tracking store (duck-typed seam).

    The store module is being built concurrently; any failure → None and
    the run degrades to a full dry run.
    """
    try:
        from . import store as tracking_store  # type: ignore

        for opener in ("open_default", "open", "connect"):
            fn = getattr(tracking_store, opener, None)
            if callable(fn):
                return fn()
        return tracking_store
    except Exception:
        log.warning("backfill: tracking store unavailable — running dry")
        return None


def _detection_packs(store: Any) -> Dict[str, Any]:
    """Lifecycle-filtered packs for detection (active only); falls back to
    all packs when the store or onboard module is unavailable."""
    try:
        from . import onboard

        return onboard.detection_packs(store)
    except Exception:
        return load_packs()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qareen.tracking.backfill",
        description="Sweep comms.db history through the tracking detect pipeline.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="persist candidates and advance the watermark (default: dry-run report only)",
    )
    parser.add_argument(
        "--max-hours",
        type=float,
        default=None,
        help="stop processing new chunks after N hours (default: config backfill.default_max_hours)",
    )
    parser.add_argument(
        "--comms-db",
        type=Path,
        default=DEFAULT_COMMS_DB,
        help="path to comms.db (default: ~/.aos/data/comms.db)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not args.comms_db.is_file():
        print("backfill: no comms.db at %s — nothing to sweep" % args.comms_db, file=sys.stderr)
        return 1

    config = TrackingConfig.load()
    store = _open_default_store()
    packs = _detection_packs(store)
    report = run_backfill(
        args.comms_db,
        store=store,
        config=config,
        packs=packs,
        write=args.write,
        max_hours=args.max_hours,
    )
    print(render_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
