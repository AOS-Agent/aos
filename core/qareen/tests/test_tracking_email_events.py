"""Tests for the email-event channel: Amazon lifecycle-email parsing, the
merchant-parser registry seam, and EmailEventChannel batch processing with
a duck-typed fake store.

Covers: confirmation / shipped-with-TBA / out-for-delivery / delivered /
delivered-with-photo milestones, order-keyed dedup, TBA linking, the
bare-TBA path, malformed input (None, never an exception), and the
amazon-email pack loading green through the real linter.
"""

import sys
from datetime import datetime
from pathlib import Path

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import packs  # noqa: E402
from qareen.tracking.email_events import (  # noqa: E402
    AMAZON_EMAIL_CARRIER,
    EmailEventChannel,
    parse_amazon_email,
    register_parser,
)
from qareen.tracking.models import Milestone  # noqa: E402

PACK_DIR = (
    Path(__file__).resolve().parents[1] / "tracking" / "carriers" / "amazon-email"
)


# ── realistic email fixtures ──────────────────────────────────────────────

def _msg(sender, content, subject=None, timestamp="2026-07-20T14:32:00", **extra):
    msg = {
        "id": extra.pop("id", 1),
        "channel": "email",
        "direction": "inbound",
        "sender_id": sender,
        "content": content,
        "timestamp": timestamp,
    }
    if subject is not None:
        msg["subject"] = subject
    msg.update(extra)
    return msg


CONFIRMATION_CA = _msg(
    "auto-confirm@amazon.ca.example.com",
    """Subject: Your Amazon.ca order of "Instant Pot Duo 7-in-1..."

Hello Sam,

Thanks for your order! Your order is confirmed and will arrive
Tuesday, July 28.

Order # 701-1234567-8901234

Instant Pot Duo 7-in-1 Electric Pressure Cooker, 6 Quart
Quantity: 1
""",
    id=101,
)

SHIPPED_COM = _msg(
    "shipment-tracking@amazon.com.example.com",
    """Subject: Your order has shipped!

Hello Sam,

Good news! Your package has shipped. Track your package to see the
expected delivery date.

Order #111-7654321-0987654
Tracking ID: TBA123456789012

Arriving:
Thursday, July 30
""",
    id=102,
    timestamp="2026-07-22T09:15:00",
)

OFD_CA = _msg(
    "shipment-tracking@amazon.ca.example.com",
    """Subject: Out for delivery: "Instant Pot Duo 7-in-1..."

Your package is out for delivery and will arrive today by 9:00 PM.

Order # 701-1234567-8901234
Tracking ID: TBA987654321098
""",
    id=103,
    timestamp="2026-07-28T08:01:00",
)

DELIVERED_CA = _msg(
    "delivery-update@amazon.ca.example.com",
    """Subject: Delivered: Your Amazon.ca order #701-1234567-8901234

Your package was delivered. It was handed directly to a resident.

Order # 701-1234567-8901234
""",
    id=104,
    timestamp="2026-07-28T16:44:00",
)

DELIVERED_PHOTO_COM = _msg(
    "shipment-tracking@amazon.com.example.com",
    """Subject: Your package was delivered!

Your package was delivered near the front door. View your delivery
photo to see where it was left.

Order #111-7654321-0987654
""",
    id=105,
    timestamp="2026-07-30T13:05:00",
)

BARE_TBA = _msg(
    "shipment-tracking@amazon.com.example.com",
    """Subject: Your package is arriving today!

Your Amazon package is out for delivery. Follow along: TBA555000111222
""",
    id=106,
    timestamp="2026-07-24T07:50:00",
)

NON_AMAZON_CONTROL = _msg(
    "news@example-shop.test",
    """Subject: Your order has shipped!

Your order #8842 has shipped. Tracking: 1Z999AA10123456784.
""",
    id=107,
)


# ── fake store (duck-typed protocol from email_events docstring) ──────────

