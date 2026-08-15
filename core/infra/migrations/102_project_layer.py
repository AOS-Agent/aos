"""
Migration 102: the project layer reaches the instance.

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

HOME = Path.home()
AOS_DIR = HOME / "aos"
PROJECT_ROOT = HOME / "project"

RULE_NAME = "project-structure.md"
RULE_SOURCE = AOS_DIR / ".claude" / "rules" / RULE_NAME
RULE_LINK = HOME / ".claude" / "rules" / RULE_NAME

CLI_NAME = "project"
CLI_SOURCE = AOS_DIR / "core" / "bin" / "cli" / CLI_NAME
CLI_LINK = HOME / ".local" / "bin" / CLI_NAME

POLICY = PROJECT_ROOT / "CLAUDE.md"

# Migration files are loaded by path (runner.py), not as part of the core
# package, so the import root goes on sys.path before importing the zone module.
# Zones are created through project_zones.ensure_zones() rather than by mkdir
# here: one definition of what a zone is, shared with `project new`, so the
# structure a migration produces and the structure the CLI produces cannot
# drift apart.
_WORK_DIR = AOS_DIR / "core" / "engine" / "work"
if str(_WORK_DIR) not in sys.path:
    sys.path.insert(0, str(_WORK_DIR))


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
    if PROJECT_ROOT.is_dir():
        return "present"
    if PROJECT_ROOT.is_symlink() or PROJECT_ROOT.exists():
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
        for d in z.zone_dirs(PROJECT_ROOT).values():
            if not d.is_dir():
                return False
        if not POLICY.exists():
            return False

    if RULE_SOURCE.exists() and not _linked(RULE_LINK, RULE_SOURCE):
        return False
    if CLI_SOURCE.exists() and not _linked(CLI_LINK, CLI_SOURCE):
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
    state = _project_root_state()
    if state == "present":
        created = z.ensure_zones(PROJECT_ROOT)
        for c in created:
            done.append(f"created {Path(c).name}/")
        if not created:
            done.append("zones already present")
    elif state == "absent":
        done.append("no ~/project/ — zones deferred to the first `project new`")
    else:
        incomplete.append(
            f"{PROJECT_ROOT} exists but cannot be written into — most likely a "
            f"symlink to a volume that is not mounted. Zones, {POLICY.name} and "
            f"the policy file were NOT installed. Mount the volume and re-run "
            f"`aos update` (this migration stays pending until it succeeds).")

    # 2. The global rule.
    if _relink(RULE_LINK, RULE_SOURCE):
        done.append(f"linked {RULE_NAME}")

    # 3. The CLI on PATH.
    if CLI_SOURCE.exists():
        try:
            CLI_SOURCE.chmod(CLI_SOURCE.stat().st_mode | 0o111)
        except OSError:
            pass
        if _relink(CLI_LINK, CLI_SOURCE):
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
