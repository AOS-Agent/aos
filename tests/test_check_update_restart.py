"""
Pattern gate: every LaunchAgent restart in check-update must go through the
guarded `_restart_launchagent` helper — never an inline
bootout → bootstrap → kickstart sequence.

Why this gate exists: during the v0.6.10 update cycle com.aos.bridge was
silently unloaded. The phase-2 restart loop ran

    launchctl bootout  gui/$uid/$la 2>/dev/null
    launchctl bootstrap gui/$uid $plist 2>/dev/null
    launchctl kickstart -k gui/$uid/$la 2>/dev/null

with no settle after bootout and every error swallowed. `launchctl bootout`
is asynchronous, so `bootstrap` raced the teardown; when it lost, the job was
left booted-out — plist intact, venv intact, job gone — and nothing re-loaded
it until a human bootstrapped it back hours later.

The fix centralizes all launchctl bootout/bootstrap/kickstart into one helper
that settles after bootout, verifies the bootstrap registered the job, retries,
and never returns having left the service silently unloaded. This test locks
that invariant in place so the racy copy-paste cannot reappear.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
CHECK_UPDATE = REPO_ROOT / "core" / "bin" / "crons" / "check-update"

HELPER_NAME = "_restart_launchagent"


def _lines():
    return CHECK_UPDATE.read_text().splitlines()


def _helper_range(lines):
    """Return (start, end) line indices (inclusive) of the helper's body.

    The helper opens with `_restart_launchagent() {` and closes with a `}` at
    column 0 — the shell function style used throughout check-update.
    """
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{HELPER_NAME}()"):
            start = i
            break
    assert start is not None, f"{HELPER_NAME} is not defined in check-update"
    for j in range(start + 1, len(lines)):
        if lines[j] == "}":
            return start, j
    pytest.fail(f"{HELPER_NAME} has no closing brace at column 0")


def test_helper_is_defined():
    assert HELPER_NAME in CHECK_UPDATE.read_text()


def test_all_launchctl_lifecycle_calls_live_in_the_helper():
    """bootout / bootstrap / kickstart may only appear inside the guarded helper.

    Comment lines are ignored; only executable `launchctl ...` calls count.
    """
    lines = _lines()
    start, end = _helper_range(lines)
    call = re.compile(r"^\s*launchctl\s+(bootout|bootstrap|kickstart)\b")
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if call.search(line):
            assert start <= i <= end, (
                f"check-update line {i + 1} runs an inline launchctl "
                f"lifecycle call outside {HELPER_NAME}: {line.strip()!r}. "
                f"Route it through {HELPER_NAME} — a bare bootout→bootstrap "
                f"races launchd's async teardown and can leave the job "
                f"unloaded (the v0.6.10 bridge-vanish incident)."
            )


def test_helper_settles_after_bootout_and_verifies_bootstrap():
    """The helper must wait after bootout and verify the job actually loaded."""
    lines = _lines()
    start, end = _helper_range(lines)
    body = "\n".join(lines[start : end + 1])

    bootout_idx = next(i for i in range(start, end + 1) if "launchctl bootout" in lines[i])
    bootstrap_idx = next(i for i in range(start, end + 1) if "launchctl bootstrap" in lines[i])
    assert bootout_idx < bootstrap_idx, "bootout must precede bootstrap in the helper"

    # A settle probe (launchctl print) must sit between bootout and bootstrap.
    settle = any(
        "launchctl print" in lines[i] for i in range(bootout_idx + 1, bootstrap_idx)
    )
    assert settle, (
        "The helper must wait for launchd to release the job (a `launchctl "
        "print` settle probe) between bootout and bootstrap."
    )

    # A verify probe must sit after bootstrap so a lost race is detected.
    verify = any(
        "launchctl print" in lines[i] for i in range(bootstrap_idx + 1, end + 1)
    )
    assert verify, (
        "The helper must verify the job registered (a `launchctl print` check) "
        "after bootstrap, and retry — never leave the service silently unloaded."
    )

    # A retry loop must guard the bootstrap so a single lost race isn't fatal.
    assert "loaded=true" in body or "for i in" in body, (
        "The helper must retry bootstrap until the job is loaded."
    )
