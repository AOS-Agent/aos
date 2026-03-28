"""
Migration 022: Update instance-side path references after v0.6.0 restructure.

Git handles the framework file moves automatically on pull.
This migration updates instance-specific files that reference framework paths:
  - ~/.claude/settings.json hooks (core/work/ → core/engine/work/)
  - ~/.aos/ data files that reference old paths
  - Installed LaunchAgent plists (core/bin/ → core/bin/internal/)

Idempotent: safe to run multiple times.
"""

DESCRIPTION = "Update instance path references for v0.6.0 restructure"

import json
import os
import re
from pathlib import Path

# Path mappings: old → new
PATH_UPDATES = {
    "core/work/inject_context.py": "core/engine/work/inject_context.py",
    "core/work/session_close.py": "core/engine/work/session_close.py",
    "core/work/reconcile.py": "core/engine/work/reconcile.py",
    "core/work/cli.py": "core/engine/work/cli.py",
    "core/bin/scheduler": "core/bin/internal/scheduler",
    "core/bin/aos-python": "core/bin/internal/aos-python",
    "core/bin/eventd": "core/bin/internal/eventd",
    "core/bin/agent-secret": "core/bin/cli/agent-secret",
    "core/reconcile/": "core/infra/reconcile/",
    "core/migrations/": "core/infra/migrations/",
    "core/integrations/": "core/infra/integrations/",
}


def check() -> bool:
    """Return True if migration already applied (settings.json uses new paths)."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return True  # No settings = nothing to migrate

    try:
        content = settings_path.read_text()
        # If old path not found, already migrated
        return "core/work/inject_context.py" not in content
    except Exception:
        return True


def up() -> bool:
    """Update all instance-side path references."""
    updated = []

    # 1. Update ~/.claude/settings.json
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            content = settings_path.read_text()
            original = content
            for old, new in PATH_UPDATES.items():
                content = content.replace(old, new)
            if content != original:
                settings_path.write_text(content)
                updated.append("settings.json")
        except Exception as e:
            print(f"  Warning: Could not update settings.json: {e}")

    # 2. Update installed LaunchAgent plists (the live copies, not templates)
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if la_dir.exists():
        for plist in la_dir.glob("com.aos.*.plist"):
            try:
                content = plist.read_text()
                original = content
                for old, new in PATH_UPDATES.items():
                    content = content.replace(old, new)
                if content != original:
                    plist.write_text(content)
                    updated.append(plist.name)
            except Exception as e:
                print(f"  Warning: Could not update {plist.name}: {e}")

    # 3. Consolidate ~/.aos/ small directories into data/
    data_dir = Path.home() / ".aos" / "data"
    for old_dir_name in ["patterns", "handoffs", "feedback", "telemetry", "steer"]:
        old_dir = Path.home() / ".aos" / old_dir_name
        new_dir = data_dir / old_dir_name
        if old_dir.exists() and old_dir.is_dir() and not new_dir.exists():
            try:
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                old_dir.rename(new_dir)
                updated.append(f"~/.aos/{old_dir_name} → data/{old_dir_name}")
            except Exception as e:
                print(f"  Warning: Could not move {old_dir_name}: {e}")

    if updated:
        print(f"  Updated: {', '.join(updated)}")
    else:
        print("  No instance-side updates needed")

    return True