class FakeStore:
    """Implements upsert_shipment / append_event / add_number in memory."""

    def __init__(self):
        self.shipments = {}  # key -> shipment id
        self.upserts = []  # every call, in order
        self.events = {}  # shipment id -> [TrackingEvent]
        self.numbers = {}  # shipment id -> [(number, carrier)]
        self._next = 0

    def upsert_shipment(self, *, key, carrier, merchant=None, merchant_domain=None,
                        source="email", label=None):
        self.upserts.append({
            "key": key, "carrier": carrier, "merchant": merchant,
            "merchant_domain": merchant_domain, "source": source, "label": label,
        })
        if key not in self.shipments:
            self._next += 1
            self.shipments[key] = "shp-%d" % self._next
            self.events[self.shipments[key]] = []
            self.numbers[self.shipments[key]] = []
        return self.shipments[key]

    def append_event(self, shipment_id, event):
        self.events[shipment_id].append(event)

    def add_number(self, shipment_id, tracking_number, carrier):
        self.numbers[shipment_id].append((tracking_number, carrier))


# ── pack loads green ──────────────────────────────────────────────────────

def test_amazon_email_pack_loads_green():
    """The pseudo-carrier manifest must pass the real linter/loader."""
    pack = packs.load_pack(PACK_DIR)
    assert pack.slug == "amazon-email"
    assert pack.auth["model"] == "none"
    assert pack.check_digit is None
    assert "TBA[0-9]{10,15}" in pack.patterns
    # Email lifecycle kinds map to canonical milestones via data.
    assert pack.status_map["ORDER_CONFIRMED"] == "label_created"
    assert pack.status_map["DELIVERED"] == "delivered"


# ── pure parsing: milestone per email type ────────────────────────────────

def test_parse_confirmation_maps_to_label_created_with_order_and_item():
    parsed = parse_amazon_email(CONFIRMATION_CA)
    assert parsed is not None
    assert parsed.kind == "confirmed"
    assert parsed.milestone is Milestone.LABEL_CREATED
    assert parsed.order_id == "701-1234567-8901234"
    assert parsed.merchant == "Amazon"
    assert parsed.merchant_domain == "amazon.ca.example.com"
    assert parsed.item_summary == "Instant Pot Duo 7-in-1..."
    assert parsed.source == "email"
    assert parsed.timestamp == datetime(2026, 7, 20, 14, 32)


def test_parse_shipped_extracts_tba_and_maps_to_in_transit():
    parsed = parse_amazon_email(SHIPPED_COM)
    assert parsed is not None
    assert parsed.kind == "shipped"
    assert parsed.milestone is Milestone.IN_TRANSIT
    assert parsed.order_id == "111-7654321-0987654"
    assert parsed.tracking_numbers == ["TBA123456789012"]
    assert parsed.merchant_domain == "amazon.com.example.com"
    assert "TBA123456789012" in parsed.description


def test_parse_out_for_delivery():
    parsed = parse_amazon_email(OFD_CA)
    assert parsed is not None
    assert parsed.kind == "out_for_delivery"
    assert parsed.milestone is Milestone.OUT_FOR_DELIVERY
    assert parsed.order_id == "701-1234567-8901234"


def test_parse_delivered():
    parsed = parse_amazon_email(DELIVERED_CA)
    assert parsed is not None
    assert parsed.kind == "delivered"
    assert parsed.milestone is Milestone.DELIVERED
    assert parsed.photo_on_delivery is False


def test_parse_delivered_with_photo_notes_photo():
    parsed = parse_amazon_email(DELIVERED_PHOTO_COM)
    assert parsed is not None
    assert parsed.kind == "delivered"
    assert parsed.milestone is Milestone.DELIVERED
    assert parsed.photo_on_delivery is True
    assert "photo" in parsed.description.lower()


def test_parse_bare_tba_without_order_id():
    """A TBA with no known order still parses (keyed on the number later)."""
    parsed = parse_amazon_email(BARE_TBA)
    assert parsed is not None
    assert parsed.milestone is Milestone.OUT_FOR_DELIVERY  # "arriving today"
    assert parsed.order_id is None
    assert parsed.tracking_numbers == ["TBA555000111222"]


