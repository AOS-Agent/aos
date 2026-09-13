"""
Migration 122: retire the fleet node registry and the host-scope override.

The single-node cleanup deleted `core/bin/cli/fleet` and the `aos fleet`
subcommand. Two instance files outlive their only consumer, and a config file
whose reader no longer exists is worse than no file at all: the next operator
reads `nodes:` and believes this machine verifies three others after every ship.

  a. ~/.aos/config/fleet.yaml — the SSH node registry. On the reference machine
     it listed `local` and `faisal-mini`. Nothing reads it any more.

  b. ~/.aos/config/allow-updates — the host-scope override marker. It meant
     "this excluded machine may update itself after all". With no exclusion
     list there is nothing to override, so the file is now a statement about a
     gate that does not exist. Absent on the reference machine; handled anyway,
     because a machine that has one is exactly the machine that would be
     confused by it.

Why the fleet registry is retired rather than kept "in case": probed
2026-09-13, none of the three other nodes on this tailnet has an AOS install
that could be verified. pi5 has no ~/aos and no ~/.aos. mbp has ~/.aos/config
and nothing else — no framework, no LaunchAgents. imac has a partial ~/aos/core
with no git history and no ~/.aos at all. A registry of nodes that cannot
report a version is not a fleet; it is a list.

**Moved, never deleted**, following 120's precedent: each file goes to
~/.aos/backups/retired-config/<name>.<timestamp>. The content is two lines of
YAML the operator wrote by hand, and restoring it is `mv` — but only if it
still exists somewhere. A migration that deletes the only copy of an operator's
config is the one shape of this change that cannot be undone.

Idempotent: check() passes once neither file is in ~/.aos/config. A second run
finds nothing to move and says so. The archive directory is never swept, so
replaying after a manual restore archives the restored copy under a new
timestamp rather than clobbering the first one.
"""

from __future__ import annotations

DESCRIPTION = "Retire fleet.yaml and the allow-updates override (single-node cleanup)"

import time
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".aos" / "config"
ARCHIVE_DIR = HOME / ".aos" / "backups" / "retired-config"

# Literal names. Never a glob over ~/.aos/config — that directory holds
# operator.yaml, accounts.yaml and every integration's settings, and a pattern
# loose enough to catch a typo'd filename is loose enough to catch one of those.
RETIRED_FILES = (
    "fleet.yaml",       # node registry for the deleted `aos fleet` CLI
    "allow-updates",    # override marker for the deleted host-scope guard
)


def _present() -> list[Path]:
    return [CONFIG_DIR / n for n in RETIRED_FILES if (CONFIG_DIR / n).exists()]


def check() -> bool:
    """Applied when neither retired file remains in ~/.aos/config."""
    return not _present()


def up() -> bool:
    targets = _present()
    if not targets:
        print("  Nothing to retire — neither file is present")
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
