"""Tests for the Auto Tracker storage layer (qareen.tracking.store)
and migration 093.

All DBs live under tmp_path. The migration is additionally verified
against a COPY of the real ~/.aos/data/qareen.db (taken via the sqlite
backup API — safe against WAL, never touches the real file).
"""

import importlib.util
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking.models import Milestone, Shipment, TrackingEvent  # noqa: E402
from qareen.tracking.store import (  # noqa: E402
    AUTO_TRACKER_TABLES,
    FIRST_SEEN_WINDOW,
    RECYCLE_TIMESTAMP_GAP,
    ShipmentStore,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "infra" / "migrations" / "093_auto_tracker_init.py"
)
REAL_QAREEN_DB = Path.home() / ".aos" / "data" / "qareen.db"


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_093", str(MIGRATION_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def store(tmp_path):
    return ShipmentStore(tmp_path / "qareen.db")


def make_shipment(number="1Z999AA10123456784", carrier="ups", **kw):
    kw.setdefault("direction", "inbound")
    kw.setdefault("source", "manual")
    return Shipment(tracking_number=number, carrier=carrier, **kw)


def make_event(milestone, days_ago=0, description="scan", **kw):
    return TrackingEvent(
        milestone=milestone,
        description=description,
        timestamp=datetime.now() - timedelta(days=days_ago),
        **kw,
    )


# ---------------------------------------------------------------- schema


def test_ensure_tables_idempotent(tmp_path):
    db = tmp_path / "qareen.db"
    ShipmentStore(db)
    ShipmentStore(db)  # second init must not fail or drop data
    conn = sqlite3.connect(str(db))
    try:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    for table in AUTO_TRACKER_TABLES:
        assert table in existing


def test_wal_and_foreign_keys_enabled(store):
    conn = store._connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


# ------------------------------------------------------------- shipments


def test_upsert_shipment_create_then_merge(store):
    shp = make_shipment(merchant=None)
    sid, created = store.upsert_shipment(shp, category="Shopping")
    assert created is True

    got = store.get_shipment(sid)
    assert got.tracking_number == "1Z999AA10123456784"
    assert got.carrier == "ups"
    assert got.status == "active"
    assert got.milestone == Milestone.LABEL_CREATED
    assert store.get_shipment_row(sid)["category"] == "Shopping"
    # canonicalization: upserting the same number with spaces/lower merges
    dupe = make_shipment(
        number="1z 999 aa1 0123456784", merchant="Amazon", label="gift"
    )
    sid2, created2 = store.upsert_shipment(dupe)
    assert created2 is False
    assert sid2 == sid
    got = store.get_shipment(sid)
    assert got.merchant == "Amazon"
    assert got.label == "gift"
    # merge never regresses milestone/status
    assert got.status == "active"


def test_upsert_shipment_forks_when_terminal(store):
    sid, _ = store.upsert_shipment(make_shipment())
    store.append_event(sid, make_event(Milestone.DELIVERED, days_ago=30))
    assert store.get_shipment(sid).status == "delivered"

    # Carrier reissued the number: a new package with the same number must
    # get a NEW row, not merge into the delivered one.
    sid2, created = store.upsert_shipment(make_shipment(merchant="Amazon"))
    assert created is True
    assert sid2 != sid
    rows = store.shipments_for_number("ups", "1Z999AA10123456784")
    assert len(rows) == 2
    assert {r.status for r in rows} == {"active", "delivered"}


def test_upsert_shipment_forks_on_stale_active(store):
    sid, _ = store.upsert_shipment(make_shipment())
    stale = datetime.now() - FIRST_SEEN_WINDOW - timedelta(days=1)
    store.update_shipment(sid, first_seen=stale)
    sid2, created = store.upsert_shipment(make_shipment())
    assert created is True
    assert sid2 != sid


def test_update_shipment_allowlist(store):
    sid, _ = store.upsert_shipment(make_shipment())
    assert store.update_shipment(sid, next_poll_at=datetime.now()) is True
    assert store.get_shipment_row(sid)["next_poll_at"] is not None
    with pytest.raises(ValueError):
        store.update_shipment(sid, tracking_number="EVIL")
    assert store.update_shipment(sid) is False


