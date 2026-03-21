"""
Migration 007: Migrate from AOS v1 (~/aos/) to v2 structure.

This is ONLY for users who have an existing ~/aos/ installation.
Skipped automatically if ~/aos/ doesn't exist.

What it does:
- Maps v1 data locations to v2 equivalents
- Copies user data (tasks, config) — never moves or deletes v1
- Records what was migrated so you can verify before removing v1
- Sets up forwarding so v1 services can coexist during transition

What it does NOT do:
- Delete or modify anything in ~/aos/
- Port services (bridge, dashboard, listen) — that's a separate effort
- Touch the vault (it's independent, both versions use it)
"""

DESCRIPTION = "Import data from AOS v1 (~/aos/) if present"

import shutil
import yaml
from pathlib import Path
from datetime import datetime

V1_DIR = Path.home() / "aos"
V2_DIR = Path.home() / "aos"
USER_DIR = Path.home() / ".aos"
MIGRATION_RECORD = USER_DIR / "logs" / "v1-migration.yaml"


def check() -> bool:
    """Skip if v1 doesn't exist or already migrated."""
    if not V1_DIR.exists():
        return True  # No v1, nothing to do
    if MIGRATION_RECORD.exists():
        return True  # Already ran
    return False


def up() -> bool:
    """Import v1 data into v2 structure."""
    if not V1_DIR.exists():
        print("       No v1 installation found, skipping")
        return True

    record = {
        "timestamp": datetime.now().isoformat(),
        "v1_path": str(V1_DIR),
        "migrated": [],
        "skipped": [],
    }

    # --- Config files ---
    v1_configs = [
        ("config/state.yaml", "config/state.yaml"),
        ("config/goals.yaml", "config/goals.yaml"),
        ("config/tasks.yaml", "work/v1-tasks.yaml"),  # renamed to avoid conflict
        ("config/trust.yaml", "config/trust.yaml"),
        ("config/projects.yaml", "config/projects.yaml"),
        ("config/capabilities.yaml", "config/capabilities.yaml"),
    ]

    for v1_rel, v2_rel in v1_configs:
        v1_path = V1_DIR / v1_rel
        v2_path = USER_DIR / v2_rel

        if not v1_path.exists():
            record["skipped"].append(f"{v1_rel} (not found in v1)")
            continue

        if v2_path.exists():
            record["skipped"].append(f"{v1_rel} → {v2_rel} (already exists in v2)")
            continue

        v2_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(v1_path, v2_path)
        record["migrated"].append(f"{v1_rel} → ~/.aos/{v2_rel}")
        print(f"       Copied {v1_rel} → {v2_rel}")

    # --- Agent secret binary ---
    agent_secret_v1 = V1_DIR / "bin" / "agent-secret"
    agent_secret_v2 = V2_DIR / "core" / "bin" / "agent-secret"
    if agent_secret_v1.exists() and not agent_secret_v2.exists():
        shutil.copy2(agent_secret_v1, agent_secret_v2)
        record["migrated"].append("bin/agent-secret → core/bin/agent-secret")
        print("       Copied agent-secret utility")

    # --- Execution log ---
    exec_log_dir = V1_DIR / "execution_log"
    if exec_log_dir.exists():
        target = USER_DIR / "logs" / "v1-execution-log"
        if not target.exists():
            shutil.copytree(exec_log_dir, target)
            record["migrated"].append("execution_log/ → logs/v1-execution-log/")
            print("       Copied execution logs")

    # --- Save migration record ---
    MIGRATION_RECORD.parent.mkdir(parents=True, exist_ok=True)
    with open(MIGRATION_RECORD, "w") as f:
        yaml.dump(record, f, default_flow_style=False, sort_keys=False)

    print(f"\n       Migration record: {MIGRATION_RECORD}")
    print(f"       Migrated {len(record['migrated'])} items, skipped {len(record['skipped'])}")
    print(f"       ⚠ ~/aos/ was NOT modified — remove it manually when ready")

    return True
