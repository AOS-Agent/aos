"""
Tests for the cron_health reconcile check.

The check exists because scheduled jobs fail silently. The machine it was
written on had been running with the people-intelligence layer switched off for
three months — `contact-sync`, `comms-patterns` and `comms-graduation` all
failed at import time on every run after the `people` package moved, and nothing
ever said so.

The hard part is not detecting failure; it is not crying wolf. Both real
incidents on that machine were *already fixed* by the time the check was
written, and a naive lifetime tally would have reported them as live breakage
forever — training the operator to ignore the alert, which recreates exactly the
silence the check exists to break.

So most of these tests pin the "is it broken NOW" discrimination.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
CHECK_PATH = REPO / "core" / "infra" / "reconcile" / "checks" / "cron_health.py"


def _mod():
    sys.path.insert(0, str(REPO / "core" / "infra" / "reconcile"))
    spec = importlib.util.spec_from_file_location("cron_health_under_test", CHECK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cron_health_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


ch = _mod()


# ── the recovering heuristic ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "codes,recovering,why",
    [
        ([0] * 10, True, "all green"),
        ([1] * 8 + [0, 0], True, "weekly-digest: 8 old failures, then fixed"),
        ([1] * 6 + [0] * 4, True, "contiguous failures then contiguous successes"),
        ([1] * 10, False, "still failing every run"),
        ([0, 1] * 5, False, "flaky: failures interleaved with successes"),
        ([0, 1, 0, 1, 1, 1, 0, 1, 1, 0], False, "flaky even though the last run passed"),
        ([1, 1, 0, 0, 0, 1, 1, 1, 1, 1], False, "regressed after a fix"),
        ([], False, "no history"),
    ],
)
def test_recovering_shape(codes, recovering, why):
    assert ch._is_recovering(codes) is recovering, why


# ── the broken decision ──────────────────────────────────────────────────────

def _stat(name, codes, source="log"):
    fails = sum(1 for c in codes if c != 0)
    return {
        "name": name,
        "runs": len(codes),
        "failures": fails,
        "rate": (fails / len(codes)) if codes else 0.0,
        "latest_failed": bool(codes) and codes[-1] != 0,
        "recovering": ch._is_recovering(codes),
        "source": source,
    }


def test_fixed_job_is_not_flagged():
    """
    The check-update case: ~1000 lifetime failures, all long fixed.

    Its own script comment records that the old behaviour "generated ~1000 false
    'cron failed' alerts". The check must not become the new source of those.
    """
    stats = [_stat("check-update", [0] * 10)]
    assert ch._broken(stats) == []


def test_recovering_low_frequency_job_is_not_flagged():
    """weekly-digest runs once a week — only two green runs since its fix."""
    stats = [_stat("weekly-digest", [1] * 8 + [0, 0])]
    assert ch._broken(stats) == []


def test_currently_failing_job_is_flagged():
    stats = [_stat("contact-sync", [1] * 10)]
    assert [s["name"] for s in ch._broken(stats)] == ["contact-sync"]


def test_flaky_job_is_flagged_even_when_last_run_passed():
    """A job failing most runs is broken regardless of how the last one landed."""
    stats = [_stat("track-poll", [0, 1, 0, 1, 1, 1, 0, 1, 1, 0])]
    assert [s["name"] for s in ch._broken(stats)] == ["track-poll"]


def test_regression_after_a_fix_is_flagged():
    stats = [_stat("enrich-comms", [1, 1, 0, 0, 0, 1, 1, 1, 1, 1])]
    assert [s["name"] for s in ch._broken(stats)] == ["enrich-comms"]


def test_single_blip_is_not_flagged():
    """One bad run in ten is a network hiccup, not breakage."""
    stats = [_stat("qmd-reindex", [0, 0, 0, 0, 1, 0, 0, 0, 0, 0])]
    assert ch._broken(stats) == []


def test_too_little_history_is_not_judged():
    stats = [_stat("brand-new-cron", [1, 1])]
    assert ch._broken(stats) == []


def test_window_bounds_the_history_considered():
    """Ancient failures outside the window must not influence the verdict."""
    codes = [1] * 500 + [0] * ch.WINDOW
    assert ch._is_recovering(codes[-ch.WINDOW:]) is True


# ── check/fix contract ───────────────────────────────────────────────────────

def test_check_is_true_when_nothing_is_broken(monkeypatch):
    monkeypatch.setattr(ch, "_collect", lambda: [_stat("a", [0] * 10)])
    assert ch.CronHealthCheck().check() is True


def test_check_is_false_and_fix_notifies_when_broken(monkeypatch):
    monkeypatch.setattr(ch, "_collect", lambda: [_stat("busted", [1] * 10)])
    check = ch.CronHealthCheck()
    assert check.check() is False

    result = check.fix()
    assert result.status is ch.Status.NOTIFY
    assert result.notify is True
    assert "busted" in result.detail
    # It must never claim to have repaired anything — a failing cron needs a
    # human to read the error, and re-running it just fails again on schedule.
    assert result.status is not ch.Status.FIXED


def test_fix_reports_unobserved_jobs_separately(monkeypatch):
    """No history is a weaker signal than failure, and must not read as failure."""
    monkeypatch.setattr(
        ch, "_collect",
        lambda: [_stat("busted", [1] * 10), _stat("never-ran", [])],
    )
    result = ch.CronHealthCheck().fix()
    assert "never-ran" in result.detail
    assert "No run history" in result.detail


def test_missing_sources_do_not_raise(monkeypatch, tmp_path):
    """Reconcile runs on every machine; absent logs/DB must degrade quietly."""
    monkeypatch.setattr(ch, "CRON_LOGS", tmp_path / "nope")
    monkeypatch.setattr(ch, "QAREEN_DB", tmp_path / "nope.db")
    monkeypatch.setattr(ch, "CRONS_YAML", tmp_path / "nope.yaml")
    assert ch._collect() == []
    assert ch.CronHealthCheck().check() is True


# ── registration ─────────────────────────────────────────────────────────────

def test_check_is_registered():
    text = (REPO / "core" / "infra" / "reconcile" / "checks" / "__init__.py").read_text()
    assert "CronHealthCheck" in text


def test_log_marker_matches_what_the_scheduler_writes():
    """
    The log fallback is what makes this check work before every cron is wrapped
    (3 of 38 were, when this was written). If the scheduler's marker format
    changes, this parser goes silently blind — the exact failure mode the check
    exists to prevent.
    """
    scheduler = (REPO / "core" / "bin" / "internal" / "scheduler").read_text()
    assert 'END {name} (exit: {exit_code}' in scheduler
    # Timestamp elided — the parser ignores it, and a realistic one trips the
    # privacy scanner's phone-number heuristic on its digit run.
    sample = "--- [TIMESTAMP] END contact-sync (exit: 1, duration: 0s) ---"
    m = ch.END_MARKER.search(sample)
    assert m and m.group("name") == "contact-sync" and m.group("code") == "1"