# ---------------------------------------------------------------- events


def test_append_event_ordering_and_milestone(store):
    sid, _ = store.upsert_shipment(make_shipment())
    seqs = [
        store.append_event(sid, make_event(Milestone.LABEL_CREATED, days_ago=3)),
        store.append_event(sid, make_event(Milestone.PICKED_UP, days_ago=2)),
        store.append_event(sid, make_event(Milestone.IN_TRANSIT, days_ago=1)),
    ]
    assert seqs == [1, 2, 3]
    events = store.events_for(sid)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.milestone for e in events] == [
        Milestone.LABEL_CREATED,
        Milestone.PICKED_UP,
        Milestone.IN_TRANSIT,
    ]
    assert store.get_shipment(sid).milestone == Milestone.IN_TRANSIT


def test_append_event_raw_json_roundtrip(store):
    sid, _ = store.upsert_shipment(make_shipment())
    raw = {"carrierCode": "X1", "nested": {"a": [1, 2]}}
    store.append_event(sid, make_event(None, raw=raw))
    (event,) = store.events_for(sid)
    assert event.raw == raw
    assert event.milestone is None  # unmapped codes keep raw, never guessed


def test_ingest_events_forks_on_recycled_number(store):
    """Delivered shipment, then the carrier reissues the number: a new
    origin scan after delivery must fork a new shipment row."""
    sid, _ = store.upsert_shipment(make_shipment())
    store.ingest_events(
        sid,
        [
            make_event(Milestone.PICKED_UP, days_ago=40),
            make_event(Milestone.DELIVERED, days_ago=30),
        ],
    )
    assert store.get_shipment(sid).status == "delivered"

    new_id, forked = store.ingest_events(
        sid, [make_event(Milestone.LABEL_CREATED, days_ago=0)]
    )
    assert forked is True
    assert new_id != sid
    # Old row untouched: still delivered, still exactly its own events.
    assert store.get_shipment(sid).status == "delivered"
    assert len(store.events_for(sid)) == 2
    # New row is active and carries only the recycled journey's events.
    new = store.get_shipment(new_id)
    assert new.status == "active"
    assert new.milestone == Milestone.LABEL_CREATED
    assert len(store.events_for(new_id)) == 1


def test_ingest_events_forks_on_timestamp_gap(store):
    """No origin milestone, but an event timestamped long after the last
    scan of a terminal shipment still signals a recycled number."""
    sid, _ = store.upsert_shipment(make_shipment())
    store.ingest_events(sid, [make_event(Milestone.DELIVERED, days_ago=60)])
    gap_event = make_event(Milestone.IN_TRANSIT, days_ago=0)
    new_id, forked = store.ingest_events(sid, [gap_event])
    assert forked is True
    assert new_id != sid

    # Within the gap, a late-arriving event merges into the old journey.
    sid3, _ = store.upsert_shipment(make_shipment(number="1Z late merge"))
    store.ingest_events(sid3, [make_event(Milestone.DELIVERED, days_ago=3)])
    late = make_event(
        Milestone.IN_TRANSIT,
        days_ago=RECYCLE_TIMESTAMP_GAP.days - 1,
        description="late sync",
    )
    same_id, forked = store.ingest_events(sid3, [late])
    assert forked is False
    assert same_id == sid3
    assert len(store.events_for(sid3)) == 2


def test_ingest_events_active_shipment_never_forks(store):
    sid, _ = store.upsert_shipment(make_shipment())
    same, forked = store.ingest_events(
        sid,
        [make_event(Milestone.PICKED_UP), make_event(Milestone.IN_TRANSIT)],
    )
    assert forked is False
    assert same == sid
    assert len(store.events_for(sid)) == 2


# ---------------------------------------------------------------- numbers


def test_numbers_add_and_link(store):
    sid, _ = store.upsert_shipment(make_shipment())
    # primary number registered at insert
    assert store.link_number("ups", "1Z999AA10123456784") == sid
    store.add_number(sid, "9400 1000 0000 0000 0000 00", carrier="usps")
    # canonical lookup: spacing/case differences resolve to the same number
    assert store.link_number("usps", "9400100000000000000000") == sid
    numbers = store.numbers_for(sid)
    assert {(n["carrier"], n["role"]) for n in numbers} == {
        ("ups", "primary"),
        ("usps", "handoff"),
    }
    # idempotent re-add of the same canonical number
    store.add_number(sid, "9400100000000000000000", carrier="usps")
    assert len(store.numbers_for(sid)) == 2
    assert store.link_number("usps", "unknown") is None


