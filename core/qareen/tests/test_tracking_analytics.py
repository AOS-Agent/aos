"""Tests for qareen.tracking.analytics — lane stats, ETA accuracy, and the
live summary functions, all against tmp stores with synthetic event
histories. Never touches the real ~/.aos/data/qareen.db.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import analytics  # noqa: E402
from qareen.tracking.models import Milestone, Shipment, TrackingEvent  # noqa: E402
from qareen.tracking.store import ShipmentStore  # noqa: E402

NOW = datetime(2026, 7, 24, 12, 0, 0)  # fixed naive-UTC "now" for determinism


def _store(tmp_path):
    return ShipmentStore(tmp_path / "qareen.db")


def _add_shipment(store, number, carrier, **kwargs):
    shipment = Shipment(tracking_number=number, carrier=carrier, **kwargs)
    shipment_id, _ = store.upsert_shipment(shipment)
    return shipment_id


def _add_event(store, shipment_id, milestone, when, location=None):
    store.append_event(
        shipment_id,
        TrackingEvent(
            milestone=milestone,
            description=milestone.value if milestone else "",
            timestamp=when,
            fetched_at=when,
            location=location,
        ),
    )


def _delivered_lane(store, number, carrier, origin, dest, pickup_days_ago,
                    transit_days, eta=None):
    """A completed shipment: picked up at origin, delivered at dest."""
    sid = _add_shipment(store, number, carrier)
    pickup = NOW - timedelta(days=pickup_days_ago)
    _add_event(store, sid, Milestone.LABEL_CREATED, pickup - timedelta(hours=5))
    _add_event(store, sid, Milestone.PICKED_UP, pickup, location=origin)
    _add_event(store, sid, Milestone.IN_TRANSIT,
               pickup + timedelta(days=transit_days / 2), location=origin)
    _add_event(store, sid, Milestone.DELIVERED,
               pickup + timedelta(days=transit_days), location=dest)
    if eta is not None:
        store.update_shipment(sid, eta=eta)
    return sid


# ── lane stats ──────────────────────────────────────────────────────────────


def test_lane_stats_groups_by_carrier_and_route(tmp_path):
    store = _store(tmp_path)
    _delivered_lane(store, "1Z111", "ups", "Toronto", "Vancouver",
                    pickup_days_ago=20, transit_days=4.0)
    _delivered_lane(store, "1Z222", "ups", "Toronto", "Vancouver",
                    pickup_days_ago=15, transit_days=6.0)
    _delivered_lane(store, "1Z333", "ups", "Toronto", "Montreal",
                    pickup_days_ago=10, transit_days=1.5)
    _delivered_lane(store, "771111111111", "fedex", "Toronto", "Vancouver",
                    pickup_days_ago=12, transit_days=3.0)

    lanes = analytics.lane_stats(store)
    by_key = {(l["carrier"], l["origin"], l["destination"]): l for l in lanes}

    lane = by_key[("ups", "Toronto", "Vancouver")]
    assert lane["samples"] == 2
    assert lane["min_days"] == 4.0
    assert lane["max_days"] == 6.0
    assert lane["median_days"] == 5.0
    assert lane["mean_days"] == 5.0

    assert by_key[("ups", "Toronto", "Montreal")]["samples"] == 1
    assert by_key[("fedex", "Toronto", "Vancouver")]["median_days"] == 3.0
    # Most-sampled lane sorts first.
    assert lanes[0]["samples"] == 2


def test_lane_stats_skips_undelivered_and_missing_locations(tmp_path):
    store = _store(tmp_path)
    # Active shipment, no delivered scan → not a lane sample.
    sid = _add_shipment(store, "1Z444", "ups")
    _add_event(store, sid, Milestone.PICKED_UP, NOW - timedelta(days=2),
               location="Toronto")
    # Delivered but no pickup scan → not derivable.
    sid2 = _add_shipment(store, "1Z555", "ups")
    _add_event(store, sid2, Milestone.DELIVERED, NOW - timedelta(days=1),
               location="Ottawa")

    lanes = analytics.lane_stats(store)
    assert lanes == []


def test_lane_stats_unknown_location_when_unmapped(tmp_path):
    store = _store(tmp_path)
    sid = _add_shipment(store, "1Z666", "ups")
    start = NOW - timedelta(days=5)
    _add_event(store, sid, Milestone.PICKED_UP, start)  # no location
    _add_event(store, sid, Milestone.DELIVERED, start + timedelta(days=2),
               location="Calgary")
    lanes = analytics.lane_stats(store)
    assert len(lanes) == 1
    assert lanes[0]["origin"] == "unknown"
    assert lanes[0]["destination"] == "Calgary"


# ── ETA accuracy ────────────────────────────────────────────────────────────


def test_eta_accuracy_measures_late_and_early(tmp_path):
    store = _store(tmp_path)
    pickup = NOW - timedelta(days=10)
    delivery = pickup + timedelta(days=4)

    # 2 days late: promised before actual delivery.
    sid = _add_shipment(store, "1Z777", "ups")
    _add_event(store, sid, Milestone.PICKED_UP, pickup, location="Toronto")
    _add_event(store, sid, Milestone.DELIVERED, delivery, location="Vancouver")
    store.update_shipment(sid, eta=delivery - timedelta(days=2))

    # 1 day early.
    sid2 = _add_shipment(store, "1Z888", "ups")
    _add_event(store, sid2, Milestone.PICKED_UP, pickup, location="Toronto")
    _add_event(store, sid2, Milestone.DELIVERED, delivery, location="Calgary")
    store.update_shipment(sid2, eta=delivery + timedelta(days=1))

    acc = analytics.eta_accuracy(store)
    assert len(acc) == 1
    row = acc[0]
    assert row["carrier"] == "ups"
    assert row["samples"] == 2
    assert row["late"] == 1
    assert row["early"] == 0   # exactly -1.0 day is within the on-time band
    assert row["on_time"] == 1
    assert row["mean_delta_days"] == pytest.approx(0.5)


def test_eta_accuracy_ignores_active_or_eta_less_shipments(tmp_path):
    store = _store(tmp_path)
    # Delivered but no ETA → skipped.
    _delivered_lane(store, "1Z999", "ups", "Toronto", "Ottawa", 8, 2.0)
    # Active with ETA → skipped.
    sid = _add_shipment(store, "1Z100", "ups", eta=NOW + timedelta(days=1))
    _add_event(store, sid, Milestone.IN_TRANSIT, NOW - timedelta(days=1))
    assert analytics.eta_accuracy(store) == []


# ── live summary ────────────────────────────────────────────────────────────


def test_arriving_today_matches_eta_date_and_ofd(tmp_path):
    store = _store(tmp_path)
    # ETA later today → arriving.
    _add_shipment(store, "1Z201", "ups", eta=NOW + timedelta(hours=6),
                  label="Laptop stand")
    # ETA tomorrow → not arriving.
    _add_shipment(store, "1Z202", "ups", eta=NOW + timedelta(days=1))
    # No ETA but out for delivery → arriving.
    sid = _add_shipment(store, "1Z203", "fedex")
    _add_event(store, sid, Milestone.OUT_FOR_DELIVERY, NOW - timedelta(hours=2))
    # ETA today but already delivered → not arriving.
    _delivered_lane(store, "1Z204", "ups", "Toronto", "Ottawa", 3, 2.0,
                    eta=NOW + timedelta(hours=3))

    arriving = analytics.arriving_today(store, now=NOW)
    numbers = {a["id"] for a in arriving}
    assert len(arriving) == 2
    labels = {a["label"] for a in arriving}
    assert "Laptop stand" in labels
    assert any(a["milestone"] == "out_for_delivery" for a in arriving)
    assert len(numbers) == 2


def test_exceptions_lists_active_exception_shipments(tmp_path):
    store = _store(tmp_path)
    sid = _add_shipment(store, "1Z301", "ups", label="Monitor")
    _add_event(store, sid, Milestone.EXCEPTION, NOW - timedelta(hours=5))
    sid2 = _add_shipment(store, "1Z302", "dhl")
    _add_event(store, sid2, Milestone.FAILED_ATTEMPT, NOW - timedelta(hours=1))
    _add_shipment(store, "1Z303", "ups")  # plain active — not an exception

    exc = analytics.exceptions(store)
    assert len(exc) == 2
    assert {e["milestone"] for e in exc} == {"exception", "failed_attempt"}


def test_summary_shape_matches_api_contract(tmp_path):
    store = _store(tmp_path)
    _add_shipment(store, "1Z401", "ups", eta=NOW + timedelta(hours=2))
    _add_shipment(store, "1Z402", "ups")
    sid = _add_shipment(store, "1Z403", "dhl")
    _add_event(store, sid, Milestone.EXCEPTION, NOW - timedelta(hours=3))
    _delivered_lane(store, "1Z404", "ups", "Toronto", "Ottawa", 6, 2.0)

    s = analytics.summary(store, now=NOW)
    assert s == {"active": 3, "arriving_today": 1, "exceptions": 1}


def test_summary_empty_store(tmp_path):
    store = _store(tmp_path)
    assert analytics.summary(store, now=NOW) == {
        "active": 0,
        "arriving_today": 0,
        "exceptions": 0,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_report_runs_against_tmp_db(tmp_path, capsys):
    store = _store(tmp_path)
    _delivered_lane(store, "1Z501", "ups", "Toronto", "Vancouver", 9, 5.0,
                    eta=NOW - timedelta(days=4))
    rc = analytics.main(["--db", str(tmp_path / "qareen.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "arriving today" in out
    assert "ups: Toronto → Vancouver" in out


def test_cli_missing_db_is_soft_failure(tmp_path, capsys):
    rc = analytics.main(["--db", str(tmp_path / "nope.db")])
    assert rc == 1
    assert "nothing to report" in capsys.readouterr().err
