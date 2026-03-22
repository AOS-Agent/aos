"""
Migration 016: Post-restructure cleanup.

Cleans up artifacts from the v1→v2 migration era:
1. Removes stale com.agent.* LaunchAgent plists (replaced by com.aos.*)
2. Removes core/lib/ directory (config.py and events.py were never used)
3. Fixes ~/CLAUDE.md reference to non-existent config/defaults/

These are safe to remove — nothing imports from core.lib, the old plists
are already unloaded, and config/defaults/ was never created.
"""

DESCRIPTION = "Post-restructure cleanup — stale plists, dead code, docs"

import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
AOS_DIR = HOME / "aos"
LA_DIR = HOME / "Library" / "LaunchAgents"

# Old-convention plists that should be removed
STALE_PLISTS = [
    "com.agent.bridge.plist",
    "com.agent.dashboard.plist",
    "com.agent.listen.plist",
    "com.agent.chrome.plist",
    "com.agent.keychain-unlock.plist",
    "com.agent.phoenix.plist",
    "com.agent.whatsmeow.plist",
    "com.agent.claude-remote.plist",
]


def _find_issues() -> list[str]:
    """Find things that need cleaning."""
    issues = []

    # Stale plists
    for name in STALE_PLISTS:
        if (LA_DIR / name).exists():
            issues.append(f"Stale plist: {name}")

    # Dead core/lib/ directory
    lib_dir = AOS_DIR / "core" / "lib"
    if lib_dir.exists():
        issues.append("Dead directory: core/lib/")

    # CLAUDE.md references config/defaults/
    root_claude = HOME / "CLAUDE.md"
    if root_claude.exists():
        content = root_claude.read_text()
        if "config/defaults/" in content:
            issues.append("~/CLAUDE.md references non-existent config/defaults/")

    return issues


def check() -> bool:
    """Applied if no cleanup needed."""
    return len(_find_issues()) == 0


def up() -> bool:
    """Clean up stale artifacts."""

    # 1. Remove stale com.agent.* plists
    for name in STALE_PLISTS:
        plist = LA_DIR / name
        if plist.exists():
            # Unload first (may already be unloaded)
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True,
            )
            plist.unlink()
            print(f"       Removed {name}")

    # 2. Remove dead core/lib/ directory
    lib_dir = AOS_DIR / "core" / "lib"
    if lib_dir.exists():
        shutil.rmtree(str(lib_dir))
        print("       Removed core/lib/ (unused config.py + events.py)")

    # 3. Fix ~/CLAUDE.md — replace config/defaults/ reference
    root_claude = HOME / "CLAUDE.md"
    if root_claude.exists():
        content = root_claude.read_text()
        if "config/defaults/" in content:
            content = content.replace(
                "│   ├── config/defaults/ ← Shipped defaults",
                "│   ├── config/          ← System configuration",
            )
            root_claude.write_text(content)
            print("       Fixed ~/CLAUDE.md config reference")

    return True