# ----------------------------------------------------------------- orders


def test_orders_upsert_items_and_links(store):
    items = [
        {"name": "USB-C cable", "qty": 2, "price": 9.99, "sku": "CBL-1"},
        {"name": "Charger", "qty": 1, "price": 24.50},
    ]
    oid = store.upsert_order(
        order_number="111-2222222-3333333",
        merchant="Amazon",
        merchant_domain="amazon.ca",
        total=44.48,
        currency="CAD",
        items=items,
    )
    order = store.get_order(oid)
    assert order["merchant"] == "Amazon"
    assert [i["name"] for i in order["items"]] == ["USB-C cable", "Charger"]
    assert order["items"][0]["qty"] == 2

    # Re-parse replaces items; same (domain, number) does not duplicate.
    oid2 = store.upsert_order(
        order_number="111-2222222-3333333",
        merchant_domain="amazon.ca",
        items=[{"name": "USB-C cable", "qty": 2}],
    )
    assert oid2 == oid
    order = store.get_order(oid)
    assert [i["name"] for i in order["items"]] == ["USB-C cable"]

    # N:M shipment links
    sid1, _ = store.upsert_shipment(make_shipment())
    sid2, _ = store.upsert_shipment(make_shipment(number="1Z other pkg"))
    store.link_shipment_order(sid1, oid)
    store.link_shipment_order(sid2, oid)
    store.link_shipment_order(sid1, oid)  # idempotent
    assert {s.id for s in store.shipments_for_order(oid)} == {sid1, sid2}
    assert [o["id"] for o in store.orders_for_shipment(sid1)] == [oid]


# ------------------------------------------------------------- candidates


def test_candidate_lifecycle(store):
    cid = store.enqueue_candidate(
        {"number": "1Z999AA10123456784", "carrier": "ups"},
        layer="regex",
        confidence=0.62,
    )
    store.enqueue_candidate({"number": "TBA123"}, layer="llm", confidence=0.4)

    pending = store.peek_candidates()
    assert len(pending) == 2
    assert pending[0]["id"] == cid
    assert pending[0]["candidate"]["carrier"] == "ups"
    assert pending[0]["resolved_at"] is None

    assert store.resolve_candidate(cid, "confirmed") is True
    assert store.resolve_candidate(cid, "rejected") is False  # already resolved
    with pytest.raises(ValueError):
        store.resolve_candidate(cid, "maybe")

    pending = store.peek_candidates()
    assert len(pending) == 1
    confirmed = store.peek_candidates(status="confirmed")
    assert len(confirmed) == 1
    assert confirmed[0]["resolved_at"] is not None


def test_eval_labels_roundtrip(store):
    store.record_eval_label({"number": "X"}, layer="regex", predicted="ups", label="ups")
    rows = store.eval_labels()
    assert len(rows) == 1
    assert rows[0]["layer"] == "regex"
    assert rows[0]["label"] == "ups"


# ----------------------------------------------------------------- priors


def test_priors_math(store):
    assert store.get_prior("domain", "digikey.com", "ups")["rate"] is None
    store.record_prior("domain", "digikey.com", "ups", hit=True)
    store.record_prior("domain", "digikey.com", "ups", hit=True)
    store.record_prior("domain", "digikey.com", "ups", hit=False)
    store.record_prior("domain", "digikey.com", "fedex", hit=True)
    p = store.get_prior("domain", "digikey.com", "ups")
    assert (p["hits"], p["misses"]) == (2, 1)
    assert abs(p["rate"] - 2 / 3) < 1e-9

    ranked = store.priors_for("domain", "digikey.com")
    assert [r["carrier"] for r in ranked] == ["fedex", "ups"]  # 1.0 > 0.66
    with pytest.raises(ValueError):
        store.record_prior("ip", "digikey.com", "ups", hit=True)


# ------------------------------------------------------------ domain rules


