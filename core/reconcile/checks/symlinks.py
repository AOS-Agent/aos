"""
Invariant: System agents and skills are symlinked from the framework.

Agents: ~/.claude/agents/chief.md → ~/aos/core/agents/chief.md
Skills: ~/.claude/skills/recall/ → ~/aos/.claude/skills/recall/

User-created agents/skills (not symlinks) are never touched.
"""

import os
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class AgentSymlinkCheck(ReconcileCheck):
    name = "agent_symlinks"
    description = "System agents symlinked to ~/aos/core/agents/"

    AGENTS_DIR = Path.home() / ".claude" / "agents"
    SOURCE_DIR = Path.home() / "aos" / "core" / "agents"
    SYSTEM_AGENTS = ["chief.md", "steward.md", "advisor.md"]

    def check(self) -> bool:
        for name in self.SYSTEM_AGENTS:
            link = self.AGENTS_DIR / name
            source = self.SOURCE_DIR / name
            if not source.exists():
                continue
            if not link.exists():
                return False
            if not link.is_symlink():
                return False
            if link.resolve() != source.resolve():
                return False
        return True

    def fix(self) -> CheckResult:
        self.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        fixed = []

        for name in self.SYSTEM_AGENTS:
            link = self.AGENTS_DIR / name
            source = self.SOURCE_DIR / name

            if not source.exists():
                continue

            if link.is_symlink() and link.resolve() == source.resolve():
                continue

            # Back up existing file if it's not a symlink
            if link.exists() and not link.is_symlink():
                backup = self.AGENTS_DIR / f"{name}.pre-reconcile"
                if not backup.exists():
                    link.rename(backup)
                else:
                    link.unlink()
            elif link.is_symlink():
                link.unlink()

            os.symlink(source, link)
            fixed.append(name)

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Re-linked agents: {', '.join(fixed)}"
            )
        return CheckResult(self.name, Status.OK, "ok")


class SkillSymlinkCheck(ReconcileCheck):
    name = "skill_symlinks"
    description = "Core skills symlinked to ~/aos/.claude/skills/"

    SKILLS_DIR = Path.home() / ".claude" / "skills"
    SOURCE_DIR = Path.home() / "aos" / ".claude" / "skills"

    # Core skills that must always be symlinked.
    # User-created skills (copies) are left alone.
    CORE_SKILLS = [
        "recall", "work", "review", "step-by-step", "obsidian-cli",
        "extract", "telegram-admin", "bridge-ops", "marketing",
        "diagram", "session-analysis", "frontend-design", "architect",
        "skill-creator", "skill-scanner", "ramble",
    ]

    def check(self) -> bool:
        for name in self.CORE_SKILLS:
            source = self.SOURCE_DIR / name
            link = self.SKILLS_DIR / name
            if not source.exists():
                continue
            if not link.exists():
                return False
            if not link.is_symlink():
                continue  # User copy — acceptable
            if link.resolve() != source.resolve():
                return False
        return True

    def fix(self) -> CheckResult:
        self.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        fixed = []

        for name in self.CORE_SKILLS:
            source = self.SOURCE_DIR / name
            link = self.SKILLS_DIR / name

            if not source.exists():
                continue

            # User copy (real directory, not symlink) — leave it alone
            if link.exists() and not link.is_symlink():
                continue

            if link.is_symlink() and link.resolve() == source.resolve():
                continue

            # Stale or missing symlink
            if link.is_symlink():
                link.unlink()

            os.symlink(source, link)
            fixed.append(name)

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Re-linked {len(fixed)} skills: {', '.join(fixed[:5])}{'...' if len(fixed) > 5 else ''}"
            )
        return CheckResult(self.name, Status.OK, "ok")
