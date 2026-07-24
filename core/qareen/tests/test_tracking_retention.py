"""Tests for qareen.tracking.retention — per-pack retention windows,
dry-run vs --apply, the audit-trail record, and the guarantee that
carriers without a retention window are never touched. All stores live
under tmp_path; the real ~/.aos/data/qareen.db is never opened.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import retention  # noqa: E402
from qareen.tracking.models import Milestone, Shipment, TrackingEvent  # noqa: E402
from qareen.tracking.store import ShipmentStore  # noqa: E402

NOW = datetime(2026, 7, 24, 12, 0, 0)
WINDOWS = {"dhl": 30}  # DHL legal terms; injected so tests don't read packs


def _store(tmp_path):
    return ShipmentStore(tmp_path / "qareen.db")


def _delivered(store, number, carrier, delivered_days_ago, events=3):
    sid, _ = store.upsert_shipment(Shipment(tracking_number=number,
                                            carrier=carrier))
    delivered = NOW - timedelta(days=delivered_days_ago)
    store.append_event(sid, TrackingEvent(
        milestone=Milestone.LABEL_CREATED, description="label",
        timestamp=delivered - timedelta(days=3),
        fetched_at=delivered - timedelta(days=3)))
    store.append_event(sid, TrackingEvent(
        milestone=Milestone.IN_TRANSIT, description="moving",
        timestamp=delivered - timedelta(days=1),
        fetched_at=delivered - timedelta(days=1)))
    store.append_event(sid, TrackingEvent(
        milestone=Milestone.DELIVERED, description="delivered",
        timestamp=delivered, fetched_at=delivered))
    assert len(store.events_for(sid)) == events
    return sid


def test_windows_from_shorthand_and_pack_objects(tmp_path):
    assert retention.retention_windows({"dhl": 30, "ups": None}) == {"dhl": 30}
    assert retention.retention_windows({"dhl": 0, "fedex": -1}) == {}
    assert retention.retention_windows(
        {"x": {"delete_days_after_delivery": 14}}) == {"x": 14}


def test_dry_run_reports_without_deleting(tmp_path):
    store = _store(tmp_path)
    old = _delivered(store, "JD0001", "dhl", delivered_days_ago=45)

    report = retention.purge(store, WINDOWS, apply=False, now=NOW)
    assert report["applied"] is False
    assert [p["id"] for p in report["purgeable"]] == [old]
    assert report["purgeable"][0]["event_count"] == 3

    # Nothing touched: shipment + events still there, no audit record.
    assert store.get_shipment(old) is not None
    assert len(store.events_for(old)) == 3
    assert store.get_state(report["audit_key"]) is None


def test_apply_purges_events_and_shipment_with_audit(tmp_path):
    store = _store(tmp_path)
    old = _delivered(store, "JD0002", "dhl", delivered_days_ago=40)

    report = retention.purge(store, WINDOWS, apply=True, now=NOW)
    assert report["applied"] is True
    assert report["purged_shipments"] == 1
    assert report["purged_events"] == 3

    assert store.get_shipment(old) is None
    assert store.events_for(old) == []
    assert store.numbers_for(old) == []

    audit = store.get_state(report["audit_key"])
    assert audit is not None
    record = json.loads(audit)
    assert report["audit_key"].startswith("retention.audit.")
    assert record["purged_shipments"] == 1
    assert record["purged_events"] == 3
    assert record["shipment_ids"] == [old]
    assert record["detail"][0]["carrier"] == "dhl"
    assert record["windows"] == WINDOWS


def test_recent_delivery_within_window_is_kept(tmp_path):
    store = _store(tmp_path)
    recent = _delivered(store, "JD0003", "dhl", delivered_days_ago=10)
    report = retention.purge(store, WINDOWS, apply=True, now=NOW)
    assert report["purgeable"] == []
    assert report["purged_shipments"] == 0
    assert store.get_shipment(recent) is not None
    assert len(store.events_for(recent)) == 3


def test_carriers_without_window_never_touched(tmp_path):
    store = _store(tmp_path)
    ups = _delivered(store, "1Z999", "ups", delivered_days_ago=365)
    cpost = _delivered(store, "CP123", "canadapost", delivered_days_ago=200)

    report = retention.purge(store, WINDOWS, apply=True, now=NOW)
    assert report["purgeable"] == []
    for sid in (ups, cpost):
        assert store.get_shipment(sid) is not None
        assert len(store.events_for(sid)) == 3


def test_active_and_archived_shipments_not_purged(tmp_path):
    store = _store(tmp_path)
    active_sid, _ = store.upsert_shipment(
        Shipment(tracking_number="JD0004", carrier="dhl"))
    store.append_event(active_sid, TrackingEvent(
        milestone=Milestone.IN_TRANSIT, description="moving",
        timestamp=NOW - timedelta(days=60),
        fetched_at=NOW - timedelta(days=60)))
    # Archived shipment: out of the delivered pipeline entirely.
    archived_sid, _ = store.upsert_shipment(
        Shipment(tracking_number="JD0005", carrier="dhl", status="archived"))

    report = retention.purge(store, WINDOWS, apply=True, now=NOW)
    assert report["purgeable"] == []
    assert store.get_shipment(active_sid) is not None
    assert store.get_shipment(archived_sid) is not None


def test_empty_windows_is_noop(tmp_path):
    store = _store(tmp_path)
    _delivered(store, "JD0006", "dhl", delivered_days_ago=90)
    report = retention.purge(store, {}, apply=True, now=NOW)
    assert report["applied"] is False
    assert report["purgeable"] == []


def test_real_dhl_pack_declares_30_day_window():
    """Guard the manifest the whole feature hangs on (DHL terms)."""
    windows = retention.retention_windows()  # loads the real packs
    assert windows.get("dhl") == 30
    # No other shipped pack may mandate deletion without a conscious edit.
    assert set(windows) == {"dhl"}


def test_cli_dry_run_then_apply(tmp_path, capsys):
    store = _store(tmp_path)
    old = _delivered(store, "JD0007", "dhl", delivered_days_ago=50)
    db = str(tmp_path / "qareen.db")

    # CLI loads the real packs (dhl=30) — dry-run first.
    rc = retention.main(["--db", db])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "JD0007" in out
    assert store.get_shipment(old) is not None

    rc = retention.main(["--db", db, "--apply"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "APPLIED" in out
    assert store.get_shipment(old) is None


def test_cli_missing_db_is_soft_failure(tmp_path, capsys):
    rc = retention.main(["--db", str(tmp_path / "nope.db")])
    assert rc == 1
    assert "nothing to enforce" in capsys.readouterr().err
