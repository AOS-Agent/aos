"""Tracking engine — generic, pack-driven normalization and validation.

Everything here is carrier-agnostic: behavior comes from the pack manifest,
never from hardcoded carrier knowledge. Implemented now (auto-tracker#1):

- ``canonicalize``      — strip spaces/hyphens, uppercase (USPS prints
                          "9400 1000 …" with spaces; dedup keys on this)
- ``validate_number``   — pack regex fullmatch + check-digit validator
- ``normalize_status``  — carrier status code → canonical Milestone via the
                          pack's status_map
- ``normalize_event``   — raw carrier event dict → TrackingEvent, using the
                          pack's response_map ``event_fields``

Not here yet (later tasks): HTTP carrier calls and OAuth (engine client),
due-queue scheduler, detection consumer. Those plug into this module's
pack-loading and normalization entry points — nothing here changes shape
when they land.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from . import checkdigits, jsonpath
from .models import Milestone, TrackingEvent
from .packs import CarrierPack

_STRIP_CHARS = re.compile(r"[\s\-]+")


def canonicalize(number: str) -> str:
    """Canonical form of a tracking number: no spaces/hyphens, uppercased.

    Canonicalization happens BEFORE keying/dedup: the same number arriving
    via merchant email + forward + iMessage must produce one shipment.
    """
    if not isinstance(number, str):
        raise TypeError("tracking number must be a string, got %r" % type(number))
    return _STRIP_CHARS.sub("", number).upper()


def matches_any_pattern(pack: CarrierPack, number: str) -> bool:
    """True iff *number* (already canonical) fullmatches any pack pattern."""
    return any(re.fullmatch(p, number) for p in pack.patterns)


def check_digit_ok(pack: CarrierPack, number: str) -> bool:
    """Run the pack's check-digit validator. No validator declared → True."""
    name = pack.check_digit
    if name is None:
        return True
    return checkdigits.get(name)(number)


def validate_number(pack: CarrierPack, number: str) -> bool:
    """True iff *number* is a plausible tracking number for *pack*.

    Canonicalizes first, then requires a pattern fullmatch AND the
    check-digit validator (when the pack declares one). Check digits kill
    garbage candidates — phone numbers, order IDs, dates — for free before
    any probe budget is spent.
    """
    canonical = canonicalize(number)
    return matches_any_pattern(pack, canonical) and check_digit_ok(pack, canonical)


def normalize_status(pack: CarrierPack, carrier_code: Any) -> Optional[Milestone]:
    """Map a carrier status code to a canonical Milestone via status_map.

    Lookup is exact, then case-insensitive. Returns None for codes the pack
    doesn't declare — callers treat None as "unknown, keep raw" rather than
    guessing a milestone the manifest never promised.
    """
    if not isinstance(carrier_code, str):
        return None
    if carrier_code in pack.status_map:
        return Milestone(pack.status_map[carrier_code])
    lowered = carrier_code.lower()
    for code, milestone in pack.status_map.items():
        if code.lower() == lowered:
            return Milestone(milestone)
    return None


def normalize_event(pack: CarrierPack, raw_event: Dict[str, Any]) -> TrackingEvent:
    """Normalize one raw carrier event dict into a TrackingEvent.

    Field paths come from the pack's ``response_map.event_fields``
    (``code``, ``description``, ``location``, ``timestamp``). The raw dict
    is preserved verbatim on the event — the append-only store keeps it,
    since carrier-side history can be purged server-side.

    Timestamp parsing is left to the storage layer (auto-tracker#2); the
    raw timestamp string rides in ``raw`` and ``carrier_code``.
    """
    fields = (pack.response_map.get("event_fields") or {})
    code = _extract_str(raw_event, fields.get("code"))
    return TrackingEvent(
        milestone=normalize_status(pack, code),
        description=_extract_str(raw_event, fields.get("description")) or "",
        location=_extract_str(raw_event, fields.get("location")),
        carrier_code=code,
        raw=raw_event,
    )


def _extract_str(data: Dict[str, Any], path: Optional[str]) -> Optional[str]:
    """jsonpath-extract a single string field; None when unmapped/missing."""
    if not path:
        return None
    value = jsonpath.extract(data, path)
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value is not None else None