def test_parse_non_amazon_sender_returns_none():
    assert parse_amazon_email(NON_AMAZON_CONTROL) is None


def test_parse_malformed_messages_return_none_never_raise():
    assert parse_amazon_email({}) is None
    assert parse_amazon_email({"sender_id": "x@amazon.ca.example.com"}) is None  # no content
    assert parse_amazon_email({"sender_id": "x@amazon.ca.example.com", "content": None}) is None
    assert parse_amazon_email({"sender_id": "x@amazon.ca.example.com", "content": 12345}) is None
    assert parse_amazon_email({"sender_id": 42, "content": "hi"}) is None
    assert parse_amazon_email(None) is None
    assert parse_amazon_email("not a dict") is None


def test_parse_amazon_marketing_email_without_signal_returns_none():
    marketing = _msg(
        "store-news@amazon.ca.example.com",
        "Subject: Deals you might like\n\nCheck out this week's offers!",
    )
    assert parse_amazon_email(marketing) is None


def test_parse_delivery_attempt_and_delay():
    attempt = _msg(
        "shipment-tracking@amazon.com.example.com",
        "Subject: We tried to deliver your package\n\n"
        "We attempted to deliver Order #111-7654321-0987654 today.",
    )
    parsed = parse_amazon_email(attempt)
    assert parsed.milestone is Milestone.FAILED_ATTEMPT

    delayed = _msg(
        "shipment-tracking@amazon.ca.example.com",
        "Subject: Your delivery has been delayed\n\n"
        "Order # 701-1234567-8901234 is delayed. We're sorry.",
    )
    parsed = parse_amazon_email(delayed)
    assert parsed.milestone is Milestone.EXCEPTION


def test_parse_epoch_and_zulu_timestamps():
    msg = _msg("auto-confirm@amazon.ca.example.com",
               "Thanks for your order! Order # 701-1234567-8901234",
               timestamp=1785000000)
    assert parse_amazon_email(msg).timestamp == datetime.fromtimestamp(1785000000)
    msg = _msg("auto-confirm@amazon.ca.example.com",
               "Thanks for your order! Order # 701-1234567-8901234",
               timestamp="2026-07-20T14:32:00Z")
    assert parse_amazon_email(msg).timestamp is not None


# ── channel: dedup, linking, bare TBA, batch safety ───────────────────────

def test_channel_two_emails_same_order_one_shipment_two_events():
    store = FakeStore()
    channel = EmailEventChannel(store)
    result = channel.process([CONFIRMATION_CA, OFD_CA, DELIVERED_CA])

    assert result.consumed == 3 and result.skipped == 0 and result.errors == 0
    # Order-keyed dedup: every upsert used the same key → one shipment.
    assert len(store.shipments) == 1
    shipment_id = store.shipments["701-1234567-8901234"]
    assert [e.milestone for e in store.events[shipment_id]] == [
        Milestone.LABEL_CREATED,
        Milestone.OUT_FOR_DELIVERY,
        Milestone.DELIVERED,
    ]
    # Events carry source: email so the UI never confuses API vs email.
    assert all(e.raw["source"] == "email" for e in store.events[shipment_id])
    # The OFD email's TBA linked to the order's pseudo-shipment.
    assert store.numbers[shipment_id] == [("TBA987654321098", AMAZON_EMAIL_CARRIER)]


def test_channel_upsert_marks_carrier_merchant_and_source():
    store = FakeStore()
    EmailEventChannel(store).process([CONFIRMATION_CA])
    upsert = store.upserts[0]
    assert upsert["carrier"] == AMAZON_EMAIL_CARRIER
    assert upsert["merchant"] == "Amazon"
    assert upsert["merchant_domain"] == "amazon.ca.example.com"
    assert upsert["source"] == "email"
    assert upsert["label"] == "Instant Pot Duo 7-in-1..."


