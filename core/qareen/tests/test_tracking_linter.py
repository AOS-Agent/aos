"""Tests for the pack manifest linter and check-digit validators.

Linter tests call lint_manifest on dicts directly — no files needed — plus
one integration check that the on-disk _template pack is lint-clean.
"""

import sys
from pathlib import Path

import pytest
import yaml

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import checkdigits, linter  # noqa: E402

TEMPLATE_MANIFEST = (
    Path(__file__).resolve().parents[1] / "tracking" / "carriers" / "_template" / "manifest.yaml"
)


def _valid_manifest() -> dict:
    return {
        "carrier": "acme",
        "display_name": "Acme",
        "auth": {"model": "none"},
        "endpoints": {"base": "https://api.example.com"},
        "tracking": {"patterns": ["[0-9]{11}"], "check_digit": "mod10"},
        "capabilities": {"edd": True, "pod": False, "push": False},
        "status_map": {"IT": "in_transit"},
        "response_map": {"events": "$.events[*]", "event_fields": {"code": "$.code"}},
        "rate_limits": {"requests_per_day": 100, "min_interval_seconds": 1},
        "retention": {"delete_days_after_delivery": None},
    }


def _lint(**overrides) -> list:
    manifest = _valid_manifest()
    manifest.update(overrides)
    return linter.lint_manifest(manifest)


# ── happy path ──────────────────────────────────────────────────────────

def test_valid_manifest_lints_clean():
    assert _lint() == []


def test_template_manifest_on_disk_lints_clean():
    manifest = yaml.safe_load(TEMPLATE_MANIFEST.read_text())
    assert linter.lint_manifest(manifest, source=str(TEMPLATE_MANIFEST)) == []


# ── ReDoS guard ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "pattern",
    [
        "[0-9]+",          # unbounded +
        "ab*",             # unbounded *
        "[0-9]{2,}",       # open-ended repeat
        "(a{2,3})+",       # nested quantifier
        "([0-9]{2}){3}",   # nested quantifier, both bounded
        "(?:ab|cd)+",      # unbounded quantified group
        "(?=x*[0-9])a{2}", # unbounded quantifier hiding in a lookahead
    ],
)
def test_linter_rejects_redos_patterns(pattern):
    problems = _lint(tracking={"patterns": [pattern], "check_digit": None})
    assert problems, "expected %r to be rejected" % pattern


@pytest.mark.parametrize(
    "pattern",
    [
        "1Z[0-9A-Z]{16}",
        "[0-9]{22}",
        "[0-9]{4} [0-9]{4} [0-9]{4}",
        "(?:1Z|9Z)[0-9A-Z]{16}",  # bounded alternation, bounded repeat
        "[A-Z]{2}[0-9]{9}[A-Z]{2}",  # S10 UPU shape
    ],
)
def test_linter_accepts_bounded_flat_patterns(pattern):
    assert _lint(tracking={"patterns": [pattern], "check_digit": None}) == []


def test_linter_rejects_uncompilable_pattern():
    problems = _lint(tracking={"patterns": ["([0-9]{3}"], "check_digit": None})
    assert any("does not compile" in p for p in problems)


# ── check-digit validator references ────────────────────────────────────

def test_linter_rejects_unknown_check_digit_validator():
    problems = _lint(tracking={"patterns": ["[0-9]{11}"], "check_digit": "sha256"})
    assert any("not a registered validator" in p for p in problems)


def test_linter_allows_null_check_digit():
    assert _lint(tracking={"patterns": ["[0-9]{11}"], "check_digit": None}) == []


# ── response_map ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "bad_path",
    ["events[*]", "$..events", "$['events']", "", "$.a[?@.b]"],
)
def test_linter_rejects_unparseable_response_map_paths(bad_path):
    problems = _lint(response_map={"events": bad_path})
    assert any("not a parseable path" in p for p in problems)


def test_linter_walks_nested_response_maps():
    problems = _lint(response_map={"event_fields": {"code": "$.code", "eta": "no-dollar"}})
    assert len(problems) == 1
    assert "eta" in problems[0]


# ── structural checks ───────────────────────────────────────────────────

def test_linter_reports_missing_sections():
    problems = linter.lint_manifest({"carrier": "acme"})
    for section in ("auth", "endpoints", "tracking", "status_map", "response_map"):
        assert any(section in p for p in problems)


def test_linter_rejects_unknown_status_map_milestone():
    problems = _lint(status_map={"IT": "flying"})
    assert any("not a canonical milestone" in p for p in problems)


def test_linter_rejects_unknown_auth_model():
    problems = _lint(auth={"model": "kerberos"})
    assert any("auth.model" in p for p in problems)


def test_linter_requires_keychain_key_names_for_authed_packs():
    problems = _lint(auth={"model": "api_key"})
    assert any("keychain_keys" in p for p in problems)


# ── mod10 validator + registry ──────────────────────────────────────────

def test_mod10_accepts_valid_luhn_numbers():
    assert checkdigits.mod10("79927398713") is True  # classic Luhn sample
    assert checkdigits.mod10("0" * 10 + "0") is True


def test_mod10_rejects_bad_check_digit():
    assert checkdigits.mod10("79927398710") is False
    assert checkdigits.mod10("79927398712") is False


def test_mod10_rejects_input_it_cannot_handle():
    assert checkdigits.mod10("") is False
    assert checkdigits.mod10("7") is False
    assert checkdigits.mod10("1ZABC") is False  # letters → False, never raises


def test_registry_exposes_mod10_and_roundtrips():
    assert "mod10" in checkdigits.names()
    assert checkdigits.get("mod10") is checkdigits.mod10
    with pytest.raises(KeyError):
        checkdigits.get("nope")


def test_registry_allows_later_packs_to_register():
    checkdigits.register("always_ok", lambda number: True)
    try:
        assert "always_ok" in checkdigits.names()
        assert checkdigits.get("always_ok")("anything") is True
        # …and the linter immediately accepts the new name.
        assert _lint(tracking={"patterns": ["[0-9]{11}"], "check_digit": "always_ok"}) == []
    finally:
        checkdigits._REGISTRY.pop("always_ok", None)
