"""
Invariant: CLAUDE.md dynamic content reflects actual system state.

Static content (rules, architecture, agent roles) rarely changes and is
managed by claude_md.py's versioned sections.

Dynamic content (services list, QMD stats) changes as the system evolves:
new services get deployed, files get indexed, collections change.
This check syncs those values by reading actual system state.

What it syncs:
1. Services list in ~/CLAUDE.md — matches actual running LaunchAgents
2. QMD stats in ~/.claude/CLAUDE.md — matches `qmd status` output

Runs on every `aos update` cycle. Drift is auto-fixed.
"""

import json
import re
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


def _get_running_services() -> list[str]:
    """Get AOS services from LaunchAgents directory.

    Reads plist files rather than launchctl to work even if services
    are temporarily stopped. Format: "name (:port)" when port is configured.
    """
    la_dir = Path.home() / "Library" / "LaunchAgents"
    services = []

    for plist in sorted(la_dir.glob("com.aos.*.plist")):
        name = plist.stem.replace("com.aos.", "")

        # Skip cron-like agents (scheduler, reindex, etc.)
        skip = {"scheduler", "qmd-reindex", "healthsync-deploy", "claude-remote", "memory"}
        if name in skip:
            continue

        # Try to find port from plist
        port = None
        try:
            text = plist.read_text()
            # Look for port in EnvironmentVariables or ProgramArguments
            port_patterns = [
                r'<key>\w*PORT\w*</key>\s*<string>(\d+)</string>',
                r'--port[= ](\d+)',
            ]
            for pattern in port_patterns:
                m = re.search(pattern, text)
                if m:
                    port = m.group(1)
                    break

            # Known port mappings
            known_ports = {
                "dashboard": "4096",
                "listen": "7600",
                "whatsmeow": "7601",
                "eventd": "4097",
                "transcriber": "7602",
            }
            if not port and name in known_ports:
                port = known_ports[name]

        except Exception:
            pass

        if port:
            services.append(f"{name} (:{port})")
        else:
            services.append(name)

    return services


def _get_qmd_stats() -> dict | None:
    """Get QMD index stats."""
    try:
        qmd = Path.home() / ".bun" / "bin" / "qmd"
        if not qmd.exists():
            return None
        result = subprocess.run(
            [str(qmd), "status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None

        # Parse total files and collection count
        output = result.stdout
        files_match = re.search(r'Total:\s+(\d+) files', output)
        collections = re.findall(r'^\s+(\S+) \(qmd://', output, re.MULTILINE)

        return {
            "files": int(files_match.group(1)) if files_match else 0,
            "collections": len(collections),
        }
    except Exception:
        return None


class ContextFreshnessCheck(ReconcileCheck):
    name = "context_freshness"
    description = "CLAUDE.md dynamic content matches system state"

    ROOT_MD = Path.home() / "CLAUDE.md"
    GLOBAL_MD = Path.home() / ".claude" / "CLAUDE.md"

    def check(self) -> bool:
        """Check if dynamic content is current."""
        # Check services list in ~/CLAUDE.md
        if not self._services_current():
            return False
        # Check QMD stats in ~/.claude/CLAUDE.md
        if not self._qmd_current():
            return False
        return True

    def _services_current(self) -> bool:
        """Check if services list matches running LaunchAgents."""
        if not self.ROOT_MD.exists():
            return True  # Can't check, skip

        text = self.ROOT_MD.read_text()
        services = _get_running_services()

        # Check if all services are mentioned
        for svc in services:
            # Extract just the name (before the port)
            name = svc.split(" ")[0]
            if name not in text:
                return False
        return True

    def _qmd_current(self) -> bool:
        """Check if QMD stats are roughly current."""
        if not self.GLOBAL_MD.exists():
            return True

        stats = _get_qmd_stats()
        if not stats:
            return True  # Can't check, skip

        text = self.GLOBAL_MD.read_text()

        # Check file count — allow 10% drift before flagging
        files_match = re.search(r'(\d+)\s+files', text)
        if files_match:
            stated = int(files_match.group(1))
            actual = stats["files"]
            if abs(stated - actual) / max(actual, 1) > 0.10:
                return False

        # Check collection count
        coll_match = re.search(r'(\d+)\s+collections', text)
        if coll_match:
            stated = int(coll_match.group(1))
            if stated != stats["collections"]:
                return False

        return True

    def fix(self) -> CheckResult:
        """Update dynamic content in CLAUDE.md files."""
        actions = []

        # Fix services list in ~/CLAUDE.md
        if self.ROOT_MD.exists():
            services = _get_running_services()
            if services:
                services_str = ", ".join(services)
                text = self.ROOT_MD.read_text()
                # Match the services line pattern
                new_text = re.sub(
                    r'\| Services \|[^|]+\|',
                    f'| Services | {services_str} |',
                    text,
                )
                if new_text != text:
                    self.ROOT_MD.write_text(new_text)
                    actions.append(f"services list → {len(services)} services")

        # Fix QMD stats in ~/.claude/CLAUDE.md
        if self.GLOBAL_MD.exists():
            stats = _get_qmd_stats()
            if stats:
                text = self.GLOBAL_MD.read_text()
                new_text = text

                # Update "N collections, M files" pattern
                new_text = re.sub(
                    r'\d+\s+collections,\s+\d+\s+files',
                    f'{stats["collections"]} collections, {stats["files"]} files',
                    new_text,
                )
                # Also update "N files, M collections" (alternate ordering)
                new_text = re.sub(
                    r'\d+\s+files,\s+\d+\s+collections',
                    f'{stats["files"]} files, {stats["collections"]} collections',
                    new_text,
                )
                # Update inline references like "(787 files, 9 collections)"
                new_text = re.sub(
                    r'\((\d+)\s+files,\s+(\d+)\s+collections\)',
                    f'({stats["files"]} files, {stats["collections"]} collections)',
                    new_text,
                )
                # Update header pattern "N collections, M files"
                new_text = re.sub(
                    r'\((\d+)\s+collections,\s+(\d+)\s+files\)',
                    f'({stats["collections"]} collections, {stats["files"]} files)',
                    new_text,
                )

                if new_text != text:
                    self.GLOBAL_MD.write_text(new_text)
                    actions.append(f"QMD stats → {stats['files']} files, {stats['collections']} collections")

        if actions:
            return CheckResult(
                self.name, Status.FIXED,
                f"Updated context: {'; '.join(actions)}"
            )
        return CheckResult(self.name, Status.OK, "ok")
