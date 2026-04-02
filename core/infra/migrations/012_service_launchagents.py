"""
Migration 012: Install LaunchAgent plists for all AOS services.

Templates live in ~/aos/config/launchagents/*.template
They use __HOME__ as a placeholder, substituted at install time.

Services: bridge, dashboard, listen, memory, scheduler.
Scheduler already has a non-template plist — this migration handles
the .template files only.

Also migrates old com.agent.* naming to com.aos.* naming.
Old plists are unloaded but not deleted (user may have customized).
"""

DESCRIPTION = "Install service LaunchAgents from templates"

import subprocess
from pathlib import Path

HOME = Path.home()
TEMPLATES_DIR = HOME / "aos" / "config" / "launchagents"
LA_DIR = HOME / "Library" / "LaunchAgents"

# Old naming convention → new
OLD_PREFIX = "com.agent."
NEW_PREFIX = "com.aos."


def _template_files() -> list[Path]:
    """Find all .template files."""
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(TEMPLATES_DIR.glob("*.plist.template"))


def _generate_plist(template: Path) -> str:
    """Read template and substitute __HOME__."""
    content = template.read_text()
    return content.replace("__HOME__", str(HOME))


def _plist_name(template: Path) -> str:
    """com.aos.bridge.plist.template → com.aos.bridge.plist"""
    return template.name.replace(".template", "")


def check() -> bool:
    """Applied if all template-generated plists exist and match."""
    for template in _template_files():
        target = LA_DIR / _plist_name(template)
        if not target.exists():
            return False
        # Check content matches (in case template was updated)
        expected = _generate_plist(template)
        if target.read_text() != expected:
            return False
    return True


def up() -> bool:
    """Generate and install plists from templates."""
    LA_DIR.mkdir(parents=True, exist_ok=True)

    for template in _template_files():
        name = _plist_name(template)
        target = LA_DIR / name
        content = _generate_plist(template)

        # Unload old version if exists
        if target.exists():
            subprocess.run(
                ["launchctl", "unload", str(target)],
                capture_output=True,
            )

        # Also unload old com.agent.* equivalent if present
        service_name = name.replace(NEW_PREFIX, "").replace(".plist", "")
        old_plist = LA_DIR / f"{OLD_PREFIX}{service_name}.plist"
        if old_plist.exists():
            subprocess.run(
                ["launchctl", "unload", str(old_plist)],
                capture_output=True,
            )
            print(f"       Unloaded old {old_plist.name}")

        # Write new plist
        target.write_text(content)

        # Load it
        result = subprocess.run(
            ["launchctl", "load", str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"       Installed + loaded {name}")
        else:
            # Load can fail if service deps aren't ready — that's OK
            print(f"       Installed {name} (load deferred: {result.stderr.strip()})")

    return True
