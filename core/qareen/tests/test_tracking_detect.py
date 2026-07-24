"""Tests for the tracking detection pipeline (detect.py + config.py).

All fixtures are tmp_path pack directories built on the fly — no test
depends on the real carriers/ tree. The store is a fake implementing the
duck-typed seam documented in detect.py.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import detect  # noqa: E402
from qareen.tracking.config import TrackingConfig, action_for  # noqa: E402
from qareen.tracking.packs import load_packs  # noqa: E402

# ── fixtures ─────────────────────────────────────────────────────────────


def _luhn_check_digit(payload: str) -> str:
    """Check digit making *payload* pass the mod10 (Luhn) validator."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def _write_pack(carriers_dir: Path, slug: str, manifest: dict) -> None:
    pack_dir = carriers_dir / slug
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))


def _manifest(slug, patterns, check_digit=None, url_templates=None):
    return {
        "carrier": slug,
        "display_name": slug.title(),
        "auth": {"model": "none"},
        "endpoints": {"base": "https://api.example.com", "track": "https://api.example.com/t/{number}"},
        "tracking": {"patterns": patterns, "check_digit": check_digit},
        "url_templates": url_templates or [],
        "capabilities": {"edd": True, "pod": False, "push": False},
        "status_map": {"IT": "in_transit", "DL": "delivered"},
        "response_map": {
            "events": "$.events[*]",
            "event_fields": {"code": "$.code", "description": "$.desc"},
        },
        "rate_limits": {"requests_per_day": 100, "min_interval_seconds": 1},
        "retention": {"delete_days_after_delivery": None},
    }


UPS_NUMBER = "1Z999AA10123456784"
USPS_NUMBER = "9400100000000000000001"  # 22 digits
ACME_NUMBER = "1234567890" + _luhn_check_digit("1234567890")  # 11 digits, Luhn-valid
ACME_GARBAGE = "12345678901"  # 11 digits, Luhn-INVALID (checked in test)


@pytest.fixture()
def packs(tmp_path):
    carriers = tmp_path / "carriers"
    _write_pack(carriers, "ups", _manifest(
        "ups", ["1Z[0-9A-Z]{16}"],
        url_templates=["https://www.ups.com/track?tracknum={number}"],
    ))
    _write_pack(carriers, "usps", _manifest(
        "usps", ["[0-9]{22}"],
        url_templates=["https://tools.usps.com/go/TrackConfirmAction?tLabels={number}"],
    ))
    _write_pack(carriers, "acme", _manifest(
        "acme", ["[0-9]{11}"], check_digit="mod10",
        url_templates=["https://track.acme.test/t/{number}"],
    ))
    return load_packs(carriers)


class FakeStore:
    """Duck-typed tracking store seam for tests."""

    def __init__(self, priors=None):
        self.priors = priors or {}
        self.shipments = []
        self.candidates = []
        self.state = {}
        self.eval_rows = []

    def get_priors(self, domain):
        return dict(self.priors.get(domain, {}))

    def add_shipment(self, tracking_number, carrier, sources, confidence, layer,
                     merchant_domain=None):
        self.shipments.append({
            "tracking_number": tracking_number,
            "carrier": carrier,
            "sources": sources,
            "confidence": confidence,
            "layer": layer,
            "merchant_domain": merchant_domain,
        })

    def enqueue_candidate(self, candidate_dict, layer=None, confidence=None):
        self.candidates.append(candidate_dict)

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value

    def add_eval_candidate(self, row):
        self.eval_rows.append(dict(row))

    def iter_eval_rows(self):
        return list(self.eval_rows)


def _msg(text, sender="shipping@merchant.example", channel="email", **kw):
    msg = {
        "message_id": 1,
        "sender": sender,
        "channel": channel,
        "text": text,
        "subject": kw.pop("subject", ""),
        "conversation_id": "conv-1",
        "timestamp": datetime.now().isoformat(),
        "from_me": False,
    }
    msg.update(kw)
    return msg


# ── layer 0: URL extraction ─────────────────────────────────────────────


def test_url_extraction_gives_number_and_carrier(packs):
    result = detect.detect(
        _msg("Your order shipped! Track it: https://www.ups.com/track?tracknum=%s" % UPS_NUMBER),
        packs,
    )
    assert len(result.candidates) == 1
    cand = result.candidates[0]
    assert cand.layer == "url"
    assert cand.carrier == "ups"
    assert cand.tracking_number == UPS_NUMBER
    assert action_for(cand.confidence) == "auto_add"


