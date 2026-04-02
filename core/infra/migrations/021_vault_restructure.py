"""
Migration 021: Vault restructure — consolidate to 2-folder architecture.

Evolves the vault from the original multi-folder layout to a clean 2-folder
structure: log/ (temporal) and knowledge/ (permanent).

Moves:
  ops/sessions/*       → log/sessions/
  ops/friction/*       → log/friction/
  ops/patterns/*       → knowledge/expertise/
  log/days/*           → log/          (flatten daily logs)
  daily/*              → log/          (Obsidian daily notes)
  sessions/*           → log/sessions/ (raw session exports)
  reviews/*            → log/          (review files)
  ideas/*              → knowledge/captures/
  materials/*          → knowledge/captures/
  knowledge/extracts/* → knowledge/captures/
  people/people.db     → ~/.aos/data/people.db

Removes empty old directories after moving.
Updates QMD collections (removes stale: ops, sessions, daily, reviews).
Configures Obsidian daily notes to write to log/ folder.
"""

DESCRIPTION = "Vault restructure — 2-folder architecture (log + knowledge)"

import json
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
VAULT = HOME / "vault"
AOS_DATA = HOME / ".aos" / "data"

# New directories to ensure exist
NEW_DIRS = [
    "log",
    "log/sessions",
    "log/friction",
    "knowledge",
    "knowledge/captures",
    "knowledge/decisions",
    "knowledge/expertise",
    "knowledge/initiatives",
    "knowledge/references",
    "knowledge/research",
    "knowledge/synthesis",
]

# Old directories to clean up (checked after file moves, removed only if empty)
OLD_DIRS = [
    "ops/sessions",
    "ops/friction",
    "ops/patterns",
    "ops",
    "log/days",
    "log/quarters",
    "log/years",
    "daily",
    "sessions",
    "reviews",
    "ideas",
    "materials",
    "knowledge/extracts",
    "people",
]

# File moves: (source_dir, dest_dir, description)
# Files are moved individually — never overwrite an existing file.
MOVES = [
    ("ops/sessions",       "log/sessions",        "session exports"),
    ("ops/friction",       "log/friction",         "friction reports"),
    ("ops/patterns",       "knowledge/expertise",  "compiled patterns"),
    ("log/days",           "log",                  "daily logs (flatten)"),
    ("daily",              "log",                  "daily notes"),
    ("sessions",           "log/sessions",         "raw session exports"),
    ("reviews",            "log",                  "review files"),
    ("ideas",              "knowledge/captures",   "idea captures"),
    ("materials",          "knowledge/captures",   "content extracts"),
    ("knowledge/extracts", "knowledge/captures",   "knowledge extracts"),
]

# QMD collections to remove (content absorbed into log/ and knowledge/)
STALE_QMD_COLLECTIONS = ["ops", "sessions", "daily", "reviews"]


def check() -> bool:
    """Applied if new structure exists and old dirs are gone."""
    if not VAULT.exists():
        return True  # No vault = nothing to migrate

    # New structure must exist
    for d in NEW_DIRS:
        if not (VAULT / d).exists():
            return False

    # Old structure must be gone (or never existed)
    old_still_present = False
    for d in ["ops", "daily", "sessions", "reviews"]:
        path = VAULT / d
        if path.exists() and any(path.iterdir()):
            old_still_present = True
            break

    return not old_still_present


def _move_files(src_dir: Path, dest_dir: Path) -> int:
    """Move all files from src to dest. Skip if dest already has that filename.

    For subdirectories, recursively move their contents into dest,
    preserving the subdirectory structure only one level deep.
    Returns count of files moved.
    """
    if not src_dir.exists():
        return 0

    moved = 0
    for item in src_dir.iterdir():
        if item.name.startswith("."):
            continue  # skip hidden files

        dest = dest_dir / item.name

        if item.is_file():
            if not dest.exists():
                shutil.move(str(item), str(dest))
                moved += 1
            # else: skip — don't overwrite

        elif item.is_dir():
            # Recursively move contents of subdirectories
            sub_dest = dest_dir / item.name
            sub_dest.mkdir(parents=True, exist_ok=True)
            for sub_item in item.iterdir():
                sub_file_dest = sub_dest / sub_item.name
                if sub_item.is_file() and not sub_file_dest.exists():
                    shutil.move(str(sub_item), str(sub_file_dest))
                    moved += 1

    return moved


