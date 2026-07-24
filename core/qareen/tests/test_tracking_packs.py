"""Tests for the five live carrier packs: ups, fedex, usps, dhl, canadapost.

Covers (auto-tracker#14/#19 pack authoring):

- every pack loads green through load_pack (linter passes)
- tracking-number validate_number accept/reject matrices per carrier, using
  real-format numbers (the published test-number corpus from
  jkeen/tracking_number_data, which encodes the carrier check-digit specs)
- check-digit validators verified against published worked examples
- response_map extracts milestone/eta/location from each DOC-DERIVED fixture
  via engine.normalize_event
- error fixtures extract no events (they are error envelopes, not payloads)
- the Canada Post XML mapper (detail, summary, error envelope)

Fixture caveat: all fixtures are DOC-DERIVED (built from official API docs,
2026-07-24). Live validation against captured real responses is gated on
credential signups per the initiative's onboarding pipeline.
"""

import json
import sys
from pathlib import Path

import pytest

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import checkdigits, engine, jsonpath, packs  # noqa: E402
from qareen.tracking.carriers.canadapost import mapper as cpc_mapper  # noqa: E402
from qareen.tracking.models import Milestone  # noqa: E402

CARRIERS_DIR = Path(__file__).resolve().parents[1] / "tracking" / "carriers"
PACK_SLUGS = ["canadapost", "dhl", "fedex", "ups", "usps"]


def _pack(slug: str) -> packs.CarrierPack:
    return packs.load_pack(CARRIERS_DIR / slug)


def _fixture(slug: str, name: str) -> dict:
    return json.loads((CARRIERS_DIR / slug / "fixtures" / name).read_text())


# ── Pack loading / lint ──────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", PACK_SLUGS)
def test_pack_loads_green(slug):
    pack = _pack(slug)
    assert pack.slug == slug
    assert pack.patterns, "pack must declare tracking patterns"
    assert pack.check_digit in checkdigits.names()
    assert pack.url_templates, "pack must declare detection URL templates"
    for template in pack.url_templates:
        assert "{number}" in template


def test_validator_names_registered():
    for name in ("ups_mod10", "usps_mod10", "fedex", "dhl_mod7", "canadapost_mod10"):
        assert name in checkdigits.names()


# ── validate_number accept/reject matrices (real-format numbers) ────────────
# Sources: jkeen/tracking_number_data couriers/*.json test_numbers — the
# published corpus encoding each carrier's check-digit spec.

ACCEPT = {
    "ups": [
        "1Z999AA10123456784",
        "1Z5R89390357567127",
        "1Z879E930346834440",
        "1Z410E7W0392751591",
        "1Z8V92A70367203024",
        "1ZXX3150YW44070023",
        "K1506235620",
        "K2479825491",
        "J4603636537",
        "V0490119172",
        # canonicalization: spaces/hyphens stripped, lowercased accepted
        "1z8v92a70367203024",
    ],
    "fedex": [
        "986578788855",          # Express 12
        "477179081230",
        "790535312317",
        "041441760228964",       # Ground 15
        "568283610012734",
        "000123450000000027",    # SSCC-18
        "9611020987654312345672",  # Ground 96 (22)
        "9622001560001234567100794808390594",  # GSN 34
        "1001921334250001000300779017972697",  # Express 34
        "9261292700768711948021",  # SmartPost 22
        "420112139261290983497923666238",      # SmartPost 30 w/ 420 routing
    ],
    "usps": [
        "03071790000523483741",  # 20-digit
        "71123456789123456787",
        "9400111206206406260787",  # 22-digit
        "9434611206206406227577",
        "9405803699300124287899",
        "92748931507708513018050063",  # 26-digit IMpb
        "420787459400111206206406260787",  # 30-digit w/ 420 routing
        "4201002334249200190132607600833457",  # 34-digit variant
        # spaced form canonicalizes before matching
        "9400 1112 0108 0805 4830 16",
    ],
    "dhl": [
        "3318810025",            # Express 10 (mod-7)
        "8487135506",
        "73891051146",           # Express 11
        "JJD0099999999",         # Express piece ID
        "JVGL0999999990",
        "GM2951173225174494",    # eCommerce
        "GM9E44608A27984866BA2D",
        "60120172242323",        # eCommerce 14
    ],
    "canadapost": [
        "0073938000549297",      # 16-digit PIN
        "7035114477138472",
        "4002847016405018",
        "RB123456785CA",         # UPU S10 international
    ],
}