def test_url_with_extra_query_params_still_matches(packs):
    result = detect.detect(
        _msg("see https://www.ups.com/track?tracknum=%s&loc=en_US&requester=ST/" % UPS_NUMBER),
        packs,
    )
    assert [c.tracking_number for c in result.candidates] == [UPS_NUMBER]


def test_url_with_garbage_number_is_dropped(packs):
    result = detect.detect(
        _msg("https://www.ups.com/track?tracknum=ZZZ"), packs,
    )
    assert result.candidates == []


def test_spaced_number_canonicalizes_from_usps_url(packs):
    spaced = "9400 1000 0000 0000 0000 01"
    url = "https://tools.usps.com/go/TrackConfirmAction?tLabels=%s" % spaced.replace(" ", "%20")
    result = detect.detect(_msg(url), packs)
    assert [c.tracking_number for c in result.candidates] == [USPS_NUMBER]


# ── layer 0.5: digest parsing ────────────────────────────────────────────


def test_usps_informed_delivery_digest_is_authoritative(packs):
    result = detect.detect(_msg(
        "Packages arriving today:\n%s\n1 item" % USPS_NUMBER,
        sender="USPSInformedDelivery@usps.com",
        subject="Your Daily Digest for Informed Delivery",
    ), packs)
    digest = [c for c in result.candidates if c.layer == "digest"]
    assert len(digest) == 1
    assert digest[0].carrier == "usps"
    assert digest[0].tracking_number == USPS_NUMBER
    assert action_for(digest[0].confidence) == "auto_add"


def test_digest_requires_sender_domain_and_subject(packs):
    # Right number, right keyword in subject — but a spoofed sender domain.
    result = detect.detect(_msg(
        USPS_NUMBER,
        sender="usps-alerts@evil.example",
        subject="Your Daily Digest for Informed Delivery",
    ), packs)
    assert not [c for c in result.candidates if c.layer == "digest"]


# ── layer 1: body pattern scan ───────────────────────────────────────────


def test_body_scan_finds_bare_number(packs):
    result = detect.detect(_msg("shipped today, tracking %s cheers" % UPS_NUMBER), packs)
    body = [c for c in result.candidates if c.layer == "body"]
    assert len(body) == 1
    assert body[0].carrier == "ups"
    # base 0.60, no domain match (merchant.example), no priors → queue band
    assert action_for(body[0].confidence) == "queue"


def test_check_digit_kills_garbage_body_candidates(packs):
    # Confirm the fixture really is Luhn-invalid, then assert it's dropped.
    from qareen.tracking import checkdigits

    assert not checkdigits.mod10(ACME_GARBAGE)
    result = detect.detect(_msg("ref %s inside" % ACME_GARBAGE), packs)
    assert result.candidates == []


def test_check_digit_valid_number_survives(packs):
    from qareen.tracking import checkdigits

    assert checkdigits.mod10(ACME_NUMBER)
    result = detect.detect(_msg("order %s on its way" % ACME_NUMBER), packs)
    assert [c.carrier for c in result.candidates] == ["acme"]


# ── layer 2: context scoring ─────────────────────────────────────────────


def test_carrier_domain_match_boosts_body_candidate(packs):
    result = detect.detect(_msg(
        "your package %s is on the way" % UPS_NUMBER, sender="noreply@ups.com",
    ), packs)
    body = [c for c in result.candidates if c.layer == "body"]
    assert len(body) == 1
    assert body[0].context["domain_match"] is True
    assert action_for(body[0].confidence) == "auto_add"  # 0.60 + 0.25 = 0.85


def test_store_priors_boost_body_candidate(packs):
    store = FakeStore(priors={"shop.example": {"ups": 0.9}})
    result = detect.detect(_msg(
        "shipped: %s" % UPS_NUMBER, sender="orders@shop.example",
    ), packs, store=store)
    body = [c for c in result.candidates if c.layer == "body"]
    assert len(body) == 1
    assert body[0].context["prior"] == pytest.approx(0.9)
    # 0.60 + 0.30*0.9 = 0.87 → auto_add
    assert action_for(body[0].confidence) == "auto_add"


def test_confidence_banding_boundaries():
    cfg = TrackingConfig()
    assert action_for(0.85, cfg) == "auto_add"
    assert action_for(0.849, cfg) == "queue"
    assert action_for(0.5, cfg) == "queue"
    assert action_for(0.499, cfg) == "ignore"


# ── layer 3: probe resolution ────────────────────────────────────────────


