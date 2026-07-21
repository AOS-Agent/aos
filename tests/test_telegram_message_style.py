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


def test_style_guide_covers_system_alerts():
    """The style guide must document the system-alert standard (aos#170)."""
    body = (BRIDGE / "MESSAGE_STYLE.md").read_text().lower()
    assert "system alert" in body
    assert "alert anatomy" in body
    assert "alert_copy" in body  # points at the central translator


# --- System-alert emitters: guard the raw machine-speak from creeping back ---

def test_watchdog_alerts_are_humanized():
    """The watchdog's *Telegram* copy (the `notify "..."` calls, not its logs)
    must not regress to [AOS]/DEGRADED/migration/flapping jargon."""
    wd = (REPO_ROOT / "core" / "bin" / "crons" / "watchdog").read_text()
    notify_lines = [ln for ln in wd.splitlines() if ln.strip().startswith('notify "')]
    assert notify_lines, "expected watchdog notify() calls"
    blob = "\n".join(notify_lines)
    for banned in ("[AOS]", "DEGRADED", "migration 083", "Flapping", "flapping"):
        assert banned not in blob, f"watchdog notify regressed to jargon: {banned!r}"


def test_heartbeat_problems_are_humanized():
    hb = (BRIDGE / "heartbeat.py").read_text()
    for banned in ('"Alert:\\n"', "is DOWN", "free pages", "pending task(s)"):
        assert banned not in hb, f"heartbeat alert regressed to jargon: {banned!r}"


def test_enrich_auth_pause_drops_jargon():
    """Guard only the operator-facing alert function, not the CLI stats print."""
    eng = (REPO_ROOT / "core" / "engine" / "comms" / "enrich" / "engine.py").read_text()
    fn = eng.split("def _alert_auth_paused", 1)[1].split("\ndef ", 1)[0]
    # Look only at the message string literal, not the surrounding comment.
    msg_src = "".join(ln for ln in fn.splitlines() if "msg = " in ln or ln.strip().startswith(("f'", 'f"', '"', "'")))
    for banned in ("backfill PAUSED", "un-extracted", "checkpoint"):
        assert banned not in msg_src, f"enrich auth-pause regressed to jargon: {banned!r}"


def test_intelligence_hook_drops_relevance_code():
    act = (REPO_ROOT / "core" / "engine" / "intelligence" / "hooks" / "actions.py").read_text()
    assert "rel_str" not in act, "intelligence hook still emits a bracketed relevance code"


def test_bus_notify_drops_source_slug_tail():
    nc = (REPO_ROOT / "core" / "engine" / "bus" / "consumers" / "notify.py").read_text()
    assert 'f"{text}\\n\\n— {source}"' not in nc, "bus notify still appends the raw source slug"


def test_reconcile_runner_routes_through_alert_copy():
    runner = (REPO_ROOT / "core" / "infra" / "reconcile" / "runner.py").read_text()
    assert "from alert_copy import render_report" in runner
    # The old slug-dumping Telegram line must be gone (terminal output may still
    # print raw name/message — that's a log surface, not the operator's phone).
    assert 'lines.append(f"  {emoji} {r.name}: {r.message}")' not in runner
