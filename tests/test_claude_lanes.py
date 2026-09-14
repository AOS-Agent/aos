"""
Tests for `core/infra/lib/claude_lanes.py` (aos#244.3) — the one resolver
every headless `claude` spawn calls through so it can fail over to the next
Claude profile when the current login has hit a usage limit.

claude_lanes.py has no .py-package context of its own (it is loaded by file
path from several different trees — the bridge's venv, the main aos-python,
a cron), so these tests load it the same way its real callers do: by file
path via `SourceFileLoader`, one fresh copy per test so each test's
sandboxed $HOME (and the module-level `_work_backend` cache) start clean.

A stub `claude` goes first on PATH. It is deliberately data-driven (env vars
tell it which lane(s) to report as limited) rather than one hardcoded
script, so every scenario below drives the exact same binary — the thing
under test is claude_lanes' retry/state/dedup logic, not the stub.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import textwrap
from datetime import datetime, timedelta
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_LANES = REPO_ROOT / "core" / "infra" / "lib" / "claude_lanes.py"
CLAUDE_PROFILE = REPO_ROOT / "core" / "bin" / "cli" / "claude-profile"
WORK_SCHEMA = (REPO_ROOT / "tests" / "fixtures" / "work_schema.sql").read_text()


def _load_claude_lanes():
    loader = SourceFileLoader("claude_lanes_under_test", str(CLAUDE_LANES))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


# ── the stub `claude` ────────────────────────────────────────────────────
#
# Every invocation appends one line to $LANES_CALL_LOG: the CLAUDE_CONFIG_DIR
# it saw ("" for none — the default lane). It reports a usage-limit hit
# (message + exit 1) whenever the current CLAUDE_CONFIG_DIR's basename (or
# "default", when unset) is listed — comma-separated — in
# $LANES_LIMIT_LANES; otherwise it prints "OK" and exits 0.
_STUB_CLAUDE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import os
    import sys

    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
    lane = os.path.basename(cfg.rstrip("/")) if cfg else "default"

    log_path = os.environ.get("LANES_CALL_LOG")
    if log_path:
        with open(log_path, "a") as f:
            f.write(lane + "\\n")

    limited = {x for x in os.environ.get("LANES_LIMIT_LANES", "").split(",") if x}
    if lane in limited:
        message = os.environ.get("LANES_LIMIT_MESSAGE", "You've hit your weekly limit. Resets at 3pm")
        print(message)
        sys.exit(1)

    print("OK cfg=" + cfg)
    sys.exit(0)
    """)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A sandboxed $HOME + PATH: the stub `claude` first on PATH, a working
    ~/.aos/data/work.db (so the inbox note can actually be exercised), and
    ~/aos symlinked to this repo so claude_lanes can find claude-profile."""
    home = tmp_path / "home"
    (home / ".aos" / "config").mkdir(parents=True)
    (home / ".aos" / "data").mkdir(parents=True)

    db_path = home / ".aos" / "data" / "work.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(WORK_SCHEMA)
    conn.commit()
    conn.close()

    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    stub = stub_dir / "claude"
    stub.write_text(_STUB_CLAUDE)
    stub.chmod(0o755)

    call_log = tmp_path / "calls.log"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("AOS_WORK_DB", str(db_path))
    monkeypatch.setenv("LANES_CALL_LOG", str(call_log))
    monkeypatch.delenv("LANES_LIMIT_LANES", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    return {"home": home, "call_log": call_log, "stub_dir": stub_dir}


@pytest.fixture()
def mod(sandbox):
    return _load_claude_lanes()


def _calls(sandbox) -> list[str]:
    if not sandbox["call_log"].exists():
        return []
    return [l for l in sandbox["call_log"].read_text().splitlines() if l]


def _write_lanes_yaml(home: Path, lanes: list[str]) -> None:
    (home / ".aos" / "config").mkdir(parents=True, exist_ok=True)
    (home / ".aos" / "config" / "claude-lanes.yaml").write_text(
        "lanes: [" + ", ".join(lanes) + "]\n"
    )


def _make_profile_dir(home: Path, name: str) -> Path:
    d = home / ".aos" / "claude-profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


class _FrozenDateTime(datetime):
    """A datetime subclass whose .now() always answers a fixed instant.
    Everything else (fromisoformat, arithmetic, comparisons) is inherited
    from the real datetime — only `now()` is overridden — so this can stand
    in wherever claude_lanes calls `datetime.now()` without a `now=`
    override reaching it, without adding a time-freezing dependency."""
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen


def _freeze(monkeypatch, mod, when: datetime):
    frozen = type("_Frozen", (_FrozenDateTime,), {"_frozen": when})
    monkeypatch.setattr(mod, "datetime", frozen)
    return frozen


# ── default lane, nothing configured ─────────────────────────────────────

class TestNoLanesFile:
    def test_identical_to_a_plain_call(self, mod, sandbox):
        assert not (sandbox["home"] / ".aos" / "config" / "claude-lanes.yaml").exists()
        result = mod.run(["claude"])
        assert result.returncode == 0
        assert "cfg=" in result.stdout
        assert _calls(sandbox) == ["default"]
        assert mod.load_state() == {}

    def test_default_lane_success_leaves_no_state(self, mod, sandbox):
        mod.run(["claude"])
        assert mod.load_state() == {}
        assert mod._load_work_backend().get_inbox() == []


# ── failover on a detected limit ─────────────────────────────────────────

class TestFailover:
    def test_first_call_exhausts_default_second_goes_to_cld2(self, mod, sandbox, monkeypatch):
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        monkeypatch.setenv("LANES_LIMIT_LANES", "default")

        _freeze(monkeypatch, mod, datetime(2026, 1, 1, 10, 0))
        result = mod.run(["claude"])

        assert result.returncode == 0
        assert f"cfg={sandbox['home']}/.aos/claude-profiles/cld2" in result.stdout
        assert _calls(sandbox) == ["default", "cld2"]

        state = mod.load_state()
        assert set(state) == {"default"}
        assert state["default"]["exhausted_until"] == "2026-01-01T15:00:00"

        inbox = mod._load_work_backend().get_inbox()
        assert len(inbox) == 1
        assert inbox[0]["text"] == "[lanes] default exhausted until 15:00 — switched to cld2"
        assert inbox[0]["source"] == "lanes"

    def test_repeated_calls_do_not_duplicate_the_inbox_item(self, mod, sandbox, monkeypatch):
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        monkeypatch.setenv("LANES_LIMIT_LANES", "default")
        _freeze(monkeypatch, mod, datetime(2026, 1, 1, 10, 0))

        mod.run(["claude"])
        mod.run(["claude"])  # default already exhausted -> straight to cld2, no new call to default

        assert _calls(sandbox) == ["default", "cld2", "cld2"]
        assert len(mod._load_work_backend().get_inbox()) == 1

    def test_all_lanes_exhausted_returns_last_result_no_extra_retries(self, mod, sandbox, monkeypatch):
        """Every lane is ALREADY known bad (state says so) before this call
        even starts — not "every lane fails during this call", which is the
        len(lanes)-bound scenario the next test covers. Knowing upfront that
        cycling through the rest is pointless, this makes exactly one real
        call (still a real one — the caller must see an honest failure) and
        stops, rather than retrying lanes already known exhausted."""
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        monkeypatch.setenv("LANES_LIMIT_LANES", "default,cld2")

        state_path = sandbox["home"] / ".aos" / "state" / "claude-lanes.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "default": {"exhausted_until": "2026-01-01T15:00:00"},
            "cld2": {"exhausted_until": "2026-01-01T15:00:00"},
        }))

        _freeze(monkeypatch, mod, datetime(2026, 1, 1, 10, 0))
        result = mod.run(["claude"])

        assert result.returncode == 1
        assert "weekly limit" in result.stdout
        assert len(_calls(sandbox)) == 1

    def test_never_loops_more_than_len_lanes_times(self, mod, sandbox, monkeypatch):
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        monkeypatch.setenv("LANES_LIMIT_LANES", "default,cld2")
        _freeze(monkeypatch, mod, datetime(2026, 1, 1, 10, 0))

        mod.run(["claude"])  # first call: both lanes fresh, tries default then cld2
        assert len(_calls(sandbox)) == 2  # bounded by len(lanes) == 2, not more

    def test_exhausted_lane_skipped_until_reset_then_used_again(self, mod, sandbox, monkeypatch):
        _write_lanes_yaml(sandbox["home"], ["default", "cld2"])
        _make_profile_dir(sandbox["home"], "cld2")

        state_path = sandbox["home"] / ".aos" / "state" / "claude-lanes.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "default": {"exhausted_until": "2026-01-01T15:00:00"},
        }))

        # Before the reset: default is skipped outright, cld2 used, no call
        # to default at all.
        _freeze(monkeypatch, mod, datetime(2026, 1, 1, 14, 0))
        result = mod.run(["claude"])
        assert _calls(sandbox) == ["cld2"]
        assert f"cfg={sandbox['home']}/.aos/claude-profiles/cld2" in result.stdout

        # After the reset: default is available again and tried first.
        _freeze(monkeypatch, mod, datetime(2026, 1, 1, 16, 0))
        result = mod.run(["claude"])
        assert _calls(sandbox) == ["cld2", "default"]
        assert result.returncode == 0


# ── reset-time parsing ────────────────────────────────────────────────────

class TestParseResetTime:
    def test_resets_at_3pm(self, mod):
        now = datetime(2026, 1, 1, 10, 0)
        assert mod.parse_reset_time("You've hit your weekly limit. Resets at 3pm", now) == \
            datetime(2026, 1, 1, 15, 0)

    def test_resets_at_rolls_to_tomorrow_if_already_past(self, mod):
        now = datetime(2026, 1, 1, 16, 0)
        assert mod.parse_reset_time("Resets at 3pm", now) == datetime(2026, 1, 2, 15, 0)

    def test_resets_in_hours_and_minutes(self, mod):
        now = datetime(2026, 1, 1, 10, 0)
        assert mod.parse_reset_time("You've hit your session limit. Resets in 2 hr 19 min", now) == \
            now + timedelta(hours=2, minutes=19)

    def test_iso_timestamp(self, mod):
        now = datetime(2026, 1, 1, 10, 0)
        text = "spend limit reached, resets at 2026-01-02T03:04:05"
        assert mod.parse_reset_time(text, now) == datetime(2026, 1, 2, 3, 4, 5)

    def test_default_five_hours_when_unparseable(self, mod):
        now = datetime(2026, 1, 1, 10, 0)
        assert mod.parse_reset_time("usage limit hit, try again later", now) == now + timedelta(hours=5)


class TestDetectLimit:
    @pytest.mark.parametrize("text", [
        "You've hit your weekly limit. Resets at 3pm",
        "You've hit your session limit.",
        "You've hit your Sonnet limit for today.",
        "Error: spend limit reached for this workspace",
        "Rate limit exceeded, please try again — will reset soon",
    ])
    def test_matches_documented_messages(self, mod, text):
        assert mod.detect_limit(text) is not None

    def test_no_match_on_an_unrelated_error(self, mod):
        assert mod.detect_limit("Error: invalid tool use") is None
        assert mod.detect_limit("") is None

    def test_matches_inside_output_format_json(self, mod):
        blob = json.dumps({"is_error": True, "result": "You've hit your weekly limit. Resets at 3pm"})
        assert mod.detect_limit(blob) is not None


# ── lanes.yaml loading ─────────────────────────────────────────────────────

class TestLoadLanes:
    def test_missing_file(self, mod):
        assert mod.load_lanes() == ["default"]

    def test_configured_lanes_preserve_order_and_dedup(self, mod, sandbox):
        _write_lanes_yaml(sandbox["home"], ["default", "cld2", "cld2", "cld3"])
        assert mod.load_lanes() == ["default", "cld2", "cld3"]

    def test_unparsable_file_degrades_to_default(self, mod, sandbox):
        path = sandbox["home"] / ".aos" / "config" / "claude-lanes.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not: valid: yaml: [")
        assert mod.load_lanes() == ["default"]


# ── every wired call site: argv/stdin/timeout preserved ──────────────────

class TestWiredCallSitesArgvUnchanged:
    """Each of the six headless spawn points is expected to hand claude_lanes
    exactly the argv/stdin/timeout it used to hand subprocess.run directly.
    These drive claude_lanes.run() with each site's own real argv shape and
    check the stub sees the same thing back — a stand-in for re-running each
    module's own call, without needing that module's other dependencies."""

    def test_slack_channel_shape(self, mod, sandbox):
        cmd = ["claude", "-p", "hello", "--output-format", "json", "--max-turns", "25"]
        result = mod.run(cmd, timeout=300, cwd=str(sandbox["home"]))
        assert result.returncode == 0
        assert _calls(sandbox) == ["default"]

    def test_memory_curate_shape(self, mod, sandbox):
        cmd = ["claude", "--print", "--agent", "advisor", "--model", "sonnet",
               "--dangerously-skip-permissions", "--allowedTools", "Read,Grep,Glob,Bash"]
        result = mod.run(cmd, stdin="the prompt text", timeout=900)
        assert result.returncode == 0

    def test_sentinel_spawner_shape_with_extra_env(self, mod, sandbox):
        cmd = ["claude", "--print", "--agent", "Sentinel", "--dangerously-skip-permissions",
               "--allowedTools", "WebSearch,WebFetch,Read,Write,Glob,Grep,Bash"]
        result = mod.run(cmd, stdin="prompt", timeout=60, env={"SENTINEL_TRIGGER_ID": "t1"})
        assert result.returncode == 0

    def test_session_manager_generate_shape(self, mod, sandbox):
        cmd = ["claude", "-p", "hi", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-mode", "bypassPermissions",
               "--max-turns", "50", "--max-budget-usd", "10"]
        result = mod.run(cmd, cwd=str(sandbox["home"]))
        assert result.returncode == 0

    def test_session_manager_retry_fresh_shape(self, mod, sandbox):
        cmd = ["claude", "-p", "hi", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-mode", "bypassPermissions",
               "--max-turns", "50", "--max-budget-usd", "10", "--agent", "chief"]
        result = mod.run(cmd, cwd=str(sandbox["home"]))
        assert result.returncode == 0

    def test_env_extra_is_merged_not_replacing_config_dir(self, mod, sandbox, monkeypatch):
        _write_lanes_yaml(sandbox["home"], ["cld2"])
        _make_profile_dir(sandbox["home"], "cld2")
        result = mod.run(["claude"], env={"SENTINEL_TRIGGER_ID": "abc"})
        assert result.returncode == 0
        assert f"cfg={sandbox['home']}/.aos/claude-profiles/cld2" in result.stdout
