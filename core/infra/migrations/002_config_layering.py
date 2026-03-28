"""
Migration 002: Ensure user config directory exists.

System config lives in ~/aos/config/ (git-tracked).
User overrides live in ~/.aos/config/ (machine-specific).

Originally planned a layered defaults system with core/lib/config.py,
but in practice all scripts read config directly from YAML files.
The layered loader was never adopted — removed in cleanup (2026-03-22).
"""

DESCRIPTION = "Ensure user config directory exists"

from pathlib import Path

USER_CONFIG = Path.home() / ".aos" / "config"


def check() -> bool:
    """Applied if user config dir exists."""
    return USER_CONFIG.exists()


def up() -> bool:
    """Create user config directory."""
    USER_CONFIG.mkdir(parents=True, exist_ok=True)
    print("       Created ~/.aos/config/")
    return True
