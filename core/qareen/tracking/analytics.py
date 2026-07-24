"""Lane analytics + live-summary functions for the Auto Tracker.

Pure read-only functions over ShipmentStore data (qareen.db). Two families:

- **Historical** (carrier-promise vs reality):
  ``lane_stats``    — carrier × route (origin → destination, where derivable
                      from scan locations) → actual transit-days distribution.
  ``eta_accuracy``  — carrier-promised ETA vs actual delivery delta, per
                      carrier. Positive delta = delivered LATE.

- **Live** (dashboard / briefing / API summary):
  ``arriving_today`` — active shipments due today (ETA date = today, or
                       already out for delivery).
  ``exceptions``     — active shipments parked at exception / failed_attempt.
  ``summary``        — {active, arriving_today, exceptions} counts; this is
                       the exact shape the API's GET /api/shipments summary
                       block and the daily briefing both consume.

Read-only by contract: the only store methods touched are ``_conn`` (SELECTs
for the full shipment scan — the store has no list-all public method) and
``events_for``. Nothing here writes.

Python 3.9-compatible.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .models import Milestone

# Pickup-side milestones: the first of these timestamps the start of transit.
_TRANSIT_START = (Milestone.PICKED_UP, Milestone.IN_TRANSIT)
_EXCEPTION_MILESTONES = (Milestone.EXCEPTION.value, Milestone.FAILED_ATTEMPT.value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(value: Union[str, datetime, None]) -> Optional[datetime]:
    """ISO string → naive-UTC datetime (matches store._parse_dt semantics)."""
    if value is None or isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _today(now: datetime, tz: Optional[tzinfo]) -> Any:
    """The local date 'today' in *tz* (naive-UTC *now* when tz is None)."""
    if tz is None:
        return now.date()
    aware = now.replace(tzinfo=timezone.utc)
    return aware.astimezone(tz).date()


# ── store reads (read-only) ------------------------------------------------


def _all_shipments(store: Any) -> List[Dict[str, Any]]:
    """Every shipment row as a dict (raw SQL — the store exposes no
    list-all; SELECT-only, never writes)."""
    with store._conn() as conn:
        rows = conn.execute("SELECT * FROM shipments ORDER BY created").fetchall()
    return [dict(r) for r in rows]


def _shipment_view(store: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    """Shipment row + its events, the unit every analytic works on."""
    return {"shipment": row, "events": store.events_for(row["id"])}


def _first_event(events: List[Any], milestones: Tuple[Milestone, ...]) -> Any:
    for event in events:
        if event.milestone in milestones and event.timestamp is not None:
            return event
    return None


def _delivered_event(events: List[Any]) -> Any:
    delivered = None
    for event in events:
        if event.milestone == Milestone.DELIVERED and event.timestamp is not None:
            delivered = event  # keep the LAST delivered scan
    return delivered


def _distribution(days: List[float]) -> Dict[str, Any]:
    return {
        "samples": len(days),
        "min_days": round(min(days), 2),
        "median_days": round(statistics.median(days), 2),
        "mean_days": round(statistics.fmean(days), 2),
        "max_days": round(max(days), 2),
    }


# ── historical analytics ---------------------------------------------------


def lane_stats(store: Any) -> List[Dict[str, Any]]:
    """Actual transit-days distribution per carrier × route.

    A lane is (carrier, origin, destination): origin is the location of the
    first pickup/in-transit scan, destination the location of the final
    delivered scan. Only shipments with BOTH timestamps are counted —
    anything else is "not derivable" and skipped, never guessed.
    """
    lanes: Dict[Tuple[str, str, str], List[float]] = {}
    for row in _all_shipments(store):
        events = store.events_for(row["id"])
        start = _first_event(events, _TRANSIT_START)
        delivered = _delivered_event(events)
        if start is None or delivered is None:
            continue
        if delivered.timestamp < start.timestamp:
            continue  # carrier clock skew — not a real lane sample
        days = (delivered.timestamp - start.timestamp).total_seconds() / 86400.0
        key = (
            row["carrier"],
            start.location or "unknown",
            delivered.location or "unknown",
        )
        lanes.setdefault(key, []).append(days)

    out = []
    for (carrier, origin, destination), days in sorted(lanes.items()):
        entry = {
            "carrier": carrier,
            "origin": origin,
            "destination": destination,
        }
        entry.update(_distribution(days))
        out.append(entry)
    out.sort(key=lambda e: (-e["samples"], e["carrier"]))
    return out


def eta_accuracy(store: Any) -> List[Dict[str, Any]]:
    """Carrier-promised ETA vs actual delivery, per carrier.

    delta_days = actual delivery time − promised ETA. Positive = late,
    negative = early. A delivery within ±1 day of the promise counts as
    on-time. Only delivered shipments carrying an ETA are counted.
    """
    deltas: Dict[str, List[float]] = {}
    for row in _all_shipments(store):
        if row["status"] != "delivered":
            continue
        eta = _parse_dt(row.get("eta"))
        if eta is None:
            continue
        delivered = _delivered_event(store.events_for(row["id"]))
        if delivered is None:
            continue
        delta = (delivered.timestamp - eta).total_seconds() / 86400.0
        deltas.setdefault(row["carrier"], []).append(delta)

    out = []
    for carrier, values in sorted(deltas.items()):
        out.append(
            {
                "carrier": carrier,
                "samples": len(values),
                "mean_delta_days": round(statistics.fmean(values), 2),
                "median_delta_days": round(statistics.median(values), 2),
                "late": sum(1 for v in values if v > 1.0),
                "on_time": sum(1 for v in values if abs(v) <= 1.0),
                "early": sum(1 for v in values if v < -1.0),
            }
        )
    out.sort(key=lambda e: (-e["samples"], e["carrier"]))
    return out


# ── live summary -----------------------------------------------------------


def _public_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """The fields a briefing/API item line needs — nothing internal."""
    return {
        "id": row["id"],
        "carrier": row["carrier"],
        "label": row.get("label"),
        "merchant": row.get("merchant"),
        "category": row.get("category"),
        "milestone": row["milestone"],
        "eta": row.get("eta"),
    }


def arriving_today(
    store: Any,
    now: Optional[datetime] = None,
    tz: Optional[tzinfo] = None,
) -> List[Dict[str, Any]]:
    """Active shipments arriving today: ETA date is today (in *tz*, UTC when
    omitted) or the shipment is already out for delivery."""
    now = now or _utcnow()
    today = _today(now, tz)
    out = []
    for row in _all_shipments(store):
        if row["status"] != "active":
            continue
        if row["milestone"] == Milestone.OUT_FOR_DELIVERY.value:
            out.append(_public_view(row))
            continue
        eta = _parse_dt(row.get("eta"))
        if eta is not None and _today(eta, tz) == today:
            out.append(_public_view(row))
    return out


def exceptions(store: Any) -> List[Dict[str, Any]]:
    """Active shipments stuck at exception / failed_attempt."""
    return [
        _public_view(row)
        for row in _all_shipments(store)
        if row["status"] == "active" and row["milestone"] in _EXCEPTION_MILESTONES
    ]


def summary(
    store: Any,
    now: Optional[datetime] = None,
    tz: Optional[tzinfo] = None,
) -> Dict[str, int]:
    """The GET /api/shipments summary block: {active, arriving_today,
    exceptions}."""
    rows = _all_shipments(store)
    return {
        "active": sum(1 for r in rows if r["status"] == "active"),
        "arriving_today": len(arriving_today(store, now=now, tz=tz)),
        "exceptions": len(exceptions(store)),
    }


# ── CLI report -------------------------------------------------------------


def render_report(store: Any, now: Optional[datetime] = None) -> str:
    lines: List[str] = []
    s = summary(store, now=now)
    lines.append("Auto Tracker — analytics report")
    lines.append(
        "active: %d | arriving today: %d | exceptions: %d"
        % (s["active"], s["arriving_today"], s["exceptions"])
    )
    lines.append("")

    lanes = lane_stats(store)
    lines.append("Lanes (carrier: origin → destination — transit days)")
    if lanes:
        for lane in lanes:
            lines.append(
                "  %s: %s → %s — n=%d median=%.1fd mean=%.1fd range=%.1f–%.1fd"
                % (
                    lane["carrier"],
                    lane["origin"],
                    lane["destination"],
                    lane["samples"],
                    lane["median_days"],
                    lane["mean_days"],
                    lane["min_days"],
                    lane["max_days"],
                )
            )
    else:
        lines.append("  (no completed lanes yet)")
    lines.append("")

    accuracy = eta_accuracy(store)
    lines.append("ETA accuracy (promised vs actual delivery, days late)")
    if accuracy:
        for acc in accuracy:
            lines.append(
                "  %s — n=%d mean=%+.1fd median=%+.1fd | late %d, on-time %d, early %d"
                % (
                    acc["carrier"],
                    acc["samples"],
                    acc["mean_delta_days"],
                    acc["median_delta_days"],
                    acc["late"],
                    acc["on_time"],
                    acc["early"],
                )
            )
    else:
        lines.append("  (no delivered shipments with an ETA yet)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qareen.tracking.analytics",
        description="Lane analytics + ETA accuracy report over qareen.db.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="path to qareen.db (default: ~/.aos/data/qareen.db)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the text report",
    )
    args = parser.parse_args(argv)

    from .store import DEFAULT_DB_PATH, ShipmentStore

    db_path = args.db or DEFAULT_DB_PATH
    if not db_path.is_file():
        print("analytics: no qareen.db at %s — nothing to report" % db_path,
              file=sys.stderr)
        return 1

    store = ShipmentStore(db_path)
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary(store),
                    "lanes": lane_stats(store),
                    "eta_accuracy": eta_accuracy(store),
                },
                indent=2,
            )
        )
    else:
        print(render_report(store))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
