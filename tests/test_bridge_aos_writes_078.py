"""
Tests for aos#2320: bridge modules writing instance state under the
read-only framework tree (~/aos) instead of ~/.aos.

Root cause (core/services/bridge/daily_briefing.py:26, before this fix):

    WORKSPACE = Path.home() / "aos"
    ...
    state_file = WORKSPACE / "data" / "bridge" / "briefing_state.txt"
    state_file.parent.mkdir(parents=True, exist_ok=True)   # <-- raises

~/aos is the read-only release symlink on every modern install (migration
008 moved ~/aos/data/ -> ~/.aos/data/ for exactly this reason). The mkdir()
above ran unguarded at the top of the daily-briefing thread's loop, so the
thread died on every boot while the log line right before it claimed
"Daily briefing scheduled at 09:00" — silent, permanent loss of the morning
briefing and evening check-in on every install.

Grepping the rest of core/ for the same class (`Path.home() / "aos"` used as
a base for a runtime *write*, not a config read or a script-invocation path)
turned up two more real writers sharing the exact bug, both fixed alongside
daily_briefing.py:

  - core/services/bridge/session_manager.py: SESSIONS_FILE lived under
    WORKSPACE/"data"/"bridge"/"sessions.json" — every save_session_id() call
    (i.e., every bridge conversation turn) hit the same mkdir() crash,
    silently breaking session continuity forever.
  - core/bin/crons/friction-rules (a nightly cron) wrote PENDING_FILE/
    STATE_FILE under AOS_DIR/"apps"/"bridge"/"data"/"bridge"/... (a path
    that was doubly wrong: read-only AND "apps/bridge" was never a real
    directory). core/services/bridge/intent_classifier.py reads the same
    PENDING_FILE path to report pending rule proposals, so both had to move
    together.

All three now resolve their instance-writable path under ~/.aos/data/bridge/
(matching the sibling convention in evening_checkin.py's STATE_FILE), and
daily_briefing.py/session_manager.py resolve it fresh on every call (a
function, not a module-level constant bound once at import time) rather
than a frozen module-level constant, so a test's Path.home() sandbox always
applies and a long-running thread never binds to a stale home directory.

Each test below chmods a *real* fake ~/aos read-only (dr-xr-xr-x) — the
literal condition from the issue, not a mock — and asserts the write lands
under ~/.aos instead, and that ~/aos is never touched.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
BRIDGE_DIR = REPO_ROOT / "core" / "services" / "bridge"
DAILY_BRIEFING_PATH = BRIDGE_DIR / "daily_briefing.py"
SESSION_MANAGER_PATH = BRIDGE_DIR / "session_manager.py"
FRICTION_RULES_PATH = REPO_ROOT / "core" / "bin" / "crons" / "friction-rules"
INTENT_CLASSIFIER_PATH = BRIDGE_DIR / "intent_classifier.py"


def _make_readonly_aos(home: Path) -> Path:
    """A real ~/aos: a populated directory, then chmod'd read-only — the
    literal shape of a release install (dr-x------, a symlink in production,
    but read-only is the property under test, not symlink-ness)."""
    aos_dir = home / "aos"
    (aos_dir / "config").mkdir(parents=True, exist_ok=True)
    aos_dir.chmod(0o555)
    return aos_dir


def _load_module(path: Path, name: str, extra_syspath: Path | None = None):
    """Load a module fresh by file path (works for extensionless scripts
    too, unlike spec_from_file_location's suffix-based loader inference)."""
    if extra_syspath is not None and str(extra_syspath) not in sys.path:
        sys.path.insert(0, str(extra_syspath))
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """A real, isolated $HOME with ~/aos populated and chmod'd read-only,
    and Path.home() patched process-wide so every module under test
    (imported fresh, after this patch) resolves the same fake home."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aos").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    aos_dir = _make_readonly_aos(home)
    yield home, aos_dir
    # Restore write permission so pytest/tmp_path cleanup can remove it.
    aos_dir.chmod(0o755)


class TestDailyBriefingWritesUnderDotAos:
    def test_thread_survives_with_aos_read_only_and_writes_only_under_dot_aos(
        self, sandbox_home
    ):
        home, aos_dir = sandbox_home
        mod = _load_module(DAILY_BRIEFING_PATH, "daily_briefing_uut_1")

        # hour=99 guarantees no send branch is ever taken (now.hour is 0-23),
        # so this never makes a real Telegram call.
        thread = mod.start_daily_briefing("fake-token", 12345, hour=99, minute=0)
        try:
            thread.join(timeout=0.5)
            assert thread.is_alive(), (
                "daily-briefing thread died — it crashed on the "
                "read-only ~/aos mkdir (aos#2320), exactly like production"
            )
        finally:
            # Daemon thread; process exit would reclaim it regardless, but
            # nothing to explicitly stop here (it's parked in Event().wait).
            pass

        assert (home / ".aos" / "data" / "bridge").is_dir()
        assert not (home / "aos" / "data").exists(), (
            "wrote under ~/aos — the read-only framework tree"
        )

    def test_learning_drip_creates_state_dir_under_dot_aos(self, sandbox_home):
        """_send_learning_drip's drip_state_file.parent.mkdir() is the exact
        crash site from the issue traceback, and it runs before either of
        the function's two early returns (no onboarding / no drip config
        due today) — so the directory must exist even on a day with no
        drip message to send, without needing a real Telegram round trip."""
        home, aos_dir = sandbox_home
        (home / ".aos" / "config").mkdir(parents=True, exist_ok=True)
        (home / ".aos" / "config" / "onboarding.yaml").write_text(
            "completed: '2026-09-12T00:00:00Z'\n"
        )

        mod = _load_module(DAILY_BRIEFING_PATH, "daily_briefing_uut_2")
        mod._send_learning_drip("fake-token", 12345, "midday")

        assert (home / ".aos" / "data" / "bridge").is_dir()
        assert not (home / "aos" / "data").exists()


class TestSessionManagerWritesUnderDotAos:
    def test_save_get_clear_round_trip_survives_readonly_aos(self, sandbox_home):
        home, aos_dir = sandbox_home
        mod = _load_module(
            SESSION_MANAGER_PATH, "session_manager_uut", extra_syspath=BRIDGE_DIR
        )

        mod.save_session_id("dm:12345", "session-abc")
        assert mod.get_session_id("dm:12345") == "session-abc"

        sessions_file = home / ".aos" / "data" / "bridge" / "sessions.json"
        assert sessions_file.exists()
        assert json.loads(sessions_file.read_text())["dm:12345"]["session_id"] == "session-abc"
        assert not (home / "aos" / "data").exists()

        mod.clear_session("dm:12345")
        assert mod.get_session_id("dm:12345") is None


class TestFrictionRulesPendingFilePairing:
    """friction-rules writes proposals; intent_classifier.handle_friction
    reads them back to report "N auto-rule proposal(s) pending". Both moved
    off the same broken AOS_DIR/"apps"/"bridge"/... path together — a test
    that only checked one side wouldn't catch them drifting apart again."""

    def test_write_then_read_round_trip_survives_readonly_aos(self, sandbox_home):
        home, aos_dir = sandbox_home
        friction_mod = _load_module(FRICTION_RULES_PATH, "friction_rules_uut")
        intent_mod = _load_module(
            INTENT_CLASSIFIER_PATH, "intent_classifier_uut", extra_syspath=BRIDGE_DIR
        )

        friction_mod.save_pending(
            [{"id": 1, "pattern": "x", "count": 5, "proposed_rule": "y"}]
        )

        pending_file = home / ".aos" / "data" / "bridge" / "pending_rules.json"
        assert pending_file.exists()
        assert not (home / "aos" / "apps").exists()
        assert not (home / "aos" / "data").exists()

        reviews_dir = home / "vault" / "reviews"
        reviews_dir.mkdir(parents=True)
        (reviews_dir / "session-friction-2026-09-13.md").write_text(
            '---\ntotal_frictions: 3\ndate: "2026-09-13"\nperiod_days: 7\n---\n\nbody\n'
        )

        reply = intent_mod.handle_friction("")
        assert "1 auto-rule proposal" in reply
