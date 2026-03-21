"""
Migration 015: Bootstrap the work system data file.

The work engine expects ~/.aos/work/work.yaml to exist.
For fresh installs, create an empty but valid work file
so the engine doesn't error on first read.
"""

DESCRIPTION = "Bootstrap work system data (work.yaml)"

import yaml
from pathlib import Path

WORK_DIR = Path.home() / ".aos" / "work"
WORK_FILE = WORK_DIR / "work.yaml"

EMPTY_WORK = {
    "tasks": [],
    "goals": [],
    "projects": [],
    "threads": [],
    "inbox": [],
}


def check() -> bool:
    """Applied if work.yaml exists and is valid."""
    if not WORK_FILE.exists():
        return False
    try:
        with open(WORK_FILE) as f:
            data = yaml.safe_load(f)
        return isinstance(data, dict)
    except Exception:
        return False


def up() -> bool:
    """Create work.yaml with empty structure."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    if WORK_FILE.exists():
        print("       work.yaml exists — validating")
        try:
            with open(WORK_FILE) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                raise ValueError("Invalid format")
            print("       work.yaml is valid")
        except Exception as e:
            # Back up corrupt file and create fresh
            backup = WORK_FILE.with_suffix(".yaml.bak")
            WORK_FILE.rename(backup)
            print(f"       Backed up invalid work.yaml to {backup.name}")
            with open(WORK_FILE, "w") as f:
                yaml.dump(EMPTY_WORK, f, default_flow_style=False)
            print("       Created fresh work.yaml")
    else:
        with open(WORK_FILE, "w") as f:
            yaml.dump(EMPTY_WORK, f, default_flow_style=False)
        print("       Created work.yaml")

    return True
