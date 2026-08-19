"""
Migration 108: decommission Qareen (aos#208).

Qareen — the companion service on :4096 (FastAPI + ontology + React screen) —
is removed from the framework in the same commit as this migration. The
operator decision (2026-08-18): the companion bet moves to aos-app; the
voice/meeting concept is archived as reference material; the Auto Tracker
(shipment tracking) is retired outright, data included.

What SURVIVES, and where it went:
  - Work adapter/types/utils  → core/engine/work/ontology/ (vendored, in-process)
  - sessions / session_tasks  → stay in qareen.db, written by the vendored
    adapter (they were never written over HTTP; aos#131 owns the rename)
  - qareen.db itself          → stays; intelligence, loop signals, cron_runs,
    people intel and the work engine all read/write it directly

What this migration removes from the INSTANCE:
  1. LaunchAgents com.aos.qareen, com.aos.qareen-deploy, com.agent.qareen-dev
     (bootout + delete plist + delete ~/.aos/launchers wrapper)
  2. Stray live-deploy.sh / vite watcher processes those agents spawned
  3. ~/.aos/services/qareen/ (venv ~1.1 GB, deploy scripts, plist backup)
  4. Dead tables in qareen.db — the Auto Tracker set (operator-approved data
     deletion) and the write-only ingest_* set nothing ever read
  5. ~/.claude/skills/companion-* symlinks (their targets left the framework)
  6. The `qareen` service entry in ~/.aos/config/state.yaml

Not reversible: the dropped tables and the venv are gone. The code lives on
in git history and ~/project/_archive/qareen-reference/.
"""

DESCRIPTION = "Decommission Qareen: LaunchAgents, venv, dead tables, skill links"

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

HOME = Path.home()
LA_DIR = HOME / "Library" / "LaunchAgents"
SERVICE_DIR = HOME / ".aos" / "services" / "qareen"
STATE_YAML = HOME / ".aos" / "config" / "state.yaml"
QAREEN_DB = HOME / ".aos" / "data" / "qareen.db"
SKILLS_DIR = HOME / ".claude" / "skills"

LABELS = {
    "com.aos.qareen": "AOS Qareen",
    "com.aos.qareen-deploy": "AOS Qareen Deploy",
    "com.agent.qareen-dev": "AOS Qareen Dev",
}

DEAD_TABLES = [
    # Auto Tracker (retired, operator-approved data deletion 2026-08-18)
    "shipment_events", "shipment_numbers", "order_shipments",
    "shipment_candidates", "shipments", "tracking_state",
    "detection_priors", "detection_eval", "domain_rules",
    # Write-only ingest landfill — nothing ever read these
    "ingest_activity", "ingest_conversations", "ingest_sessions",
]

COMPANION_SKILLS = [
    "companion-meeting", "companion-thinking",
    "companion-planning", "companion-email",
]


def _bootout(label: str) -> None:
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True, timeout=30,
    )


def _remove_launchagents() -> list[str]:
    removed = []
    for label, launcher_name in LABELS.items():
        _bootout(label)
        plist = LA_DIR / f"{label}.plist"
        if plist.exists():
            plist.unlink()
            removed.append(str(plist))
        launcher = HOME / ".aos" / "launchers" / launcher_name
        if launcher.exists():
            launcher.unlink()
            removed.append(str(launcher))
    return removed


def _kill_strays() -> None:
    """Deploy loops / vite watchers the agents left behind. Targeted by path
    so nothing outside qareen's tooling can match."""
    for pattern in (
        str(SERVICE_DIR / "live-deploy.sh"),
        "core/qareen/screen/node_modules/.bin/vite",
    ):
        subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=15)


def _drop_dead_tables() -> list[str]:
    if not QAREEN_DB.exists():
        return []
    dropped = []
    con = sqlite3.connect(str(QAREEN_DB), timeout=30)
    try:
        for table in DEAD_TABLES:
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row:
                con.execute(f"DROP TABLE {table}")
                dropped.append(table)
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    return dropped


def _remove_service_dir() -> bool:
    if SERVICE_DIR.exists():
        shutil.rmtree(SERVICE_DIR, ignore_errors=True)
        return True
    return False


def _remove_skill_links() -> list[str]:
    removed = []
    for name in COMPANION_SKILLS:
        link = SKILLS_DIR / name
        # islink() also covers now-dangling symlinks (target left the framework)
        if link.is_symlink() or link.exists():
            try:
                if link.is_dir() and not link.is_symlink():
                    shutil.rmtree(link)
                else:
                    link.unlink()
                removed.append(name)
            except OSError:
                pass
    return removed


def _clean_state_yaml() -> bool:
    if not STATE_YAML.exists():
        return False
    try:
        import yaml
    except ImportError:
        return False
    try:
        state = yaml.safe_load(STATE_YAML.read_text()) or {}
    except Exception:
        return False
    services = state.get("services") or {}
    if "qareen" not in services:
        return False
    del services["qareen"]
    STATE_YAML.write_text(yaml.dump(state, default_flow_style=False, sort_keys=False))
    return True


def check() -> bool:
    """True when nothing qareen-shaped remains on the instance."""
    if any((LA_DIR / f"{label}.plist").exists() for label in LABELS):
        return False
    if SERVICE_DIR.exists():
        return False
    if QAREEN_DB.exists():
        con = sqlite3.connect(str(QAREEN_DB), timeout=30)
        try:
            for table in DEAD_TABLES:
                if con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone():
                    return False
        finally:
            con.close()
    return True


def up() -> bool:
    removed = _remove_launchagents()
    _kill_strays()
    dropped = _drop_dead_tables()
    dir_gone = _remove_service_dir()
    skills = _remove_skill_links()
    state = _clean_state_yaml()
    print(f"  LaunchAgents/launchers removed: {len(removed)}")
    print(f"  Tables dropped: {', '.join(dropped) if dropped else 'none'}")
    print(f"  Service dir removed: {dir_gone}")
    print(f"  Skill links removed: {', '.join(skills) if skills else 'none'}")
    print(f"  state.yaml cleaned: {state}")
    return check()


def down() -> bool:
    # Not reversible: venv and dropped tables are gone. Reinstalling Qareen
    # means checking out a pre-107 tree — documented, not automated.
    return False
