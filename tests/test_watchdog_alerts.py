"""
Issue #2354 — false "system down" Telegram alerts from core/bin/crons/watchdog:

  1. Internet reachability used ICMP (`ping -c1 -W3 1.1.1.1`), which is
     filtered by policy on many consumer/corporate networks and VPNs. The
     operator's own network dropped 100% of ICMP while HTTPS worked fine —
     `watchdog.log` recorded "Internet is DOWN" continuously for 23 days,
     ~5,700 times, every one of them false.
  2. No consecutive-failure debounce: a single timed-out probe flipped the
     state straight to "down" and alerted immediately.
  3. Tailscale health was `tailscale status --self | head -1` grepped for the
     substring "stopped" — `--self` is silently ignored by the real CLI (self
     is not guaranteed to be line 1), and `NeedsLogin` / a logged-out backend
     never matches "stopped", so it can both false-positive (peers sorted
     before self) and false-negative (real failure states read as healthy).

Fix (core/bin/crons/watchdog): HTTPS reachability across two independent
hosts (`internet_up`), gated behind a two-consecutive-tick debounce persisted
under $STATE_DIR (`check_internet`); Tailscale's supported `BackendState`
field parsed from `tailscale status --json` instead of scraped text
(`check_tailscale` / `tailscale_backend_state`).

Testing approach: the real script is sourced with WATCHDOG_UNDER_TEST=1,
which (per the guard added right after all function definitions) makes
sourcing define every function without running the service/disk/main-loop
body below it. Each "tick" is its own subprocess (mirroring the real cron:
watchdog runs freshly every 5 minutes, state persists on disk between runs)
with curl/tailscale stubbed on PATH and `notify()` redefined to append to a
local file — this suite never shells out to aos-notify and never sends a
real Telegram message.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WATCHDOG = REPO / "core" / "bin" / "crons" / "watchdog"
UNUSUAL_TAILSCALE_FIXTURE = REPO / "tests" / "fixtures" / "tailscale_status_unusual.json"

# The real pinned interpreter, not the `aos-python` resolver script: the
# resolver's fallback chain looks under `$HOME/.aos/python/...` first, and
# this suite deliberately sandboxes $HOME, so on a machine where every other
# fallback candidate fails its own health probe (the documented pyexpat/expat
# mismatch in aos-python's own comments) the resolver would find nothing.
# Pointing AOS_PY straight at the real interpreter sidesteps that HOME
# dependency; it's the same interpreter the rest of this suite runs under.
_PINNED_PYTHON = Path.home() / ".aos" / "python" / "bin" / "python"
AOS_PYTHON = _PINNED_PYTHON if _PINNED_PYTHON.exists() else Path(sys.executable)


def _make_env(tmp_path, curl_ok: bool, tailscale_json: dict | None = None,
              tailscale_present: bool = True):
    """A sandboxed HOME + PATH: fake curl/tailscale, real aos-python (for the
    JSON parse), the script's own state dir under the fake HOME."""
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    curl_stub = bin_dir / "curl"
    curl_stub.write_text("#!/bin/bash\nexit 0\n" if curl_ok else "#!/bin/bash\nexit 1\n")
    curl_stub.chmod(0o755)

    if tailscale_present:
        payload = json.dumps(tailscale_json if tailscale_json is not None else {"BackendState": "Running"})
        ts_stub = bin_dir / "tailscale"
        ts_stub.write_text(f"#!/bin/bash\ncat <<'JSONEOF'\n{payload}\nJSONEOF\n")
        ts_stub.chmod(0o755)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["AOS_PY"] = str(AOS_PYTHON)
    env["WATCHDOG_UNDER_TEST"] = "1"
    return env


def _tick(tmp_path, env, fn_call: str):
    """One simulated cron invocation: source the script, stub notify(), call
    the function under test. Returns (CompletedProcess, new_notify_lines)."""
    notify_log = tmp_path / "notify.log"
    before = notify_log.read_text() if notify_log.exists() else ""

    script = f'''
source "{WATCHDOG}"
notify() {{ echo "$1" >> "{notify_log}"; }}
{fn_call}
'''
    result = subprocess.run(["bash", "-c", script], env=env,
                             capture_output=True, text=True, timeout=30)

    after = notify_log.read_text() if notify_log.exists() else ""
    assert after.startswith(before)
    return result, after[len(before):]


# ── internet: HTTPS probe + two-tick debounce ───────────────────────────────

def test_single_failed_probe_does_not_alert(tmp_path):
    env = _make_env(tmp_path, curl_ok=False)
    result, notified = _tick(tmp_path, env, "check_internet")
    assert result.stderr == "", result.stderr
    assert notified == ""


