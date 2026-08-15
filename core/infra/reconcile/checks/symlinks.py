"""
Invariant: System agents, skills, and rules are symlinked from the framework.

Agents: ~/.claude/agents/chief.md → ~/aos/core/agents/chief.md
Skills: ~/.claude/skills/recall/  → ~/aos/core/skills/recall/
Rules:  ~/.claude/rules/work-awareness.md → ~/aos/.claude/rules/work-awareness.md

All framework items are auto-discovered (no hardcoded lists).
User-created items (not in framework source) are never touched.
Deprecated items (instagram, youtube) are removed if found.

Backups NEVER live in the live directories. Claude Code loads every .md/dir
under ~/.claude/{agents,skills,rules} into session context, so a
`foo.pre-reconcile` backup left in place becomes a live duplicate agent/skill
that every session pays for (this actually happened: ios-dev-loop.pre-reconcile
loaded as a second skill for months). Backups go to
~/.aos/backups/pre-reconcile/<kind>/ and any strays found in live dirs are
swept there on fix.
"""

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

DEPRECATED_SKILLS = {"instagram", "youtube"}

BACKUP_ROOT = Path.home() / ".aos" / "backups" / "pre-reconcile"


def _backup_dest(kind: str, name: str) -> Path:
    """Timestamped backup path outside the live directory."""
    dest_dir = BACKUP_ROOT / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return dest_dir / f"{stamp}-{name}"


def _backup_out_of_live(path: Path, kind: str) -> None:
    """Move a file/dir out of the live directory into the backup area."""
    dest = _backup_dest(kind, path.name)
    shutil.move(str(path), str(dest))


def _stale_backups(live_dir: Path):
    """Any *.pre-reconcile entries lingering in a live directory."""
    if not live_dir.is_dir():
        return []
    return sorted(live_dir.glob("*.pre-reconcile"))


def _sweep_stale_backups(live_dir: Path, kind: str):
    """Move lingering *.pre-reconcile entries out of the live directory."""
    swept = []
    for stray in _stale_backups(live_dir):
        _backup_out_of_live(stray, kind)
        swept.append(stray.name)
    return swept


class AgentSymlinkCheck(ReconcileCheck):
    name = "agent_symlinks"
    description = "System agents symlinked to ~/aos/core/agents/"

    AGENTS_DIR = Path.home() / ".claude" / "agents"
    SOURCE_DIR = Path.home() / "aos" / "core" / "agents"

    def _system_agents(self):
        """Auto-discover agent files from framework source."""
        if not self.SOURCE_DIR.is_dir():
            return []
        return [f.name for f in self.SOURCE_DIR.glob("*.md") if f.is_file()]

    def check(self) -> bool:
        for name in self._system_agents():
            link = self.AGENTS_DIR / name
            source = self.SOURCE_DIR / name
            if not link.exists():
                return False
            if not link.is_symlink():
                return False
            if link.resolve() != source.resolve():
                return False
        if _stale_backups(self.AGENTS_DIR):
            return False  # Backups in the live dir load as duplicate agents
        return True

    def fix(self) -> CheckResult:
        self.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        fixed = []

        for name in self._system_agents():
            link = self.AGENTS_DIR / name
            source = self.SOURCE_DIR / name

            if link.is_symlink() and link.resolve() == source.resolve():
                continue

            # Back up existing file OUTSIDE the live dir, then link
            if link.exists() and not link.is_symlink():
                _backup_out_of_live(link, "agents")
            elif link.is_symlink():
                link.unlink()

            os.symlink(source, link)
            fixed.append(name)

        swept = _sweep_stale_backups(self.AGENTS_DIR, "agents")
        if swept:
            fixed.append(f"swept {len(swept)} stale backup(s)")

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Re-linked agents: {', '.join(fixed)}"
            )
        return CheckResult(self.name, Status.OK, "ok")


