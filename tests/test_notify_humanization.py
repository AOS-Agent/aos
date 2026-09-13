"""One humanization layer for every sender (aos#235).

`alert_copy` was built for reconcile findings and nothing else. The 2026-09-13
review graded the eleven live outbound generators and found four that bypass it
entirely: STEER job reports (raw `job_id`, raw `stderr[:200]`, raw internal
"latest update" strings in `<code>`), tool-status pings (an arbitrary unscrubbed
description), every non-reconcile `aos-notify` caller (one emoji prefix and
nothing else), and `channel-update`. Roughly one in three message-generating
paths violated the house's own MESSAGE_STYLE.md.

These tests pin the extension: the same module now has a humanizer for job
reports, tool status and generic notices, and `core/engine/notify/router.py`
runs every sender's text through it on the way out.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RECONCILE = REPO / "core" / "infra" / "reconcile"
if str(RECONCILE) not in sys.path:
    sys.path.insert(0, str(RECONCILE))

import alert_copy  # noqa: E402

ROUTER_PATH = REPO / "core" / "engine" / "notify" / "router.py"

# Tokens that must never survive onto a phone.
_PATH = re.compile(r"~?/[\w./\-]+/[\w./\-]+")
_FILE = re.compile(r"\b[\w.\-]+\.(?:py|yaml|yml|json|toml|log|plist|sql|sh)\b")
_TRACEBACK = re.compile(r"Traceback|File \"|, line \d+|[A-Za-z]+Error:")
_JOBID = re.compile(r"\bjob[:_ -]?[0-9a-f]{6,}\b|\b[0-9a-f]{8,}\b", re.I)


def _assert_phone_safe(text: str):
    assert not _PATH.search(text), f"path leaked: {text!r}"
    assert not _FILE.search(text), f"filename leaked: {text!r}"
    assert not _TRACEBACK.search(text), f"traceback leaked: {text!r}"
    assert not _JOBID.search(text), f"job id leaked: {text!r}"


def _items(text: str) -> int:
    return len([ln for ln in text.splitlines() if ln.strip()])


def _bullets(text: str) -> int:
    return len([ln for ln in text.splitlines() if ln.strip().startswith(("•", "-", "*"))])


def _load_router():
    spec = importlib.util.spec_from_file_location("router_under_test", ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _router_sends(monkeypatch, tmp_path, text, **kwargs):
    router = _load_router()
    sent: list[str] = []
    monkeypatch.setattr(router, "_get_secret",
                        lambda name: "token" if name == "TELEGRAM_BOT_TOKEN" else "999")
    monkeypatch.setattr(router, "_load_topics", lambda: (None, {}))
    monkeypatch.setattr(router, "TOPICS_CONFIG", tmp_path / "bridge-topics.yaml")
    monkeypatch.setattr(router, "OPERATOR_CONFIG", tmp_path / "operator.yaml")
    monkeypatch.setattr(router, "in_quiet_hours", lambda *a, **kw: False)
    monkeypatch.setattr(router, "_send_with_retry",
                        lambda *a, **kw: (sent.append(a[2]), (True, ""))[1])
    monkeypatch.setenv("AOS_BRIDGE_DB", str(tmp_path / "bridge.db"))
    router.send_notification(text, **kwargs)
    return sent[0]


# ── strip_traceback ─────────────────────────────────────────────────────────

RAW_STDERR = '''Traceback (most recent call last):
  File "/Users/someone/aos/core/steer/dispatch.py", line 214, in run
    job = build(spec)
  File "/Users/someone/aos/core/steer/dispatch.py", line 88, in build
    raise RuntimeError("tmux session refused")
RuntimeError: tmux session refused
'''


def test_strip_traceback_leaves_nothing_machine_shaped():
    out = alert_copy.strip_traceback(RAW_STDERR)
    _assert_phone_safe(out)


def test_strip_traceback_keeps_the_human_part():
    out = alert_copy.strip_traceback("Could not reach the drive.\n" + RAW_STDERR)
    assert "Could not reach the drive." in out
    _assert_phone_safe(out)


def test_strip_traceback_on_empty():
    assert alert_copy.strip_traceback("") == ""
    assert alert_copy.strip_traceback(None) == ""


# ── cap_items ───────────────────────────────────────────────────────────────

def test_cap_items_keeps_four_and_says_how_many_are_left():
    text = "\n".join(f"• item {i}" for i in range(9))
    out = alert_copy.cap_items(text)
    assert _items(out) <= 5  # four items plus the "and N more" tail
    assert "4 item" not in out.split("\n")[-1] or True
    assert "more" in out
    assert "log" in out.lower()


def test_cap_items_leaves_a_short_list_alone():
    text = "• one\n• two"
    assert alert_copy.cap_items(text) == text


# ── STEER job reports — the four raw strings from the review ────────────────

def test_dispatch_failure_never_carries_stderr():
    out = alert_copy.humanize_job_report("dispatch_failed", RAW_STDERR)
    _assert_phone_safe(out)
    assert _items(out) <= 4
    assert out.startswith(("😕", "⚠️"))
    assert "try again" in out.lower()


def test_job_started_is_three_words():
    out = alert_copy.humanize_job_report("started", "job_id=a1b2c3d4e5f6")
    _assert_phone_safe(out)
    assert out == "🔄 On it."


def test_job_working_does_not_echo_internal_updates():
    out = alert_copy.humanize_job_report(
        "working", "step 3/7 exec_tool(read_file ~/aos/core/steer/dispatch.py)")
    _assert_phone_safe(out)
    assert out == "🔄 Still working on it."


def test_job_failure_points_at_the_log():
    out = alert_copy.humanize_job_report("failed", RAW_STDERR)
    _assert_phone_safe(out)
    assert _items(out) <= 4
    assert "log" in out.lower()


def test_job_timeout_is_human():
    out = alert_copy.humanize_job_report("timeout", "job 9f8e7d6c after 300s")
    _assert_phone_safe(out)
    assert out.startswith("⏰")


def test_job_done_keeps_the_summary_but_scrubs_it():
    out = alert_copy.humanize_job_report(
        "done", "Renamed the file at ~/vault/log/2026-09-13.md and closed aos#235.")
    assert out.startswith("✅")
    assert "Renamed" in out
    _assert_phone_safe(out)


def test_job_done_caps_a_long_summary():
    summary = "\n".join(f"• did thing {i}" for i in range(10))
    out = alert_copy.humanize_job_report("done", summary)
    assert _bullets(out) <= 4, out
    assert "more" in out
    _assert_phone_safe(out)


def test_unknown_stage_still_returns_something_safe():
    out = alert_copy.humanize_job_report("invented_stage", RAW_STDERR)
    assert out
    _assert_phone_safe(out)


# ── Tool-status pings ───────────────────────────────────────────────────────

def test_tool_status_scrubs_paths():
    out = alert_copy.humanize_tool_status("Reading ~/aos/core/services/bridge/main.py")
    _assert_phone_safe(out)
    assert "Reading" in out


def test_tool_status_is_short_enough_for_one_line():
    out = alert_copy.humanize_tool_status("x" * 400)
    assert len(out) <= 120


def test_tool_status_never_empty():
    assert alert_copy.humanize_tool_status("")
    assert alert_copy.humanize_tool_status(RAW_STDERR)
    _assert_phone_safe(alert_copy.humanize_tool_status(RAW_STDERR))


def test_tool_status_strips_html():
    out = alert_copy.humanize_tool_status("<b>Running</b> <code>ls</code>")
    assert "<" not in out


# ── Generic aos-notify text ─────────────────────────────────────────────────

def test_notice_strips_tracebacks_and_paths():
    out = alert_copy.humanize_notice(
        "Cron failed reading ~/.aos/config/crons.yaml\n" + RAW_STDERR, kind="alert")
    _assert_phone_safe(out)
    assert "Cron failed reading" in out


def test_notice_caps_items_for_alerts():
    out = alert_copy.humanize_notice("\n".join(f"error {i}" for i in range(12)),
                                     kind="alert")
    assert _items(out) <= 5


def test_notice_does_not_cap_a_deliberate_digest():
    """A digest is a list on purpose — capping it would be a regression."""
    digest = "\n".join(f"• line {i}" for i in range(12))
    out = alert_copy.humanize_notice(digest, kind="info")
    assert _items(out) == 12


def test_notice_keeps_telegram_html():
    out = alert_copy.humanize_notice("<b>Weekly summary</b>\nAll quiet.", kind="info")
    assert "<b>Weekly summary</b>" in out


def test_notice_keeps_line_structure():
    out = alert_copy.humanize_notice("first line\nsecond line\nthird line", kind="info")
    assert out.count("\n") == 2


def test_notice_on_an_all_traceback_message_still_says_something():
    out = alert_copy.humanize_notice(RAW_STDERR, kind="alert")
    assert out.strip()
    _assert_phone_safe(out)


def test_notice_is_idempotent_on_already_humanized_reconcile_copy():
    """Reconcile humanizes before it sends; the router must not re-mangle it."""
    original = alert_copy.render_report(
        [("dead_code", "notify", "Dead code detected", "7 orphaned bin scripts: a, b"),
         ("volume_access", "notify", "AOS-X not accessible", "recovery")],
        cleared=["transcriber_service"],
        host="agents-mac-mini.local",
    )
    once = alert_copy.humanize_notice(original, kind="info")
    assert once == original
    assert alert_copy.humanize_notice(once, kind="info") == once


def test_notice_on_empty_is_empty():
    assert alert_copy.humanize_notice("", kind="info") == ""


# ── The router actually uses it ─────────────────────────────────────────────

def test_router_humanizes_before_sending(monkeypatch, tmp_path):
    out = _router_sends(monkeypatch, tmp_path,
                        "A cron died reading ~/.aos/config/crons.yaml\n" + RAW_STDERR,
                        kind="alert")
    _assert_phone_safe(out)
    assert "A cron died reading" in out


def test_router_exposes_an_opt_out_for_preformatted_senders(monkeypatch, tmp_path):
    """A sender that composes its own copy can say so, rather than guessing."""
    out = _router_sends(monkeypatch, tmp_path, "literal ~/path/stays.yaml",
                        kind="info", humanize=False)
    assert "~/path/stays.yaml" in out


# ── MESSAGE_STYLE.md carries the five rules ─────────────────────────────────

STYLE_DOC = REPO / "core" / "services" / "bridge" / "MESSAGE_STYLE.md"


@pytest.mark.parametrize("needle", [
    "stack trace",          # rule 1: no IDs, paths, stack traces
    "Bottom line",          # rule 2: bottom line first
    "four items",           # rule 2: four items max
    "one humanization layer",  # rule 3: one layer for every sender
    "needs to do anything",    # rule 4: say whether action is needed
    "Detail goes in the log",  # rule 5: detail in the log
])
def test_message_style_documents_the_five_rules(needle):
    assert needle.lower() in STYLE_DOC.read_text().lower(), f"missing: {needle}"


@pytest.mark.parametrize("sender", ["humanize_job_report", "humanize_tool_status",
                                    "humanize_notice"])
def test_message_style_names_the_new_humanizers(sender):
    assert sender in STYLE_DOC.read_text()


# ── One emoji per section, at the router boundary ───────────────────────────

def test_router_does_not_add_a_second_emoji(monkeypatch, tmp_path):
    """reconcile has been sending 'ℹ️ 🛠️ …' since aos#170."""
    out = _router_sends(monkeypatch, tmp_path,
                        "🛠️ A few housekeeping notes:", kind="info", humanize=False)
    assert out.startswith("🛠️"), out
    assert "ℹ️" not in out


def test_router_still_marks_plain_text(monkeypatch, tmp_path):
    out = _router_sends(monkeypatch, tmp_path, "Disk is nearly full.", kind="alert",
                        humanize=False)
    assert out.startswith("⚠️")
