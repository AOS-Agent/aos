"""
Migration 063: Move people DB module from instance to framework.

(Renumbered from 039 during release-train wave 4 promotion. Hardened
during transplant: each file removal is now wrapped individually — a
locked WAL file or other OSError on one path no longer aborts cleanup
of the rest, and no longer aborts the remaining migration batch
(040-048 in the original numbering ran after this one).)

The people CRUD layer (db.py, resolver.py, schema.sql) previously lived
at ~/.aos/services/people/ — an instance path. Framework code imported
from it via sys.path hacking. These modules are now shipped in the
framework at core/engine/people/. This migration removes the stale
instance copies and any leftover ghost people.db files.

Idempotent: re-running is safe.
"""

DESCRIPTION = "Move people DB module from instance to framework"

import shutil
from pathlib import Path

_SERVICES_PEOPLE = Path.home() / ".aos" / "services" / "people"
_VAULT_PEOPLE_DB = Path.home() / "vault" / "knowledge" / "people" / "people.db"
_FRAMEWORK_DB_MOD = Path.home() / "aos" / "core" / "engine" / "people" / "db.py"


def check() -> bool:
    """Return True if migration already applied (nothing to clean up)."""
    stale_module = _SERVICES_PEOPLE / "db.py"
    stale_vault = _VAULT_PEOPLE_DB
    return not stale_module.exists() and not stale_vault.exists()


def _safe_unlink(path: Path, cleaned: list, errors: list) -> None:
    try:
        path.unlink()
        cleaned.append(str(path))
    except OSError as e:
        errors.append(f"{path}: {e}")


def _safe_rmtree(path: Path, cleaned: list, errors: list) -> None:
    try:
        shutil.rmtree(path)
        cleaned.append(str(path))
    except OSError as e:
        errors.append(f"{path}: {e}")


def up() -> bool:
    """Remove stale instance copies of the people module."""
    # Safety: only clean up if the framework copy is deployed
    if not _FRAMEWORK_DB_MOD.exists():
        print("  Skipping: framework db.py not yet deployed at", _FRAMEWORK_DB_MOD)
        return True  # Don't block other migrations; will run next cycle

    cleaned: list = []
    errors: list = []

    # Remove stale module files at services/people/
    if _SERVICES_PEOPLE.exists():
        for name in ("db.py", "resolver.py", "schema.sql"):
            f = _SERVICES_PEOPLE / name
            if f.exists():
                _safe_unlink(f, cleaned, errors)
        # Remove __pycache__ if present
        cache = _SERVICES_PEOPLE / "__pycache__"
        if cache.exists():
            _safe_rmtree(cache, cleaned, errors)
        # Remove stale people.db and WAL files if any remain
        for name in ("people.db", "people.db-shm", "people.db-wal"):
            f = _SERVICES_PEOPLE / name
            if f.exists():
                _safe_unlink(f, cleaned, errors)

    # Remove ghost vault people.db (0-byte file created by accident)
    if _VAULT_PEOPLE_DB.exists() and _VAULT_PEOPLE_DB.stat().st_size == 0:
        _safe_unlink(_VAULT_PEOPLE_DB, cleaned, errors)

    if cleaned:
        print(f"  Cleaned up {len(cleaned)} stale files")
        for f in cleaned:
            print(f"    - {f}")

    if errors:
        print(f"  {len(errors)} file(s) could not be removed (logged, not fatal):")
        for e in errors:
            print(f"    - {e}")

    return True


if __name__ == "__main__":
    if check():
        print("Migration 063 already applied")
    else:
        success = up()
        print("Done" if success else "Failed")
