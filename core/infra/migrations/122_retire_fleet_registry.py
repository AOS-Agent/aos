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
(v0.7.6, migration level 107, its own agents, Qareen resident). `aos fleet
update all` would have pushed `check-update --apply` onto it over SSH, which is
exactly what must never happen — that machine takes an update only when its own
operator asks for one. The host scope guard in core/infra/lib/channels.py
already refuses the update on the machine itself; removing the registry removes
the other half, the command here that would have reached for it.

**The `allow-updates` override is NOT touched.** It is the companion to the host
scope guard, which this release keeps: it is how the owner of an excluded
machine opts their own machine back in. Retiring it would quietly take that
choice away from someone whose computer this is.

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

HOME = Path.home()
CONFIG_DIR = HOME / ".aos" / "config"
ARCHIVE_DIR = HOME / ".aos" / "backups" / "retired-config"

# Literal names. Never a glob over ~/.aos/config — that directory holds
# operator.yaml, accounts.yaml, update-policy.yaml, the allow-updates override
# and every integration's settings, and a pattern loose enough to catch a typo'd
# filename is loose enough to catch one of those.
RETIRED_FILES = (
    "fleet.yaml",    # node registry for the deleted `aos fleet` CLI
)


def _present() -> list[Path]:
    return [CONFIG_DIR / n for n in RETIRED_FILES if (CONFIG_DIR / n).exists()]


def check() -> bool:
    """Applied when the retired registry no longer sits in ~/.aos/config."""
    return not _present()


def up() -> bool:
    targets = _present()
    if not targets:
        print("  Nothing to retire — no fleet.yaml in ~/.aos/config")
        return True

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    for src in targets:
        dest = ARCHIVE_DIR / f"{src.name}.{stamp}"
        # A same-second replay would otherwise overwrite the first archive.
        n = 1
        while dest.exists():
            dest = ARCHIVE_DIR / f"{src.name}.{stamp}-{n}"
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
