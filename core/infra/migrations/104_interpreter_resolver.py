"""
Migration 104: AOS services resolve their interpreter via aos-python.

Service templates hardcoded /opt/homebrew/bin/python3 — the dev machine's
interpreter. On machines without that exact path the service dies at exec
(observed 2026-08-15: faisal-mini's sentinel exit 126 after the launcher
rename regenerated its launcher around a python3 that didn't exist there).

The templates now exec core/bin/internal/aos-python, the per-machine resolver
that reads ~/.aos/config/python (uv-managed since migration 096) and falls
back sanely. This migration brings already-deployed instances in line:

1. Rewrite /opt/homebrew/bin/python3 → ~/aos/core/bin/internal/aos-python in
   deployed com.aos.* plists and their ~/.aos/launchers/ wrapper scripts.
2. Restart only services that are currently RUNNING (a PID exists). Services
   that are stopped, disabled, or ship-off (converse) are updated on disk and
   left alone — starting them is not this migration's business.

Non-AOS launchers (personal services wrapped by launcher_naming) are never
touched: their interpreter choice belongs to the operator.
"""

DESCRIPTION = "AOS service plists/launchers use the aos-python resolver, not a hardcoded interpreter"

import plistlib
import re
import subprocess
from pathlib import Path

HOME = Path.home()
HARDCODED = "/opt/homebrew/bin/python3"
RESOLVER = str(HOME / "aos" / "core" / "bin" / "internal" / "aos-python")
LAUNCHAGENTS = HOME / "Library" / "LaunchAgents"
LAUNCHERS = HOME / ".aos" / "launchers"


def _aos_plists():
    return sorted(LAUNCHAGENTS.glob("com.aos.*.plist"))


def _launcher_for(plist_path: Path):
    """The wrapper script a plist points at, if it points into ~/.aos/launchers."""
    try:
        pl = plistlib.loads(plist_path.read_bytes())
    except Exception:
        return None, None
    args = pl.get("ProgramArguments") or ([pl["Program"]] if pl.get("Program") else [])
    if args and args[0].startswith(str(LAUNCHERS) + "/"):
        return pl, Path(args[0])
    return pl, None


def _needs_fix(plist_path: Path) -> bool:
    pl, launcher = _launcher_for(plist_path)
    if pl is None:
        return False
    args = pl.get("ProgramArguments") or []
    if HARDCODED in args:
        return True
    if launcher and launcher.exists() and HARDCODED in launcher.read_text():
        return True
    return False


def _is_running(label: str) -> bool:
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == label:
            return parts[0] != "-"  # a PID means running
    return False


def check() -> bool:
    """Applied when no deployed com.aos.* plist or its launcher hardcodes the interpreter."""
    return not any(_needs_fix(p) for p in _aos_plists())


def up() -> bool:
    if not Path(RESOLVER).exists():
        print(f"  ERROR: resolver missing at {RESOLVER} — aborting without changes")
        return False

    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    fixed = []
    for plist_path in _aos_plists():
        if not _needs_fix(plist_path):
            continue
        label = plist_path.stem
        pl, launcher = _launcher_for(plist_path)

        # 1. Plist args (direct-exec plists, pre-launcher layouts)
        args = pl.get("ProgramArguments") or []
        if HARDCODED in args:
            pl["ProgramArguments"] = [RESOLVER if a == HARDCODED else a for a in args]
            plist_path.write_bytes(plistlib.dumps(pl))

        # 2. Launcher wrapper script
        if launcher and launcher.exists() and HARDCODED in launcher.read_text():
            launcher.write_text(
                re.sub(re.escape(HARDCODED), RESOLVER, launcher.read_text())
            )

        # 3. Restart ONLY if it is currently running — never start stopped,
        #    disabled, or ships-off services.
        if _is_running(label):
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                           capture_output=True)
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                           capture_output=True)
            fixed.append(f"{label} (restarted)")
        else:
            fixed.append(f"{label} (on disk only — not running)")

    if fixed:
        print("  interpreter → aos-python resolver: " + ", ".join(fixed))
    else:
        print("  nothing referenced the hardcoded interpreter")
    return True