REJECT = {
    "ups": [
        "1Z1111111111111111",    # right shape, bad check digit
        "2Z5R89390357567127",    # wrong prefix
        "K1506235622",           # waybill, bad check digit
        "1Z5R89390357567128",    # one digit flipped
        "4165551234",            # phone number
    ],
    "fedex": [
        "996578788855",          # bad mod-11 check
        "568283610012732",       # Ground 15, bad check
        "9600000000000000000001",
        "9622001560001234567100794808390595",
        "123456789013",          # 12 digits, bad check digit
        "4165551234",
    ],
    "usps": [
        "03071790000523483742",  # bad check digit
        "9434611206206407667131",
        "2334611306206407667222",
        "9200000000000000000000",
        "9400111206206406260788",  # one digit flipped
        "4165551234",
    ],
    "dhl": [
        "3318810010",            # bad mod-7 check
        "3318810034",
        "XJD0099999998",         # bad piece-ID prefix
        "160120172242323",       # 15 digits — no DHL format
        "4165551234",
    ],
    "canadapost": [
        "0073938000549292",      # bad check digit
        "7035114477138471",
        "5002847016405018",
        "RB123456786CA",         # bad S10 check digit
        "4165551234",
    ],
}


@pytest.mark.parametrize("slug", PACK_SLUGS)
def test_validate_number_accepts_real_formats(slug):
    pack = _pack(slug)
    for number in ACCEPT[slug]:
        assert engine.validate_number(pack, number), "%s should accept %r" % (slug, number)


@pytest.mark.parametrize("slug", PACK_SLUGS)
def test_validate_number_rejects_garbage(slug):
    pack = _pack(slug)
    for number in REJECT[slug]:
        assert not engine.validate_number(pack, number), "%s should reject %r" % (slug, number)


# ── Check-digit worked examples (published) ──────────────────────────────────


def test_checkdigits_worked_examples():
    # UPS: 1Z5R89390357567127 → weighted payload sum 123 → check 7
    assert checkdigits.get("ups_mod10")("1Z5R89390357567127")
    # USPS: 9400111206206406260787 → weighted payload sum 123 → check 7
    assert checkdigits.get("usps_mod10")("9400111206206406260787")
    # USPS IMpb prepend rule: 7196… validates as "91" + serial
    assert checkdigits.get("usps_mod10")("71969010756003077385")
    # FedEx Express: 986578788855 → weighted sum 269 → 269 % 11 % 10 = 5
    assert checkdigits.get("fedex")("986578788855")
    # DHL Express: 3318810025 → 331881002 % 7 = 5
    assert checkdigits.get("dhl_mod7")("3318810025")
    # Canada Post: 0073938000549297 → weighted payload sum 153 → check 7
    assert checkdigits.get("canadapost_mod10")("0073938000549297")
    # S10 (UPU): RB123456785CA → weights 8,6,4,2,3,5,9,7 → check 5
    assert checkdigits.get("canadapost_mod10")("RB123456785CA")


def test_checkdigits_never_raise_on_garbage():
    for name in ("ups_mod10", "usps_mod10", "fedex", "dhl_mod7", "canadapost_mod10"):
        validator = checkdigits.get(name)
        for garbage in ("", "X", "!!!!", "0", "abc123", "1" * 50):
            assert validator(garbage) is False


# ── response_map extraction from DOC-DERIVED fixtures ───────────────────────

# slug → (happy fixture, expected first-event milestone, expected location,
#         expected eta substring)
HAPPY_CASES = {
    "ups": ("track_delivered.json", Milestone.DELIVERED, "ANYTOWN", "20240105"),
    "fedex": ("track_in_transit.json", Milestone.IN_TRANSIT, "MEMPHIS", "2024-01-08"),
    "usps": ("track_detail.json", Milestone.DELIVERED, "CEDAR RAPIDS", "2024-01-05"),
    "dhl": ("track_delivered.json", Milestone.DELIVERED, "TORONTO - Ontario - Canada", "2024-01-05"),
}

