"""
Migration 002: Set up config layering (defaults → user overrides).

System ships defaults in ~/aos/config/defaults/.
User overrides live in ~/.aos/config/.
At runtime, scripts merge: defaults ← user overrides.

This migration:
- Moves current config/ files to config/defaults/ (if not already split)
- Creates user config stubs from current values (for existing installs)
- For new users: user config stays empty, defaults apply
"""

DESCRIPTION = "Config layering — system defaults + user overrides"

import shutil
import yaml
from pathlib import Path

AOS_DIR = Path.home() / "aos"
DEFAULTS_DIR = AOS_DIR / "config" / "defaults"
USER_CONFIG = Path.home() / ".aos" / "config"

# Files that should be in defaults (shipped with the system)
# vs files that are machine-specific (user overrides)
SYSTEM_FILES = [
    "capabilities.yaml",
    "crons.yaml",
    "vault-sources.yaml",
]

USER_FILES = [
    "state.yaml",       # machine-specific: user, IP, services
    "operator.yaml",    # who you are
]


def check() -> bool:
    """Applied if defaults/ dir exists with at least one file."""
    return DEFAULTS_DIR.exists() and any(DEFAULTS_DIR.glob("*.yaml"))


def up() -> bool:
    """Split config into defaults (git) and user overrides (local)."""
    DEFAULTS_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG.mkdir(parents=True, exist_ok=True)

    config_dir = AOS_DIR / "config"

    # Move system files to defaults/
    for fname in SYSTEM_FILES:
        src = config_dir / fname
        dst = DEFAULTS_DIR / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            print(f"       Copied {fname} → config/defaults/")

    # Copy machine-specific files to user config (if not already there)
    for fname in USER_FILES:
        src = config_dir / fname
        dst = USER_CONFIG / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            print(f"       Copied {fname} → ~/.aos/config/")

    # Create the config loader utility
    loader_path = AOS_DIR / "core" / "lib" / "config.py"
    loader_path.parent.mkdir(parents=True, exist_ok=True)

    if not loader_path.exists():
        loader_path.write_text('''"""
AOS Config Loader — merges system defaults with user overrides.

Usage:
    from core.lib.config import load_config
    config = load_config("state")         # loads state.yaml
    config = load_config("crons")         # loads crons.yaml

Merge order: defaults ← user overrides (user wins on conflicts).
"""

import yaml
from pathlib import Path

AOS_DIR = Path.home() / "aos"
DEFAULTS_DIR = AOS_DIR / "config" / "defaults"
USER_CONFIG = Path.home() / ".aos" / "config"
# Legacy: direct config/ files (pre-layering)
LEGACY_CONFIG = AOS_DIR / "config"


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(name: str) -> dict:
    """Load a config file with layered merging.

    Priority: user override > system default > legacy location
    """
    result = {}

    # 1. Try legacy location (config/<name>.yaml)
    legacy = LEGACY_CONFIG / f"{name}.yaml"
    if legacy.exists():
        with open(legacy) as f:
            data = yaml.safe_load(f) or {}
        result = deep_merge(result, data)

    # 2. Load system defaults
    default = DEFAULTS_DIR / f"{name}.yaml"
    if default.exists():
        with open(default) as f:
            data = yaml.safe_load(f) or {}
        result = deep_merge(result, data)

    # 3. Load user overrides (wins)
    user = USER_CONFIG / f"{name}.yaml"
    if user.exists():
        with open(user) as f:
            data = yaml.safe_load(f) or {}
        result = deep_merge(result, data)

    return result


def save_user_config(name: str, data: dict):
    """Save to user config (never writes to system defaults)."""
    USER_CONFIG.mkdir(parents=True, exist_ok=True)
    path = USER_CONFIG / f"{name}.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
''')
        print("       Created core/lib/config.py (config loader)")

    return True