def test_channel_tba_linked_to_order_shipment():
    store = FakeStore()
    EmailEventChannel(store).process([SHIPPED_COM])
    shipment_id = store.shipments["111-7654321-0987654"]
    assert store.numbers[shipment_id] == [("TBA123456789012", AMAZON_EMAIL_CARRIER)]


def test_channel_bare_tba_creates_number_keyed_pseudo_shipment():
    store = FakeStore()
    result = EmailEventChannel(store).process([BARE_TBA])
    assert result.consumed == 1
    # No order ID in the email → the TBA itself keys the pseudo-shipment.
    assert list(store.shipments) == ["TBA555000111222"]
    shipment_id = store.shipments["TBA555000111222"]
    assert store.numbers[shipment_id] == [("TBA555000111222", AMAZON_EMAIL_CARRIER)]


def test_channel_skips_control_and_survives_malformed_in_batch():
    store = FakeStore()
    channel = EmailEventChannel(store)
    result = channel.process([NON_AMAZON_CONTROL, {}, None, {"content": 1}, CONFIRMATION_CA])
    assert result.consumed == 1
    assert result.skipped == 4
    assert result.errors == 0
    assert len(store.shipments) == 1


def test_channel_store_failure_is_wrapped_not_raised():
    class ExplodingStore(FakeStore):
        def upsert_shipment(self, **kwargs):
            raise RuntimeError("db gone")

    result = EmailEventChannel(ExplodingStore()).process([CONFIRMATION_CA, SHIPPED_COM])
    assert result.errors == 2
    assert result.consumed == 0


# ── registry seam: a new merchant plugs in without touching core ──────────

def test_custom_merchant_parser_via_registry():
    import re

    def parse_acme(message):
        content = message.get("content") or ""
        if "ACME order" not in content:
            return None
        from qareen.tracking.email_events import ParsedEmailEvent
        return ParsedEmailEvent(
            kind="shipped",
            milestone=Milestone.IN_TRANSIT,
            merchant="Acme",
            merchant_domain="acme-widgets.test",
            description="Acme order shipped",
            order_id="ACME-42",
        )

    channel = EmailEventChannel(FakeStore())
    channel._registry = channel._registry + [
        (re.compile(r"(?:^|[.@])acme-widgets\.test$"), parse_acme)
    ]
    msg = _msg("orders@acme-widgets.test", "ACME order shipped!")
    assert channel.process([msg]).consumed == 1
    assert "ACME-42" in channel.store.shipments


def test_module_level_register_parser():
    import re

    def noop_parser(message):
        return None

    register_parser(r"(?:^|[.@])example\.test$", noop_parser)
    channel = EmailEventChannel(FakeStore())
    assert any(p.pattern == re.compile(r"(?:^|[.@])example\.test$").pattern
               for p, _ in channel._registry)


# ── integration: real ShipmentStore (seam regression) ──────────────────────


def test_channel_against_real_store(tmp_path):
    """EmailEventChannel against the REAL ShipmentStore — regression for the
    upsert_shipment(key=...) signature mismatch (the store has
    upsert_shipment_key)."""
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    channel = EmailEventChannel(store)
    msg = {
        "id": "m1",
        "sender_id": "auto-confirm@amazon.ca.example.com",
        "content": "Ordered: \"Instant Pot Duo\"\nOrder # 701-1234567-8901234",
        "subject": "Ordered: \"Instant Pot Duo\"",
        "timestamp": "2026-07-20T10:00:00",
    }
    assert channel.process_message(msg) is True
    rows = store.list_shipments()
    assert len(rows) == 1
    assert rows[0]["carrier"] == "amazon-email"
    assert rows[0]["merchant"] == "Amazon"
    assert rows[0]["label"] == "Instant Pot Duo"
    # Second email for the same order merges (idempotent on key), two events.
    msg2 = dict(msg, id="m2", content="Shipped: \"Instant Pot Duo\"\nOrder # 701-1234567-8901234",
                subject="Shipped: \"Instant Pot Duo\"")
    assert channel.process_message(msg2) is True
    assert len(store.list_shipments()) == 1
    assert len(store.events_for(rows[0]["id"])) == 2
