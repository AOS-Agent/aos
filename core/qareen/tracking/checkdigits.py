"""Check-digit validators for tracking numbers.

Check digits kill garbage candidates (phone numbers, order IDs, dates) for
free before any carrier probe is spent — the detection layer's cheapest
filter. Validators are looked up BY NAME from a manifest's
``tracking.check_digit`` field, so the linter can reject unknown names and
later packs can register carrier-specific algorithms (USPS mod-10 variant,
FedEx mod-11, …) without touching the engine.

Contract: a validator takes the CANONICAL tracking number (spaces/hyphens
stripped, uppercased) and returns True iff the check digit — by convention
the LAST character — is consistent with the rest of the number. Validators
must return False (never raise) on input they can't handle.
"""

from typing import Callable, Dict, List

Validator = Callable[[str], bool]

_REGISTRY: Dict[str, Validator] = {}


def register(name: str, func: Validator) -> None:
    """Register a validator under *name* (manifests reference this name)."""
    if not name:
        raise ValueError("validator name must be non-empty")
    _REGISTRY[name] = func


def get(name: str) -> Validator:
    """Look up a validator; KeyError if unknown (linter catches this earlier)."""
    return _REGISTRY[name]


def names() -> List[str]:
    """All registered validator names — used by the pack linter."""
    return sorted(_REGISTRY)


def mod10(number: str) -> bool:
    """Luhn mod-10 check over a digit string; last digit is the check digit.

    Doubling every second digit starting from the right of the payload,
    summing digit-wise, the total including the check digit must be ≡ 0
    (mod 10). Returns False on empty/non-digit input or length < 2.

    Note: UPS/USPS/FedEx/DHL/Canada Post each have their own variants,
    registered below (ups_mod10, usps_mod10, fedex, dhl_mod7,
    canadapost_mod10). This is the generic baseline the _template pack
    references.
    """
    if not number or len(number) < 2 or not number.isdigit():
        return False
    digits = [int(c) for c in number]
    payload, check = digits[:-1], digits[-1]
    total = 0
    # Walk payload right-to-left, doubling every second digit.
    for i, d in enumerate(reversed(payload)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total + check) % 10 == 0


register("mod10", mod10)


# ── Carrier-specific validators (auto-tracker#14/#19) ───────────────────────
#
# Algorithms and test numbers come from the published specs as encoded in
# jkeen/tracking_number_data (couriers/*.json — UPS/USPS/FedEx/DHL/Canada
# Post sections) and the MysteryTrackingNumber reference implementation;
# every validator below is verified in test_tracking_packs.py against that
# repo's full valid/invalid test-number corpus.
#
# Shared weighted-mod10 shape (the "mod10" checksum in the data repo):
# walk the payload left-to-right multiplying digits at even 0-based indexes
# by *even_mult* and odd indexes by *odd_mult*; the check digit makes the
# total a multiple of 10.

import re as _re


def _char_value(c: str) -> int:
    """Digit value of a tracking-number char; UPS maps A-Z to (ord-3)%10."""
    return int(c) if c.isdigit() else (ord(c) - 3) % 10


def _mod10_weighted(payload: str, even_mult: int, odd_mult: int, check: str) -> bool:
    """Weighted mod-10: check makes sum(digit*weight) ≡ 0 (mod 10)."""
    total = sum(
        _char_value(c) * (even_mult if i % 2 == 0 else odd_mult)
        for i, c in enumerate(payload)
    )
    return (10 - total % 10) % 10 == int(check)


def _mod11_weighted(payload: str, weights: List[int], check: str) -> bool:
    """FedEx Express: sum(digit*weight) % 11 % 10 == check digit."""
    total = sum(int(d) * w for d, w in zip(payload, weights))
    return total % 11 % 10 == int(check)


def _s10(payload: str, check: str) -> bool:
    """UPU S10 check over the 8-digit serial of a 13-char postal number."""
    total = sum(int(d) * w for d, w in zip(payload, (8, 6, 4, 2, 3, 5, 9, 7)))
    remainder = total % 11
    expected = 0 if remainder == 1 else (5 if remainder == 0 else 11 - remainder)
    return expected == int(check)


def ups_mod10(number: str) -> bool:
    """UPS check digit for 1Z… (18 chars) and waybill (A/H/J/K/T/V…) numbers.

    1Z: 15-char serial after "1Z", letters mapped via (ord-3)%10, weights
    1 (even index) / 2 (odd index). Waybill: 9-digit serial, same weights.
    Worked example: 1Z5R89390357567127 → payload sum 123 → check 7.
    """
    if _re.fullmatch(r"1Z[0-9A-Z]{16}", number):
        return _mod10_weighted(number[2:17], 1, 2, number[-1])
    if _re.fullmatch(r"[AHJKTV][0-9]{10}", number):
        return _mod10_weighted(number[1:10], 1, 2, number[-1])
    return False


