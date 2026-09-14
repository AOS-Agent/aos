"""
Migration 135: per-profile Claude Code launchers on PATH (aos#244.2).

Part 2 of Claude profiles teaches `core/bin/cli/cld` to resolve a
CLAUDE_CONFIG_DIR from its own invoked name (argv[0]'s basename) or an
explicit `--profile <name>` flag. That resolution logic needs no launcher
beyond plain `cld` to exist — `cld --profile work` works today, on any
machine. `cld2`/`cld3` are pure convenience: a second (and third) command
someone can type without spelling out `--profile <name>` every time.

This migration is the bridge the atomic-migration rule requires for that
convenience: it puts `~/.local/bin/cld2` and `~/.local/bin/cld3` on PATH,
each symlinked to the exact same target `~/.local/bin/cld` already uses
today — `~/aos/core/bin/cld` (see `install.sh`'s `setup_path()`, which
symlinks `~/.local/bin/cld` there; `core/bin/cld` is itself a checked-in
symlink to `core/bin/cli/cld`, so this resolves to the real script two hops
in, exactly like the existing `cld` launcher does). A machine that installed
before this shipped gets the two extra names on its next `aos update`,
without re-running the installer.

WHAT THIS MIGRATION DELIBERATELY DOES NOT DO
---------------------------------------------

It does not create `~/.aos/claude-profiles/cld2` or `.../cld3`. A launcher
that resolves to a profile with no login is a one-line, actionable error —
`claude-profile add <name>` — not a silent auto-create (see `cld`'s own
`--profile` handling, and `claude-profile add`'s own refusal to be called
implicitly). Provisioning an account's login state is the operator's call,
made on purpose, once; this migration only makes the *name* typeable. The
operator still runs `aos claude-profile add cld2` themselves.

IDEMPOTENCY AND THE .pre-135 BACKUP
-------------------------------------

Same shape as migration 119's `_relink()`: a link already pointing at the
right target is left untouched; a symlink pointing anywhere else (a stale
worktree, a dangling target) is repointed; and a REAL file sitting at either
path — something the operator wrote or copied there by hand — is renamed to
`.pre-135` (never deleted) before the symlink goes in. A second collision at
that backup name gets `.pre-135.2`, and so on: nothing here ever overwrites
a previous backup.

`up()` also confirms `~/.local/bin/aos` still resolves after the two new
links go in — read-only, and reported rather than repaired. `aos` is
`install.sh`'s symlink to maintain, not this migration's, but a migration
that writes two new files into `~/.local/bin/` owes the operator a sanity
check that doing so didn't disturb the sibling that was already there.
"""

from __future__ import annotations

DESCRIPTION = "Claude profile launchers: cld2/cld3 on PATH (aos#244.2)"

import os
from pathlib import Path


# Resolved on every call, never captured at import — a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process. See
# core/infra/lib/default_off.py and migrations 123-134 for the same pattern.
def _home() -> Path:
    return Path.home()


def _local_bin() -> Path:
    return _home() / ".local" / "bin"


def _cld_source() -> Path:
    """The same target ~/.local/bin/cld already points at."""
    return _home() / "aos" / "core" / "bin" / "cld"


LAUNCHER_NAMES = ("cld2", "cld3")


def _cld2_link() -> Path:
    return _local_bin() / "cld2"


def _cld3_link() -> Path:
    return _local_bin() / "cld3"


def _launcher_link(name: str) -> Path:
    return _local_bin() / name


def _aos_link() -> Path:
    return _local_bin() / "aos"


def _linked(link: Path, source: Path) -> bool:
    """True when `link` is a symlink already pointing at `source`."""
    if not link.is_symlink():
        return False
    try:
        return link.resolve() == source.resolve()
    except OSError:
        return False


def _backup_name(link: Path) -> Path:
    """A free `.pre-135` name for `link`. Never reuses an occupied one —
    see migration 119's `_backup_name`, same reasoning: an operator who has
    already been through one relink still has their hand-written original
    sitting there, and a second pass must not be the thing that throws it
    away."""
    base = link.with_name(link.name + ".pre-135")
    if not base.exists() and not base.is_symlink():
        return base
    n = 2
    while True:
        cand = link.with_name(f"{link.name}.pre-135.{n}")
        if not cand.exists() and not cand.is_symlink():
            return cand
        n += 1


def _relink(link: Path, source: Path) -> str | None:
    """Point `link` at `source`. Backs up anything real that is in the way."""
    if not source.exists():
        return None
    link.parent.mkdir(parents=True, exist_ok=True)
    if _linked(link, source):
        return None
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        link.rename(_backup_name(link))
    os.symlink(source, link)
    return str(link)


def _aos_still_resolves() -> bool:
    """Read-only sanity check: did adding the new launchers disturb
    ~/.local/bin/aos? Never repaired here — install.sh owns that symlink;
    this migration only reports if it looks broken."""
    link = _aos_link()
    if not link.exists() and not link.is_symlink():
        return True  # never installed on this machine — not our problem
    return link.is_symlink() and link.resolve().exists()


def check() -> bool:
    source = _cld_source()
    if not source.exists():
        return False
    return all(_linked(_launcher_link(name), source) for name in LAUNCHER_NAMES)


def up() -> bool:
    source = _cld_source()
    if not source.exists():
        print("       ! ~/aos/core/bin/cld does not exist — framework tree "
              "incomplete; cld2/cld3 not installed")
        return False

    done: list[str] = []
    for name in LAUNCHER_NAMES:
        if _relink(_launcher_link(name), source):
            done.append(f"linked {name}")

    if done:
        for line in done:
            print(f"       - {line}")
    else:
        print("       - cld2/cld3 already linked")

    if not _aos_still_resolves():
        print("       ! ~/.local/bin/aos no longer resolves — not touched by "
              "this migration, but worth a look (install.sh owns that link)")

    print("       - profile directories are NOT created here — run "
          "`aos claude-profile add <name>` for each launcher you use")

    return True
