"""
Migration 020: Clean up dead code artifacts from instances.

Removes:
  - Dead bin scripts that were removed from the framework
  - Stale LaunchAgent (healthsync-deploy)
  - Dead app directories (phoenix, bridge v1, xfeed)
  - Dead bridge .pyc cache files for removed modules
  - Stale patterns output directory

Does NOT remove:
  - apps/people/ (referenced by comms bus, active dev)
  - apps/roborock/ (wired via crons.yaml)
  - apps/content-engine/ (standalone app, operator may use)
"""

DESCRIPTION = "Remove dead code artifacts from instance"

import os
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
AOS_DIR = HOME / "aos"
INSTANCE_DIR = HOME / ".aos"

# Bin scripts removed from framework
DEAD_BIN_SCRIPTS = [
    "aos-release", "deploy-chief", "deploy-metrics", "email-cleanup",
    "healthsync-deploy", "imessage-watch", "iphone-tap", "onboard-project",
    "setup-github-labels", "setup-healthchecks", "setup-launchagent-permissions.sh",
    "technician-create-topic", "vacuum",
]

# Dead app directories in ~/aos/apps/ (instance-level)
DEAD_APPS = ["phoenix", "bridge", "xfeed"]

# Dead bridge modules (remove cached .pyc files)
DEAD_BRIDGE_MODULES = [
    "vault_tasks", "claude_cli", "clickup_client",
    "context_loader", "shared_context", "tracing",
]

# Stale LaunchAgent
HEALTHSYNC_PLIST = HOME / "Library" / "LaunchAgents" / "com.aos.healthsync-deploy.plist"


def check() -> bool:
    """Applied if the major dead artifacts are already gone."""
    # Check if most dead scripts are already removed
    bin_dir = AOS_DIR / "core" / "bin"
    remaining = sum(1 for s in DEAD_BIN_SCRIPTS if (bin_dir / s).exists())

    # Check if dead apps are gone
    apps_dir = AOS_DIR / "apps"
    dead_apps_remaining = sum(
        1 for a in DEAD_APPS
        if apps_dir.exists() and (apps_dir / a).exists()
    )

    # Check if healthsync plist is gone
    plist_gone = not HEALTHSYNC_PLIST.exists()

    # Consider applied if most things are already clean
    return remaining == 0 and dead_apps_remaining == 0 and plist_gone


def up() -> bool:
    """Remove dead code artifacts."""
    cleaned = []

    # 1. Remove dead bin scripts from runtime
    bin_dir = AOS_DIR / "core" / "bin"
    for script in DEAD_BIN_SCRIPTS:
        path = bin_dir / script
        if path.exists():
            path.unlink()
            cleaned.append(f"bin/{script}")

    # 2. Remove dead pattern output
    patterns_dir = bin_dir / "patterns"
    if patterns_dir.exists():
        shutil.rmtree(patterns_dir, ignore_errors=True)
        cleaned.append("bin/patterns/")

    # 3. Unload and remove healthsync-deploy LaunchAgent
    if HEALTHSYNC_PLIST.exists():
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(HEALTHSYNC_PLIST)],
            capture_output=True, timeout=10,
        )
        HEALTHSYNC_PLIST.unlink(missing_ok=True)
        cleaned.append("com.aos.healthsync-deploy.plist")

    # 4. Remove dead app directories
    apps_dir = AOS_DIR / "apps"
    if apps_dir.exists():
        for app_name in DEAD_APPS:
            app_path = apps_dir / app_name
            if app_path.exists():
                shutil.rmtree(app_path, ignore_errors=True)
                cleaned.append(f"apps/{app_name}/")

    # 5. Clean dead bridge module caches
    cache_dir = AOS_DIR / "core" / "services" / "bridge" / "__pycache__"
    if cache_dir.exists():
        for mod_name in DEAD_BRIDGE_MODULES:
            for pyc in cache_dir.glob(f"{mod_name}.*.pyc"):
                pyc.unlink()
                cleaned.append(f"cache: {pyc.name}")

    # 6. Remove dead config assets from runtime
    icon = AOS_DIR / "config" / "icon-node.png"
    if icon.exists():
        icon.unlink()
        cleaned.append("config/icon-node.png")

    # 7. Remove tracing.py from runtime bridge
    tracing = AOS_DIR / "core" / "services" / "bridge" / "tracing.py"
    if tracing.exists():
        tracing.unlink()
        cleaned.append("bridge/tracing.py")

    # 8. Remove vault_tasks.py from runtime bridge
    vault_tasks = AOS_DIR / "core" / "services" / "bridge" / "vault_tasks.py"
    if vault_tasks.exists():
        vault_tasks.unlink()
        cleaned.append("bridge/vault_tasks.py")

    if cleaned:
        print(f"  Cleaned {len(cleaned)} dead artifacts: {', '.join(cleaned[:10])}")
        if len(cleaned) > 10:
            print(f"  ... and {len(cleaned) - 10} more")
    else:
        print("  No dead artifacts found — already clean")

    return True
