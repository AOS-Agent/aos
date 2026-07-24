"""Handoff / linked-number extraction from carrier responses (pure).

Initiative §5 (``shipment_numbers``) + risk #3: international shipments
change tracking numbers at the border — DHL eCommerce and UPS Mail
Innovations tender to USPS for the last mile, Chit Chats inducts into USPS
/ Canada Post, and S10 UPU 13-char numbers are shared across national
posts. The carrier's track response tells us about the handoff in free-text
event descriptions ("Tendered to USPS 9400 …"), so we parse those texts and
return linked-number records for the caller to persist via
``store.add_number(shipment_id, number, carrier, role)``.

Everything here is a pure function: no I/O, no store access, no network.
``extract_from_text`` scans one string; ``extract_from_response`` walks a
whole carrier track-response JSON (any nesting) and applies it to every
string value. Both dedupe and support an ``exclude`` set so the shipment's
own primary number is never re-registered as a handoff.

S10 candidates are check-digit validated (standard UPU algorithm, same as
``checkdigits._s10`` — mirrored here to keep this module dependency-light)
so serial numbers and SKUs that merely LOOK like S10 don't become phantom
links.

Python 3.9-compatible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Set, Tuple

from .engine import canonicalize

# ---------------------------------------------------------------------------
# Number patterns
# ---------------------------------------------------------------------------

# USPS IMpb: 22 digits starting with 9 (tolerate 20–26 for channel variants).
_USPS_RE = re.compile(r"\b9\d{19,25}\b")

# UPU S10: 2 letters, 9 digits, 2 letters (e.g. RA123456785CN). Shared
# across national posts — the last two letters are the ORIGIN country.
_S10_RE = re.compile(r"\b[A-Z]{2}\d{9}[A-Z]{2}\b")

# Canada Post domestic PIN: 16 digits. High false-positive risk, so it is
# only accepted inside a Canada-Post-named handoff phrase window.
_CANPOST_16_RE = re.compile(r"\b\d{16}\b")

_S10_WEIGHTS = (8, 6, 4, 2, 3, 5, 9, 7)


def s10_check_ok(number: str) -> bool:
    """UPU S10 check digit over the 8-digit serial of a 13-char number."""
    if not _S10_RE.fullmatch(number):
        return False
    serial, check = number[2:10], number[10]
    total = sum(int(d) * w for d, w in zip(serial, _S10_WEIGHTS))
    remainder = total % 11
    expected = 0 if remainder == 1 else (5 if remainder == 0 else 11 - remainder)
    return expected == int(check)


# ---------------------------------------------------------------------------
# Handoff phrase patterns: (regex, carrier slug or None)
# ---------------------------------------------------------------------------
#
# carrier None means "a last-mile/national-post handoff was announced but
# the phrase doesn't name which post" — the number shape then decides (USPS
# digits → usps; S10 → unknown national post, carrier stays None and the
# caller resolves it against destination-country context if it has any).

_PHRASES: List[Tuple[re.Pattern, Optional[str]]] = [
    (re.compile(r"tendered\s+to\s+(?:the\s+)?(?:usps|u\.?s\.?\s*postal\s+service"
                r"|united\s+states\s+postal\s+service)", re.I), "usps"),
    (re.compile(r"(?:handed|transferred|delivered|tendered|given)\s+(?:over\s+)?to\s+"
                r"(?:the\s+)?usps\b", re.I), "usps"),
    # UPS Mail Innovations final mile is always USPS.
    (re.compile(r"mail\s+innovations", re.I), "usps"),
    (re.compile(r"(?:tendered|handed|transferred|given)\s+(?:over\s+)?to\s+"
                r"(?:the\s+)?canada\s*post", re.I), "canadapost"),
    # Generic last-mile phrases (DHL eCommerce → local post, etc.).
    (re.compile(r"tendered\s+to\s+(?:the\s+)?delivery\s+(?:partner|agent)", re.I), None),
    (re.compile(r"tendered\s+to\s+(?:the\s+)?(?:final[-\s]?mile|last[-\s]?mile|local)"
                r"(?:\s+(?:delivery\s+)?(?:carrier|agent|partner|post))?", re.I), None),
    (re.compile(r"(?:handed|transferred|delivered|tendered)\s+(?:over\s+)?to\s+"
                r"(?:the\s+)?(?:local\s+)?post(?:al)?\s+(?:office|service|carrier|operator)",
                re.I), None),
    (re.compile(r"arrived\s+at\s+(?:the\s+)?(?:destination\s+)?post\s+office", re.I), None),
]

# How far past a phrase start we look for the handoff number.
_WINDOW = 120


@dataclass
class LinkedNumber:
    """One linked tracking number discovered in carrier data.

    ``carrier`` is the pack slug of the carrier the number belongs to
    (``usps``, ``canadapost``), or None when the text proves a handoff but
    not which national post — the caller may resolve it from destination
    context or store it under the shipment's own carrier.
    """

    number: str  # canonical (engine.canonicalize)
    carrier: Optional[str]
    role: str = "handoff"  # store.add_number role: primary|handoff
    evidence: str = ""  # matched text snippet (debugging/audit)

    def key(self) -> Tuple[Optional[str], str]:
        return (self.carrier, self.number)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _numbers_in_window(window: str, phrase_carrier: Optional[str]) -> List[Tuple[str, Optional[str]]]:
    """(raw_number, carrier) pairs found in one phrase window."""
    found: List[Tuple[str, Optional[str]]] = []
    m = _USPS_RE.search(window)
    if m:
        found.append((m.group(0), "usps"))
    m = _S10_RE.search(window)
    if m and s10_check_ok(m.group(0)):
        found.append((m.group(0), phrase_carrier))
    if phrase_carrier == "canadapost":
        m = _CANPOST_16_RE.search(window)
        if m:
            found.append((m.group(0), "canadapost"))
    return found


def extract_from_text(
    text: str,
    *,
    exclude: Optional[Iterable[str]] = None,
) -> List[LinkedNumber]:
    """Scan one free-text string for last-mile handoff numbers.

    Two passes: (1) each handoff phrase gets a forward window searched for
    a number; (2) bare S10 numbers anywhere in the text (they are shared
    across national posts, so a stray S10 in a carrier response is almost
    always a cross-post link). ``exclude`` holds canonical numbers to skip
    (the shipment's own primary number).
    """
    if not text or not isinstance(text, str):
        return []
    skip: Set[str] = set()
    for n in exclude or ():
        try:
            skip.add(canonicalize(n))
        except TypeError:
            continue

    upper = text.upper()
    out: List[LinkedNumber] = []
    seen: Set[str] = set()

    def emit(raw: str, carrier: Optional[str], evidence: str) -> None:
        number = canonicalize(raw)
        if number in skip or number in seen:
            return
        seen.add(number)
        out.append(LinkedNumber(number=number, carrier=carrier,
                                evidence=evidence.strip()[:160]))

    # Pass 1: phrase-anchored numbers.
    for pattern, phrase_carrier in _PHRASES:
        for match in pattern.finditer(text):
            window = text[match.start(): match.start() + _WINDOW]
            window_upper = upper[match.start(): match.start() + _WINDOW]
            for raw, carrier in _numbers_in_window(window_upper, phrase_carrier):
                emit(raw, carrier or phrase_carrier, window)

    # Pass 2: bare S10 anywhere (check-digit validated kills lookalikes).
    for match in _S10_RE.finditer(upper):
        if s10_check_ok(match.group(0)):
            emit(match.group(0), None, text[match.start(): match.start() + 40])

    return out


def _walk_strings(data: Any) -> Iterable[str]:
    """Yield every string value in a nested JSON structure."""
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for value in data.values():
            for s in _walk_strings(value):
                yield s
    elif isinstance(data, (list, tuple)):
        for item in data:
            for s in _walk_strings(item):
                yield s


def extract_from_response(
    data: Any,
    *,
    exclude: Optional[Iterable[str]] = None,
) -> List[LinkedNumber]:
    """Walk a whole carrier track-response JSON for handoff numbers.

    Applies ``extract_from_text`` to every string value and dedupes on
    (carrier, number) — a handoff reported in both the status summary and
    an event description yields ONE record. ``exclude`` should include the
    shipment's primary tracking number.
    """
    out: List[LinkedNumber] = []
    seen: Set[Tuple[Optional[str], str]] = set()
    for text in _walk_strings(data):
        for link in extract_from_text(text, exclude=exclude):
            if link.key() in seen:
                continue
            seen.add(link.key())
            out.append(link)
    return out
