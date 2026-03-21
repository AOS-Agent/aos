"""
AOS Config Loader — merges framework defaults with user overrides.

Usage:
    from config_loader import load_config
    config = load_config("state")         # loads state.yaml
    config = load_config("crons")         # loads crons.yaml

Merge order: framework default ← user override (user wins).

Convention:
    ~/aos/config/<name>.yaml       Framework default (git-tracked)
    ~/.aos/config/<name>.yaml      User override (never in git)
"""

import yaml
from pathlib import Path

FRAMEWORK_CONFIG = Path.home() / "aos" / "config"
USER_CONFIG = Path.home() / ".aos" / "config"


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

    Priority: user override > framework default
    """
    result = {}

    # 1. Load framework default
    default = FRAMEWORK_CONFIG / f"{name}.yaml"
    if default.exists():
        with open(default) as f:
            data = yaml.safe_load(f) or {}
        result = deep_merge(result, data)

    # 2. Load user override (wins)
    user = USER_CONFIG / f"{name}.yaml"
    if user.exists():
        with open(user) as f:
            data = yaml.safe_load(f) or {}
        result = deep_merge(result, data)

    return result


def save_user_config(name: str, data: dict):
    """Save to user config (never writes to framework)."""
    USER_CONFIG.mkdir(parents=True, exist_ok=True)
    path = USER_CONFIG / f"{name}.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
