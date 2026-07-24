"""Carrier-mandated retention enforcement (REPORT-THEN-ASK).

Some carrier agreements REQUIRE deleting tracking data a fixed number of
days after delivery — DHL's Unified Tracking terms mandate 30 days (see
``carriers/dhl/manifest.yaml`` → ``retention.delete_days_after_delivery``).
The store is deliberately append-only; this module is the single sanctioned
exception, and it is loud about it:

- **Dry-run by default.** ``python3 -m qareen.tracking.retention`` prints
  exactly what WOULD be purged and touches nothing.
- **``--apply`` purges.** For each delivered shipment past its carrier's
  window: all ``shipment_events`` rows, the shipment's ``shipment_numbers``
  rows, and the shipment row itself are deleted (keeping a delivered row
  whose history is gone would be worse than removing it — DHL's terms are
  about the tracking data, not our bookkeeping of "a package arrived").
- **Audit trail survives.** Every applied purge writes one
  ``tracking_state`` record under ``retention.audit.<ts>`` with counts and
  the purged shipment ids, so "where did that shipment go" is always
  answerable.

Packs without a retention window (``delete_days_after_delivery: null`` —
UPS, FedEx, …) are NEVER touched. Python 3.9-compatible.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Milestone

logger = logging.getLogger(__name__)

AUDIT_KEY_PREFIX = "retention.audit."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def retention_windows(packs: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """{carrier slug: delete_days_after_delivery} for packs that mandate
    deletion. Packs with a null/missing window don't appear.

    *packs* is injectable (``{slug: CarrierPack}`` from ``packs.load_packs()``,
    or a test fake mapping slug → int/dict) so tests don't depend on the
    real carrier manifests.
    """
    if packs is None:
        from .packs import load_packs

        packs = load_packs()
    windows: Dict[str, int] = {}
    for slug, pack in packs.items():
        if pack is None:  # test shorthand: {"ups": None} = no window
            continue
        if isinstance(pack, int):  # test shorthand: {"dhl": 30}
            days: Optional[int] = pack
        elif isinstance(pack, dict):
            days = pack.get("delete_days_after_delivery")
        else:
            days = (pack.retention or {}).get("delete_days_after_delivery")
        if isinstance(days, bool):  # bool is an int subclass — never a window
            continue
        if isinstance(days, int) and days > 0:
            windows[slug] = days
    return windows


def _delivered_at(store: Any, shipment_id: str) -> Optional[datetime]:
    """When the shipment was delivered: the last delivered scan's timestamp,
    falling back to fetched_at when the carrier didn't report one."""
    delivered = None
    for event in store.events_for(shipment_id):
        if event.milestone == Milestone.DELIVERED:
            when = event.timestamp or event.fetched_at
            if when is not None and (delivered is None or when > delivered):
                delivered = when
    return delivered


