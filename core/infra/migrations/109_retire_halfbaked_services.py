"""
Migration 109: retire the half-baked services sweep (operator-approved 2026-08-18).

Companion decision follows Qareen's (migration 108): the framework removes the
code in the same commit; this migration cleans each machine.

  - companion  — the pre-Qareen meeting service. Never had a LaunchAgent on
    current machines; superseded twice (Qareen, then aos-app). Code dir
    removed; the ~1.1 GB instance venv goes here.
  - listen     — retired since April (aos#180); manifest lives on as a
    tombstone in config/services.d/listen.yaml. Code dir removed; the
    instance dir deletion the old manifest deferred to "a separate operator
    decision" was approved 2026-08-18.
  - n8n        — workflow-automation experiment that never held a workflow.
    Venv + stale state.yaml entry + plist template removed.
  - slack-watch — superseded by sana-watch (aos#198, slack-lite venv). Its
    LaunchAgent, launcher, and instance dir are removed. sana-watch and
    slack-lite are NOT touched.
"""

DESCRIPTION = "Retire companion, listen, n8n, slack-watch instance remnants"

import os
import shutil
import subprocess
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _home() -> Path:
    return Path.home()


def _la_dir() -> Path:
    return _home() / "Library" / "LaunchAgents"


def _services() -> Path:
    return _home() / ".aos" / "services"


def _state_yaml() -> Path:
    return _home() / ".aos" / "config" / "state.yaml"

# label -> launcher display name (None = no launcher wrapper ever existed)
LABELS = {
    "com.aos.slack-watch": "AOS Slack Watch",
    "com.aos.n8n": None,
    "com.aos.companion": None,
    "com.aos.listen": None,
}

VENV_DIRS = ["companion", "listen", "n8n", "slack-watch"]
STATE_SERVICES = ["n8n", "listen", "companion"]


def _bootout(label: str) -> None:
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
        capture_output=True, timeout=30,
    )


def _remove_launchagents() -> list[str]:
    removed = []
    for label, launcher_name in LABELS.items():
        _bootout(label)
        plist = _la_dir() / f"{label}.plist"
        if plist.exists():
            plist.unlink()
            removed.append(label)
        if launcher_name:
            launcher = _home() / ".aos" / "launchers" / launcher_name
            if launcher.exists():
                launcher.unlink()
    return removed


def _remove_service_dirs() -> list[str]:
    removed = []
    for name in VENV_DIRS:
        d = _services() / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(name)
    return removed


def _clean_state_yaml() -> bool:
    if not _state_yaml().exists():
        return False
    try:
        import yaml
    except ImportError:
        return False
    try:
        state = yaml.safe_load(_state_yaml().read_text()) or {}
    except Exception:
        return False
    services = state.get("services") or {}
    changed = False
    for name in STATE_SERVICES:
        if name in services:
            del services[name]
            changed = True
    if changed:
        _state_yaml().write_text(
            yaml.dump(state, default_flow_style=False, sort_keys=False)
        )
    return changed


def check() -> bool:
    if any((_la_dir() / f"{label}.plist").exists() for label in LABELS):
        return False
    if any((_services() / name).exists() for name in VENV_DIRS):
        return False
    return True


def up() -> bool:
    agents = _remove_launchagents()
    dirs = _remove_service_dirs()
    state = _clean_state_yaml()
    print(f"  LaunchAgents removed: {', '.join(agents) if agents else 'none'}")
    print(f"  Service dirs removed: {', '.join(dirs) if dirs else 'none'}")
    print(f"  state.yaml cleaned: {state}")
    return check()


def down() -> bool:
    return False  # venvs are gone; code lives in git history
