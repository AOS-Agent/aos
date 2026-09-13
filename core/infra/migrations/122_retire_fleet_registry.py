"""
Migration 122: retire the fleet node registry.

The single-node cleanup deleted `core/bin/cli/fleet` and the `aos fleet`
subcommand. Its instance config outlives its only consumer, and a config file
whose reader no longer exists is worse than no file at all: the next operator
reads `nodes:` and believes this machine verifies others after every ship.

  ~/.aos/config/fleet.yaml — the SSH node registry. On the reference machine it
  listed `local` and `faisal-mini`, both `auto_update: true`.

That second entry is the reason this file is retired rather than trimmed.
`faisal-mini` is another operator's Mac mini, running its own live AOS install
(v0.7.6, migration level 107, its own agents). It takes 0.7.7 like any other AOS
machine — on its own update cycle, decided on that machine. What must not exist
is a command here that reaches across SSH and applies an update to it from this
one, and `aos fleet update all` was exactly that: `check-update --apply` over
SSH, driven by this registry. Updating is a machine's own business, and this
file made it somebody else's.

The three nodes this registry would otherwise imply are installs — pi5, mbp,
imac — have no AOS install at all (probed 2026-09-13): no `~/aos` and no
`~/.aos` on pi5; `~/.aos/config` and nothing under it on mbp; a partial
`~/aos/core` with no git history and no `~/.aos` on imac. A registry of nodes
that cannot report a version is not a fleet; it is a list.

**Moved, never deleted**, following 120's precedent: the file goes to
~/.aos/backups/retired-config/fleet.yaml.<timestamp>. It is config the operator
wrote by hand, and restoring it is `mv` — but only if it still exists somewhere.
A migration that deletes the only copy of an operator's config is the one shape
of this change that cannot be undone.

Idempotent: check() passes once the file is gone from ~/.aos/config. A second
run finds nothing to move and says so. The archive directory is never swept, so
replaying after a manual restore archives the restored copy under a new
timestamp rather than clobbering the first one.
"""

from __future__ import annotations

DESCRIPTION = "Retire the fleet.yaml node registry (single-node cleanup)"

import time
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _config_dir() -> Path:
    return Path.home() / ".aos" / "config"


def _archive_dir() -> Path:
    return Path.home() / ".aos" / "backups" / "retired-config"


# Literal names. Never a glob over ~/.aos/config — that directory holds
# operator.yaml, accounts.yaml, channel, channel-update.yaml and every
# integration's settings, and a pattern loose enough to catch a typo'd filename
# is loose enough to catch one of those.
RETIRED_FILES = (
    "fleet.yaml",    # node registry for the deleted `aos fleet` CLI
)


def _present() -> list[Path]:
    config_dir = _config_dir()
    return [config_dir / n for n in RETIRED_FILES if (config_dir / n).exists()]


def check() -> bool:
    """Applied when the retired registry no longer sits in ~/.aos/config."""
    return not _present()


def up() -> bool:
    targets = _present()
    if not targets:
        print("  Nothing to retire — no fleet.yaml in ~/.aos/config")
        return True

    archive_dir = _archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    for src in targets:
        dest = archive_dir / f"{src.name}.{stamp}"
        # A same-second replay would otherwise overwrite the first archive.
        n = 1
        while dest.exists():
            dest = archive_dir / f"{src.name}.{stamp}-{n}"
            n += 1
        try:
            src.rename(dest)
        except OSError as e:
            # Cross-device or permission failure: keep the original. An
            # un-archived config beats a config that exists in neither place.
            print(f"  ! Could not archive {src.name} ({e}) — left in place")
            continue
        print(f"  ✓ {src.name} → {dest}")

    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 122 already applied" if check() else ("Done" if up() else "Failed"))
