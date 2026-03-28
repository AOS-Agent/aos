"""
Migration 003: Symlink system agents from core/agents/ to ~/.claude/agents/.

Instead of copying agent files, we symlink them. This means:
- `git pull` on aos immediately updates the active agents
- User agents (not from AOS) are left untouched
- If a symlink already points to the right place, it's skipped

For users migrating from custom setups:
- Their existing agents are preserved
- AOS agents are added alongside (no conflicts — different filenames)
"""

DESCRIPTION = "Symlink system agents to ~/.claude/agents/"

import os
from pathlib import Path

AOS_DIR = Path.home() / "aos"
SOURCE_DIR = AOS_DIR / "core" / "agents"
TARGET_DIR = Path.home() / ".claude" / "agents"

SYSTEM_AGENTS = ["chief.md", "steward.md", "advisor.md"]


def check() -> bool:
    """Applied if all system agents are correctly symlinked."""
    for agent in SYSTEM_AGENTS:
        target = TARGET_DIR / agent
        source = SOURCE_DIR / agent
        if not source.exists():
            continue  # Agent not in source, skip
        if not target.exists():
            return False
        if not target.is_symlink():
            return False
        if target.resolve() != source.resolve():
            return False
    return True


def up() -> bool:
    """Create symlinks for system agents."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    for agent in SYSTEM_AGENTS:
        source = SOURCE_DIR / agent
        target = TARGET_DIR / agent

        if not source.exists():
            print(f"       Skipped {agent} (not in core/agents/)")
            continue

        if target.exists() or target.is_symlink():
            if target.is_symlink() and target.resolve() == source.resolve():
                print(f"       {agent} already linked ✓")
                continue

            # Existing file — back it up, then replace
            backup = TARGET_DIR / f"{agent}.pre-aos"
            if not backup.exists():
                os.rename(target, backup)
                print(f"       Backed up existing {agent} → {agent}.pre-aos")
            else:
                os.remove(target)

        os.symlink(source, target)
        print(f"       Linked {agent} → {source}")

    return True
