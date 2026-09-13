"""
Migration 113: purge three named stale DB backups (~238 MB).

~/.aos/data/ accumulated one-off pre-migration snapshots that were never
cleaned up after the operations they guarded were verified stable:

    comms.db.bak-preconverse                 182 MB  (2026-08-05)
    qareen.db.bak-2026-06-26-chief-dejunk     28 MB
    qareen.db.bak-2026-06-28-pre-goal         28 MB

All three guard events six to eight weeks past, on databases that have been
written to daily since. They are dead weight on the internal SSD, which AOS's
own storage policy says holds system, config and services only.

Named files, never a glob. A `*.bak-*` sweep would be shorter and would be the
bug: the next operator to take a safety copy before a risky migration would
find it deleted by an unrelated update, which is the one moment a backup has to
survive. Anything not on this literal list is left alone, including backups
this machine has never seen.

Backup-first, per the atomic-migration rule: each file is copied to
~/.aos/backups/pre-purge/<name>.<timestamp> before deletion — so the purge is
itself recoverable — unless a copy of that file is already there from a
previous run. On a volume with less free space than the file needs, the copy
fails and the original is kept: a purge that cannot be undone does not happen.

Idempotent: check() passes once none of the three names exist.
"""

from __future__ import annotations

DESCRIPTION = "Purge 3 named stale DB backups (~238 MB), safety-copied first"

import shutil
import time
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _data_dir() -> Path:
    return Path.home() / ".aos" / "data"


def _backup_dir() -> Path:
    return Path.home() / ".aos" / "backups" / "pre-purge"

# Literal names only. Never a glob — see module docstring.
STALE_BACKUPS = (
    "comms.db.bak-preconverse",
    "qareen.db.bak-2026-06-26-chief-dejunk",
    "qareen.db.bak-2026-06-28-pre-goal",
)


def _present() -> list[Path]:
    data_dir = _data_dir()
    return [data_dir / n for n in STALE_BACKUPS if (data_dir / n).exists()]


def _already_copied(name: str) -> bool:
    """True if a safety copy of this file already sits in the backup dir."""
    backup_dir = _backup_dir()
    if not backup_dir.exists():
        return False
    return any(p.name.startswith(name + ".") for p in backup_dir.iterdir())


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except Exception:  # noqa: BLE001
        return 0


def check() -> bool:
    """Applied when none of the three named backups remain."""
    return not _present()


def up() -> bool:
    targets = _present()
    if not targets:
        print("  Nothing to purge — none of the three named backups are present")
        return True

    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    freed = 0

    for src in targets:
        size = src.stat().st_size

        if _already_copied(src.name):
            print(f"  · Safety copy already exists for {src.name}")
        else:
            if _free_bytes(backup_dir) < size * 1.1:
                print(f"  ⚠ Not enough free space to safety-copy {src.name} — KEEPING it")
                continue
            dest = backup_dir / f"{src.name}.{stamp}"
            try:
                shutil.copy2(src, dest)
            except OSError as e:
                print(f"  ⚠ Could not safety-copy {src.name} ({e}) — KEEPING it")
                continue
            print(f"  ✓ Safety copy → {dest}")

        try:
            src.unlink()
        except OSError as e:
            print(f"  ⚠ Could not delete {src.name}: {e}")
            continue
        freed += size
        print(f"  ✓ Removed {src} ({size / 1e6:.1f} MB)")

    print(f"  Reclaimed {freed / 1e6:.1f} MB; copies retained in {backup_dir}")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 113 already applied" if check() else ("Done" if up() else "Failed"))
