#!/usr/bin/env python3
"""Zones and on-disk lifecycle markers for ``~/project/``.

``~/project/`` is flat by decision, not by accident. The council of 2026-08-14
(``knowledge/decisions/2026-08-14-aos-project-layer-framework-level-design-of-projec-council.md``)
rejected domain nesting — ``quran/``, ``business/`` — because a directory can
belong to two domains and to neither, and every nesting scheme eventually
forces a directory to lie about itself. What survives is four zones, three of
them named with a leading underscore so they sort together and read as
infrastructure rather than as peers of real work::

    ~/project/
      <project>/     active work — git, and a .aos/project.yaml manifest
      _ref/          third-party clones, kept to read. Fetch, never commit.
      _archive/      finished work, moved here by `project archive`
      _scratch/      ephemeral. Nothing here is tracked, nothing is missed.

Location is itself a signal
---------------------------

This is the load-bearing idea, and it is why the council chose move-when-clean
over the cheaper flip-a-field-in-place. The felt deliverable is the operator's
``ls ~/project`` in the morning: a flag inside a YAML file nobody stats changes
a ledger, while moving the directory changes what they see. Agents and crons
that never read a manifest still cannot mistake ``_archive/old-thing`` for live
work, because they can see where it lives.

So the zone is not metadata about a project. The zone *is* the statement.

Lifecycle facts are markers, never manifest fields
---------------------------------------------------

``project_manifest.validate()`` rejects ``status``, ``state``, ``progress`` and
``last_activity`` outright, because a hand-maintained status field is accurate
on the day it is written and wrong a week later. That rule does not get a
carve-out for the lifecycle. ``archived`` and ``no-git`` are therefore *files*:

  * ``.aos/archived`` — written by ``project archive``. Records the date and a
    reason. Its presence is the claim; ``_archive/`` membership is the proof.
  * ``.aos/no-git`` — an operator deny marker. Says: this directory must never
    be put under version control. It exists because the skeptic lens made the
    point that nothing stops an agent from running ``git init`` inside a 26GB
    OneDrive mirror if the policy lives only in prose an agent never reads. A
    marker is statable by any code path in two lines, which prose is not.

A marker is a fact about the directory, checkable with ``exists()``, and it
cannot disagree with the tracker because the tracker does not hold a copy.

This module is primitives only
-------------------------------

Zone geometry, name rules, and marker read/write. It has no opinion about
work.db, no git calls, and no report of its own. ``project_lifecycle`` builds
the verbs on top; ``project_reconcile`` reads the same primitives so drift is
judged against exactly the definitions the CLI writes against — one definition,
two readers, no drift between them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover — yaml is a hard dep; defensive only
    yaml = None

HOME = Path.home()
PROJECT_ROOT = HOME / "project"

ZONE_REF = "_ref"
ZONE_ARCHIVE = "_archive"
ZONE_SCRATCH = "_scratch"

# Ordered for display, not by importance. `_data/` is deliberately absent: the
# council cut it because a dataset's membership would then be declared twice —
# once by where it sits and once by a manifest `depends_on` — and two competing
# signals for one fact is the shape that produces drift. Datasets live inside
# the ecosystem that owns them.
ZONES = (ZONE_REF, ZONE_ARCHIVE, ZONE_SCRATCH)

ZONE_READMES = {
    ZONE_REF: """\
# _ref — other people's code, kept to read

Clones of third-party repositories. They live here so they stop sitting as
peers of real work in the directory listing.

- **Fetch, never commit.** Nothing here is yours to change. If you need to
  modify one, fork it and make the fork a real project.
- Nothing here gets a manifest, and the reconciler will not ask for one.
- Delete freely — everything here is re-clonable from its remote.
""",
    ZONE_ARCHIVE: """\
# _archive — finished work

Projects that are done. Moved here by `project archive <name>`, which makes a
final commit, tags `archived/YYYY-MM`, writes a `.aos/archived` marker, and
updates the work tracker.

Nothing is ever deleted by archiving. A project here keeps its full git
history, and moving it back out is a `git mv` away.

Do not move directories in here by hand — the tracker's path column and any
registered worktrees would be left pointing at a directory that no longer
exists. Use the CLI, which fixes both.
""",
    ZONE_SCRATCH: """\
# _scratch — ephemeral

One-off experiments, throwaway clones, things you are about to delete.

Nothing here is tracked, nothing here is reported as drift, and nothing here
should be trusted to survive.

