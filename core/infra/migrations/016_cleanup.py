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

    # Dead core/lib/ directory.
    # NOTE: in the post-infra-reorg tree (migration-era >= wave 1, 2026-07),
    # core/lib is a live SYMLINK -> core/infra/lib. That is the CURRENT
    # structure, not the v1-era dead directory this migration targets —
    # a symlink here means there is nothing to clean up. Only a real,
    # non-symlink directory is the v1 artifact. (Found by clean-box VM:
    # shutil.rmtree refuses symlinks, so the old check bricked every
    # fresh install at migration 16.)
    lib_dir = AOS_DIR / "core" / "lib"
    if lib_dir.is_dir() and not lib_dir.is_symlink():
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

    # 2. Remove dead core/lib/ directory — but NEVER through a symlink:
    #    post-reorg core/lib is a live symlink to core/infra/lib (current
    #    structure, must be left alone). Only a real directory is the
    #    v1-era artifact this step exists to remove.
    lib_dir = AOS_DIR / "core" / "lib"
    if lib_dir.is_dir() and not lib_dir.is_symlink():
        shutil.rmtree(str(lib_dir))
        print("       Removed core/lib/ (unused config.py + events.py)")
    elif lib_dir.is_symlink():
        print("       core/lib is a live symlink (current structure) — left alone")

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
