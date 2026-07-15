"""
Telegram message style (aos#170).

Bridge messages land on a phone. They must read as plain human English — no
internal priority codes, bracketed status tags, or terse "3d" shorthand. These
tests lock the humanization helpers in daily_briefing.py and guard the two
templates against the codes creeping back.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
BRIDGE = REPO_ROOT / "core" / "services" / "bridge"
DAILY = BRIDGE / "daily_briefing.py"


def _load_daily():
    if str(BRIDGE) not in sys.path:
        sys.path.insert(0, str(BRIDGE))
    spec = importlib.util.spec_from_file_location("daily_briefing_under_test", DAILY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db():
    return _load_daily()


def test_status_words_humanizes_internal_statuses(db):
    assert db._status_words("executing") == "in progress"
    assert db._status_words("planning") == "being planned"
    assert db._status_words("research") == "in research"
    assert db._status_words("shaping") == "taking shape"


def test_status_words_passes_through_unknown(db):
    assert db._status_words("whatever") == "whatever"


def test_days_ago_pluralizes(db):
    assert db._days_ago(1) == "1 day"
    assert db._days_ago(3) == "3 days"
    assert db._days_ago(0) == "0 days"


def test_templates_have_no_internal_codes():
    """The composed briefing/wrap strings must not carry P1/P2/[status]/Nd codes."""
    banned = [
        "— P1",
        "— P2",
        "[{init['status']}]",
        "{days_late}d\"",
        "phase {init['phase']}/{init['total_phases']}",
    ]
    for name in ("daily_briefing.py", "evening_checkin.py"):
        text = (BRIDGE / name).read_text()
        for token in banned:
            assert token not in text, f"{name} still emits internal code: {token!r}"


def test_message_style_guide_ships():
    guide = BRIDGE / "MESSAGE_STYLE.md"
    assert guide.exists(), "MESSAGE_STYLE.md must ship alongside the templates"
    body = guide.read_text().lower()
    assert "plain human english" in body
    assert "p1" in body  # documents the swap