@pytest.fixture()
def ambiguous_packs(tmp_path):
    """Two packs whose patterns both match an 11-digit number."""
    carriers = tmp_path / "carriers"
    _write_pack(carriers, "acme", _manifest("acme", ["[0-9]{11}"], check_digit="mod10"))
    _write_pack(carriers, "globex", _manifest("globex", ["[0-9]{11}"]))
    return load_packs(carriers)


def test_ambiguous_number_is_log_only_by_default(ambiguous_packs):
    result = detect.detect(_msg("tracking %s" % ACME_NUMBER), ambiguous_packs)
    assert len(result.probe_plans) == 1
    plan = result.probe_plans[0]
    assert plan.outcome == "log_only"
    assert sorted(plan.carriers) == ["acme", "globex"]
    # Both body candidates survive (nothing probed away)
    assert {c.carrier for c in result.candidates} == {"acme", "globex"}


def test_probe_check_digit_prevalidation_kills_garbage(ambiguous_packs):
    cfg = TrackingConfig(probe_enabled=True)
    result = detect.detect(
        _msg("tracking %s" % ACME_GARBAGE), ambiguous_packs, config=cfg,
        probe_fn=lambda carrier, number: {"ship_date": datetime.now().isoformat()},
    )
    # acme's mod10 kills the number at body scan for acme, so only globex
    # claims it → no ambiguity → no probe plan; single globex candidate.
    assert result.probe_plans == []
    assert [c.carrier for c in result.candidates] == ["globex"]


def test_probe_resolves_ambiguity_when_enabled(ambiguous_packs):
    cfg = TrackingConfig(probe_enabled=True)
    calls = []

    def probe_fn(carrier, number):
        calls.append(carrier)
        if carrier == "globex":
            return {"ship_date": datetime.now().isoformat(), "milestone": "in_transit"}
        return None

    result = detect.detect(
        _msg("tracking %s" % ACME_NUMBER), ambiguous_packs, config=cfg, probe_fn=probe_fn,
    )
    assert len(result.probe_plans) == 1
    plan = result.probe_plans[0]
    assert plan.outcome == "probed"
    assert plan.resolved_carrier == "globex"
    resolved = [c for c in result.candidates if c.layer == "probe"]
    assert len(resolved) == 1
    assert resolved[0].carrier == "globex"
    assert len(calls) <= cfg.probe_max_carriers


def test_probe_rejects_recycled_numbers(ambiguous_packs):
    cfg = TrackingConfig(probe_enabled=True)
    old_ship = (datetime.now() - timedelta(days=60)).isoformat()
    result = detect.detect(
        _msg("tracking %s" % ACME_NUMBER),
        ambiguous_packs,
        config=cfg,
        probe_fn=lambda carrier, number: {"ship_date": old_ship, "first_event_at": old_ship},
    )
    plan = result.probe_plans[0]
    assert plan.outcome == "rejected_recycled"
    assert not [c for c in result.candidates if c.layer == "probe"]


def test_probe_orders_carriers_by_context_prior(ambiguous_packs):
    cfg = TrackingConfig(probe_enabled=True)
    store = FakeStore(priors={"merchant.example": {"globex": 0.95}})
    calls = []

    def probe_fn(carrier, number):
        calls.append(carrier)
        return {"ship_date": datetime.now().isoformat()}

    result = detect.detect(
        _msg("tracking %s" % ACME_NUMBER), ambiguous_packs,
        store=store, config=cfg, probe_fn=probe_fn,
    )
    assert calls[0] == "globex"  # highest prior probed first
    assert result.probe_plans[0].resolved_carrier == "globex"


# ── layer 4: LLM fallback ────────────────────────────────────────────────


def test_llm_fallback_log_only_by_default(packs):
    result = detect.detect(_msg("something odd: parcel ref ZX-Q-4482 en route"), packs)
    assert result.candidates == []
    assert result.llm is not None
    assert result.llm["enabled"] is False
    assert result.llm["would_call"] is True
    assert "ZX-Q-4482" in result.llm["prompt"]


def test_llm_not_invoked_when_cheaper_layers_hit(packs):
    result = detect.detect(_msg("track %s" % UPS_NUMBER), packs)
    assert result.llm is None


def test_llm_fallback_when_enabled(packs):
    cfg = TrackingConfig(llm_enabled=True)
    response = '[{"tracking_number": "%s", "carrier": "ups", "confidence": 0.9}]' % UPS_NUMBER
    result = detect.detect(
        _msg("weird phrasing, no clean number"), packs,
        config=cfg, llm_fn=lambda prompt: response,
    )
    llm = [c for c in result.candidates if c.layer == "llm"]
    assert len(llm) == 1
    assert llm[0].carrier == "ups"
    assert llm[0].confidence <= 0.70  # capped at the LLM base confidence


