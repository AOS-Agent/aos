"""Tests for handoff / linked-number extraction (auto-tracker#20).

Pattern matrix over realistic carrier response snippets (shapes mirror the
pack fixtures in carriers/*/fixtures/), covering: DHL eCommerce → USPS,
UPS Mail Innovations → USPS, Canada Post tender, generic last-mile phrases,
S10 UPU numbers (shared across national posts, check-digit validated),
dedupe, exclusion of the shipment's own primary number, and the
whole-response JSON walk.

Pure functions — no DB, no network.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking.handoff import (  # noqa: E402
    LinkedNumber,
    extract_from_response,
    extract_from_text,
    s10_check_ok,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tracking" / "carriers"

# Check-digit-valid S10: serial 12345678, weights 8,6,4,2,3,5,9,7 → 204,
# 204 % 11 = 6 → check = 5.
S10_VALID = "RA123456785CN"
S10_BAD_CHECK = "RA123456789CN"

USPS_22 = "9400100000000000000001"  # IMpb shape: 9 + 21 digits


# -- S10 check digit ------------------------------------------------------------


def test_s10_check_digit():
    assert s10_check_ok(S10_VALID)
    assert not s10_check_ok(S10_BAD_CHECK)
    assert not s10_check_ok("NOT-A-NUMBER")
    assert not s10_check_ok("")


# -- phrase matrix ----------------------------------------------------------------


def test_tendered_to_usps_with_impb_number():
    text = "Tendered to USPS 9400100000000000000001 for final delivery"
    links = extract_from_text(text)
    assert len(links) == 1
    assert links[0].number == USPS_22
    assert links[0].carrier == "usps"
    assert links[0].role == "handoff"


def test_dhl_ecommerce_style_handoff():
    # DHL eCommerce event descriptions announcing the USPS last mile.
    text = "Tendered to delivery partner USPS, tracking number 9400100000000000000001"
    links = extract_from_text(text)
    assert any(l.number == USPS_22 and l.carrier == "usps" for l in links)


def test_ups_mail_innovations_implies_usps():
    text = "UPS Mail Innovations: package tendered to USPS 9400100000000000000001"
    links = extract_from_text(text)
    assert len(links) == 1
    assert links[0].carrier == "usps"
    assert links[0].number == USPS_22


def test_canada_post_tender_16_digit_pin():
    text = "Tendered to Canada Post 1234567890123456"
    links = extract_from_text(text)
    assert len(links) == 1
    assert links[0].carrier == "canadapost"
    assert links[0].number == "1234567890123456"


def test_generic_last_mile_phrase_with_s10():
    text = "Tendered to final mile carrier, tracking RA123456785CN"
    links = extract_from_text(text)
    assert len(links) == 1
    assert links[0].number == S10_VALID
    assert links[0].carrier is None  # phrase doesn't name which post


def test_s10_named_post_gets_that_carrier():
    text = "Tendered to USPS, international tracking RA123456785CN"
    links = extract_from_text(text)
    assert len(links) == 1
    assert links[0].number == S10_VALID
    assert links[0].carrier == "usps"


def test_bare_s10_anywhere_is_captured():
    # S10 numbers are shared across national posts — a stray S10 in carrier
    # data is almost always a cross-post link even without a phrase.
    text = "Transit scan at ISC NEW YORK NY(USPS), ref RA123456785CN"
    links = extract_from_text(text)
    assert any(l.number == S10_VALID for l in links)


def test_s10_bad_check_digit_rejected():
    text = "Tendered to USPS, tracking RA123456789CN"
    assert extract_from_text(text) == []


def test_no_handoff_text_yields_nothing():
    assert extract_from_text("Out for delivery today") == []
    assert extract_from_text("Delivered - Signed for by R EXAMPLE") == []
    assert extract_from_text("") == []
    assert extract_from_text(None) == []


def test_exclude_primary_number():
    text = "Tendered to USPS 9400100000000000000001 for final delivery"
    assert extract_from_text(text, exclude=[USPS_22]) == []
    # exclusion canonicalizes: spaced/mixed-case forms also match
    assert extract_from_text(text, exclude=["9400 1000 0000 0000 0000 01"]) == []


def test_dedupe_repeated_mentions():
    text = (
        "Tendered to USPS 9400100000000000000001. "
        "Reminder: tendered to USPS 9400100000000000000001."
    )
    links = extract_from_text(text)
    assert len(links) == 1


def test_canonicalizes_extracted_numbers():
    text = "Tendered to USPS, tracking ra123456785cn"
    links = extract_from_text(text)
    assert links[0].number == S10_VALID  # uppercased


# -- whole-response walk ----------------------------------------------------------


def _ups_mi_response():
    """UPS-shaped response (mirrors carriers/ups/fixtures shape) with a Mail
    Innovations handoff in the activity description."""
    return {
        "trackResponse": {
            "shipment": [
                {
                    "inquiryNumber": "1Z999AA10123456784",
                    "package": [
                        {
                            "trackingNumber": "1Z999AA10123456784",
                            "activity": [
                                {
                                    "status": {
                                        "type": "I",
                                        "description": "Tendered to USPS "
                                                       "9400100000000000000001",
                                        "code": "MP",
                                    },
                                    "date": "20240104",
                                    "time": "120300",
                                },
                                {
                                    "status": {
                                        "type": "I",
                                        "description": "Departed from UPS facility",
                                        "code": "DP",
                                    },
                                    "date": "20240103",
                                    "time": "081200",
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    }


def test_extract_from_response_finds_handoff():
    links = extract_from_response(
        _ups_mi_response(), exclude=["1Z999AA10123456784"]
    )
    assert len(links) == 1
    assert links[0].number == USPS_22
    assert links[0].carrier == "usps"
    assert links[0].role == "handoff"
    assert isinstance(links[0], LinkedNumber)


def test_extract_from_response_dedupes_across_fields():
    data = {
        "statusSummary": "Tendered to USPS 9400100000000000000001",
        "trackingEvents": [
            {"eventType": "Tendered to USPS 9400100000000000000001"},
        ],
    }
    links = extract_from_response(data)
    assert len(links) == 1


def test_extract_from_response_ignores_home_number():
    # Without exclude, the primary USPS-shaped number in the response is
    # only returned if a handoff phrase points at it; here none does.
    data = {"trackingNumber": USPS_22, "status": "In Transit"}
    assert extract_from_response(data, exclude=[USPS_22]) == []


# -- real pack fixtures (shapes stay honest) --------------------------------------


@pytest.mark.parametrize("fixture", [
    FIXTURES / "dhl" / "fixtures" / "track_delivered.json",
    FIXTURES / "ups" / "fixtures" / "track_delivered.json",
    FIXTURES / "usps" / "fixtures" / "track_detail.json",
    FIXTURES / "fedex" / "fixtures" / "track_in_transit.json",
])
def test_real_fixtures_no_crash(fixture):
    """Walking real pack fixtures must never raise and must not invent
    handoffs (none of these responses announces a last-mile transfer)."""
    data = json.loads(fixture.read_text())
    links = extract_from_response(data, exclude=["1Z999AA10123456784"])
    assert isinstance(links, list)
    assert links == []