def test_two_consecutive_failures_alert_once(tmp_path):
    env = _make_env(tmp_path, curl_ok=False)

    _, notified_1 = _tick(tmp_path, env, "check_internet")
    assert notified_1 == ""

    _, notified_2 = _tick(tmp_path, env, "check_internet")
    assert "down" in notified_2.lower()

    # Debounced: state already "down", a third failure must not re-alert.
    _, notified_3 = _tick(tmp_path, env, "check_internet")
    assert notified_3 == ""


def test_recovery_clears_the_debounce(tmp_path):
    env_fail = _make_env(tmp_path, curl_ok=False)
    _tick(tmp_path, env_fail, "check_internet")
    _, notified_down = _tick(tmp_path, env_fail, "check_internet")
    assert "down" in notified_down.lower()

    # Same sandbox (same fake HOME => same $STATE_DIR), internet recovers.
    env_ok = _make_env(tmp_path, curl_ok=True)
    _, notified_up = _tick(tmp_path, env_ok, "check_internet")
    assert "back" in notified_up.lower()

    # A fresh failure right after recovery must NOT alert immediately —
    # the debounce reset, so it takes two more consecutive ticks again.
    env_fail_again = _make_env(tmp_path, curl_ok=False)
    _, notified_again = _tick(tmp_path, env_fail_again, "check_internet")
    assert notified_again == ""


def test_internet_check_no_longer_uses_icmp_ping():
    text = WATCHDOG.read_text()
    assert "ping -c1" not in text
    assert "curl" in text  # replaced with an HTTPS reachability probe


# ── tailscale: `status --json` BackendState, not scraped text ──────────────

def test_tailscale_running_state_reads_as_up(tmp_path):
    env = _make_env(tmp_path, curl_ok=True, tailscale_json={"BackendState": "Running"})
    result, notified = _tick(tmp_path, env, "check_tailscale")
    assert result.returncode == 0
    # First-ever tick: prior state is unknown, so the up transition itself
    # is reported once — this mirrors every other state key in the script
    # (services, disk) and is not part of #2354's false-alert bug.
    assert "back up" in notified.lower() or notified == ""


def test_tailscale_needs_login_reads_as_down_not_up(tmp_path):
    """Pre-fix, only the literal substring "stopped" counted as down, so
    NeedsLogin (a real failure state) read as healthy. That must be fixed."""
    env = _make_env(tmp_path, curl_ok=True, tailscale_json={"BackendState": "NeedsLogin"})
    result1, notified1 = _tick(tmp_path, env, "check_tailscale")
    # check_tailscale's return value IS the up/down signal (used by the
    # caller as `check_tailscale || issues=$(( issues + 1 ))`) — NeedsLogin
    # must read as down, exit 1, same as the old "stopped" substring did.
    assert result1.returncode == 1
    assert "down" in notified1.lower()

    # Second tick: state was already "down" from the first, so no repeat
    # alert — but it must never have flipped to "up".
    result2, notified2 = _tick(tmp_path, env, "check_tailscale")
    assert result2.returncode == 1
    assert "back up" not in notified2.lower()


def test_tailscale_unusual_peer_fixture_parses_without_error(tmp_path):
    """A real-world `tailscale status --json` blob with edge cases the old
    line-scraping approach couldn't handle: peers with a null LastSeen,
    duplicate hostnames, nested/irregular fields, and no guarantee self is
    first. The fixed parser only ever reads top-level BackendState, so none
    of that should matter."""
    fixture = json.loads(UNUSUAL_TAILSCALE_FIXTURE.read_text())
    assert fixture["BackendState"] == "Running"

    env = _make_env(tmp_path, curl_ok=True, tailscale_json=fixture)
    result, notified = _tick(tmp_path, env, "check_tailscale")

    assert result.returncode == 0
    assert result.stderr == "", result.stderr
    assert "back up" in notified.lower() or notified == ""


def test_tailscale_backend_state_helper_extracts_the_field_directly(tmp_path):
    env = _make_env(tmp_path, curl_ok=True, tailscale_present=False)
    script = f'''
source "{WATCHDOG}"
echo '{{"BackendState": "Stopped", "Other": "junk"}}' | tailscale_backend_state
'''
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "Stopped"


def test_tailscale_check_no_longer_scrapes_status_text():
    """The old buggy invocation (`tailscale status --self ... | head -1`,
    matched against the literal substring "stopped") must be gone from the
    executable code — referencing it in an explanatory comment is fine and
    expected, so this checks for the actual call shape, not a bare substring."""
    text = WATCHDOG.read_text()
    assert "tailscale status --self" not in text.replace("`tailscale status --self`", "")
    assert "head -1" not in text
    assert '=~ "stopped"' not in text and "=~ 'stopped'" not in text
    assert "status --json" in text
    assert "BackendState" in text
