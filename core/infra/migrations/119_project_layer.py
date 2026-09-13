"""
Migration 119: the project layer reaches the instance.

The framework now ships a `project` CLI, a zone-aware reconciler, a steward
check, a `~/project/CLAUDE.md` policy template and a global agent rule. None of
that does anything until the instance layer has received it, and the
atomic-migration rule says the bridge ships in the same diff as the change. This
is the bridge.

Four things, each idempotent:

1. **Zones** — `_ref/`, `_archive/`, `_scratch/`, each with a README, plus
   `~/project/CLAUDE.md` from the framework template. Only when `~/project/`
   already exists: a machine that has never had a projects directory does not
   get one created for it, and the first `project new` lazy-creates the whole
   structure through the same `ensure_zones()` this calls.
2. **The rule** — `~/.claude/rules/project-structure.md`, symlinked to the
   framework copy. `RuleSymlinkCheck` would do this on the next reconcile
   anyway; doing it here means the rule is live the moment the update lands
   rather than up to a cycle later.
3. **PATH** — `~/.local/bin/project`, the same symlink shape `install.sh`
   uses for `aos` and `cld`. `project` is a command the operator types, so it
   has to be typeable.
4. Nothing else.

BOTH SYMLINKS ARE *REPOINTED*, NOT MERELY CREATED
--------------------------------------------------

The first machine to run this feature ran it from a development worktree: the
rule and the CLI were hand-linked to
`~/project/aos/.claude/worktrees/feat-project-layer/...` so the work could be
used while it was still being written. That is the normal shape of pre-release
AOS work, and it leaves an instance whose two links point at a branch checkout
that `aos update` neither owns nor keeps. When the worktree is eventually
deleted the operator's `project` command dies with it, silently, with no failed
check to explain why.

So `_relink()` is a repoint, not a create. A link that already resolves to the
runtime path is left untouched (that is the idempotent case, and the second run
of `up()` takes it); a link pointing anywhere else — a worktree, an old release
directory, a dangling target — is replaced with one pointing at `~/aos/...`;
and a *real* file sitting at either path is renamed to `.pre-reconcile` rather
than removed, because it may be something the operator wrote by hand.

WHAT THIS MIGRATION DELIBERATELY DOES NOT DO
---------------------------------------------

It does not move, rename, git-init, adopt, or archive a single existing
directory, and that is a design decision rather than caution.

On this machine `~/project/` holds 37 directories: 14 with no version control
at all, a 16GB course archive, a 30GB OneDrive mirror, three empty husks left
over from a retired worktree convention, and seven clones of other people's
repositories. An automated pass over that would have to guess, for each one,
which of those it is — and the entire reason this layer exists is that
guessing produced the mess. The council was explicit: migration of existing
contents is an operator-supervised `adopt` triage session, never automated.

So the migration installs the tools and the policy, and the reconcile check
starts reporting what is unaccounted for on every run, forever. The operator
does the triage when they sit down to it.

`~/project/CLAUDE.md` is written only when absent. An operator who has extended
their policy file keeps their version.

WHEN ~/project IS THERE BUT NOT REACHABLE
------------------------------------------

On this machine `~/project` is a symlink onto an external volume. With that
volume unmounted the link dangles, and `Path.exists()` — which follows symlinks
— reports exactly what it reports for a machine that has never had a projects
directory at all. The two states want opposite answers: a fresh machine has
nothing to install into and this migration is genuinely complete, while an
unmounted volume has everything to install into and the migration has not run.
Answering "complete" for the second one is unrecoverable, because the runner
records the watermark and never offers the migration again.

So the symlink itself is checked, not just its target, and an unreachable root
makes `up()` return False. The runner's contract (`runner.py:112-133`) treats
anything other than True/None as a failure: the watermark stays put and the
migration is retried on the next update cycle, by which time the volume is
probably back.
"""

from __future__ import annotations

DESCRIPTION = "Project layer: zones, ~/project/CLAUDE.md, rule, project CLI on PATH"

import os
import sys
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _home() -> Path:
    return Path.home()


def _aos_dir() -> Path:
    return _home() / "aos"


def _project_root() -> Path:
    return _home() / "project"


RULE_NAME = "project-structure.md"


def _rule_source() -> Path:
    return _aos_dir() / ".claude" / "rules" / RULE_NAME


def _rule_link() -> Path:
    return _home() / ".claude" / "rules" / RULE_NAME


CLI_NAME = "project"


def _cli_source() -> Path:
    return _aos_dir() / "core" / "bin" / "cli" / CLI_NAME


def _cli_link() -> Path:
    return _home() / ".local" / "bin" / CLI_NAME


def _policy() -> Path:
    return _project_root() / "CLAUDE.md"


