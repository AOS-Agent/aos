"""Tests for the AOS scheduler's cron failure-alerting logic.

Covers exit-code -> success detection and the Telegram alert debounce, without
touching the network or real secrets (the send is monkeypatched).

Run:  python3 -m pytest core/bin/internal/tests/test_scheduler.py -q
"""

import importlib.machinery
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

# The scheduler is an extensionless executable script; load it as a module.
_SCHEDULER = Path(__file__).resolve().parent.parent / "scheduler"
_loader = importlib.machinery.SourceFileLoader("aos_scheduler", str(_SCHEDULER))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
sched = importlib.util.module_from_spec(_spec)
_loader.exec_module(sched)  # module __name__ != "__main__", so main() does not run


# ── exit-code detection ────────────────────────────────────────────────────────

def test_job_succeeded_default_zero_only():
    assert sched._job_succeeded(0, None) is True
    assert sched._job_succeeded(1, None) is False


def test_job_succeeded_custom_codes():
    assert sched._job_succeeded(2, [0, 2]) is True
    assert sched._job_succeeded(3, [0, 2]) is False


def test_job_succeeded_malformed_codes_fall_back_to_zero():
    assert sched._job_succeeded(0, "nonsense") is True
    assert sched._job_succeeded(0, []) is True
    assert sched._job_succeeded(1, []) is False


def test_timeout_and_exception_sentinels_are_failures():
    # scheduler uses -1 for timeout, -2 for internal exception
    assert sched._job_succeeded(-1, None) is False
    assert sched._job_succeeded(-2, None) is False


# ── log tail extraction ────────────────────────────────────────────────────────

def test_tail_log_lines_skips_markers(tmp_path):
    log_file = tmp_path / "job.log"
    log_file.write_text(
        "--- [t] START job (timeout: 120s) ---\n"
        "line1\nline2\n\nline3\n"
        "--- [t] END job (exit: 1, duration: 0s) ---\n"
    )
    assert sched._tail_log_lines(log_file, 5) == ["line1", "line2", "line3"]


def test_tail_log_lines_caps_at_n(tmp_path):
    log_file = tmp_path / "job.log"
    log_file.write_text("\n".join(f"l{i}" for i in range(20)) + "\n")
    assert sched._tail_log_lines(log_file, 5) == ["l15", "l16", "l17", "l18", "l19"]


def test_tail_log_lines_missing_file():
    assert sched._tail_log_lines(Path("/no/such/file.log"), 5) == []


# ── alert firing + debounce ────────────────────────────────────────────────────

def _capture_send(monkeypatch, result=True):
    sent = []

    def fake_send(text):
        sent.append(text)
        return result

    monkeypatch.setattr(sched, "_send_telegram", fake_send)
    return sent


def test_alert_fires_on_first_failure(tmp_path, monkeypatch):
    sent = _capture_send(monkeypatch)
    log_file = tmp_path / "job.log"
    log_file.write_text(
        "--- START ---\nTraceback (most recent call last):\n"
        "ModuleNotFoundError: No module named 'db'\n--- END ---\n"
    )
    entry = {"consecutive_failures": 1, "exit_code": 1}
    sched._maybe_alert_failure("comms-graduation", entry, log_file)
    assert len(sent) == 1
    body = sent[0]
    assert "comms-graduation" in body
    assert "exit 1" in body
    assert "ModuleNotFoundError" in body
    assert entry.get("last_alert")  # marked as sent for debounce


def test_no_alert_below_threshold(tmp_path, monkeypatch):
    sent = _capture_send(monkeypatch)
    log_file = tmp_path / "job.log"
    log_file.write_text("ok\n")
    entry = {"consecutive_failures": 0, "exit_code": 0}
    sched._maybe_alert_failure("job", entry, log_file)
    assert sent == []


def test_alert_debounced_within_window(tmp_path, monkeypatch):
    sent = _capture_send(monkeypatch)
    log_file = tmp_path / "job.log"
    log_file.write_text("error\n")
    entry = {
        "consecutive_failures": 5,
        "exit_code": 1,
        "last_alert": datetime.now().isoformat(timespec="seconds"),
    }
    sched._maybe_alert_failure("job", entry, log_file)
    assert sent == []  # still inside the debounce window


def test_alert_refires_after_window(tmp_path, monkeypatch):
    sent = _capture_send(monkeypatch)
    log_file = tmp_path / "job.log"
    log_file.write_text("error\n")
    old = (
        datetime.now() - timedelta(seconds=sched.ALERT_DEBOUNCE_SECONDS + 60)
    ).isoformat(timespec="seconds")
    entry = {"consecutive_failures": 5, "exit_code": 1, "last_alert": old}
    sched._maybe_alert_failure("job", entry, log_file)
    assert len(sent) == 1


def test_graceful_skip_when_creds_missing_does_not_mark_sent(tmp_path, monkeypatch):
    # _send_telegram returns False when Telegram isn't configured.
    _capture_send(monkeypatch, result=False)
    log_file = tmp_path / "job.log"
    log_file.write_text("error\n")
    entry = {"consecutive_failures": 1, "exit_code": 1}
    sched._maybe_alert_failure("job", entry, log_file)
    assert "last_alert" not in entry  # not debounced, so it retries next run


def test_send_failure_does_not_mark_sent(tmp_path, monkeypatch):
    # _send_telegram returns None on a transient send error.
    _capture_send(monkeypatch, result=None)
    log_file = tmp_path / "job.log"
    log_file.write_text("error\n")
    entry = {"consecutive_failures": 3, "exit_code": 1}
    sched._maybe_alert_failure("job", entry, log_file)
    assert "last_alert" not in entry
