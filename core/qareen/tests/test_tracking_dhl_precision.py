"""DHL pattern precision — a regression guard against the phone-number harvester.

Unlike test_tracking_detect.py (which builds toy packs in tmp_path), this
file deliberately loads the REAL carriers/dhl pack. The v0.7.0 precision
collapse was invisible precisely because no test ever ran detection against
a shipped pack.

Measured baseline: the original pattern set produced 120 distinct matches in
a 40,000-message sample of the real comms.db — English words ("INFRASTRUCTURE",
"INTERPRETATION") and phone numbers (international and NANP). After the fix:
zero.

The negative cases below mirror the SHAPE of what was matched, using 555-block
and all-zero fillers. They are deliberately NOT the observed strings: those
were real phone numbers belonging to real contacts, and test fixtures ship to
a public repo. Preserve the digit counts when editing — that is what the
patterns keyed on.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import detect  # noqa: E402
from qareen.tracking.checkdigits import dhl_mod7  # noqa: E402
from qareen.tracking.packs import load_packs  # noqa: E402


@pytest.fixture(scope="module")
def dhl_scan():
    packs = load_packs()
    assert "dhl" in packs, "real dhl pack must load"
    return detect._scan_regex(packs["dhl"])


def _detects(scan, token: str) -> bool:
    """Full pipeline gate: pattern scan AND check-digit validation."""
    return bool(scan.search(token)) and dhl_mod7(token)


# ── real formats must still be found (precision must not cost all recall) ──

@pytest.mark.parametrize(
    "number,label",
    [
        ("JJD0099999999", "Express piece ID (JJD)"),
        ("JVGL0999999990", "Express piece ID (JVGL)"),
        ("GM2951173225174494", "eCommerce GM (all digits)"),
        ("GM9E44608A27984866BA2D", "eCommerce GM (mixed alnum)"),
    ],
)
def test_real_dhl_formats_are_detected(dhl_scan, number, label):
    """Precision must not cost all recall — these still detect in body text."""
    assert _detects(dhl_scan, number), f"{label} no longer detected: {number}"


@pytest.mark.parametrize(
    "number,label",
    [
        ("3318810025", "Express waybill 10 (mod-7 valid)"),
        ("73891051146", "Express waybill 11"),
        ("60120172242323", "eCommerce 14-digit"),
    ],
)
def test_excluded_formats_stay_valid_for_trusted_paths(number, label):
    """Excluded from body scanning, still valid for manual add / URLs.

    body_scan_exclude must not narrow what the carrier *recognizes* — a
    number pasted by the operator or lifted from a dhl.com URL is already
    known to be a tracking number, so recall wins there.
    """
    from qareen.tracking import engine

    pack = load_packs()["dhl"]
    assert engine.validate_number(pack, number), (
        f"{label} must remain valid for trusted paths: {number}"
    )


# ── the measured false positives must stay rejected ───────────────────────

@pytest.mark.parametrize(
    "token,why",
    [
        ("INFRASTRUCTURE", "English word: 'IN' + 12 alnum under IGNORECASE"),
        ("INTERPRETATION", "English word"),
        ("MYAB1CD2EF3GH4IJ5KL6MN7OP8", "opaque token starting 'MY'"),
        ("00966555000000", "intl. phone number, 14 digits"),
        ("00989555000000", "intl. phone number, 14 digits"),
        ("4165550123", "NANP 10-digit phone number"),
        ("02135550123", "11-digit phone number"),
        ("53055500000000", "bare 14-digit number"),
    ],
)
def test_measured_false_positives_stay_rejected(dhl_scan, token, why):
    assert not _detects(dhl_scan, token), (
        f"DHL pattern regression — {token} matches again ({why}). "
        "See the precision note in carriers/dhl/manifest.yaml."
    )


# ── the two lists must not drift apart ────────────────────────────────────

def test_manifest_patterns_and_checkdigit_shapes_agree():
    """Every pattern the manifest matches must survive dhl_mod7.

    A shape matched by the manifest but rejected by the check digit is
    silently dropped after detection — the failure mode is invisible
    because detection "works" and nothing is ever persisted.
    """
    packs = load_packs()
    scan = detect._scan_regex(packs["dhl"])
    for number in ("JJD0099999999", "GM2951173225174494", "GM9E44608A27984866BA2D"):
        assert scan.search(number), f"manifest no longer matches {number}"
        assert dhl_mod7(number), (
            f"{number} matches the manifest but fails dhl_mod7 — the two "
            "have drifted; see the docstring in checkdigits.dhl_mod7"
        )


def test_bare_digit_patterns_are_excluded_from_body_scanning():
    """Guard the specific decision, not just its symptoms.

    Bare-digit waybills cannot be told apart from phone numbers in free
    text, and dhl_mod7 filters only ~6/7 by chance. They stay in `patterns`
    (valid for manual add and URLs) but must never reach `scan_patterns`.
    """
    pack = load_packs()["dhl"]
    bare = {"[0-9]{10}", "[0-9]{11}", "[0-9]{14}"}

    assert bare.issubset(set(pack.patterns)), (
        "bare-digit formats are real DHL waybills and must stay valid "
        "for trusted paths"
    )
    offenders = bare.intersection(pack.scan_patterns)
    assert not offenders, (
        f"bare-digit DHL patterns reached body scanning: {sorted(offenders)} "
        "— this re-opens the phone-number harvester (120 FPs / 40K messages)."
    )


def test_word_matching_prefixes_are_excluded_from_body_scanning():
    """IN…/MY… prefixes matched INFRASTRUCTURE and INTERPRETATION."""
    pack = load_packs()["dhl"]
    scan_blob = " ".join(pack.scan_patterns)
    assert "IN|" not in scan_blob and "|MY" not in scan_blob, (
        "the IN/MY eCommerce prefix alternation is back in body scanning"
    )
