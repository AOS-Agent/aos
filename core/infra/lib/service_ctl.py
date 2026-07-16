#!/usr/bin/env python3
"""
The single guarded choke-point for all AOS service stop/start.

Every LaunchAgent restart in AOS — the reconcile checks, the update deployer,
the watchdog, and `aos repair` — must route through ``restart_launchagent()``
here. It is the promoted, shared form of check-update's ``_restart_launchagent``
(commit 4342a0f):

    bootout → settle (wait for launchd to release) → bootstrap → VERIFY the job
    registered → retry the bootstrap → kickstart

It NEVER returns having left the job silently unloaded: either the job is
verified loaded (return True), or it returns False so the caller escalates.
A bare ``launchctl bootout`` followed by an un-verified ``bootstrap`` races
launchd's async teardown; when bootstrap loses that race the job is left
booted-out — plist intact, venv intact, job gone. That is exactly how
com.aos.bridge and com.aos.transcriber vanished during the v0.6.10/0.6.11
update cycles (aos#180). Centralizing here means that race is fixed in one
place and cannot be re-introduced by a copy-pasted bootout/bootstrap.

Every action is appended to ~/.aos/logs/service-lifecycle.jsonl (one JSON
object per line: ts, service, action, actor, result[, detail]) so
"the service just vanished" becomes a one-grep answer, and so callers can
reason about recent restarts (anti-flap) across process boundaries.

Usable two ways so bash and python callers share ONE implementation:

  Python:
      from lib.service_ctl import restart_launchagent
      ok = restart_launchagent("com.aos.bridge", actor="reconcile:transcriber")

  Shell (via aos-python, so PYTHONPATH/interpreter are consistent):
      aos-python core/infra/lib/service_ctl.py restart com.aos.bridge \
          --plist ~/Library/LaunchAgents/com.aos.bridge.plist --actor watchdog
      # exit 0 = job verified loaded, 1 = failed (caller escalates)
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
LA_DIR = HOME / "Library" / "LaunchAgents"
LIFECYCLE_LOG = HOME / ".aos" / "logs" / "service-lifecycle.jsonl"

# launchd's bootout is async — wait up to this many 1s ticks for it to release
# the job before bootstrapping, and retry the bootstrap this many times while
# verifying the job actually registered.
SETTLE_TRIES = 5
BOOTSTRAP_TRIES = 3

# Known service health endpoints. Ports are defined in each service's own code
# (not declared by the filesystem), so a small explicit map is the honest
# source of truth. Kept HERE, in the shared choke-point, so the reconcile
# ServiceLoadedCheck and any future caller share ONE definition rather than the
# drifting copies that produced the 7601-vs-7602 transcriber bug (aos#180).
# The state.yaml migration keeps a frozen point-in-time copy by design
# (migrations must not import evolving lib code).
KNOWN_HEALTH_URLS = {
    "bridge": "http://127.0.0.1:4098/health",
    "transcriber": "http://127.0.0.1:7602/health",
    "qareen": "http://127.0.0.1:4096/api/health",
    "n8n": "http://127.0.0.1:5678/healthz",
    "listen": "http://127.0.0.1:7600/health",
    "whatsmeow": "http://127.0.0.1:7601/health",
}


def _audit(service: str, action: str, actor: str, result: str, detail: str | None = None) -> None:
    """Append one lifecycle event. Best-effort — never breaks a restart."""
    try:
        LIFECYCLE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "service": service,
            "action": action,
            "actor": actor,
            "result": result,
        }
        if detail:
            entry["detail"] = detail
        with open(LIFECYCLE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _print_loaded(service_target: str) -> bool:
    """True if launchctl reports the job registered in the domain."""
    try:
        r = subprocess.run(
            ["launchctl", "print", service_target],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def is_loaded(label: str) -> bool:
    """True if the LaunchAgent ``label`` is registered with launchd."""
    return _print_loaded(f"gui/{os.getuid()}/{label}")


def last_restart_age(label: str) -> float | None:
    """Seconds since the last restart of ``label`` recorded in the lifecycle
    log, or None if there is no record. Callers use this to avoid re-restarting
    a service that was just restarted by another check (anti-flap)."""
    try:
        if not LIFECYCLE_LOG.exists():
            return None
        newest: str | None = None
        with open(LIFECYCLE_LOG) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("service") == label and e.get("action") == "restart" \
                        and e.get("result") == "ok":
                    ts = e.get("ts")
                    if ts and (newest is None or ts > newest):
                        newest = ts
        if not newest:
            return None
        return (datetime.now(timezone.utc) - datetime.fromisoformat(newest)).total_seconds()
    except Exception:
        return None


def restart_launchagent(label: str, plist_path=None, actor: str = "unknown") -> bool:
    """Guarded restart of LaunchAgent ``label``. Returns True iff the job is
    verified loaded afterwards.

    bootout → settle → bootstrap (verify + retry) → kickstart. On failure to
    load, returns False (never silently leaves the job unloaded) and records a
    ``failed`` lifecycle event so the caller can escalate.
    """
    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"gui/{uid}/{label}"
    plist = Path(plist_path) if plist_path else (LA_DIR / f"{label}.plist")

    if not plist.exists():
        _audit(label, "restart", actor, "error", f"plist missing: {plist}")
        return False

    # 1. Bootout, then wait for launchd to actually release the job before
    #    bootstrapping (bootout is async — up to ~5s to settle).
    subprocess.run(["launchctl", "bootout", service], capture_output=True, timeout=10)
    for _ in range(SETTLE_TRIES):
        if not _print_loaded(service):
            break
        time.sleep(1)

    # 2. Bootstrap, then VERIFY the job registered. Retry — a lost bootout race
    #    must never be left as a silently-unloaded service.
    loaded = False
    for _ in range(BOOTSTRAP_TRIES):
        subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)],
            capture_output=True, timeout=10,
        )
        if _print_loaded(service):
            loaded = True
            break
        time.sleep(1)

    if not loaded:
        _audit(label, "restart", actor, "failed",
               "not loaded after bootstrap retries — service left DOWN")
        return False

    # 3. In-place restart to pick up new code/venv. `-k` can block past the
    #    timeout while the old instance drains before the new one binds its
    #    port — not a failure; the job is already verified loaded.
    try:
        subprocess.run(["launchctl", "kickstart", "-k", service],
                       capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        _audit(label, "restart", actor, "ok",
               "kickstart draining (old instance) — job loaded, KeepAlive owns liveness")
        return True

    _audit(label, "restart", actor, "ok")
    return True


def _main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="service_ctl", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("restart", help="Guarded restart of a LaunchAgent")
    r.add_argument("label", help="LaunchAgent label, e.g. com.aos.bridge")
    r.add_argument("--plist", default=None,
                   help="Plist path (defaults to ~/Library/LaunchAgents/<label>.plist)")
    r.add_argument("--actor", default="cli", help="Who requested the restart (audit)")

    args = p.parse_args(argv)
    if args.cmd == "restart":
        return 0 if restart_launchagent(args.label, args.plist, actor=args.actor) else 1
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