def usps_mod10(number: str) -> bool:
    """USPS weighted mod-10 (weights 3/1) for 20–34 digit USPS numbers.

    Payload is the number minus its last (check) digit; a leading 420…
    routing block (ZIP routing prefix) is skipped. IMpb numbers that don't
    start with 91–95 are also tried with the "91" application-identifier
    prepended, per the published serial_number_format rule (e.g.
    "7196 9010 …" validates as "91" + serial).
    Worked example: 9400111206206406260787 → payload sum 123 → check 7.
    """
    if not number.isdigit() or not (20 <= len(number) <= 34):
        return False
    if len(number) > 22 and number.startswith("420"):
        payload = number[-22:-1]
    else:
        payload = number[:-1]
    candidates = [payload]
    if not _re.match(r"9[1-5]", payload):
        candidates.append("91" + payload)
    return any(_mod10_weighted(c, 3, 1, number[-1]) for c in candidates)


def fedex(number: str) -> bool:
    """FedEx check digit, dispatched on the number's format/length.

      12 digits            Express: weights [3,1,7]…, %11 %10
      15 digits            Ground: mod-10 weights 1/3 over first 14
      18 digits "00…"      SSCC-18: mod-10 weights 3/1 over digits[2:17]
      22 digits "96…"      Ground 96: mod-10 weights 1/3 over digits[7:21]
      34 digits "96…"/"10…" GSN/Express-34: weights [1,7,3]…, %11 %10,
                           over the 13-digit serial before the check digit
      20/22/30 digits      SmartPost ("92…"/"61…", optional 420 routing):
                           USPS-style mod-10 weights 3/1, "92" prepended
                           when absent
    Worked example: 986578788855 → weighted sum 269 → 269%11%10 = 5.
    """
    if not number.isdigit():
        return False
    n = len(number)
    if n == 12:
        return _mod11_weighted(number[:-1], [3, 1, 7] * 4, number[-1])
    if n == 15:
        return _mod10_weighted(number[:-1], 1, 3, number[-1])
    if n == 18 and number.startswith("00"):
        return _mod10_weighted(number[2:17], 3, 1, number[-1])
    if n == 22 and number.startswith("96"):
        return _mod10_weighted(number[7:21], 1, 3, number[-1])
    if n == 34 and (number.startswith("96") or number.startswith("10")):
        return _mod11_weighted(number[-14:-1], [1, 7, 3] * 5, number[-1])
    if n in (20, 22, 30):
        payload = number[:-1] if n == 20 else number[-22:-1]
        if payload.startswith("92") or payload.startswith("61"):
            if not payload.startswith("92"):
                payload = "92" + payload
            return _mod10_weighted(payload, 3, 1, number[-1])
    return False


def dhl_mod7(number: str) -> bool:
    """DHL Express check digit: int(serial) % 7 == check digit (10–11 digits).

    DHL formats without a check digit (JJD…/JVGL… piece IDs, GM…
    e-commerce, S10-style e-commerce) return True once they match their
    documented shape — the check digit only exists for Express waybills.
    Worked example: 3318810025 → 331881002 % 7 = 5.

    The accepted shapes below MUST stay in step with
    carriers/dhl/manifest.yaml `tracking.patterns`. A shape validated here
    but not matched there is dead code; a shape matched there but not
    validated here is silently dropped after detection. The manifest
    carries a precision note explaining why the bare-digit e-commerce
    pattern was removed — do not re-add it here either.
    """
    if number.isdigit() and 10 <= len(number) <= 11:
        return int(number[:-1]) % 7 == int(number[-1])
    if _re.fullmatch(r"J[A-Z]{2,3}[0-9]{9,10}", number):
        return True
    if _re.fullmatch(r"(?:GM|LX|RX|UV|CN|SG|TH|IN|HK|MY)[0-9A-Z]{12,39}", number):
        return True
    if _re.fullmatch(r"[0-9]{14}", number):
        return True
    return False


def canadapost_mod10(number: str) -> bool:
    """Canada Post: weighted mod-10 (3/1) for 16-digit PINs; UPU S10 for
    13-char international numbers (…CA). Worked example:
    0073938000549297 → payload sum 153 → check 7."""
    if number.isdigit() and len(number) == 16:
        return _mod10_weighted(number[:15], 3, 1, number[-1])
    if _re.fullmatch(r"[A-Z]{2}[0-9]{9}[A-Z]{2}", number):
        return _s10(number[2:10], number[10])
    return False


register("ups_mod10", ups_mod10)
register("usps_mod10", usps_mod10)
register("fedex", fedex)
register("dhl_mod7", dhl_mod7)
register("canadapost_mod10", canadapost_mod10)