def _remove_empty_dir(path: Path) -> bool:
    """Remove directory if it exists and is empty (no files, maybe just empty subdirs).

    Recursively removes empty subdirs first. Returns True if removed.
    """
    if not path.exists() or not path.is_dir():
        return False

    # First, remove any empty subdirs
    for child in list(path.iterdir()):
        if child.is_dir():
            _remove_empty_dir(child)

    # Now check if this dir is empty
    remaining = list(path.iterdir())
    if not remaining:
        path.rmdir()
        return True

    return False


def _update_qmd():
    """Remove stale QMD collections. Non-fatal — QMD may not be installed."""
    qmd = HOME / ".bun" / "bin" / "qmd"
    if not qmd.exists():
        return

    for coll in STALE_QMD_COLLECTIONS:
        try:
            result = subprocess.run(
                [str(qmd), "collection", "show", coll],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                continue  # collection doesn't exist, skip

            subprocess.run(
                [str(qmd), "collection", "remove", coll],
                capture_output=True, text=True, timeout=30,
            )
            print(f"       Removed QMD collection: {coll}")
        except Exception:
            pass  # Non-fatal


def _configure_obsidian():
    """Set up Obsidian daily notes to use log/ folder."""
    obsidian_dir = VAULT / ".obsidian"
    if not obsidian_dir.exists():
        return  # Obsidian not configured

    # Daily notes config
    dn_config = obsidian_dir / "daily-notes.json"
    if not dn_config.exists():
        config = {
            "folder": "log",
            "format": "YYYY-MM-DD",
            "template": ".obsidian/templates/daily-note",
        }
        with open(dn_config, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        print("       Configured Obsidian daily notes → log/")

    # Daily note template
    templates_dir = obsidian_dir / "templates"
    templates_dir.mkdir(exist_ok=True)
    template_file = templates_dir / "daily-note.md"
    if not template_file.exists():
        template_file.write_text(
            '---\n'
            'title: "{{date:dddd, MMMM D}}"\n'
            'type: daily\n'
            'date: "{{date:YYYY-MM-DD}}"\n'
            'day: "{{date:dddd}}"\n'
            'tags: [daily]\n'
            'sessions: 0\n'
            'tasks_completed: 0\n'
            '---\n'
            '\n'
            '# {{date:dddd, MMMM D}}\n'
            '\n'
            '## Work\n'
            '\n'
            '## Journal\n'
            '\n'
            '## Sessions\n'
        )
        print("       Created daily note template")

    # Enable daily-notes and bases plugins
    core_plugins = obsidian_dir / "core-plugins.json"
    if core_plugins.exists():
        try:
            with open(core_plugins) as f:
                plugins = json.load(f)
            changed = False
            for plugin in ["daily-notes", "bases"]:
                if not plugins.get(plugin):
                    plugins[plugin] = True
                    changed = True
            if changed:
                with open(core_plugins, "w") as f:
                    json.dump(plugins, f, indent=2)
                    f.write("\n")
                print("       Enabled Obsidian daily-notes + bases plugins")
        except Exception:
            pass


def up() -> bool:
    """Restructure vault to 2-folder architecture."""
    if not VAULT.exists():
        print("       No vault found — skipping")
        return True

    # 1. Create new directories
    created = 0
    for d in NEW_DIRS:
        path = VAULT / d
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created += 1
    if created:
        print(f"       Created {created} new directories")

    # 2. Move files from old to new locations
    total_moved = 0
    for src_rel, dest_rel, desc in MOVES:
        src = VAULT / src_rel
        dest = VAULT / dest_rel
        count = _move_files(src, dest)
        if count > 0:
            total_moved += count
            print(f"       Moved {count} {desc}: {src_rel}/ → {dest_rel}/")
    if total_moved:
        print(f"       Total files moved: {total_moved}")
    else:
        print("       No files needed moving")

    # 3. Move people.db to instance data
    people_db = VAULT / "people" / "people.db"
    dest_db = AOS_DATA / "people.db"
    if people_db.exists():
        AOS_DATA.mkdir(parents=True, exist_ok=True)
        if not dest_db.exists():
            shutil.move(str(people_db), str(dest_db))
            print("       Moved people.db → ~/.aos/data/")
        else:
            # Both exist — keep the newer one at ~/.aos/data/, remove old
            people_db.unlink()
            print("       Removed duplicate people.db (kept ~/.aos/data/ copy)")

    # 4. Clean up empty old directories
    removed_dirs = []
    for d in OLD_DIRS:
        path = VAULT / d
        if _remove_empty_dir(path):
            removed_dirs.append(d)
    if removed_dirs:
        print(f"       Removed {len(removed_dirs)} empty old directories")

    # 5. Update QMD collections
    _update_qmd()

    # 6. Configure Obsidian
    _configure_obsidian()

    return True
