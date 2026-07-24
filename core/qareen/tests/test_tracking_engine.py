"""Tests for the tracking engine: pack discovery/loading, canonicalization,
number validation, status/event normalization, and the jsonpath subset.

Fixtures are tmp_path pack directories — no test touches the real carriers/
tree except the read-only "template pack loads green" check.
"""

import sys
from pathlib import Path

import pytest
import yaml

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import engine, jsonpath, packs  # noqa: E402
from qareen.tracking.models import Milestone  # noqa: E402

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "tracking" / "carriers" / "_template"


def _write_pack(carriers_dir: Path, slug: str, manifest: dict) -> Path:
    pack_dir = carriers_dir / slug
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    return pack_dir


def _minimal_manifest(slug: str, **overrides) -> dict:
    manifest = {
        "carrier": slug,
        "display_name": slug.title(),
        "auth": {"model": "none"},
        "endpoints": {"base": "https://api.example.com", "track": "https://api.example.com/t/{number}"},
        "tracking": {"patterns": ["[0-9]{11}"], "check_digit": "mod10"},
        "capabilities": {"edd": True, "pod": False, "push": False},
        "status_map": {"IT": "in_transit", "DL": "delivered"},
        "response_map": {
            "events": "$.events[*]",
            "event_fields": {"code": "$.code", "description": "$.desc"},
        },
        "rate_limits": {"requests_per_day": 100, "min_interval_seconds": 1},
        "retention": {"delete_days_after_delivery": None},
    }
    manifest.update(overrides)
    return manifest


# ── discovery + loading ─────────────────────────────────────────────────

def test_template_pack_loads_green():
    """The scaffold itself must pass lint and load — it's the source for
    every future `aos track add`."""
    pack = packs.load_pack(TEMPLATE_DIR)
    assert pack.slug == "_template"
    assert pack.check_digit == "mod10"
    assert pack.patterns  # non-empty


def test_discover_packs_finds_template_in_real_tree():
    found = packs.discover_packs()
    assert TEMPLATE_DIR in found


def test_load_packs_skips_underscore_scaffolds_by_default():
    loaded = packs.load_packs()
    assert "_template" not in loaded
    with_template = packs.load_packs(include_scaffolds=True)
    assert "_template" in with_template


def test_discover_and_load_from_tmp_tree(tmp_path):
    carriers = tmp_path / "carriers"
    _write_pack(carriers, "acme", _minimal_manifest("acme"))
    _write_pack(carriers, "globex", _minimal_manifest("globex"))
    (carriers / "not-a-pack").mkdir()  # no manifest.yaml → not discovered

    assert [d.name for d in packs.discover_packs(carriers)] == ["acme", "globex"]
    loaded = packs.load_packs(carriers)
    assert sorted(loaded) == ["acme", "globex"]
    assert loaded["acme"].display_name == "Acme"


def test_load_pack_rejects_invalid_manifest(tmp_path):
    carriers = tmp_path / "carriers"
    bad = _minimal_manifest("bad", tracking={"patterns": ["[0-9]+"]})  # unbounded
    pack_dir = _write_pack(carriers, "bad", bad)
    with pytest.raises(packs.PackError):
        packs.load_pack(pack_dir)


def test_load_pack_rejects_slug_mismatch(tmp_path):
    carriers = tmp_path / "carriers"
    pack_dir = _write_pack(carriers, "acme", _minimal_manifest("other-name"))
    with pytest.raises(packs.PackError):
        packs.load_pack(pack_dir)


def test_load_pack_rejects_missing_manifest(tmp_path):
    empty = tmp_path / "carriers" / "ghost"
    empty.mkdir(parents=True)
    with pytest.raises(packs.PackError):
        packs.load_pack(empty)


# ── canonicalization ────────────────────────────────────────────────────