class SkillSymlinkCheck(ReconcileCheck):
    name = "skill_symlinks"
    description = "Framework skills symlinked to ~/aos/core/skills/"

    SKILLS_DIR = Path.home() / ".claude" / "skills"
    SOURCE_DIR = Path.home() / "aos" / "core" / "skills"

    def _framework_skills(self):
        """Auto-discover skill directories from framework source."""
        if not self.SOURCE_DIR.is_dir():
            return []
        return [
            d.name for d in self.SOURCE_DIR.iterdir()
            if d.is_dir() and d.name not in DEPRECATED_SKILLS
        ]

    def check(self) -> bool:
        for name in self._framework_skills():
            source = self.SOURCE_DIR / name
            link = self.SKILLS_DIR / name
            if not link.exists():
                return False
            if not link.is_symlink():
                return False  # Copy instead of symlink — needs fixing
            if link.resolve() != source.resolve():
                return False
        # Check for deprecated skills that should be removed
        for name in DEPRECATED_SKILLS:
            if (self.SKILLS_DIR / name).exists():
                return False
        if _stale_backups(self.SKILLS_DIR):
            return False  # Backups in the live dir load as duplicate skills
        return True

    def fix(self) -> CheckResult:
        self.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        fixed = []

        for name in self._framework_skills():
            source = self.SOURCE_DIR / name
            link = self.SKILLS_DIR / name

            if link.is_symlink() and link.resolve() == source.resolve():
                continue

            # Replace copy with symlink (back up OUTSIDE the live dir first)
            if link.exists() and not link.is_symlink():
                _backup_out_of_live(link, "skills")
                fixed.append(f"{name} (was copy)")
            elif link.is_symlink():
                link.unlink()
                fixed.append(f"{name} (stale link)")
            else:
                fixed.append(name)

            os.symlink(str(source) + "/", link)

        # Remove deprecated skills
        for name in DEPRECATED_SKILLS:
            dep = self.SKILLS_DIR / name
            if dep.is_symlink():
                dep.unlink()
                fixed.append(f"{name} (deprecated, removed)")
            elif dep.is_dir():
                shutil.rmtree(dep)
                fixed.append(f"{name} (deprecated, removed)")

        swept = _sweep_stale_backups(self.SKILLS_DIR, "skills")
        if swept:
            fixed.append(f"swept {len(swept)} stale backup(s)")

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Re-linked {len(fixed)} skills: {', '.join(fixed[:5])}{'...' if len(fixed) > 5 else ''}"
            )
        return CheckResult(self.name, Status.OK, "ok")


class RuleSymlinkCheck(ReconcileCheck):
    name = "rule_symlinks"
    description = "Framework rules symlinked to ~/aos/.claude/rules/"

    RULES_DIR = Path.home() / ".claude" / "rules"
    SOURCE_DIR = Path.home() / "aos" / ".claude" / "rules"

    def _framework_rules(self):
        """Auto-discover rule files from framework source."""
        if not self.SOURCE_DIR.is_dir():
            return []
        return [f.name for f in self.SOURCE_DIR.glob("*.md") if f.is_file()]

    def check(self) -> bool:
        if not self.RULES_DIR.is_dir():
            return False
        for name in self._framework_rules():
            source = self.SOURCE_DIR / name
            link = self.RULES_DIR / name
            if not link.exists():
                return False
            if not link.is_symlink():
                return False
            if link.resolve() != source.resolve():
                return False
        if _stale_backups(self.RULES_DIR):
            return False  # Backups in the live dir load into every session
        return True

    def fix(self) -> CheckResult:
        self.RULES_DIR.mkdir(parents=True, exist_ok=True)
        fixed = []

        for name in self._framework_rules():
            source = self.SOURCE_DIR / name
            link = self.RULES_DIR / name

            if link.is_symlink() and link.resolve() == source.resolve():
                continue

            if link.exists() and not link.is_symlink():
                _backup_out_of_live(link, "rules")
            elif link.is_symlink():
                link.unlink()

            os.symlink(source, link)
            fixed.append(name)

        swept = _sweep_stale_backups(self.RULES_DIR, "rules")
        if swept:
            fixed.append(f"swept {len(swept)} stale backup(s)")

        if fixed:
            return CheckResult(
                self.name, Status.FIXED,
                f"Re-linked rules: {', '.join(fixed)}"
            )
        return CheckResult(self.name, Status.OK, "ok")
