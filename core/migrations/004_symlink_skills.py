"""
Migration 004: Symlink AOS skills to ~/.claude/skills/.

AOS skills live in ~/aos/.claude/skills/ (shipped with the system).
They need to be accessible at ~/.claude/skills/ for Claude Code to find them.

Strategy:
- Symlink each AOS skill directory (not individual files)
- User's existing skills are preserved
- Name collisions: back up user's version, link AOS version
"""

DESCRIPTION = "Symlink system skills to ~/.claude/skills/"

import os
from pathlib import Path

AOS_DIR = Path.home() / "aos"
SOURCE_DIR = AOS_DIR / ".claude" / "skills"
TARGET_DIR = Path.home() / ".claude" / "skills"


def _aos_skills() -> list[str]:
    """Get list of AOS skill directories."""
    if not SOURCE_DIR.exists():
        return []
    return [d.name for d in SOURCE_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]


def check() -> bool:
    """Applied if all AOS skills are correctly symlinked."""
    skills = _aos_skills()
    if not skills:
        return True  # Nothing to link
    for skill in skills:
        target = TARGET_DIR / skill
        source = SOURCE_DIR / skill
        if not target.exists():
            return False
        if not target.is_symlink():
            return False
        if target.resolve() != source.resolve():
            return False
    return True


def up() -> bool:
    """Create symlinks for AOS skills."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    for skill in _aos_skills():
        source = SOURCE_DIR / skill
        target = TARGET_DIR / skill

        if target.exists() or target.is_symlink():
            if target.is_symlink() and target.resolve() == source.resolve():
                print(f"       {skill}/ already linked ✓")
                continue

            backup = TARGET_DIR / f"{skill}.pre-aos"
            if not backup.exists():
                os.rename(target, backup)
                print(f"       Backed up existing {skill}/ → {skill}.pre-aos")
            else:
                # Already backed up, just remove the current
                import shutil
                if target.is_symlink():
                    os.remove(target)
                else:
                    shutil.rmtree(target)

        os.symlink(source, target)
        print(f"       Linked {skill}/ → {source}")

    return True
