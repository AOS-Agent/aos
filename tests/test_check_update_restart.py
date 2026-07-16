"""
Routing gate: every LaunchAgent restart in AOS must go through the shared
guarded choke-point (core/infra/lib/service_ctl.py) — never a raw
bootout / bootstrap / kickstart / unload / load.

Why this gate exists: during the v0.6.10/0.6.11 update cycles com.aos.bridge and
com.aos.transcriber were silently unloaded. Several restart paths ran an inline

    launchctl bootout  gui/$uid/$svc
    launchctl bootstrap gui/$uid $plist
    launchctl kickstart -k gui/$uid/$svc

with no settle after bootout and errors swallowed. `launchctl bootout` is
asynchronous, so `bootstrap` raced the teardown; when it lost, the job was left
booted-out — plist intact, venv intact, job gone (aos#180).

The fix promotes check-update's guarded `_restart_launchagent` into ONE shared
helper (settle → verify → retry → kickstart, with lifecycle audit logging) and
routes all six restart paths through it. This test proves no raw launchctl
lifecycle call survives in the live restart surface, so the racy copy-paste
cannot reappear. The settle/verify logic itself is tested in test_service_ctl.py.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
CHECK_UPDATE = REPO_ROOT / "core" / "bin" / "crons" / "check-update"
SERVICE_CTL = REPO_ROOT / "core" / "infra" / "lib" / "service_ctl.py"
HELPER_NAME = "_restart_launchagent"

# The live restart surface: every file that used to (or could) restart a
# LaunchAgent. service_ctl.py is the ONLY file allowed to issue raw launchctl
# lifecycle verbs; everything else must delegate to it. Migrations are excluded
# — they are one-time, already-applied, frozen historical artifacts with their
# own guarded (settle + kickstart-timeout + health-poll) pattern.
LIVE_RESTART_SURFACE = [
    "core/bin/crons/check-update",
    "core/bin/crons/watchdog",
    "core/bin/cli/aos",
    "core/infra/reconcile/checks/transcriber.py",
    "core/infra/reconcile/checks/bridge_poll_liveness.py",
    "core/infra/reconcile/checks/launchagents.py",
    "core/infra/reconcile/checks/sentinel_plist.py",
    "core/infra/reconcile/checks/n8n.py",
]

# Raw launchd lifecycle verbs that must not appear outside the choke-point.
# `launchctl list` / `launchctl print` are read-only probes and are allowed.
RAW_LIFECYCLE = re.compile(
    r"launchctl\s+(bootout|bootstrap|kickstart)\b"
    r"|launchctl\s+(unload|load)\b"
)


def _is_comment(line: str) -> bool:
    # Both Python and shell use '#'; every file in the surface is one or the other.
    return line.lstrip().startswith("#")


def test_choke_point_exists():
    assert SERVICE_CTL.exists(), "service_ctl.py choke-point is missing"
    text = SERVICE_CTL.read_text()
    assert "def restart_launchagent" in text
    # The guarded sequence lives here.
    assert "bootout" in text and "bootstrap" in text and "kickstart" in text


def test_check_update_helper_delegates_to_choke_point():
    """check-update's _restart_launchagent must now delegate to service_ctl.py,
    not run launchctl inline."""
    text = CHECK_UPDATE.read_text()
    assert HELPER_NAME in text
    assert "service_ctl.py" in text and "restart" in text


@pytest.mark.parametrize("rel", LIVE_RESTART_SURFACE)
def test_no_raw_launchctl_lifecycle_in_restart_surface(rel):
    """No file in the live restart surface may issue a raw launchctl
    bootout/bootstrap/kickstart/unload/load — all must route through the
    shared choke-point (grep-proof, encoded as a test)."""
    path = REPO_ROOT / rel
    assert path.exists(), f"{rel} not found"
    offenders = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if _is_comment(line):
            continue
        if RAW_LIFECYCLE.search(line):
            offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "Raw launchctl lifecycle call outside the choke-point — route it "
        "through core/infra/lib/service_ctl.py restart_launchagent():\n  "
        + "\n  ".join(offenders)
    )


def test_service_ctl_is_the_only_place_with_raw_lifecycle():
    """Sanity: the choke-point itself DOES own the raw verbs."""
    assert RAW_LIFECYCLE.search(SERVICE_CTL.read_text())
