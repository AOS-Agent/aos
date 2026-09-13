"""
Migration 133: remove the operator-side `.last-boot` directory mitigation for
issue #2356, now that the scheduler's boot dedupe is fixed.

Background: `check_reboot()` in `core/bin/internal/scheduler` deduped reboots
with an exact string match on `kern.boottime`, which NTP clock slew defeats —
the reported boot second oscillates across a second boundary, so the exact
match fails on almost every tick and the scheduler sends a false "System
rebooted" Telegram push forever (aos#2356). Until the framework fix shipped,
the operator's documented workaround (see the issue) was to replace
`~/.aos/logs/crons/.last-boot` — normally a plain state file holding the last
handled boot — with a *directory*. `LAST_BOOT_FILE.read_text()` then raises
`IsADirectoryError`, caught by check_reboot()'s own broad `except Exception`,
so the function aborts before writing to uptime.log or sending anything. A
`WHY-THIS-IS-A-DIRECTORY.txt` marker file was left inside documenting exactly
this and its own revert instructions (`rm -rf ~/.aos/logs/crons/.last-boot`).

The framework fix (tolerant dedupe with a persisted integer boot-second, the
"uptime < 10 min" guard actually enforced) makes the workaround unnecessary —
but it also makes it actively harmful: as long as the directory is there,
`check_reboot()` keeps hitting `IsADirectoryError` on every tick, silently, so
a genuinely new boot is never logged to uptime.log and never notified, on any
machine that applied the mitigation. This migration removes it so the fixed
code can resume writing an ordinary state file there.

Conservative by construction: only removes the directory when it IS a
directory AND carries the `WHY-THIS-IS-A-DIRECTORY.txt` marker this exact
mitigation is documented to leave inside it — never an arbitrary directory a
machine that never hit #2356 might have created at that path for some other
reason. A directory without the marker is reported and left alone; a machine
that never applied the mitigation (ordinary file, or nothing at all) is
already converged.

Idempotent: check() is True once `.last-boot` is not a marked directory.
Reversible: down() cannot safely recreate a mitigation whose whole point was
a deliberate, documented, manual operator action — returns False, same as
migration 131's own one-way archive step.
"""
from __future__ import annotations

import shutil
from pathlib import Path

DESCRIPTION = (
    "Remove the operator-side .last-boot directory mitigation for aos#2356 "
    "now that the scheduler's boot dedupe is fixed"
)

MARKER_NAME = "WHY-THIS-IS-A-DIRECTORY.txt"


# Resolved on every call, never captured at import — a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process (see
# migration 131's own note on this same pattern).
def _last_boot_file() -> Path:
    return Path.home() / ".aos" / "logs" / "crons" / ".last-boot"


def _is_marked_mitigation(path: Path) -> bool:
    return path.is_dir() and (path / MARKER_NAME).exists()


def check() -> bool:
    return not _is_marked_mitigation(_last_boot_file())


def up() -> bool:
    path = _last_boot_file()

    if not path.exists():
        print(f"  · {path} not found — nothing to revert")
        return True

    if not _is_marked_mitigation(path):
        if path.is_dir():
            print(f"  · {path} is a directory without the {MARKER_NAME} marker — "
                  "leaving it alone, not this mitigation")
        else:
            print(f"  · {path} is already an ordinary file — nothing to revert")
        return check()

    shutil.rmtree(path)
    print(f"  ✓ removed {path} (operator mitigation for aos#2356, no longer needed)")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 133 already applied" if check() else ("Done" if up() else "Failed"))
