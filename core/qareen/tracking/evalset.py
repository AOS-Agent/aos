"""Eval set — extraction + per-layer precision/recall for the detect pipeline.

Initiative §2: before confidence thresholds are trusted, hand-label ~200
extracted candidates from comms.db and measure per-layer precision/recall.
Regex/URL layers ship acting; probe/LLM layers stay log-only until measured.

Two modes:

- ``--export``  run detection over recent comms.db messages (READ-ONLY) and
  write up to ``config.eval_export_limit`` candidate rows into the store's
  detection_eval table (label NULL) for hand-labeling.
- ``--report``  read the labeled rows back and print per-layer
  precision/recall.

Label vocabulary (set by the human labeler, e.g. via SQL):
  "correct"    — the candidate is a real shipment detection (true positive)
  "incorrect"  — the candidate is garbage (false positive)
  "missed"     — a hand-added row marking a shipment a layer SHOULD have
                 found (false negative; counted for recall only)

Store seam (duck-typed):
- ``add_eval_candidate(row_dict)``   persist one detection_eval row
- ``iter_eval_rows()``               yield dicts with at least layer, label
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import detect as _detect
from .config import TrackingConfig
from .packs import load_packs

log = logging.getLogger(__name__)

DEFAULT_COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

LABEL_CORRECT = "correct"
LABEL_INCORRECT = "incorrect"
LABEL_MISSED = "missed"
KNOWN_LABELS = (LABEL_CORRECT, LABEL_INCORRECT, LABEL_MISSED)


# ── export ───────────────────────────────────────────────────────────────


def export_candidates(
    comms_db: Path,
    store: Any,
    config: Optional[TrackingConfig] = None,
    packs: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Extract up to *limit* detection candidates into detection_eval rows.

    comms.db is opened READ-ONLY. Rows are handed to the store via
    ``add_eval_candidate``; the returned list is what was exported (also
    useful for tests with a fake store).
    """
    cfg = config or TrackingConfig()
    if packs is None:
        packs = load_packs()
    limit = limit or cfg.eval_export_limit
    now = now or datetime.now()
    since = now - timedelta(days=cfg.backfill_window_days)

    add_row = getattr(store, "add_eval_candidate", None)
    exported: List[Dict[str, Any]] = []

    conn = sqlite3.connect("file:%s?mode=ro" % Path(comms_db), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, channel, direction, sender_id, content, timestamp,"
            "       conversation_id"
            " FROM messages ORDER BY rowid DESC"
        )
        for row in rows:
            if len(exported) >= limit:
                break
            if (row["direction"] or "").lower() in ("out", "sent", "outbound"):
                continue
            msg = {
                "message_id": row["id"],
                "channel": row["channel"],
                "sender": row["sender_id"],
                "text": row["content"] or "",
                "conversation_id": row["conversation_id"],
                "timestamp": row["timestamp"],
                "from_me": False,
            }
            ts = _detect._parse_ts(msg["timestamp"])
            if ts is not None and ts < since:
                continue
            result = _detect.detect(msg, packs, store=store, config=cfg)
            for cand in result.candidates:
                if len(exported) >= limit:
                    break
                eval_row = {
                    "tracking_number": cand.tracking_number,
                    "carrier": cand.carrier,
                    "confidence": round(cand.confidence, 4),
                    "layer": cand.layer,
                    "message_id": msg["message_id"],
                    "channel": msg["channel"],
                    "sender_domain": cand.context.get("sender_domain", ""),
                    # UI source snippet (sender/subject where available).
                    "source": cand.source,
                    "label": None,
                }
                if callable(add_row):
                    try:
                        add_row(eval_row)
                    except Exception:
                        log.exception("evalset: failed to add eval row")
                        continue
                exported.append(eval_row)
    finally:
        conn.close()
    return exported


# ── report ───────────────────────────────────────────────────────────────


def compute_metrics(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-layer precision/recall from labeled detection_eval rows.

    precision = correct / (correct + incorrect)
    recall    = correct / (correct + missed)

    Unlabeled rows (label NULL/other) are counted separately and excluded
    from the math. Layers with no denominators report None, not 0.0.
    """
    layers: Dict[str, Dict[str, Any]] = {}

    def bucket(layer: str) -> Dict[str, Any]:
        return layers.setdefault(
            layer, {"tp": 0, "fp": 0, "fn": 0, "unlabeled": 0, "precision": None, "recall": None}
        )

    for row in rows:
        layer = str(row.get("layer", "unknown"))
        label = row.get("label")
        b = bucket(layer)
        if label == LABEL_CORRECT:
            b["tp"] += 1
        elif label == LABEL_INCORRECT:
            b["fp"] += 1
        elif label == LABEL_MISSED:
            b["fn"] += 1
        else:
            b["unlabeled"] += 1

    for b in layers.values():
        if b["tp"] + b["fp"] > 0:
            b["precision"] = b["tp"] / (b["tp"] + b["fp"])
        if b["tp"] + b["fn"] > 0:
            b["recall"] = b["tp"] / (b["tp"] + b["fn"])
    return layers


def render_report(rows: Iterable[Dict[str, Any]]) -> str:
    """Human-readable per-layer precision/recall table."""
    metrics = compute_metrics(rows)
    lines = [
        "detection eval — per-layer precision/recall",
        "  %-8s %4s %4s %4s %9s %10s %9s" % ("layer", "tp", "fp", "fn", "precision", "recall", "unlabeled"),
    ]
    if not metrics:
        lines.append("  (no eval rows — run --export first, then hand-label)")
        return "\n".join(lines)
    for layer in sorted(metrics):
        m = metrics[layer]
        precision = "%.3f" % m["precision"] if m["precision"] is not None else "-"
        recall = "%.3f" % m["recall"] if m["recall"] is not None else "-"
        lines.append(
            "  %-8s %4d %4d %4d %9s %10s %9d"
            % (layer, m["tp"], m["fp"], m["fn"], precision, recall, m["unlabeled"])
        )
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────


def _open_default_store() -> Any:
    try:
        from . import store as tracking_store  # type: ignore

        for opener in ("open_default", "open", "connect"):
            fn = getattr(tracking_store, opener, None)
            if callable(fn):
                return fn()
        return tracking_store
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qareen.tracking.evalset",
        description="Export detection candidates for hand-labeling, or report per-layer precision/recall.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export", action="store_true", help="extract candidates from comms.db into detection_eval")
    mode.add_argument("--report", action="store_true", help="print per-layer precision/recall from labeled rows")
    parser.add_argument("--limit", type=int, default=None, help="max candidates to export (default: config)")
    parser.add_argument("--comms-db", type=Path, default=DEFAULT_COMMS_DB, help="path to comms.db")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    store = _open_default_store()
    if store is None:
        print("evalset: tracking store unavailable", file=sys.stderr)
        return 1

    if args.export:
        if not args.comms_db.is_file():
            print("evalset: no comms.db at %s" % args.comms_db, file=sys.stderr)
            return 1
        packs = None
        try:
            from . import onboard

            packs = onboard.detection_packs(store)  # lifecycle: active only
        except Exception:
            pass
        exported = export_candidates(args.comms_db, store, limit=args.limit, packs=packs)
        print("exported %d candidates for labeling" % len(exported))
        return 0

    iter_rows = getattr(store, "iter_eval_rows", None)
    if not callable(iter_rows):
        print("evalset: store has no iter_eval_rows()", file=sys.stderr)
        return 1
    print(render_report(iter_rows()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
