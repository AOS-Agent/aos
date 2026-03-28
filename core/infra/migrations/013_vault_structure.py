"""
Migration 013: Bootstrap the knowledge vault directory structure.

Creates ~/vault/ with the standard AOS directory layout.
If the vault already exists (user has Obsidian), we only add
missing directories — never touch existing content.

Also creates a minimal .obsidian/app.json if Obsidian isn't
configured yet (so the vault opens cleanly in Obsidian).
"""

DESCRIPTION = "Bootstrap knowledge vault structure (~/vault/)"

import json
from pathlib import Path

VAULT = Path.home() / "vault"

# Standard vault directories
DIRS = [
    "daily",                # Daily notes
    "knowledge",            # Curated knowledge
    "knowledge/decisions",  # Decision records
    "knowledge/extracts",   # Extracted content (YouTube, articles, etc.)
    "knowledge/research",   # Research notes
    "knowledge/synthesis",  # Synthesized insights
    "log",                  # Periodic logs
    "log/days",             # Daily logs
    "log/weeks",            # Weekly logs
    "log/months",           # Monthly logs
    "log/quarters",         # Quarterly logs
    "log/years",            # Yearly logs
    "ops",                  # Operational data
    "ops/sessions",         # Session summaries
    "ops/friction",         # Friction reports
    "ops/patterns",         # Compiled patterns
    "reviews",              # Work reviews
    "sessions",             # Raw session exports
]


def check() -> bool:
    """Applied if vault dir and all subdirs exist."""
    if not VAULT.exists():
        return False
    return all((VAULT / d).exists() for d in DIRS)


def up() -> bool:
    """Create vault structure. Never overwrites existing content."""
    VAULT.mkdir(parents=True, exist_ok=True)

    created = 0
    for d in DIRS:
        path = VAULT / d
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created += 1

    if created:
        print(f"       Created {created} vault directories")
    else:
        print("       All vault directories exist")

    # Bootstrap minimal Obsidian config if not present
    obsidian_dir = VAULT / ".obsidian"
    app_json = obsidian_dir / "app.json"
    if not app_json.exists():
        obsidian_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "newFileLocation": "current",
            "showLineNumber": True,
            "strictLineBreaks": True,
        }
        with open(app_json, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        print("       Created .obsidian/app.json")
    else:
        print("       Obsidian config exists")

    return True