def test_canonicalize_strips_spaces_hyphens_uppercases():
    assert engine.canonicalize("9400 1000 0000 0000 0000 00") == "9400100000000000000000"
    assert engine.canonicalize("1z-abc-123") == "1ZABC123"
    assert engine.canonicalize("  1Z999  \n") == "1Z999"


def test_canonicalize_rejects_non_strings():
    with pytest.raises(TypeError):
        engine.canonicalize(123)


# ── number validation (regex + check digit) ─────────────────────────────

def _pack(tmp_path, **overrides):
    pack_dir = _write_pack(tmp_path / "carriers", "acme", _minimal_manifest("acme", **overrides))
    return packs.load_pack(pack_dir)


def test_validate_number_accepts_valid_luhn_number(tmp_path):
    pack = _pack(tmp_path)  # pattern [0-9]{11} + mod10
    assert engine.validate_number(pack, "7992 7398 713") is True  # classic Luhn sample
    assert engine.validate_number(pack, "79927398713") is True


def test_validate_number_rejects_bad_check_digit(tmp_path):
    pack = _pack(tmp_path)
    assert engine.validate_number(pack, "79927398710") is False


def test_validate_number_rejects_pattern_mismatch_even_with_valid_luhn(tmp_path):
    pack = _pack(tmp_path, tracking={"patterns": ["[0-9]{12}"], "check_digit": "mod10"})
    # 79927398713 is valid Luhn but only 11 digits → pattern fails.
    assert engine.validate_number(pack, "79927398713") is False


def test_validate_number_without_check_digit_is_pattern_only(tmp_path):
    pack = _pack(tmp_path, tracking={"patterns": ["[0-9]{11}"], "check_digit": None})
    assert engine.validate_number(pack, "79927398710") is True  # bad Luhn, no validator
    assert engine.validate_number(pack, "123") is False


# ── status + event normalization ────────────────────────────────────────

def test_normalize_status_exact_and_case_insensitive(tmp_path):
    pack = _pack(tmp_path)
    assert engine.normalize_status(pack, "IT") is Milestone.IN_TRANSIT
    assert engine.normalize_status(pack, "dl") is Milestone.DELIVERED


def test_normalize_status_unknown_code_returns_none(tmp_path):
    pack = _pack(tmp_path)
    assert engine.normalize_status(pack, "SOME_NEW_CODE") is None
    assert engine.normalize_status(pack, None) is None


def test_normalize_event_maps_via_response_map(tmp_path):
    pack = _pack(tmp_path)
    raw = {"code": "IT", "desc": "Departed facility", "extra": {"keep": 1}}
    event = engine.normalize_event(pack, raw)
    assert event.milestone is Milestone.IN_TRANSIT
    assert event.description == "Departed facility"
    assert event.carrier_code == "IT"
    assert event.raw is raw  # raw preserved verbatim for the append-only store


def test_normalize_event_unknown_code_keeps_raw_with_no_milestone(tmp_path):
    pack = _pack(tmp_path)
    event = engine.normalize_event(pack, {"code": "WEIRD", "desc": "???"})
    assert event.milestone is None
    assert event.carrier_code == "WEIRD"


# ── jsonpath subset ─────────────────────────────────────────────────────

def test_jsonpath_extract_nested_key_and_index():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert jsonpath.extract(data, "$.a.b[1].c") == 2
    assert jsonpath.extract(data, "$.a.b[-1].c") == 2


def test_jsonpath_extract_wildcard_returns_list():
    data = {"events": [{"code": "A"}, {"code": "B"}]}
    assert jsonpath.extract(data, "$.events[*].code") == ["A", "B"]


def test_jsonpath_extract_missing_path_returns_none():
    assert jsonpath.extract({"a": 1}, "$.b.c") is None
    assert jsonpath.extract({"a": [1]}, "$.a[5]") is None


def test_jsonpath_parse_rejects_outside_subset():
    for bad in ("a.b", "$..b", "$['a']", "$.a[?@.x]", ""):
        assert not jsonpath.is_valid(bad)
    assert jsonpath.is_valid("$.a.b[0].c")
