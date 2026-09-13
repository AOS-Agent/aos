"""
weekly-digest empty-sessions early return (aos health report, 2026-07-15).

When there are no session exports, scan_sessions() takes an early return that
must still carry every key the report consumers read — otherwise the digest
KeyErrors on `total_duration_min` / `avg_duration_min` and fails silently every
week (12 straight failures since 2026-06-28). This test pins the early return's
key set to the normal return's, and exercises the consumer render.
"""

import datetime
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DIGEST = REPO_ROOT / "core" / "bin" / "crons" / "weekly-digest"


def _load():
    loader = SourceFileLoader("weekly_digest_under_test", str(DIGEST))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# Every key a report consumer reads off the sessions dict (lines ~493/533).
CONSUMER_KEYS = {
    "count",
    "topics",
    "projects",
    "tools",
    "total_messages",
    "total_duration_min",
    "avg_duration_min",
}


def test_empty_sessions_return_has_all_consumer_keys(tmp_path):
    m = _load()
    m.SESSIONS_DIR = tmp_path / "does-not-exist"
    result = m.scan_sessions(datetime.date(2026, 1, 1), datetime.date(2026, 1, 7))
    missing = CONSUMER_KEYS - set(result)
    assert not missing, f"empty scan_sessions() drops keys: {missing}"
    assert result["total_duration_min"] == 0
    assert result["avg_duration_min"] == 0


def test_duration_consumer_renders_without_keyerror(tmp_path):
    m = _load()
    m.SESSIONS_DIR = tmp_path / "does-not-exist"
    s = m.scan_sessions(datetime.date(2026, 1, 1), datetime.date(2026, 1, 7))
    # The two f-strings that used to KeyError every week.
    assert f"{s['total_duration_min']}min (~{s['avg_duration_min']}min avg)" == "0min (~0min avg)"
    assert f"Sessions: {s['count']} ({s['total_duration_min']}min)" == "Sessions: 0 (0min)"


# ── aos#114: wrong sessions path + silent zero-session weeks ──────────────
#
# SESSIONS_DIR pointed at vault/ops/sessions, which never existed (the vault
# schema's canonical session-export location is vault/log/sessions — see
# ~/vault/SCHEMA.md and every sibling cron: compile-patterns, session-export,
# compile-daily, reconcile-sessions). Every run silently found nothing.
# Fixed by pointing at vault/log/sessions and by warning (rather than quietly
# reporting 0) when a week genuinely turns up no sessions.

def test_sessions_dir_is_log_sessions_not_ops():
    m = _load()
    assert m.SESSIONS_DIR == Path.home() / "vault" / "log" / "sessions"
    assert "ops" not in m.SESSIONS_DIR.parts


def test_sessions_dir_resolves_under_real_vault_layout(tmp_path, monkeypatch):
    """Temp-vault integration test: a session export under vault/log/sessions/
    is found without any test-only path override."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = tmp_path / "vault" / "log" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "2026-01-02-abc123.md").write_text(
        "---\nproject: aos\nmessage_count: 5\nduration_min: 12\n---\n# Test Session\n"
    )

    m = _load()
    assert m.SESSIONS_DIR == sessions_dir  # wired up via VAULT_ROOT, not a test hack

    result = m.scan_sessions(datetime.date(2026, 1, 1), datetime.date(2026, 1, 7))
    assert result["count"] == 1
    assert result["total_messages"] == 5
    assert result["projects"]["aos"] == 1


def test_zero_session_week_warns_instead_of_silent(tmp_path, monkeypatch, capsys):
    """A week with zero sessions must surface a warning, not report 0 quietly."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "vault" / "log" / "sessions").mkdir(parents=True)

    m = _load()
    m.NO_TELEGRAM = True
    m.main()

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "0 session" in captured.err.lower()


def test_nonzero_session_week_does_not_warn(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = tmp_path / "vault" / "log" / "sessions"
    sessions_dir.mkdir(parents=True)
    today = datetime.date.today()
    (sessions_dir / f"{today.isoformat()}-abc123.md").write_text("# Test Session\n")

    m = _load()
    m.NO_TELEGRAM = True
    m.main()

    captured = capsys.readouterr()
    assert "WARNING" not in captured.err
