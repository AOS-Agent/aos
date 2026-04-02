"""
Invariant: No orphaned bin scripts or stale module references in the framework.

Detects:
  - Bin scripts not referenced by crons.yaml, the aos CLI, install.sh, or any other script
  - Bridge pyproject.toml listing modules that don't exist as files
  - LaunchAgent plists pointing at scripts that don't exist

This check reports findings (NOTIFY) but does not auto-delete.
Dead code removal requires human judgment.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status


class DeadCodeCheck(ReconcileCheck):
    name = "dead_code"
    description = "Detect orphaned bin scripts and stale module references"

    AOS_DIR = Path.home() / "aos"
    BIN_DIR = AOS_DIR / "core" / "bin"
    BRIDGE_DIR = AOS_DIR / "core" / "services" / "bridge"

    # Scripts that are entry points (not called by other scripts)
    # but are valid — e.g., run by crons, LaunchAgents, or the operator directly
    KNOWN_ENTRY_POINTS = {
        "aos", "scheduler", "watchdog", "check-update",
        "agent-secret", "activate-agent", "cld",
    }

    def check(self) -> bool:
        """True if no orphaned items detected."""
        orphans = self._find_orphaned_bin_scripts()
        stale_modules = self._find_stale_bridge_modules()
        return len(orphans) == 0 and len(stale_modules) == 0

    def fix(self) -> CheckResult:
        """Report findings — don't auto-delete."""
        orphans = self._find_orphaned_bin_scripts()
        stale_modules = self._find_stale_bridge_modules()

        issues = []
        if orphans:
            issues.append(f"{len(orphans)} orphaned bin scripts: {', '.join(sorted(orphans)[:5])}")
        if stale_modules:
            issues.append(f"{len(stale_modules)} stale bridge modules in pyproject.toml: {', '.join(sorted(stale_modules))}")

        if issues:
            return CheckResult(
                self.name, Status.NOTIFY,
                "Dead code detected — review and remove manually",
                detail="; ".join(issues),
                notify=False,  # Log only, don't Telegram-spam
            )
        return CheckResult(self.name, Status.OK, "No dead code detected")

    def _find_orphaned_bin_scripts(self) -> set[str]:
        """Find bin scripts that nothing references."""
        if not self.BIN_DIR.exists():
            return set()

        # Collect all bin script names
        all_scripts = set()
        for f in self.BIN_DIR.iterdir():
            if f.is_file() and not f.name.startswith(".") and f.name != "__pycache__":
                all_scripts.add(f.name)

        # Build reference set: scripts mentioned anywhere in the codebase
        referenced = set(self.KNOWN_ENTRY_POINTS)

        # Check crons.yaml
        crons_file = self.AOS_DIR / "config" / "crons.yaml"
        if crons_file.exists():
            crons_text = crons_file.read_text()
            for script in all_scripts:
                if script in crons_text:
                    referenced.add(script)

        # Check the aos CLI
        aos_cli = self.BIN_DIR / "aos"
        if aos_cli.exists():
            aos_text = aos_cli.read_text()
            for script in all_scripts:
                if script in aos_text:
                    referenced.add(script)

        # Check install.sh
        install_sh = self.AOS_DIR / "install.sh"
        if install_sh.exists():
            install_text = install_sh.read_text()
            for script in all_scripts:
                if script in install_text:
                    referenced.add(script)

        # Check LaunchAgent templates
        la_dir = self.AOS_DIR / "config" / "launchagents"
        if la_dir.exists():
            for plist in la_dir.iterdir():
                try:
                    text = plist.read_text()
                    for script in all_scripts:
                        if script in text:
                            referenced.add(script)
                except (UnicodeDecodeError, PermissionError):
                    pass

        # Check loaded LaunchAgent plists
        la_user_dir = Path.home() / "Library" / "LaunchAgents"
        if la_user_dir.exists():
            for plist in la_user_dir.glob("com.aos.*.plist"):
                try:
                    text = plist.read_text()
                    for script in all_scripts:
                        if script in text:
                            referenced.add(script)
                except (UnicodeDecodeError, PermissionError):
                    pass

        # Cross-reference: check if any bin script references another
        for f in self.BIN_DIR.iterdir():
            if f.is_file() and f.name in referenced:
                try:
                    text = f.read_text()
                    for script in all_scripts:
                        if script in text and script != f.name:
                            referenced.add(script)
                except (UnicodeDecodeError, PermissionError):
                    pass

        # Also check skills for bin script references
        skills_dir = self.AOS_DIR / ".claude" / "skills"
        if skills_dir.exists():
            for skill_file in skills_dir.rglob("SKILL.md"):
                try:
                    text = skill_file.read_text()
                    for script in all_scripts:
                        if script in text:
                            referenced.add(script)
                except (UnicodeDecodeError, PermissionError):
                    pass

        return all_scripts - referenced

    def _find_stale_bridge_modules(self) -> set[str]:
        """Find modules listed in bridge pyproject.toml that don't exist."""
        pyproject = self.BRIDGE_DIR / "pyproject.toml"
        if not pyproject.exists():
            return set()

        text = pyproject.read_text()
        match = re.search(r'py-modules\s*=\s*\[([^\]]+)\]', text)
        if not match:
            return set()

        modules = re.findall(r'"(\w+)"', match.group(1))
        stale = set()
        for mod in modules:
            mod_file = self.BRIDGE_DIR / f"{mod}.py"
            if not mod_file.exists():
                stale.add(mod)

        return stale
