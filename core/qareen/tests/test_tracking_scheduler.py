"""Tests for the due-queue scheduler and the milestone notifier.

All tests use an injected clock (explicit ``now``), injected transports,
fake stores, and a tmp-path qareen.db — no network, no Keychain, no real
timing.
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking.client import (  # noqa: E402
    CarrierAuthError,
    RateLimited,
    TransportResponse,
)
from qareen.tracking.models import Milestone  # noqa: E402
from qareen.tracking.notify import Notifier  # noqa: E402
from qareen.tracking.packs import CarrierPack  # noqa: E402
from qareen.tracking.scheduler import LOCK_KEY, TrackingScheduler  # noqa: E402

NOW = datetime(2026, 7, 24, 12, 0, 0)


# ── fakes ─────────────────────────────────────────────────────────────────


def _pack(slug="ups", per_day=10, min_interval=0):
    manifest = {
        "display_name": slug.title(),
        "auth": {"model": "none"},
        "endpoints": {"base": "https://api.test", "track": "https://api.test/t/{number}"},
        "tracking": {"patterns": [], "check_digit": None},
        "capabilities": {"edd": True, "pod": False, "push": False},
        "status_map": {
            "IT": "in_transit",
            "OFD": "out_for_delivery",
            "DL": "delivered",
            "EX": "exception",
        },
        "response_map": {
            "events": "$.events[*]",
            "eta": "$.eta",
            "event_fields": {"code": "$.code", "description": "$.desc", "location": "$.city"},
        },
        "rate_limits": {"requests_per_day": per_day, "min_interval_seconds": min_interval},
        "retention": {"delete_days_after_delivery": None},
    }
    return CarrierPack(slug=slug, path=Path("."), manifest=manifest)


def _shipment(ship_id="s1", carrier="ups", milestone="in_transit", **overrides):
    shipment = {
        "id": ship_id,
        "tracking_number": "1ZTEST%s" % ship_id,
        "carrier": carrier,
        "milestone": milestone,
        "status": "active",
        "category": "Business/Electronics",
        "merchant": "DigiKey",
        "label": None,
        "next_poll_at": NOW.isoformat(),  # due
    }
    shipment.update(overrides)
    return shipment


class FakeStore:
    """Duck-typed tracking store per scheduler's documented protocol."""

    def __init__(self, shipments=None, preferences=None):
        self.shipments = list(shipments or [])
        self.events = []
        self.state = {}
        self.preferences = dict(preferences or {})

    def due_shipments(self, now_iso, limit):
        due = [
            s
            for s in self.shipments
            if s.get("status") == "active"
            and s.get("next_poll_at")
            and s["next_poll_at"] <= now_iso
        ]
        return due[:limit]

    def append_event(self, shipment_id, event):
        self.events.append((shipment_id, event))

    def upsert_shipment(self, shipment_id, fields):
        for s in self.shipments:
            if s["id"] == shipment_id:
                s.update(fields)

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value

    def category_preference(self, category):
        return self.preferences.get(category)