ERROR_FIXTURES = {
    "ups": "track_error.json",
    "fedex": "track_error.json",
    "usps": "track_error.json",
    "dhl": "track_error.json",
}


def _events(pack: packs.CarrierPack, data: dict):
    extracted = jsonpath.extract(data, pack.response_map["events"])
    return extracted or []


@pytest.mark.parametrize("slug", sorted(HAPPY_CASES))
def test_response_map_extracts_events_eta_location(slug):
    fixture_name, expected_milestone, expected_location, eta_part = HAPPY_CASES[slug]
    pack = _pack(slug)
    data = _fixture(slug, fixture_name)

    events = _events(pack, data)
    assert len(events) >= 3, "%s fixture should carry several events" % slug

    normalized = [engine.normalize_event(pack, e) for e in events]
    assert normalized[0].milestone is expected_milestone
    assert normalized[0].location == expected_location
    assert normalized[0].description
    assert normalized[0].carrier_code

    # every event in these fixtures maps to a canonical milestone
    assert all(n.milestone is not None for n in normalized)

    eta = jsonpath.extract(data, pack.response_map["eta"])
    assert eta is not None and eta_part in str(eta)


@pytest.mark.parametrize("slug", sorted(ERROR_FIXTURES))
def test_error_fixtures_yield_no_events(slug):
    pack = _pack(slug)
    data = _fixture(slug, ERROR_FIXTURES[slug])
    assert _events(pack, data) == []
    assert jsonpath.extract(data, pack.response_map["eta"]) is None


# ── Canada Post mapper ───────────────────────────────────────────────────────


def test_canadapost_mapper_detail_fixture():
    pack = _pack("canadapost")
    xml_text = (CARRIERS_DIR / "canadapost" / "fixtures" / "track_detail.xml").read_text()
    data = cpc_mapper.track_xml_to_dict(xml_text)

    assert data["pin"] == "0073938000549297"
    assert data["service"] == "Xpresspost"
    assert data["eta"] == "2024-01-08"
    assert data["delivered_on"] == "2024-01-05"
    assert len(data["events"]) == 4

    # response_map applies cleanly on the mapper output
    events = _events(pack, data)
    assert len(events) == 4
    normalized = [engine.normalize_event(pack, e) for e in events]
    assert normalized[0].milestone is Milestone.DELIVERED
    assert normalized[0].location == "TORONTO"
    assert normalized[0].description == "Delivered"
    assert normalized[0].carrier_code == "1476"
    assert normalized[1].milestone is Milestone.OUT_FOR_DELIVERY
    assert normalized[2].milestone is Milestone.IN_TRANSIT
    assert normalized[3].milestone is Milestone.PICKED_UP


def test_canadapost_mapper_error_envelope():
    xml_text = (CARRIERS_DIR / "canadapost" / "fixtures" / "track_error.xml").read_text()
    with pytest.raises(cpc_mapper.CanadaPostError) as excinfo:
        cpc_mapper.track_xml_to_dict(xml_text)
    assert excinfo.value.code == "004"


def test_canadapost_mapper_summary_shape():
    # Get Tracking Summary carries one current-status event per pin-summary.
    xml_text = """<?xml version="1.0"?>
    <tracking-summary>
      <pin-summary>
        <pin>0073938000549297</pin>
        <service-name>Xpresspost</service-name>
        <expected-delivery-date>2024-01-08</expected-delivery-date>
        <event-type>OUT</event-type>
        <event-description>Item out for delivery</event-description>
        <event-date-time>2024-01-05T08:15:00</event-date-time>
        <event-location>TORONTO</event-location>
      </pin-summary>
    </tracking-summary>"""
    data = cpc_mapper.track_xml_to_dict(xml_text)
    assert data["pin"] == "0073938000549297"
    assert data["eta"] == "2024-01-08"
    assert len(data["events"]) == 1
    assert data["events"][0]["location"] == "TORONTO"


def test_canadapost_mapper_rejects_bad_xml():
    with pytest.raises(cpc_mapper.CanadaPostError):
        cpc_mapper.track_xml_to_dict("not xml at all")
    with pytest.raises(cpc_mapper.CanadaPostError):
        cpc_mapper.track_xml_to_dict("<something-else/>")
