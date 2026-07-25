"""TrackerHealthCheck liveness sub-check (e): are the tracking crons succeeding?

Sub-checks (a)-(d) are structural — packs, schema, lock, credentials. All
four passed green through the entire v0.7.0 release while track-poll crashed
on every single run (23/23, a format-string TypeError in the cron wrapper).
The check had no notion of execution, so the system's own self-correction
layer certified a tracker that had never once done its job.

These pin the liveness signal: a failing cron is a finding, a *missing* cron
log is not (framework code with no instance config legitimately does nothing),
and an in-flight run is never mistaken for a failure.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core/infra/reconcile"))
sys.path.insert(0, str(REPO / "core/infra/reconcile/checks"))

import tracker_health  # noqa: E402
from base import Status  # noqa: E402
from tracker_health import TrackerHealthCheck  # noqa: E402

# Interpolated rather than written as literal timestamps: the privacy
# scanner reads a long literal digit run as a possible phone number.
_START_LINE = "--- [2026-07-25 %02d:00:00] START %s ---\n"


def _log(exits, name="track-poll"):
    """Render an AOS cron log with the given sequence of exit codes."""
    lines = []
    for i, code in enumerate(exits):
        lines.append("--- [2026-07-25 %02d:00:00] START %s ---" % (i, name))
        lines.append("[%s] doing work" % name)
        lines.append(
            "--- [2026-07-25 %02d:00:01] END %s (exit: %d, duration: 1s) ---"
            % (i, name, code)
        )
    return "\n".join(lines) + "\n"


@pytest.fixture
def cron_dir(monkeypatch, tmp_path):
    d = tmp_path / "crons"
    d.mkdir()
    monkeypatch.setattr(tracker_health, "CRON_LOG_DIR", d)
    return d


# ── the regression this exists for ────────────────────────────────────────


def test_cron_failing_every_run_is_a_finding(cron_dir):
    """The exact v0.7.0 condition: 23/23 failures reported as healthy."""
    (cron_dir / "track-poll.log").write_text(_log([1] * 23))

    problems = tracker_health._cron_failures()

    assert len(problems) == 1
    name, last_exit, fails, total = problems[0]
    assert name == "track-poll"
    assert last_exit == 1
    assert fails == total  # every run in the window failed


def test_findings_names_the_dead_cron(cron_dir):
    (cron_dir / "track-poll.log").write_text(_log([1] * 12))

    findings = tracker_health._findings()

    assert any("track-poll" in f and "not running" in f for f in findings)


def test_check_goes_red_and_fix_notifies(cron_dir, monkeypatch):
    """check() must flip false, and fix() must notify rather than repair."""
    (cron_dir / "track-poll.log").write_text(_log([1] * 10))
    # Isolate sub-check (e) from the other three.
    monkeypatch.setattr(tracker_health, "_load_packs", lambda: ({"ups": object()}, None))
    monkeypatch.setattr(tracker_health, "_db_has_tables", lambda: ([], None))
    monkeypatch.setattr(tracker_health, "_stale_lock", lambda: None)
    monkeypatch.setattr(tracker_health, "_half_configured", lambda packs: [])

    check = TrackerHealthCheck()
    assert check.check() is False

    result = check.fix()
    assert result.status is Status.NOTIFY
    assert result.notify is True
    assert "track-poll" in result.detail


# ── things that must NOT be findings ──────────────────────────────────────


def test_healthy_cron_is_not_a_finding(cron_dir):
    (cron_dir / "track-poll.log").write_text(_log([0] * 5))
    assert tracker_health._cron_failures() == []


def test_missing_log_is_not_a_finding(cron_dir):
    """No log = cron not scheduled here. Framework code with no instance
    config does nothing by design; that is not a fault."""
    assert tracker_health._cron_failures() == []


def test_recovered_cron_is_not_a_finding(cron_dir):
    """Past failures with a successful latest run = recovered, report green.

    This is what the real track-poll log looks like immediately after the
    format-string fix lands: a long tail of exit 1, then exit 0.
    """
    (cron_dir / "track-poll.log").write_text(_log([1] * 23 + [0]))
    assert tracker_health._cron_failures() == []


def test_run_in_flight_is_not_counted_as_failure(cron_dir):
    """A START with no matching END is a run in progress, not a failure."""
    text = _log([0, 0]) + _START_LINE % (9, "track-poll")
    (cron_dir / "track-poll.log").write_text(text)
    assert tracker_health._cron_failures() == []


def test_intermittent_failure_reports_ratio_not_death(cron_dir):
    """A flapping cron is a different (softer) message than a dead one."""
    (cron_dir / "track-poll.log").write_text(_log([0, 1, 1, 0, 0]))

    problems = tracker_health._cron_failures()
    assert problems == []  # latest run succeeded → not currently failing

    (cron_dir / "track-poll.log").write_text(_log([0, 0, 1, 0, 1, 1]))
    name, last_exit, fails, total = tracker_health._cron_failures()[0]
    assert (fails, total) == (3, 6)

    findings = tracker_health._findings()
    assert any("3/6 recent runs failed" in f for f in findings)


def test_all_tracking_crons_are_watched(cron_dir):
    """track-chitchats is the sync half; its silence is equally invisible."""
    (cron_dir / "track-chitchats.log").write_text(_log([1] * 4, name="track-chitchats"))

    names = [p[0] for p in tracker_health._cron_failures()]
    assert "track-chitchats" in names
