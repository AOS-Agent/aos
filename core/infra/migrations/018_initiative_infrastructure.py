"""
Migration 018: Initiative pipeline + Bridge v2 infrastructure.

Creates directories and config entries needed for the initiative pipeline
and Bridge v2 integration. Ensures every AOS machine has the structural
foundation for initiative tracking and enhanced notifications.

Creates:
1. vault/knowledge/expertise/ — expertise accumulation
2. vault/ideas/ — idea capture
3. core/lib/ — shared library modules (already in git, but vault dirs are data layer)
4. initiatives: block in operator.yaml (with defaults)
"""

DESCRIPTION = "Initiative pipeline infrastructure (dirs, config)"

from pathlib import Path

import yaml

HOME = Path.home()
VAULT = HOME / "vault"
OPERATOR_YAML = HOME / ".aos" / "config" / "operator.yaml"

# Directories to create on the data layer (vault)
VAULT_DIRS = [
    VAULT / "knowledge" / "expertise",
    VAULT / "knowledge" / "initiatives",
    VAULT / "ideas",
]

# Default initiatives config for operator.yaml
DEFAULT_INITIATIVES = {
    "enabled": True,
    "max_active": 3,
    "deliberation": "advisor",
    "auto_surface": True,
    "stale_threshold_days": 3,
}


def check() -> bool:
    """Applied if all directories exist and operator.yaml has initiatives block."""
    for d in VAULT_DIRS:
        if not d.exists():
            return False

    if not OPERATOR_YAML.exists():
        return False

    try:
        with open(OPERATOR_YAML) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return False
        if "initiatives" not in data:
            return False
        return True
    except Exception:
        return False


def up() -> bool:
    """Create directories and add initiatives config."""
    # 1. Create vault directories
    for d in VAULT_DIRS:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            print(f"       Created {d}")
        else:
            print(f"       Exists: {d}")

    # 2. Add initiatives block to operator.yaml if missing
    if not OPERATOR_YAML.exists():
        print("       operator.yaml not found — skipping config (will be set during onboarding)")
        return True

    try:
        with open(OPERATOR_YAML) as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            print("       operator.yaml is invalid — skipping config update")
            return True

        if "initiatives" in data:
            print("       initiatives config already present in operator.yaml")
        else:
            data["initiatives"] = DEFAULT_INITIATIVES
            # Read original file to do surgical append instead of yaml.dump
            # (yaml.dump would reformat the entire file)
            OPERATOR_YAML.read_text()
            initiative_block = "\ninitiatives:\n"
            for key, val in DEFAULT_INITIATIVES.items():
                if isinstance(val, bool):
                    initiative_block += f"  {key}: {'true' if val else 'false'}\n"
                else:
                    initiative_block += f"  {key}: {val}\n"

            with open(OPERATOR_YAML, "a") as f:
                f.write(initiative_block)
            print("       Added initiatives config to operator.yaml")

    except Exception as e:
        print(f"       Warning: could not update operator.yaml: {e}")
        # Non-fatal — config can be added manually

    return True
