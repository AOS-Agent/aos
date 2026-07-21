"""Reconcile alert copy (aos#170 — Telegram message quality).

Reconcile findings speak in slugs and paths; the operator reads them on a
phone. `alert_copy` is the single translation layer. These tests lock the
contract: no slugs, no paths, no jargon reaches the wire, human templates win
for known checks, and the fallback still scrubs anything untemplated.
"""

import re
import sys
from pathlib import Path

import pytest

RECONCILE = Path(__file__).parent.parent / "core" / "infra" / "reconcile"
if str(RECONCILE) not in sys.path:
    sys.path.insert(0, str(RECONCILE))

import alert_copy  # noqa: E402

# Tokens that must never survive into a phone-facing message.
_PATH = re.compile(r"~?/[\w./\-]+/[\w./\-]+")
_FILE = re.compile(r"\b[\w.\-]+\.(?:py|yaml|yml|json|toml|log|plist|sql|sh)\b")
_SLUG = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")
_VERSION = re.compile(r"\bv\d+\.\d+\.\d+")
_MIGRATION = re.compile(r"[Mm]igration \d+")
_BRACKET = re.compile(r"\[[^\]]*\]")
_HTML = re.compile(r"</?[a-zA-Z][^>]*>")


def _assert_clean(text: str):
    assert not _PATH.search(text), f"path leaked: {text!r}"
    assert not _FILE.search(text), f"filename leaked: {text!r}"
    assert not _SLUG.search(text), f"snake_case slug leaked: {text!r}"
    assert not _VERSION.search(text), f"version ref leaked: {text!r}"
    assert not _MIGRATION.search(text), f"migration ref leaked: {text!r}"
    assert not _BRACKET.search(text), f"bracketed code leaked: {text!r}"
    assert not _HTML.search(text), f"HTML tag leaked: {text!r}"


# ---------------------------------------------------------------------------
# strip_jargon — the fallback scrubber.
# ---------------------------------------------------------------------------

def test_strip_jargon_removes_paths():
    out = alert_copy.strip_jargon("Cannot read ~/.aos/logs/transcriber.err.log now")
    _assert_clean(out)
    assert "read" in out.lower()


def test_strip_jargon_despaces_slugs():
    out = alert_copy.strip_jargon("dead_code detected in bridge_poll_liveness")
    assert "dead code" in out
    assert "bridge poll liveness" in out
    _assert_clean(out)


def test_strip_jargon_drops_version_migration_bracket():
    out = alert_copy.strip_jargon("run reconcile (migration 083) v0.6.19 [executing] aos#170")
    _assert_clean(out)


def test_strip_jargon_empty():
    assert alert_copy.strip_jargon("") == ""
    assert alert_copy.strip_jargon(None) == ""


# ---------------------------------------------------------------------------
# humanize_finding — the operator example from the directive.
# ---------------------------------------------------------------------------

def test_dead_code_matches_target_voice():
    out = alert_copy.humanize_finding(
        "dead_code", "notify",
        "Dead code detected — review and remove manually",
        "7 orphaned bin scripts: aos-report, eventd, foo, bar, baz",
    )
    assert "7 old scripts" in out
    assert out.startswith("🧹")
    _assert_clean(out)


def test_volume_access_has_fix_steps():
    out = alert_copy.humanize_finding(
        "volume_access", "notify",
        "AOS-X mounted but NOT accessible (TCC permission revoked?)",
        "AOS-X unreadable — likely a macOS permission revoke. Fix: System Settings...",
    )
    assert "external drive" in out
    assert "Privacy & Security" in out
    _assert_clean(out)


def test_dead_code_singular_pluralizes():
    out = alert_copy.humanize_finding(
        "dead_code", "notify", "Dead code detected",
        "1 orphaned bin script: aos-report",
    )
    assert "1 old script " in out
    assert "scripts" not in out


def test_storage_layout_keeps_count_drops_slug():
    out = alert_copy.humanize_finding(
        "storage_layout", "notify",
        "3 directories not on data drive (12.5GB local)",
        None,
    )
    assert "3 folders" in out
    assert "12.5GB" in out
    _assert_clean(out)


def test_service_loaded_restarted_names_service():
    out = alert_copy.humanize_finding(
        "service_loaded", "fixed",
        "restarted transcriber (not loaded), n8n (health failed)",
        None,
    )
    assert "transcriber" in out
    assert "restarted" in out.lower()
    _assert_clean(out)


def test_service_loaded_failed_is_a_warning():
    out = alert_copy.humanize_finding(
        "service_loaded", "notify",
        "FAILED to reload: whatsmeow (crash loop)",
        None,
    )
    assert "whatsmeow" in out
    assert "didn't come back" in out
    _assert_clean(out)


def test_error_bucket_never_dumps_traceback():
    out = alert_copy.humanize_finding(
        "vault_contract", "error", "Scanner did not produce stats",
        "Traceback (most recent call last):\n  File ~/aos/core/x.py line 3\n",
    )
    _assert_clean(out)
    assert "Traceback" not in out


def test_unknown_check_falls_back_clean():
    out = alert_copy.humanize_finding(
        "some_new_check", "notify",
        "widget_thing broke at ~/aos/core/foo/bar.py (migration 099)",
        None,
    )
    _assert_clean(out)
    assert out  # non-empty


# ---------------------------------------------------------------------------
# Length bounds & report assembly.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,status,message,detail", [
    ("volume_access", "notify", "AOS-X not accessible", "long recovery..."),
    ("dead_code", "notify", "Dead code detected", "7 orphaned bin scripts: a, b"),
    ("disk_smart_health", "notify", "1 disk failing SMART", None),
    ("google_workspace", "notify", "gws CLI not installed — run: brew install x", None),
    ("transcriber_service", "notify", "Transcriber plist deployed but kickstart failed", "x"),
    ("bridge_poll_liveness", "fixed", "Bridge poll restarted", None),
])
def test_every_line_is_bounded_and_clean(name, status, message, detail):
    out = alert_copy.humanize_finding(name, status, message, detail)
    _assert_clean(out)
    assert len(out) <= 500, f"{name} line too long for a phone: {len(out)}"
    assert out[0] not in "abcdefghijklmnopqrstuvwxyz"  # leads with an emoji/capital


def test_render_report_bundles_with_header():
    msg = alert_copy.render_report(
        [
            ("dead_code", "notify", "Dead code detected", "7 orphaned bin scripts: a"),
            ("volume_access", "notify", "AOS-X not accessible", "recovery"),
        ],
        cleared=["transcriber_service"],
        host="agents-mac-mini.local",
    )
    assert msg.startswith("🛠️")
    assert "agents-mac-mini" in msg and ".local" not in msg
    assert "back to normal" in msg
    _assert_clean(msg)


def test_render_report_single_finding_no_header():
    msg = alert_copy.render_report(
        [("dead_code", "notify", "Dead code detected", "2 orphaned bin scripts: a, b")],
        cleared=[],
        host="host",
    )
    assert msg.startswith("🧹")


def test_render_report_empty_is_none():
    assert alert_copy.render_report([], [], "host") is None


def test_cleared_only_still_sends():
    msg = alert_copy.render_report([], ["volume_access"], "host")
    assert msg is not None
    assert "external drive" in msg