class FakeClient:
    """Stands in for CarrierClient: returns canned payloads or raises."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def track(self, number):
        self.calls.append(number)
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


def _scheduler(store, clients, notifier=None, owner="test-owner", packs=None):
    packs = packs or {"ups": _pack("ups")}
    return TrackingScheduler(
        store,
        packs,
        client_factory=lambda pack: clients[pack.slug],
        notifier=notifier,
        owner=owner,
    )


def _events_payload(*codes):
    return {"events": [{"code": c, "desc": "scan %s" % c, "city": "Mississauga"} for c in codes]}


class FakeNotifier:
    def __init__(self):
        self.nudges = []
        self.alerts = []

    def notify_milestone(self, shipment, milestone, event=None):
        self.nudges.append((shipment["id"], milestone))
        return True

    def alert(self, title, body):
        self.alerts.append((title, body))
        return True


# ── due queue + cadence ───────────────────────────────────────────────────


def test_only_due_shipments_are_polled():
    due = _shipment("s1")
    future = _shipment("s2", next_poll_at=(NOW + timedelta(hours=3)).isoformat())
    store = FakeStore([due, future])
    client = FakeClient(_events_payload("IT"))
    _scheduler(store, {"ups": client}).run_once(NOW)

    assert client.calls == ["1ZTESTs1"]
    assert store.shipments[1]["next_poll_at"] == future["next_poll_at"]


def test_cadence_per_milestone():
    cases = [
        ("label_created", _events_payload("IT"), 5 * 3600),  # IT scan → in_transit cadence
        ("in_transit", _events_payload("IT"), 5 * 3600),  # no transition
        ("out_for_delivery", _events_payload("OFD"), 1 * 3600),  # hourly
    ]
    for milestone, payload, expected_interval in cases:
        store = FakeStore([_shipment("s1", milestone=milestone)])
        _scheduler(store, {"ups": FakeClient(payload)}).run_once(NOW)
        next_poll = datetime.fromisoformat(store.shipments[0]["next_poll_at"])
        assert (next_poll - NOW).total_seconds() == expected_interval, milestone


def test_events_are_appended_and_eta_stored():
    store = FakeStore([_shipment("s1")])
    payload = _events_payload("IT", "OFD")
    payload["eta"] = "2026-07-25"
    _scheduler(store, {"ups": FakeClient(payload)}).run_once(NOW)

    assert len(store.events) == 2
    assert all(ship_id == "s1" for ship_id, _ in store.events)
    assert store.events[0][1].fetched_at == NOW
    assert store.shipments[0]["eta"] == "2026-07-25T00:00:00"


def test_delivered_stops_polling_and_auto_archives():
    store = FakeStore([_shipment("s1", milestone="out_for_delivery")])
    notifier = FakeNotifier()
    report = _scheduler(
        store, {"ups": FakeClient(_events_payload("OFD", "DL"))}, notifier=notifier
    ).run_once(NOW)

    shipment = store.shipments[0]
    assert shipment["milestone"] == "delivered"
    assert shipment["status"] == "archived"
    assert shipment["next_poll_at"] is None
    assert report["archived"] == 1
    # archived → never due again
    assert store.due_shipments((NOW + timedelta(days=30)).isoformat(), 50) == []


def test_milestone_transition_triggers_notify():
    store = FakeStore([_shipment("s1", milestone="in_transit")])
    notifier = FakeNotifier()
    _scheduler(
        store, {"ups": FakeClient(_events_payload("IT", "OFD"))}, notifier=notifier
    ).run_once(NOW)
    assert notifier.nudges == [("s1", Milestone.OUT_FOR_DELIVERY)]


def test_no_transition_no_notify():
    store = FakeStore([_shipment("s1", milestone="in_transit")])
    notifier = FakeNotifier()
    _scheduler(
        store, {"ups": FakeClient(_events_payload("IT"))}, notifier=notifier
    ).run_once(NOW)
    assert notifier.nudges == []


# ── budgets: token bucket, 429 backoff, quota marker ──────────────────────


def test_daily_bucket_exhaustion_skips_and_persists_marker():
    shipments = [_shipment("s1"), _shipment("s2")]
    store = FakeStore(shipments)
    client = FakeClient(_events_payload("IT"))
    packs = {"ups": _pack("ups", per_day=1)}  # one call per day only
    report = _scheduler(store, {"ups": client}, packs=packs).run_once(NOW)

    assert client.calls == ["1ZTESTs1"]  # second shipment skipped
    assert report["skipped_quota"] == ["ups"]
    assert "quota:ups:exhausted_until" in store.state
    # second shipment rescheduled to next midnight
    assert store.shipments[1]["next_poll_at"] > NOW.isoformat()

    # a later run the same day: marker still blocks, no HTTP at all
    later = NOW + timedelta(hours=2)
    store.shipments[0]["next_poll_at"] = later.isoformat()
    store.shipments[0]["status"] = "active"
    client.calls.clear()
    _scheduler(store, {"ups": client}, packs=packs).run_once(later)
    assert client.calls == []


def test_429_trips_backoff_and_persists_quota_marker():
    store = FakeStore([_shipment("s1"), _shipment("s2")])
    client = FakeClient(RateLimited(retry_after=600))
    report = _scheduler(store, {"ups": client}).run_once(NOW)

    assert report["rate_limited"] == ["ups"]
    assert len(client.calls) == 1  # second shipment skipped after the 429
    until = float(store.state["quota:ups:exhausted_until"])
    # scheduler stores calendar.timegm on naive-UTC; compare via parsed state
    marker_dt = datetime.utcfromtimestamp(until)
    assert abs((marker_dt - (NOW + timedelta(seconds=600))).total_seconds()) < 2
    # shipment rescheduled past the marker
    assert store.shipments[0]["next_poll_at"] == marker_dt.isoformat()

    # next run inside the backoff window: no HTTP call
    inside = NOW + timedelta(seconds=300)
    store.shipments[0]["next_poll_at"] = inside.isoformat()
    client.calls.clear()
    client.behavior = _events_payload("IT")
    _scheduler(store, {"ups": client}).run_once(inside)
    assert client.calls == []


def test_429_without_retry_after_uses_exponential_backoff():
    store = FakeStore([_shipment("s1")])
    client = FakeClient(RateLimited())
    _scheduler(store, {"ups": client}).run_once(NOW)
    first_until = datetime.utcfromtimestamp(
        float(store.state["quota:ups:exhausted_until"])
    )
    assert abs((first_until - (NOW + timedelta(seconds=300))).total_seconds()) < 2
    assert json.loads(store.state["backoff:ups"])["count"] == 1

    # second consecutive 429 doubles the delay
    later = first_until + timedelta(seconds=1)
    store.shipments[0]["next_poll_at"] = later.isoformat()
    _scheduler(store, {"ups": client}).run_once(later)
    second_until = datetime.utcfromtimestamp(
        float(store.state["quota:ups:exhausted_until"])
    )
    assert abs((second_until - (later + timedelta(seconds=600))).total_seconds()) < 2
    assert json.loads(store.state["backoff:ups"])["count"] == 2


def test_min_interval_pacer_defers_second_call():
    shipments = [_shipment("s1"), _shipment("s2")]
    store = FakeStore(shipments)
    client = FakeClient(_events_payload("IT"))
    packs = {"ups": _pack("ups", per_day=100, min_interval=3600)}
    _scheduler(store, {"ups": client}, packs=packs).run_once(NOW)
    assert client.calls == ["1ZTESTs1"]  # second paced out, still due


# ── singleton lock ────────────────────────────────────────────────────────


def test_singleton_lock_blocks_second_poller():
    store = FakeStore([_shipment("s1")])
    epoch = float(__import__("calendar").timegm(NOW.utctimetuple()))
    store.set_state(LOCK_KEY, json.dumps({"owner": "other-host:999", "at": epoch}))
    client = FakeClient(_events_payload("IT"))

    report = _scheduler(store, {"ups": client}, owner="me:1").run_once(NOW)

    assert report["locked"] is True
    assert client.calls == []


def test_stale_lock_is_taken_over():
    store = FakeStore([_shipment("s1")])
    import calendar

    stale_epoch = float(calendar.timegm((NOW - timedelta(hours=1)).utctimetuple()))
    store.set_state(LOCK_KEY, json.dumps({"owner": "dead-host:1", "at": stale_epoch}))
    client = FakeClient(_events_payload("IT"))

    report = _scheduler(store, {"ups": client}, owner="me:1").run_once(NOW)

    assert report["locked"] is False
    assert client.calls == ["1ZTESTs1"]
    # lock released at the end of the run
    assert store.get_state(LOCK_KEY) == ""


# ── probe budget + circuit breaker ────────────────────────────────────────


def test_probe_budget_is_separate_and_bounded():
    store = FakeStore()
    sched = _scheduler(store, {}, packs={"ups": _pack("ups", per_day=30)})
    assert sched.probe_budget("ups") == 3
    for _ in range(3):
        assert sched.probe_allow("ups", NOW) is True
        sched.record_probe("ups", ok=True, now=NOW)
    assert sched.probe_allow("ups", NOW) is False


def test_probe_circuit_breaker_opens_after_failures():
    store = FakeStore()
    sched = _scheduler(store, {}, packs={"ups": _pack("ups", per_day=100)})
    for i in range(5):
        assert sched.probe_allow("ups", NOW) is True
        sched.record_probe("ups", ok=False, now=NOW)
    # breaker open: budget remains but probes are blocked
    assert sched.probe_allow("ups", NOW) is False
    # still open an hour minus a second later; closed after the window
    assert sched.probe_allow("ups", NOW + timedelta(seconds=3599)) is False
    assert sched.probe_allow("ups", NOW + timedelta(seconds=3601)) is True


# ── error handling ────────────────────────────────────────────────────────


def test_poll_error_reschedules_and_continues():
    good = _shipment("s1")
    bad = _shipment("s2")
    store = FakeStore([good, bad])
    # one client that fails on the second call
    calls = []

    class FlakyClient:
        def track(self, number):
            calls.append(number)
            if number.endswith("s2"):
                raise CarrierAuthError(401, "bad key")
            return _events_payload("IT")

    notifier = FakeNotifier()
    report = _scheduler(store, {"ups": FlakyClient()}, notifier=notifier).run_once(NOW)

    assert len(calls) == 2  # one failure didn't kill the run
    assert report["auth_errors"]
    assert notifier.alerts  # auth rejection surfaced
    # failed shipment rescheduled into the future
    assert store.shipments[1]["next_poll_at"] > NOW.isoformat()


# ── notifier ──────────────────────────────────────────────────────────────


class FakeTelegram:
    def __init__(self, status=200):
        self.sent = []
        self.status = status

    def __call__(self, method, url, headers, body):
        payload = json.loads(body.decode())
        self.sent.append(payload["text"])
        return TransportResponse(status=self.status, body=b"{}")


def _notifier(tmp_path, store=None, telegram=None, secrets=None, bus=None):
    return Notifier(
        db_path=tmp_path / "qareen.db",
        secret_getter=secrets or (lambda name: {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "42"}.get(name)),
        telegram_transport=telegram or FakeTelegram(),
        store=store,
        bus=bus,
    )


def _db_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notifications'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT type, title, priority, channels FROM notifications"
        ).fetchall()
    finally:
        conn.close()
    return rows


def test_notify_loud_category_sends_both_channels(tmp_path):
    telegram = FakeTelegram()
    n = _notifier(tmp_path, telegram=telegram)
    sent = n.notify_milestone(_shipment("s1", category="Business/Electronics"), Milestone.DELIVERED)

    assert sent is True
    assert len(telegram.sent) == 1
    assert "Delivered" in telegram.sent[0]
    rows = _db_rows(tmp_path / "qareen.db")
    assert len(rows) == 1
    assert rows[0][0] == "shipment.delivered"
    assert json.loads(rows[0][3]) == ["app", "telegram"]


def test_notify_uncategorized_is_silent_no_telegram(tmp_path):
    telegram = FakeTelegram()
    n = _notifier(tmp_path, telegram=telegram)
    sent = n.notify_milestone(_shipment("s1", category=None), Milestone.DELIVERED)

    assert sent is True  # dashboard insert still happened
    assert telegram.sent == []  # but no push
    rows = _db_rows(tmp_path / "qareen.db")
    assert len(rows) == 1
    assert json.loads(rows[0][3]) == ["app"]


def test_notify_store_preference_overrides_defaults(tmp_path):
    telegram = FakeTelegram()
    store = FakeStore(preferences={"shopping": "loud"})
    n = _notifier(tmp_path, store=store, telegram=telegram)
    n.notify_milestone(_shipment("s1", category="Shopping/Amazon"), Milestone.OUT_FOR_DELIVERY)
    assert len(telegram.sent) == 1


def test_notify_dedup_same_milestone(tmp_path):
    telegram = FakeTelegram()
    n = _notifier(tmp_path, telegram=telegram)
    ship = _shipment("s1")
    assert n.notify_milestone(ship, Milestone.DELIVERED) is True
    assert n.notify_milestone(ship, Milestone.DELIVERED) is False  # deduped
    assert len(telegram.sent) == 1
    assert len(_db_rows(tmp_path / "qareen.db")) == 1


def test_notify_dedup_survives_restart_via_store(tmp_path):
    store = FakeStore()
    telegram = FakeTelegram()
    n1 = _notifier(tmp_path, store=store, telegram=telegram)
    n1.notify_milestone(_shipment("s1"), Milestone.DELIVERED)
    # "restart": fresh Notifier, same store → still deduped
    n2 = _notifier(tmp_path, store=store, telegram=telegram)
    assert n2.notify_milestone(_shipment("s1"), Milestone.DELIVERED) is False
    assert len(telegram.sent) == 1


def test_notify_non_nudge_milestone_is_ignored(tmp_path):
    telegram = FakeTelegram()
    n = _notifier(tmp_path, telegram=telegram)
    assert n.notify_milestone(_shipment("s1"), Milestone.IN_TRANSIT) is False
    assert telegram.sent == []
    assert _db_rows(tmp_path / "qareen.db") == []


def test_notify_without_telegram_credentials_still_inserts(tmp_path):
    telegram = FakeTelegram()
    n = _notifier(tmp_path, secrets=lambda name: None, telegram=telegram)
    sent = n.notify_milestone(_shipment("s1"), Milestone.DELIVERED)
    assert sent is True  # graceful skip of telegram, dashboard row written
    assert telegram.sent == []
    assert len(_db_rows(tmp_path / "qareen.db")) == 1


def test_notify_emits_on_event_bus(tmp_path):
    emitted = []

    class FakeBus:
        async def emit(self, event):
            emitted.append((event.event_type, event.payload))

    n = _notifier(tmp_path, bus=FakeBus())
    n.notify_milestone(_shipment("s1"), Milestone.DELIVERED)
    # standalone context: coroutine driven via asyncio.run inside Notifier
    assert emitted and emitted[0][0] == "shipment.milestone"
    assert emitted[0][1]["milestone"] == "delivered"


def test_alert_is_always_loud(tmp_path):
    telegram = FakeTelegram()
    n = _notifier(tmp_path, telegram=telegram)
    n.alert("Tracker auth failed: ups", "details here")
    assert telegram.sent and "ups" in telegram.sent[0]
    rows = _db_rows(tmp_path / "qareen.db")
    assert rows[0][2] == "urgent"


# ── integration: real ShipmentStore (seam regression) ──────────────────────


def test_run_once_against_real_shipment_store(tmp_path):
    """run_once must work against the REAL ShipmentStore (tmp db), not just
    duck-typed fakes — this is the regression test for the live TypeError
    (due_shipments limit) and the upsert_shipment/update_shipment mixup."""
    from qareen.tracking.models import Shipment
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    ship_id, created = store.upsert_shipment(
        Shipment(tracking_number="1ZTESTREAL1", carrier="ups", source="manual"),
        next_poll_at=NOW,
    )
    assert created

    payload = {
        "events": [
            {
                "code": "IT",
                "desc": "Departed facility — tendered to USPS 9400100000000000000012",
                "city": "Mississauga",
            },
            {"code": "OFD", "desc": "Out for delivery", "city": "Mississauga"},
        ],
        "eta": "2026-07-25",
    }
    client = FakeClient(payload)
    report = _scheduler(store, {"ups": client}).run_once(NOW)

    assert report["polled"] == 1
    assert report["errors"] == []
    row = store.get_shipment_row(ship_id)
    assert row["milestone"] == "out_for_delivery"
    assert row["status"] == "active"
    assert row["eta"] == "2026-07-25T00:00:00"
    # out_for_delivery cadence: rescheduled one hour out
    next_poll = datetime.fromisoformat(row["next_poll_at"])
    assert (next_poll - NOW).total_seconds() == 3600
    # events appended through the real append-only store
    milestones = [e.milestone for e in store.events_for(ship_id)]
    assert milestones == [Milestone.IN_TRANSIT, Milestone.OUT_FOR_DELIVERY]
    # handoff wiring: the USPS last-mile number in the event text was
    # auto-registered on the shipment
    numbers = store.numbers_for(ship_id)
    assert any(
        n["carrier"] == "usps"
        and n["number"] == "9400100000000000000012"
        and n["role"] == "handoff"
        for n in numbers
    ), numbers


def test_real_store_delivered_archives_and_stops(tmp_path):
    """Terminal milestone through the real store: archived + never due."""
    from qareen.tracking.models import Shipment
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    ship_id, _ = store.upsert_shipment(
        Shipment(
            tracking_number="1ZTESTREAL2",
            carrier="ups",
            milestone=Milestone.OUT_FOR_DELIVERY,
            source="manual",
        ),
        next_poll_at=NOW,
    )
    report = _scheduler(
        store, {"ups": FakeClient(_events_payload("OFD", "DL"))}
    ).run_once(NOW)

    assert report["archived"] == 1
    row = store.get_shipment_row(ship_id)
    assert row["milestone"] == "delivered"
    assert row["status"] == "archived"
    assert row["next_poll_at"] is None
    later = (NOW + timedelta(days=30)).isoformat()
    assert store.due_shipments(later, 50) == []


def test_scaffolded_pack_is_inert_for_polling(tmp_path):
    """Lifecycle gate: a pack in state 'scaffolded' must not be polled."""
    from qareen.tracking.models import Shipment
    from qareen.tracking.onboard import STATE_SCAFFOLDED, set_carrier_state
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    store.upsert_shipment(
        Shipment(tracking_number="1ZTESTREAL3", carrier="ups", source="manual"),
        next_poll_at=NOW,
    )
    set_carrier_state(store, "ups", STATE_SCAFFOLDED, note="test")
    client = FakeClient(_events_payload("IT"))
    _scheduler(store, {"ups": client}).run_once(NOW)
    assert client.calls == []  # inert: no HTTP


def test_pseudo_carrier_pack_is_skipped(tmp_path):
    """Packs with endpoints.base null (amazon-email) have no HTTP surface."""
    from qareen.tracking.models import Shipment
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    store.upsert_shipment(
        Shipment(tracking_number="TBA123456789", carrier="email", source="email"),
        next_poll_at=NOW,
    )
    manifest = {
        "display_name": "Email",
        "auth": {"model": "none"},
        "endpoints": {"base": None, "track": None},
        "tracking": {"patterns": [], "check_digit": None},
        "capabilities": {"edd": False, "pod": False, "push": False},
        "status_map": {},
        "response_map": {},
        "rate_limits": {"requests_per_day": 100, "min_interval_seconds": 0},
        "retention": {"delete_days_after_delivery": None},
    }
    pack = CarrierPack(slug="email", path=Path("."), manifest=manifest)
    client = FakeClient(_events_payload("IT"))
    report = _scheduler(store, {"email": client}, packs={"email": pack}).run_once(NOW)
    assert client.calls == []  # skipped: email channel owns this carrier
    assert report["errors"] == []