If something in this directory turns out to matter, promote it by MOVING it
out first, then adopting it:

    mv ~/project/_scratch/<name> ~/project/<name>
    project adopt <name>

Adopting it where it sits does not promote it. Zone membership is the
classification — `project list` skips zone contents and the reconciler
classifies everything under here as ephemeral by location alone — so a manifest
written inside `_scratch/` is real and permanently invisible.
""",
}

# A project directory name. Kebab-case, because every id that reaches work.db,
# a git branch or a QMD collection is kebab-case already, and one casing rule
# beats four.
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# The three worktree conventions found on this machine in the 2026-08-14
# survey were `aos-wt`, `dod-wt` and `quran-tools-wt`. Two were empty husks and
# the third held 19GB of live checkouts sitting as a peer of its own project.
# Worktrees belong at `<project>/.claude/worktrees/<branch-slug>`, so the suffix
# that named the old scheme is refused outright rather than deprecated quietly.
WORKTREE_SUFFIX = "-wt"

MARKER_DIR = ".aos"
ARCHIVED_MARKER = "archived"
NO_GIT_MARKER = "no-git"

# The policy file that loads automatically for any session under ~/project/.
# FRAMEWORK ships the template; the INSTANCE gets a copy it may then edit. Both
# migration 119 and the first `project new` install it through ``ensure_zones``,
# because a fresh machine has no ~/project/ when migrations run and the policy
# must not wait for the migration watermark to come round again — it never
# would.
POLICY_FILENAME = "CLAUDE.md"
POLICY_TEMPLATE = (Path(__file__).resolve().parents[3]
                   / "config" / "templates" / "project-root-CLAUDE.md")


# ── names ───────────────────────────────────────────────────────────

def validate_name(name: str) -> str | None:
    """Return a plain-English error, or ``None`` when the name is usable.

    Returns the error rather than raising so callers can print it next to the
    offending input. Every rule here exists because of a shape already on disk,
    not as general tidiness.
    """
    if not name:
        return "a project needs a name"
    if name.startswith("_"):
        return (f"'{name}' starts with '_', which is reserved for zones "
                f"({', '.join(ZONES)}). Pick a name that describes the work.")
    if name.endswith(WORKTREE_SUFFIX):
        return (f"'{name}' ends with '{WORKTREE_SUFFIX}', the old sibling-worktree "
                f"convention. Worktrees live at "
                f"<project>/.claude/worktrees/<branch-slug>, so this name would "
                f"describe something that is no longer a thing.")
    if not NAME_RE.match(name):
        return (f"'{name}' is not kebab-case. Use lowercase letters, digits and "
                f"single hyphens — e.g. 'quran-garden', not 'Quran_Garden'.")
    return None


# ── zone geometry ───────────────────────────────────────────────────

def zone_of(path: Path, root: Path | None = None) -> str | None:
    """Which zone ``path`` lives in, or ``None`` for an active top-level project.

    Answers by location alone — no manifest is read and no git command is run,
    because the whole point of the zones is that they are legible without
    consulting anything. A path outside ``root`` entirely also returns ``None``;
    callers that care about containment check that themselves.
    """
    base = root or PROJECT_ROOT
    try:
        rel = Path(path).resolve().relative_to(Path(base).resolve())
    except (ValueError, OSError):
        return None
    head = rel.parts[0] if rel.parts else ""
    return head if head in ZONES else None


def zone_dirs(root: Path | None = None) -> dict[str, Path]:
    """The three zone directories, whether or not they exist yet."""
    base = root or PROJECT_ROOT
    return {z: base / z for z in ZONES}


def ensure_zones(root: Path | None = None) -> list[Path]:
    """Create any missing zone directory, each with its README. Idempotent.

    Lazy by design: a fresh install has no ``~/project/`` at all, and shipping
    three empty directories to a machine that has never created a project would
    be the framework asserting a structure the operator has not started using.
    The first ``project new`` is the moment the structure becomes true, so that
    is the moment it appears.

    An existing README is never overwritten — the operator may have added their
    own notes to it, and this function has no way to tell edits from drift.
    """
    base = root or PROJECT_ROOT
    created: list[Path] = []
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        created.append(base)
    for zone, d in zone_dirs(base).items():
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)
        readme = d / "README.md"
        if not readme.exists():
            readme.write_text(ZONE_READMES[zone])
    policy = ensure_policy(base)
    if policy is not None:
        created.append(policy)
    return created


def ensure_policy(root: Path | None = None) -> Path | None:
    """Install ``~/project/CLAUDE.md`` from the framework template, if absent.

    Returns the path when it wrote one, ``None`` when there was already a file
    there or the template is missing.

    **Never overwrites.** The installed copy is the operator's — they are
    expected to add their own conventions to it, and this function has no way to
    tell an edit from drift. A framework that silently reverts the file its own
    policy invites people to extend would be teaching them not to trust it.
    Updated policy therefore ships as a new template that an operator adopts
    deliberately, not as a background overwrite.
    """
    base = root or PROJECT_ROOT
    target = base / POLICY_FILENAME
    if target.exists() or not POLICY_TEMPLATE.exists():
        return None
    base.mkdir(parents=True, exist_ok=True)
    target.write_text(POLICY_TEMPLATE.read_text())
    return target


# ── markers ─────────────────────────────────────────────────────────

@dataclass
class ArchivedMarker:
    """The contents of a ``.aos/archived`` file."""
    date: str
    reason: str | None = None
    entangled: bool = False     # written in place because a move was unsafe
    path: str | None = None     # where the marker was read from


def marker_path(directory: Path, marker: str) -> Path:
    return Path(directory) / MARKER_DIR / marker


def has_no_git_marker(directory: Path) -> bool:
    """True when this directory has declared itself off-limits to git.

    Checked by ``project new``/``project adopt`` before any ``git init``, and by
    the reconciler when deciding whether missing version control is a finding or
    a stated decision. The live case is a 26GB OneDrive mirror plus a 4.6GB mail
    archive: putting that under git would be actively harmful, and the marker is
    how that judgement survives contact with an agent that did not read the
    policy.
    """
    return marker_path(directory, NO_GIT_MARKER).exists()


def write_no_git_marker(directory: Path, reason: str | None = None) -> Path:
    """Declare that this directory must never be put under version control."""
    p = marker_path(directory, NO_GIT_MARKER)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# This directory must NEVER be put under version control.",
        "#",
        "# Checked by `project new`, `project adopt`, and the project-layer",
        "# reconcile check. Its presence is a deny, not a preference: some",
        "# directories are too large, too binary, or too full of other people's",
        "# data for git to be the right answer. Back them up instead.",
        "#",
        "# Delete this file to lift the restriction.",
        f"date: {date.today().isoformat()}",
    ]
    if reason:
        body.append(f"reason: {_yaml_scalar(reason)}")
    p.write_text("\n".join(body) + "\n")
    return p


def read_archived_marker(directory: Path) -> ArchivedMarker | None:
    """Read ``.aos/archived``, or ``None`` when the project is not archived.

    A malformed marker still counts as archived — the *existence* of the file is
    the claim, and refusing to acknowledge it because a field failed to parse
    would turn a formatting slip into a project silently rejoining live work.
    """
    p = marker_path(directory, ARCHIVED_MARKER)
    if not p.exists():
        return None
    raw: dict = {}
    if yaml is not None:
        try:
            loaded = yaml.safe_load(p.read_text())
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = {}
    return ArchivedMarker(
        date=str(raw.get("date") or ""),
        reason=raw.get("reason"),
        entangled=bool(raw.get("entangled")),
        path=str(p),
    )


def write_archived_marker(directory: Path, reason: str | None = None, *,
                          entangled: bool = False,
                          when: str | None = None) -> Path:
    """Record that this project is finished. Written by ``project archive``.

    ``entangled=True`` marks the flip-in-place path: the project could not be
    moved because something still points at its current location. The marker
    then carries the *only* signal, which is exactly why the CLI shouts about it
    — a claim with no corroborating location is the weak case the council
    accepted only as a fallback.
    """
    p = marker_path(directory, ARCHIVED_MARKER)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Archived by `project archive`. This project is finished.",
        "#",
        "# Lifecycle lives in markers, not in the manifest: project.yaml holds",
        "# declarations only, and its validator rejects status fields outright.",
        f"date: {when or date.today().isoformat()}",
    ]
    if reason:
        lines.append(f"reason: {_yaml_scalar(reason)}")
    if entangled:
        lines += [
            "# Flipped in place — NOT moved to _archive/. Something still points",
            "# at this path (a registered worktree, or open tasks). Until that is",
            "# cleared, this file is the only signal that the project is done.",
            "entangled: true",
        ]
    p.write_text("\n".join(lines) + "\n")
    return p


def _yaml_scalar(s: str) -> str:
    """Quote a value so a reason containing ``:`` cannot break the file."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