def test_domain_rules_crud(store):
    store.set_domain_rule("Amazon.CA ", category="Shopping", display_name="Amazon")
    rule = store.get_domain_rule("amazon.ca")
    assert rule["category"] == "Shopping"
    assert rule["display_name"] == "Amazon"

    store.set_domain_rule("amazon.ca", category="Shopping/Household")
    assert store.get_domain_rule("amazon.ca")["category"] == "Shopping/Household"
    assert len(store.list_domain_rules()) == 1

    store.set_domain_rule("digikey.com", category="Business/Electronics")
    assert [r["domain"] for r in store.list_domain_rules()] == [
        "amazon.ca",
        "digikey.com",
    ]
    assert store.delete_domain_rule("amazon.ca") is True
    assert store.get_domain_rule("amazon.ca") is None
    assert store.delete_domain_rule("amazon.ca") is False


# ------------------------------------------------------------ due_shipments


def test_due_shipments(store):
    now = datetime.now()
    due_id, _ = store.upsert_shipment(make_shipment())
    future_id, _ = store.upsert_shipment(make_shipment(number="1Z future"))
    unscheduled_id, _ = store.upsert_shipment(make_shipment(number="1Z unsched"))
    returned_id, _ = store.upsert_shipment(make_shipment(number="1Z returned"))

    store.update_shipment(due_id, next_poll_at=now - timedelta(minutes=5))
    store.update_shipment(future_id, next_poll_at=now + timedelta(hours=1))
    store.update_shipment(returned_id, next_poll_at=now - timedelta(minutes=5))
    store.append_event(returned_id, make_event(Milestone.RETURNED))

    due = store.due_shipments(now)
    ids = [d["id"] for d in due]
    assert ids == [due_id]
    assert unscheduled_id not in ids  # next_poll_at NULL
    # archived/delivered rows are never due
    store.update_shipment(due_id, status="archived")
    assert store.due_shipments(now) == []


# ------------------------------------------------------------------ state


def test_state_get_set(store):
    assert store.get_state("watermark:email") is None
    assert store.get_state("watermark:email", default="0") == "0"
    store.set_state("watermark:email", "2026-07-01T00:00:00")
    assert store.get_state("watermark:email") == "2026-07-01T00:00:00"
    store.set_state("watermark:email", "2026-07-02T00:00:00")  # upsert
    assert store.get_state("watermark:email") == "2026-07-02T00:00:00"
    store.set_state("quota:dhl:exhausted_until", "2026-07-25T00:00:00")
    store.set_state("quota:dhl:exhausted_until", None)  # None deletes
    assert store.get_state("quota:dhl:exhausted_until") is None


# --------------------------------------------------------------- migration


def test_migration_applies_and_is_idempotent(tmp_path):
    mig = load_migration()
    db = tmp_path / "qareen.db"
    assert mig.check(db_path=db) is False
    assert mig.up(db_path=db) is True
    assert mig.check(db_path=db) is True
    assert mig.up(db_path=db) is True  # re-run is a no-op, still fine
    # schema matches the store's: a store opened on the migrated DB works
    s = ShipmentStore(db)
    sid, created = s.upsert_shipment(make_shipment())
    assert created is True
    assert s.get_shipment(sid).carrier == "ups"


def test_migration_check_missing_db(tmp_path):
    mig = load_migration()
    assert mig.check(db_path=tmp_path / "nope.db") is False


@pytest.mark.skipif(not REAL_QAREEN_DB.exists(), reason="no real qareen.db")
def test_migration_on_copy_of_real_db(tmp_path):
    """Apply 093 to a COPY of the live qareen.db (sqlite backup — WAL-safe;
    the real DB is never opened for writing)."""
    mig = load_migration()
    copy = tmp_path / "qareen-copy.db"
    src = sqlite3.connect("file:%s?mode=ro" % REAL_QAREEN_DB, uri=True)
    try:
        dst = sqlite3.connect(str(copy))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    assert mig.up(db_path=copy) is True
    assert mig.check(db_path=copy) is True
    # Pre-existing data survived and the new tables are usable.
    s = ShipmentStore(copy)
    sid, _ = s.upsert_shipment(make_shipment())
    assert s.get_shipment(sid) is not None
