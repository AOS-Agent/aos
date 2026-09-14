"""
End-to-end release smoke test for Claude profiles (aos#244, v0.7.10 — all
four parts together: isolated profiles, the cld2/cld3 launchers, lane
failover, and this release itself).

One test, driving the REAL executables (`claude-profile`, the `cld` launcher
via a `cld2` symlink) as real subprocesses, plus the real `claude_lanes.run()`
loaded by file path, all against a single stub `claude` on PATH and a single
sandboxed $HOME — nothing here touches the operator's real `~/.claude`,
`~/.aos`, or Keychain (see conftest.py's session-wide
`_live_instance_is_never_touched` backstop).

The stub plays two roles, distinguished by its own argv shape rather than by
call order — the launcher check and the lanes check are independent and must
not interfere with each other's counters:

  - Invoked WITHOUT `-p` (the shape `cld`'s `exec claude
    --dangerously-skip-permissions --agent ...` uses): just echoes its env
    and argv and exits 0 — this is the launcher check's stub.
  - Invoked WITH `-p` (the shape `claude_lanes.run(["claude", "-p", ...])`
    uses): the FIRST such call prints "You've hit your weekly limit. Resets
    at 3pm" and exits 1; every call after that prints "ok" and exits 0 — this
    is the lanes-failover check's stub, and its own call count is tracked
    separately from the launcher check's calls.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
import textwrap
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PROFILE = REPO_ROOT / "core" / "bin" / "cli" / "claude-profile"
CLD = REPO_ROOT / "core" / "bin" / "cld"
CLAUDE_LANES = REPO_ROOT / "core" / "infra" / "lib" / "claude_lanes.py"
WORK_SCHEMA = (REPO_ROOT / "tests" / "fixtures" / "work_schema.sql").read_text()

_STUB_CLAUDE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")

    if "-p" in sys.argv:
        # Lanes-failover shape: claude -p "<message>" ...
        counter_path = os.environ["E2E_LANE_COUNTER"]
        n = 0
        if os.path.exists(counter_path):
            n = int(open(counter_path).read().strip() or "0")
        with open(counter_path, "w") as f:
            f.write(str(n + 1))

        log_path = os.environ.get("E2E_LANE_CALL_LOG")
        if log_path:
            with open(log_path, "a") as f:
                f.write((cfg or "default") + "\\n")

        if n == 0:
            print("You've hit your weekly limit. Resets at 3pm")
            sys.exit(1)
        print("ok")
        sys.exit(0)

    # Launcher-check shape: claude --dangerously-skip-permissions --agent ...
    print("CLAUDE_CONFIG_DIR=" + (cfg or "<unset>"))
    print("ARGV=" + json.dumps(sys.argv[1:]))
    sys.exit(0)
    """)


def _load_claude_lanes():
    loader = SourceFileLoader("claude_lanes_e2e", str(CLAUDE_LANES))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


def test_claude_profiles_release_smoke(tmp_path, monkeypatch):
    """The v0.7.10 release smoke test: add a profile, launch it, and prove
    headless lane failover, end to end, against real executables."""

    # ── sandbox ──────────────────────────────────────────────────────────
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".aos" / "config").mkdir(parents=True)
    (home / ".aos" / "data").mkdir(parents=True)
    # `cld` resolves claude-profile via $HOME/aos/core/bin/cli/claude-profile
    # — the same repo-root-relative shape every other test in this suite uses.
    (home / "aos").symlink_to(REPO_ROOT)

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

    lane_counter = tmp_path / "lane_counter"
    lane_call_log = tmp_path / "lane_calls.log"

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AOS_WORK_DB"] = str(db_path)
    env["E2E_LANE_COUNTER"] = str(lane_counter)
    env["E2E_LANE_CALL_LOG"] = str(lane_call_log)
    env.pop("CLAUDE_CONFIG_DIR", None)

    # ── 1. `claude-profile add cld2` — the real executable ────────────────
    add = subprocess.run(
        [sys.executable, str(CLAUDE_PROFILE), "add", "cld2"],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert add.returncode == 0, add.stderr
    profile_dir = home / ".aos" / "claude-profiles" / "cld2"
    assert profile_dir.is_dir()

    # ── 2. the real `cld2` launcher (a symlink to core/bin/cld) ───────────
    cld2_link = tmp_path / "cld2"
    cld2_link.symlink_to(CLD)
    launch = subprocess.run(
        [str(cld2_link)], env=env, capture_output=True, text=True, timeout=15,
    )
    assert launch.returncode == 0, launch.stderr
    assert f"CLAUDE_CONFIG_DIR={profile_dir}" in launch.stdout
    assert 'ARGV=["--dangerously-skip-permissions", "--agent", "chief"]' in launch.stdout

    # ── 3. lane failover — a real usage-limit hit fails over to cld2 ──────
    (home / ".aos" / "config" / "claude-lanes.yaml").write_text("lanes: [default, cld2]\n")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AOS_WORK_DB", str(db_path))
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("E2E_LANE_COUNTER", str(lane_counter))
    monkeypatch.setenv("E2E_LANE_CALL_LOG", str(lane_call_log))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    lanes = _load_claude_lanes()
    result = lanes.run(["claude", "-p", "hi"])

    assert result.returncode == 0
    assert "ok" in result.stdout

    calls = [l for l in lane_call_log.read_text().splitlines() if l]
    assert calls == ["default", str(profile_dir)], (
        "expected the first attempt on default, the retry on cld2's own config dir"
    )

    state = lanes.load_state()
    assert "default" in state
    assert "exhausted_until" in state["default"]  # exact value depends on wall-clock at test time

    inbox = lanes._load_work_backend().get_inbox()
    assert len(inbox) == 1
    assert inbox[0]["source"] == "lanes"
    assert "default" in inbox[0]["text"] and "cld2" in inbox[0]["text"]