def test_llm_garbage_response_yields_nothing(packs):
    cfg = TrackingConfig(llm_enabled=True)
    result = detect.detect(
        _msg("nothing here"), packs, config=cfg, llm_fn=lambda p: "not json at all",
    )
    assert result.candidates == []
    assert "response" in result.llm


# ── dedup / multi-source ─────────────────────────────────────────────────


def test_dedup_merges_same_number_multiple_sources(packs):
    src_a = {"message_id": 1, "channel": "email", "sender": "a@x.example"}
    src_b = {"message_id": 2, "channel": "imessage", "sender": "b@y.example"}
    a = detect.DetectionCandidate(UPS_NUMBER, "ups", 0.60, "body", src_a)
    b = detect.DetectionCandidate(UPS_NUMBER, "ups", 0.98, "url", src_b)
    merged = detect.dedup([a, b])
    assert len(merged) == 1
    assert merged[0].confidence == 0.98
    assert merged[0].layer == "url"  # most precise layer wins
    assert merged[0].sources == [src_a, src_b]  # one shipment, many links


def test_url_and_body_layers_merge_in_one_message(packs):
    text = "https://www.ups.com/track?tracknum=%s also bare: %s" % (UPS_NUMBER, UPS_NUMBER)
    result = detect.detect(_msg(text), packs)
    ups = [c for c in result.candidates if c.tracking_number == UPS_NUMBER]
    assert len(ups) == 1
    assert ups[0].layer == "url"


# ── privacy / skips ──────────────────────────────────────────────────────


def test_from_me_messages_are_skipped(packs):
    result = detect.detect(_msg("track %s" % UPS_NUMBER, from_me=True), packs)
    assert result.skipped_reason == "from_me"
    assert result.candidates == []


def test_restricted_sender_is_excluded(packs, tmp_path):
    people_db = tmp_path / "people.db"
    conn = sqlite3.connect(str(people_db))
    conn.execute("CREATE TABLE people (id TEXT, email TEXT, privacy_level INTEGER)")
    conn.execute(
        "INSERT INTO people VALUES ('p1', 'vip@private.example', 2)"
    )
    conn.commit()
    conn.close()

    result = detect.detect(
        _msg("track %s" % UPS_NUMBER, sender="vip@private.example"),
        packs, people_db_path=people_db,
    )
    assert result.skipped_reason == "privacy"
    assert result.candidates == []

    # Unrestricted sender in the same db still detects.
    conn = sqlite3.connect(str(people_db))
    conn.execute("INSERT INTO people VALUES ('p2', 'shop@other.example', 0)")
    conn.commit()
    conn.close()
    result = detect.detect(
        _msg("track %s" % UPS_NUMBER, sender="shop@other.example"),
        packs, people_db_path=people_db,
    )
    assert result.skipped_reason is None
    assert result.candidates


def test_missing_people_db_never_blocks(packs, tmp_path):
    result = detect.detect(
        _msg("track %s" % UPS_NUMBER), packs,
        people_db_path=tmp_path / "nope.db",
    )
    assert result.skipped_reason is None
    assert result.candidates


# ── persist / config ─────────────────────────────────────────────────────


def test_persist_bands_candidates_into_store(packs):
    store = FakeStore()
    url_msg = _msg("https://www.ups.com/track?tracknum=%s" % UPS_NUMBER)
    body_msg = _msg("tracking %s" % UPS_NUMBER, sender="x@nowhere.example")
    url_msg["message_id"] = 1
    body_msg["message_id"] = 2

    r1 = detect.detect(url_msg, packs, store=store)
    counts = detect.persist(r1, store)
    assert counts["auto_add"] == 1
    assert store.shipments[0]["carrier"] == "ups"

    r2 = detect.detect(body_msg, packs, store=store)
    counts = detect.persist(r2, store)
    assert counts["queue"] == 1
    assert store.candidates[0]["tracking_number"] == UPS_NUMBER


def test_config_loads_yaml_overrides(tmp_path):
    (tmp_path / "tracking.yaml").write_text(yaml.safe_dump({
        "detection": {"auto_add": 0.9},
        "probe": {"enabled": True, "daily_budget": 5},
        "backfill": {"window_days": 30, "chunk_size": 100},
    }))
    cfg = TrackingConfig.load(tmp_path / "tracking.yaml")
    assert cfg.auto_add == 0.9
    assert cfg.probe_enabled is True
    assert cfg.probe_daily_budget == 5
    assert cfg.backfill_window_days == 30
    assert cfg.backfill_chunk_size == 100
    # untouched keys keep defaults
    assert cfg.llm_enabled is False
    assert cfg.queue_min == 0.5


