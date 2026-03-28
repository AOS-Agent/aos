"""
Migration 004: Symlink AOS skills to ~/.claude/skills/.

AOS skills live in ~/aos/.claude/skills/ (shipped with the system).
They need to be accessible at ~/.claude/skills/ for Claude Code to find them.

For ongoing skill management, use `aos sync-skills` which handles tiered
installation (default vs developer) and deprecated skill removal.
This migration just ensures the initial bootstrap happened.

Strategy:
- Symlink each AOS skill directory (not individual files)
- User's existing skills are preserved (copied skills like onboard are OK)
- Deprecated skills (instagram, youtube) are excluded from checks
"""

DESCRIPTION = "Symlink system skills to ~/.claude/skills/"

import os
from pathlib import Path

AOS_DIR = Path.home() / "aos"
SOURCE_DIR = AOS_DIR / ".claude" / "skills"
TARGET_DIR = Path.home() / ".claude" / "skills"

# Skills that are deprecated and should NOT be checked/linked
DEPRECATED = {"instagram", "youtube"}

# Core skills that must be present (subset — full list managed by sync-skills)
CORE_SKILLS = [
    "recall", "work", "review", "step-by-step",
]


def check() -> bool:
    """Applied if core skills are accessible (symlinked or copied)."""
    for skill in CORE_SKILLS:
        target = TARGET_DIR / skill
        if not target.exists():
            return False
    return True


def up() -> bool:
    """Create symlinks for AOS skills. Delegates to sync-skills for full list."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    if not SOURCE_DIR.exists():
        print("       No skills source dir, skipping")
        return True

    for d in sorted(SOURCE_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name in DEPRECATED:
            continue

        skill = d.name
        source = SOURCE_DIR / skill
        target = TARGET_DIR / skill

        if target.exists() or target.is_symlink():
            if target.is_symlink() and target.resolve() == source.resolve():
                continue  # correct
            if not target.is_symlink():
                continue  # user-managed copy (e.g. onboard), leave it
            # Stale symlink — fix it
            os.remove(target)

        os.symlink(source, target)
        print(f"       Linked {skill}/")

    return True