def find_purgeable(
    store: Any,
    windows: Dict[str, int],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Delivered shipments past their carrier's retention window.

    Returns one dict per purgeable shipment (id, carrier, tracking_number,
    delivered_at, window_days, event_count) — exactly what a purge would
    remove. Empty when *windows* is empty (no carrier mandates deletion).
    """
    if not windows:
        return []
    now = now or _utcnow()
    out: List[Dict[str, Any]] = []
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM shipments WHERE status = 'delivered'"
        ).fetchall()
    for row in rows:
        carrier = row["carrier"]
        if carrier not in windows:
            continue
        delivered = _delivered_at(store, row["id"])
        if delivered is None:
            # Status says delivered but no delivered scan survived — fall
            # back to the row's last update rather than keeping it forever.
            try:
                delivered = datetime.fromisoformat(row["updated"])
            except (ValueError, TypeError):
                continue
        window = windows[carrier]
        if now - delivered < timedelta(days=window):
            continue
        with store._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM shipment_events WHERE shipment_id = ?",
                (row["id"],),
            ).fetchone()[0]
        out.append(
            {
                "id": row["id"],
                "carrier": carrier,
                "tracking_number": row["tracking_number"],
                "delivered_at": delivered.isoformat(),
                "window_days": window,
                "event_count": int(count),
            }
        )
    out.sort(key=lambda p: p["delivered_at"])
    return out


def purge(
    store: Any,
    windows: Dict[str, int],
    apply: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Dry-run report, or (apply=True) purge + audit record.

    Returns a report dict: {applied, purgeable: [...], purged_shipments,
    purged_events, audit_key}. In dry-run mode ``applied`` is False and the
    counts describe what WOULD be removed; nothing is written.
    """
    now = now or _utcnow()
    purgeable = find_purgeable(store, windows, now=now)
    report: Dict[str, Any] = {
        "applied": False,
        "purgeable": purgeable,
        "purged_shipments": 0,
        "purged_events": 0,
        "audit_key": None,
    }
    if not apply or not purgeable:
        return report

    with store._conn() as conn:
        for item in purgeable:
            sid = item["id"]
            cur = conn.execute(
                "DELETE FROM shipment_events WHERE shipment_id = ?", (sid,)
            )
            report["purged_events"] += cur.rowcount
            conn.execute(
                "DELETE FROM shipment_numbers WHERE shipment_id = ?", (sid,)
            )
            conn.execute("DELETE FROM order_shipments WHERE shipment_id = ?", (sid,))
            conn.execute("DELETE FROM shipments WHERE id = ?", (sid,))
            report["purged_shipments"] += 1

    audit_key = AUDIT_KEY_PREFIX + now.strftime("%Y%m%dT%H%M%S")
    store.set_state(
        audit_key,
        json.dumps(
            {
                "at": now.isoformat(),
                "windows": windows,
                "purged_shipments": report["purged_shipments"],
                "purged_events": report["purged_events"],
                "shipment_ids": [p["id"] for p in purgeable],
                "detail": [
                    {
                        "id": p["id"],
                        "carrier": p["carrier"],
                        "tracking_number": p["tracking_number"],
                        "delivered_at": p["delivered_at"],
                        "window_days": p["window_days"],
                    }
                    for p in purgeable
                ],
            }
        ),
    )
    report["applied"] = True
    report["audit_key"] = audit_key
    return report


def render_report(report: Dict[str, Any]) -> str:
    mode = "APPLIED" if report["applied"] else "DRY-RUN (nothing deleted; pass --apply to purge)"
    lines = ["Auto Tracker — retention enforcement [%s]" % mode]
    purgeable = report["purgeable"]
    if not purgeable:
        lines.append("  nothing past its carrier retention window")
        return "\n".join(lines)
    for p in purgeable:
        lines.append(
            "  %s %s (%s) — delivered %s, window %dd, %d event(s)"
            % (
                p["carrier"],
                p["tracking_number"],
                p["id"],
                p["delivered_at"][:10],
                p["window_days"],
                p["event_count"],
            )
        )
    verb = "purged" if report["applied"] else "would purge"
    lines.append(
        "%s: %d shipment(s), %d event(s)"
        % (verb, report["purged_shipments"] or len(purgeable),
           report["purged_events"] or sum(p["event_count"] for p in purgeable))
    )
    if report["audit_key"]:
        lines.append("audit record: tracking_state[%s]" % report["audit_key"])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qareen.tracking.retention",
        description=(
            "Enforce per-carrier retention windows "
            "(retention.delete_days_after_delivery). Dry-run by default."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually purge (default: dry-run report only)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="path to qareen.db (default: ~/.aos/data/qareen.db)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    from .store import DEFAULT_DB_PATH, ShipmentStore

    db_path = args.db or DEFAULT_DB_PATH
    if not db_path.is_file():
        print("retention: no qareen.db at %s — nothing to enforce" % db_path,
              file=sys.stderr)
        return 1

    store = ShipmentStore(db_path)
    report = purge(store, retention_windows(), apply=args.apply)
    print(render_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
