"""
Tests for core/infra/lib/validate.py's operator.yaml key allowlist.

_OPERATOR_KNOWN is a claim about what the system reads, and it has drifted
before: commit f67adde added `location`, `prayer`, `nickname`, and
`notifications` after they were flagged "Unknown key" on every single
self-test run despite being read by shipped code. The same audit that found
that (aos#237, the faisal-mini parity comparison) found two more fields a
real operator's operator.yaml uses that the allowlist still didn't know
about: `email` and `businesses`. Four permanent warnings already trained one
operator to ignore the warnings section — the fix is the same each time:
add the field here, in the same commit that notices it's missing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
VALIDATE_PATH = REPO / "core" / "infra" / "lib" / "validate.py"


def _load_validate_module():
    spec = importlib.util.spec_from_file_location("validate_under_test", VALIDATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


validate = _load_validate_module()


def _operator_warnings(tmp_path: Path, data: dict) -> list[str]:
    path = tmp_path / "operator.yaml"
    path.write_text(yaml.dump(data))
    results = validate._validate_operator(str(path))
    return [r.message for r in results if r.level == "warning"]


def _operator_errors(tmp_path: Path, data: dict) -> list[str]:
    path = tmp_path / "operator.yaml"
    path.write_text(yaml.dump(data))
    results = validate._validate_operator(str(path))
    return [r.message for r in results if r.level == "error"]


REQUIRED = {"name": "Test Operator", "timezone": "UTC", "schedule": {}}


# ── The two fields this migration/audit found ────────────────────────────────


def test_email_is_a_known_operator_field():
    assert "email" in validate._OPERATOR_KNOWN


def test_businesses_is_a_known_operator_field():
    assert "businesses" in validate._OPERATOR_KNOWN


def test_operator_yaml_with_email_and_businesses_has_no_unknown_key_warning(tmp_path):
    data = {
        **REQUIRED,
        "email": "operator@example.com",
        "businesses": ["Example Co", "Side Project LLC"],
    }
    warnings = _operator_warnings(tmp_path, data)
    assert warnings == []


# ── The four fields f67adde already fixed — regression guard ────────────────


def test_previously_fixed_fields_remain_known(tmp_path):
    data = {
        **REQUIRED,
        "location": {"latitude": 0, "longitude": 0, "city": "Nowhere"},
        "prayer": {"method": "NorthAmerica"},
        "nickname": "Boss",
        "notifications": {"morning_briefing": True},
    }
    warnings = _operator_warnings(tmp_path, data)
    assert warnings == []


# ── This Mini's real operator.yaml shape produces no warnings ───────────────


def test_reference_operator_yaml_shape_has_no_unknown_key_warnings(tmp_path):
    """Every top-level key this machine's own operator.yaml actually uses."""
    data = {
        "name": "Hisham Al Hadi",
        "timezone": "America/Toronto",
        "location": {"latitude": 43.59, "longitude": -79.64, "city": "Mississauga"},
        "prayer": {"method": "NorthAmerica"},
        "communication": {"style": "concise", "questions": "one-at-a-time", "language": "en"},
        "schedule": {"blocks": [], "weekends": {"active_after": "fajr"}},
        "daily_loop": {"morning_briefing": "06:00", "evening_checkin": "21:00"},
        "trust": {"default_level": 1, "escalation": "always"},
        "agent_name": "chief",
        "initiatives": {"enabled": True, "max_active": 3},
        "nickname": "Mr. Al Hadi",
        "notifications": {"learning_tips": True, "morning_briefing": True},
        "role": "developer",
    }
    warnings = _operator_warnings(tmp_path, data)
    assert warnings == []


# ── The allowlist still rejects an actually-unknown key ─────────────────────


def test_a_genuinely_unknown_key_still_warns(tmp_path):
    data = {**REQUIRED, "totally_made_up_field": 1}
    warnings = _operator_warnings(tmp_path, data)
    assert warnings == ["Unknown key: totally_made_up_field"]


def test_missing_required_fields_still_error(tmp_path):
    errors = _operator_errors(tmp_path, {"name": "Test"})
    assert "Missing required field: schedule" in errors
    assert "Missing required field: timezone" in errors
