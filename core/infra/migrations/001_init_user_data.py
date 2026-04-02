"""
Migration 001: Initialize ~/.aos/ user data structure.

For new users: creates the directory layout.
For existing users: ensures all expected dirs exist.
For v1 migrants: doesn't touch ~/aos/, just builds the v2 structure.
"""

DESCRIPTION = "Initialize user data directory (~/.aos/)"

from pathlib import Path

USER_DIR = Path.home() / ".aos"

DIRS = [
    "work",
    "config",
    "services",
    "logs",
    "logs/crons",
    "data",
    "data/health",
    "handoffs",
]


def check() -> bool:
    """Already applied if all dirs exist."""
    return all((USER_DIR / d).exists() for d in DIRS)


def up() -> bool:
    """Create user data directories."""
    for d in DIRS:
        (USER_DIR / d).mkdir(parents=True, exist_ok=True)

    # Create README if missing
    readme = USER_DIR / "README"
    if not readme.exists():
        readme.write_text("AOS User Data — not tracked in git.\nSystem code lives at ~/aos/\n")

    return True