# Migration files are loaded by path (runner.py), not as part of the core
# package, so the import root goes on sys.path before importing the zone module.
# Zones are created through project_zones.ensure_zones() rather than by mkdir
# here: one definition of what a zone is, shared with `project new`, so the
# structure a migration produces and the structure the CLI produces cannot
# drift apart.
#
# This sys.path insertion is a one-time, import-time bootstrap — it runs once,
# right now, under whatever Path.home() is active at that moment (real or
# sandboxed), and is never consulted again afterward. That is a different
# shape from the frozen-constant bug this module is otherwise being fixed for:
# nothing here is read again later by check()/up() after a test's sandbox
# patch has expired.
_work_dir = _aos_dir() / "core" / "engine" / "work"
if str(_work_dir) not in sys.path:
    sys.path.insert(0, str(_work_dir))


def _zones():
    """Import the zone module, or None when the framework tree isn't there yet."""
    try:
        import project_zones
        return project_zones
    except Exception:
        return None


def _linked(link: Path, source: Path) -> bool:
    """True when `link` is a symlink already pointing at `source`."""
    if not link.is_symlink():
        return False
    try:
        return link.resolve() == source.resolve()
    except OSError:
        return False


def _backup_name(link: Path) -> Path:
    """A free `.pre-reconcile` name for `link`. Never reuses an occupied one.

    The obvious version deletes the old backup to make room. That breaks the
    repo-wide rule that nothing auto-deletes, and it breaks it on exactly the
    file the backup exists to protect: an operator who has already been through
    one relink still has their hand-written original sitting there, and a second
    pass must not be the thing that throws it away.
    """
    base = link.with_name(link.name + ".pre-reconcile")
    if not base.exists() and not base.is_symlink():
        return base
    n = 2
    while True:
        cand = link.with_name(f"{link.name}.pre-reconcile.{n}")
        if not cand.exists() and not cand.is_symlink():
            return cand
        n += 1


def _relink(link: Path, source: Path) -> str | None:
    """Point `link` at `source`. Backs up anything real that is in the way.

    Same shape as the symlink checks in core/infra/reconcile/checks/symlinks.py:
    a real file gets renamed to `.pre-reconcile` rather than deleted, because
    this may be a rule an operator wrote by hand and nothing here is entitled to
    throw that away.
    """
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


# ``present`` — a real directory to install into.
# ``absent``  — nothing there, and nothing claiming to be there. Correct on a
#               fresh machine; zones are deferred to the first `project new`.
# ``unreachable`` — something IS there (a symlink, or a non-directory) but it
#               cannot be written into. See the module docstring: this is the
#               unmounted-volume case, and it must not be mistaken for `absent`.
def _project_root_state() -> str:
    project_root = _project_root()
    if project_root.is_dir():
        return "present"
    if project_root.is_symlink() or project_root.exists():
        return "unreachable"
    return "absent"


def check() -> bool:
    """True when the instance already has everything this migration installs."""
    z = _zones()
    if z is None:
        return False

    state = _project_root_state()
    if state == "unreachable":
        return False            # nothing could have been installed there yet
    if state == "present":
        for d in z.zone_dirs(_project_root()).values():
            if not d.is_dir():
                return False
        if not _policy().exists():
            return False

    rule_source = _rule_source()
    if rule_source.exists() and not _linked(_rule_link(), rule_source):
        return False
    cli_source = _cli_source()
    if cli_source.exists() and not _linked(_cli_link(), cli_source):
        return False
    return True


def up() -> bool:
    z = _zones()
    if z is None:
        print("       ! project_zones is not importable — framework tree "
              "incomplete; nothing installed")
        return False

    done: list[str] = []
    incomplete: list[str] = []

    # 1. Zones + policy file. Only for a ~/project/ that already exists; a
    #    machine without one gets the whole structure from its first
    #    `project new`, through this same function.
    project_root = _project_root()
    policy = _policy()
    state = _project_root_state()
    if state == "present":
        created = z.ensure_zones(project_root)
        for c in created:
            done.append(f"created {Path(c).name}/")
        if not created:
            done.append("zones already present")
    elif state == "absent":
        done.append("no ~/project/ — zones deferred to the first `project new`")
    else:
        incomplete.append(
            f"{project_root} exists but cannot be written into — most likely a "
            f"symlink to a volume that is not mounted. Zones, {policy.name} and "
            f"the policy file were NOT installed. Mount the volume and re-run "
            f"`aos update` (this migration stays pending until it succeeds).")

    # 2. The global rule.
    if _relink(_rule_link(), _rule_source()):
        done.append(f"linked {RULE_NAME}")

    # 3. The CLI on PATH.
    cli_source = _cli_source()
    if cli_source.exists():
        try:
            cli_source.chmod(cli_source.stat().st_mode | 0o111)
        except OSError:
            pass
        if _relink(_cli_link(), cli_source):
            done.append(f"linked {CLI_NAME} into ~/.local/bin")

    for line in done:
        print(f"       - {line}")

    # Said out loud on every install, because the thing this migration most
    # needs the operator to know is what it chose not to touch.
    if state == "present":
        print("       - existing directories left exactly as they were. "
              "Run `project list` to see what is unaccounted for, then "
              "`project adopt` them one at a time.")

    for line in incomplete:
        print(f"       ! {line}")
    return not incomplete