def test_config_missing_file_uses_defaults(tmp_path):
    cfg = TrackingConfig.load(tmp_path / "absent.yaml")
    assert cfg.auto_add == 0.85
    assert cfg.probe_enabled is False
    assert cfg.backfill_chunk_size == 500


# ── consumer (smoke: never raises) ───────────────────────────────────────


def test_consumer_processes_event_and_never_raises(packs):
    from engine.bus.consumers.tracking_detect import TrackingDetectConsumer
    from engine.bus.event import Event

    store = FakeStore()
    consumer = TrackingDetectConsumer(packs=packs, store=store, config=TrackingConfig())
    event = Event(
        type="comms.message_received",
        data={
            "sender": "noreply@ups.com",
            "channel": "email",
            "text": "your package %s shipped" % UPS_NUMBER,
            "conversation_id": "c1",
            "from_me": False,
            "timestamp": datetime.now().isoformat(),
        },
        source="test",
    )
    consumer.process(event)  # must not raise
    assert store.shipments or store.candidates

    # Garbage event data must not raise either.
    consumer.process(Event(type="comms.message_received", data={}, source="test"))
    consumer.process(Event(type="comms.message_received", data=None, source="test"))


def test_consumer_skips_from_me(packs):
    from engine.bus.consumers.tracking_detect import TrackingDetectConsumer
    from engine.bus.event import Event

    store = FakeStore()
    consumer = TrackingDetectConsumer(packs=packs, store=store, config=TrackingConfig())
    consumer.process(Event(
        type="comms.message_received",
        data={"sender": "me", "text": "track %s" % UPS_NUMBER, "from_me": True},
        source="test",
    ))
    assert not store.shipments and not store.candidates


# ── integration: real ShipmentStore (seam regression) ──────────────────────


def test_persist_against_real_store(tmp_path):
    """detect.persist against the REAL ShipmentStore — regression for
    enqueue_candidate being called without layer/confidence (queue-band
    candidates were silently dropped)."""
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    cfg = TrackingConfig()
    result = detect.DetectionResult(
        candidates=[
            detect.DetectionCandidate(
                tracking_number=UPS_NUMBER,
                carrier="ups",
                confidence=0.98,
                layer="url",
                source={
                    "message_id": 42,
                    "channel": "email",
                    "sender": "orders@acme-shop.com",
                },
            ),
            detect.DetectionCandidate(
                tracking_number="1Z000AA10123456780",
                carrier="ups",
                confidence=0.55,
                layer="body",
                source={"channel": "email"},
            ),
        ]
    )
    counts = detect.persist(result, store, cfg)

    assert counts == {"auto_add": 1, "queue": 1, "ignore": 0}
    rows = store.list_shipments()
    assert [r["tracking_number"] for r in rows] == [UPS_NUMBER]
    assert rows[0]["next_poll_at"] is not None  # auto-add starts polling
    pending = store.peek_candidates()
    assert len(pending) == 1
    assert pending[0]["layer"] == "body"
    assert pending[0]["candidate"]["tracking_number"] == "1Z000AA10123456780"


def test_store_eval_roundtrip(tmp_path):
    """The evalset/backfill duck-typed seam on the real store:
    add_eval_candidate / iter_eval_rows / add_shipment / get_priors /
    open_default-style opener."""
    from qareen.tracking import store as store_mod
    from qareen.tracking.store import ShipmentStore

    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    eval_id = store.add_eval_candidate(
        {"tracking_number": UPS_NUMBER, "carrier": "ups", "layer": "body"}
    )
    rows = list(store.iter_eval_rows())
    assert [r["id"] for r in rows] == [eval_id]
    assert rows[0]["layer"] == "body"

    ship_id, created = store.add_shipment(
        UPS_NUMBER,
        "ups",
        sources=[{"message_id": 7, "channel": "email"}],
        confidence=0.95,
        layer="digest",
        merchant_domain="acme-shop.com",
    )
    assert created
    assert store.get_shipment_row(ship_id)["merchant_domain"] == "acme-shop.com"

    # priors flow into detection context scoring via get_priors
    store.record_prior("domain", "acme-shop.com", "ups", hit=True)
    assert store.get_priors("acme-shop.com") == {"ups": 1.0}

    # the module-level opener convention backfill/evalset look for
    assert callable(store_mod.open_default)
    assert callable(store_mod.connect)
