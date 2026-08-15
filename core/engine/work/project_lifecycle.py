#!/usr/bin/env python3
"""Project lifecycle — the four verbs behind the ``project`` CLI.

Everything else in this package reports. ``project_reconcile`` classifies and
refuses to correct; ``project_manifest`` plans and makes you call
``write_manifest()`` yourself. This module is the exception: it is the one place
that creates directories, runs ``git init``, moves projects and writes to
work.db. The council made that concentration deliberate — "the CLI is the sole
sanctioned mutation interface" — so there is exactly one file to read when you
want to know what is allowed to change ``~/project/``.

The four verbs
--------------

``create``   a new project: directory, git repo, manifest, README, first commit.
``adopt``    a manifest for a directory that already exists.
``survey``   what is actually there, grouped by zone, with drift called out.
``archive``  a finished project, moved to ``_archive/`` when that is safe.

Why archive is two paths and not one
-------------------------------------

The obvious implementation is a field flip: set ``archived: true`` somewhere and
be done in twenty lines. The council rejected it as the *only* path, and the
argument is worth keeping next to the code, because the cheap version looks
correct right up until you use it.

A flip changes a ledger nobody opens. ``ls ~/project`` still shows the same
peers, agents still see a live directory, and crons still walk it. The signal
exists but nothing consults it. Moving the directory changes the thing the
operator and every tool actually look at — location — which is why
``move-when-clean`` is the default.

But a move is not free, and the builder lens priced the breakage exactly:

  * **registered git worktrees store absolute paths.** Move the main checkout
    and every worktree's ``.git`` file points at a directory that is gone.
  * **work.db's ``projects.path`` column goes stale**, so ``detect_project_from_cwd``
    stops resolving and new tasks land unscoped.
  * **QMD keeps serving the old path** until it is reindexed.

Two of those this module fixes itself: it updates ``projects.path`` in the same
operation as the move (it is the *only* writer allowed to touch both a manifest
and that column) and it fires ``qmd update`` afterwards. The third — registered
worktrees — it cannot fix, because rewriting another checkout's git plumbing
from here is exactly the kind of clever repair that leaves a machine in a state
nobody can reason about. So worktrees become an *eligibility* question instead:
if any are registered, the project is entangled, the move does not happen, and
the marker is written in place with a loud, specific warning naming the paths
that blocked it.

Nothing is ever deleted. Not by archive, not by adopt, not by any path here.

What this module still refuses to do
-------------------------------------

It will not ``git init`` a directory carrying a ``.aos/no-git`` marker, at any
call site, under any flag. It will not move a directory out of ``_archive/``.
It will not invent a work.db project unless asked with ``--work``: a manifest is
the identity, and a tracker row is a separate decision the operator makes when
they actually have tasks to put in it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_zones as zones  # noqa: E402

try:
    import project_manifest as pm
except Exception:  # pragma: no cover — the CLI reports this as a hard error
    pm = None

try:
    import backend as engine
except ImportError:  # pragma: no cover — work.db is optional for most verbs
    engine = None

HOME = Path.home()
PROJECT_ROOT = zones.PROJECT_ROOT

# Best-effort reindex after a move. Absent on a machine that never installed
# QMD, which is not an error — the search index simply stays as stale as it was.
QMD_BIN = HOME / ".bun" / "bin" / "qmd"

ARCHIVE_COMMIT_MESSAGE = "archive: final state"

README_STUB = """\
# {title}

{description}

## Status

Tracked by AOS. Declarations live in `.aos/project.yaml`; state is derived, not
written down here — see `work projects` for what is actually happening.
"""


# ── results ─────────────────────────────────────────────────────────

@dataclass
class Outcome:
    """What a verb did, in a shape the CLI can print and a test can assert.

    Verbs return this rather than raising, because half of what they have to
    communicate is not failure — it is "done, and here are three things you
    should know". An exception can only carry the first kind.
    """
    ok: bool
    action: str
    path: str | None = None
    steps: list[str] = field(default_factory=list)      # what was done
    warnings: list[str] = field(default_factory=list)   # what to look at
    errors: list[str] = field(default_factory=list)     # why it stopped

    @classmethod
    def failed(cls, action: str, *errors: str) -> "Outcome":
        return cls(ok=False, action=action, errors=list(errors))


@dataclass
class Entanglement:
    """What is still holding a project in place, blocking a clean archive.

    Both fields are *citable*: worktree paths you can ``ls``, task ids you can
    ``work show``. A warning that says "this project is entangled" and stops
    there is a warning the operator cannot act on, so the eligibility check
    carries the specifics or it is not worth running.
    """
    worktrees: list[str] = field(default_factory=list)
    open_tasks: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.worktrees and not self.open_tasks


@dataclass
class SurveyRow:
    """One active project as ``project list`` shows it."""
    name: str
    project_id: str | None
    has_manifest: bool
    is_git: bool
    branch: str | None
    dirty: bool
    archived: bool
    no_git_marker: bool
    manifest_invalid: list[str] = field(default_factory=list)


@dataclass
class Survey:
    rows: list[SurveyRow] = field(default_factory=list)
    zone_counts: dict[str, int] = field(default_factory=dict)
    zones_present: dict[str, bool] = field(default_factory=dict)
    drift: list[str] = field(default_factory=list)
    root_exists: bool = True


# ── git plumbing ────────────────────────────────────────────────────

def _git(cwd: Path, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run git and hand back the whole truth: code, stdout, stderr.

    Unlike the reporting modules' ``_git`` — which collapses failure to ``None``
    because a reporter only needs the answer — a mutating caller needs the
    reason. "git init failed" without stderr is an unactionable message.
    """
    try:
        out = subprocess.run(("git", "-C", str(cwd), *args),
                             capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return 1, "", str(e)
    return out.returncode, out.stdout.strip(), out.stderr.strip()


def is_git_repo(directory: Path) -> bool:
    return (Path(directory) / ".git").exists()


def is_dirty(directory: Path) -> bool:
    """True when the working tree has uncommitted changes (tracked or not).

    Delegates to ``project_reconcile`` so archive eligibility and the drift
    report can never disagree about the same tree — a safety check and a
    warning that use different definitions of "dirty" is a bug waiting for the
    day they diverge.

    The local fallback exists because this is a *mutation* path: an archive must
    not lose its final-commit step because a reporting module failed to import.
    """
    if not is_git_repo(directory):
        return False
    try:
        import project_reconcile
        return project_reconcile.is_dirty(Path(directory))
    except Exception:
        code, out, _err = _git(Path(directory), "status", "--porcelain")
        return code == 0 and bool(out.strip())


def _branch(directory: Path) -> str | None:
    code, out, _ = _git(Path(directory), "rev-parse", "--abbrev-ref", "HEAD")
    return out or None if code == 0 else None


def _registered_worktrees(repo: Path) -> list[str]:
    """Worktree paths git has registered for this repo, excluding the main one.

    Asked of git rather than inferred from directory names, for the reason
    ``project_reconcile`` documents at length: the ``-wt`` naming convention
    drifted, and on this machine the directories carrying that suffix were empty
    husks while the real worktrees were named after their branches.
    """
    if not is_git_repo(repo):
        return []
    try:
        import project_worktrees as pw
        rows = pw.list_worktrees(Path(repo))
    except Exception:
        return []
    main = str(Path(repo).resolve())
    out: list[str] = []
    for row in rows:
        wt = row.get("worktree")
        if not wt:
            continue
        try:
            resolved = str(Path(wt).resolve())
        except Exception:
            resolved = wt
        if resolved != main:
            out.append(wt)
    return out


# ── work.db lookups ─────────────────────────────────────────────────

def _live_projects() -> list[dict]:
    if engine is None:
        return []
    try:
        return [p for p in engine.load_all().get("projects", [])
                if p.get("status") not in ("cancelled", "archived")]
    except Exception:
        return []


def _project_for_directory(directory: Path) -> dict | None:
    """The live work project whose ``path`` resolves to this directory.

    Resolved comparison, never string equality: ``~/project`` is a symlink onto
    the AOS-X volume, and the live records spell the same directory both ways.
    """
    try:
        target = Path(directory).resolve()
    except OSError:
        return None
    for p in _live_projects():
        if not p.get("path"):
            continue
        try:
            if Path(p["path"]).expanduser().resolve() == target:
                return p
        except OSError:
            continue
    return None


def _writable_project(manifest, directory: Path) -> dict | None:
    """The live work project this archive is allowed to rewrite, if any.

    A manifest id is a *claim*, and archiving must not act on it unverified. If
    a directory's manifest says ``id: hre`` while the live ``hre`` project points
    somewhere else entirely, writing the archive path into that project's row
    would relocate a project that was never archived — a one-line convenience
    that silently corrupts the tracker.

    So the write is permitted in exactly two cases: the project's ``path``
    already resolves to this directory (checked by the caller), or the project
    has no path at all — the normal state straight after adoption, where the
    manifest is the only thing that knows where the project lives. Anything
    else is left alone and reported.
    """
    if manifest is None or engine is None:
        return None
    for p in _live_projects():
        if p["id"] != manifest.id:
            continue
        if not p.get("path"):
            return p
        try:
            if Path(p["path"]).expanduser().resolve() == Path(directory).resolve():
                return p
        except OSError:
            pass
        return None
    return None


def _open_tasks(project_id: str) -> list[str]:
    """Open task ids + titles for a project. Empty when work.db is unavailable."""
    if engine is None or not project_id:
        return []
    try:
        tasks = engine.get_all_tasks()
    except Exception:
        return []
    out = []
    for t in tasks:
        if t.get("project") != project_id:
            continue
        if t.get("status") in ("done", "cancelled"):
            continue
        out.append(f"{t.get('id')} {t.get('title', '')}".strip())
    return out


# ── create ──────────────────────────────────────────────────────────

def create(name: str, *, title: str | None = None, kind: str = "mixed",
           description: str | None = None, root: Path | None = None,
           git: bool = True, register_work: bool = False) -> Outcome:
    """Create a new project: directory, git repo, manifest, README, first commit.

    Zones are created here rather than at install time. A fresh machine has no
    ``~/project/`` and no opinion about it yet; shipping three empty zone
    directories to it would be the framework asserting a structure the operator
    has not begun to use. The first project is the moment the structure becomes
    true.

    ``register_work`` is off by default. The manifest is the identity — that is
    the whole point of the manifest — and a tracker row is a separate decision
    for when there are actually tasks to put in it. Creating both by default
    would recreate the divergence class this workstream exists to close.
    """
    if pm is None:
        return Outcome.failed("new", "project_manifest is unavailable — cannot "
                                     "write a manifest, so nothing was created")

    err = zones.validate_name(name)
    if err:
        return Outcome.failed("new", err)

    base = Path(root) if root else PROJECT_ROOT
    directory = base / name
    if directory.exists():
        return Outcome.failed(
            "new", f"{directory} already exists. To bring an existing directory "
                   f"into the layer, use `project adopt {name}` instead.")

    out = Outcome(ok=True, action="new", path=str(directory))
    for created in zones.ensure_zones(base):
        out.steps.append(f"created zone {created}")

    directory.mkdir(parents=True)
    out.steps.append(f"created {directory}")

    if git:
        code, _stdout, stderr = _git(directory, "init")
        if code != 0:
            out.warnings.append(f"git init failed ({stderr}) — the project "
                                f"exists but is unversioned")
            git = False
        else:
            out.steps.append("git init")
    else:
        zones.write_no_git_marker(
            directory, "created with --no-git; version control declined at creation")
        out.steps.append("wrote .aos/no-git marker (this directory will never "
                         "be git-initialised by AOS)")

    manifest = pm.Manifest(id=name, kind=kind, title=title or name,
                           description=description)
    rendered = pm.render_manifest_yaml(manifest)
    errors = pm.validate(_reparse(rendered))
    if errors:
        # The renderer and the validator are the same module's two halves; if
        # they disagree, that is a framework bug and the operator should see it
        # rather than inherit a manifest that will fail every future load.
        out.warnings.append("generated manifest does not validate: "
                            + "; ".join(errors))
    mpath = pm.manifest_path_for(directory)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(rendered)
    out.steps.append(f"wrote {mpath.relative_to(directory)}")

    readme = directory / "README.md"
    readme.write_text(README_STUB.format(
        title=title or name,
        description=description or "One line about what this project is."))
    out.steps.append("wrote README.md")

    if git:
        _git(directory, "add", "-A")
        code, _stdout, stderr = _git(
            directory, "commit", "-m", f"init: {name}")
        if code == 0:
            out.steps.append("initial commit")
        else:
            out.warnings.append(
                f"initial commit failed ({stderr or 'unknown'}) — the files are "
                f"on disk and staged; commit them yourself")

    if register_work:
        out.steps.extend(_register(name, title or name, str(directory),
                                   out.warnings))
    else:
        out.steps.append("not registered in work.db (pass --work to register) — "
                         "the manifest is the identity; a tracker row is a "
                         "separate decision")
    return out


def _reparse(rendered: str) -> dict:
    """Parse rendered YAML back to a dict so ``validate()`` can grade it."""
    try:
        import yaml
        return yaml.safe_load(rendered) or {}
    except Exception:
        return {}


def _register(project_id: str, title: str, path: str,
              warnings: list[str]) -> list[str]:
    """Create the work.db row for a new project. Additive, never destructive."""
    if engine is None:
        warnings.append("work engine unavailable — project not registered")
        return []
    try:
        engine.add_project(title, project_id=project_id)
        engine.update_project(project_id, path=path)
        return [f"registered work project '{project_id}' with path"]
    except Exception as e:
        warnings.append(f"work.db registration failed ({e}) — the project "
                        f"exists on disk; register it with `work projects create`")
        return []


# ── adopt ───────────────────────────────────────────────────────────

def adopt(target: str | Path, *, root: Path | None = None,
          git: bool | None = None) -> Outcome:
    """Write a manifest for a directory that already exists.

    Adoption, not scaffolding. Where the directory belongs to a live work
    project, the plan comes from ``project_manifest.plan_adoption`` so the
    nested repos, vault docs and the ``appetite``/``initiative`` fields crammed
    into the description string are all recovered rather than retyped. Where it
    does not, the plan is built from the directory itself.

    ``git`` is tri-state on purpose:

      ``None``   decide nothing. An unversioned directory stays unversioned and
                 the outcome says so — the default must not quietly put 16GB of
                 someone's business archive under version control.
      ``True``   run ``git init`` — unless ``.aos/no-git`` says never, which
                 wins over the flag. A deny marker that a flag can override is
                 not a deny marker.
      ``False``  write the ``.aos/no-git`` marker, recording the decision where
                 the next agent will actually look for it.
    """
    if pm is None:
        return Outcome.failed("adopt", "project_manifest is unavailable")

    base = Path(root) if root else PROJECT_ROOT
    directory = _resolve_target(target, base)
    if not directory.exists():
        return Outcome.failed("adopt", f"{directory} does not exist")
    if not directory.is_dir():
        return Outcome.failed("adopt", f"{directory} is not a directory")

    out = Outcome(ok=True, action="adopt", path=str(directory))

    zone = zones.zone_of(directory, base)
    if zone == zones.ZONE_REF:
        out.warnings.append(
            f"{directory.name} is in {zones.ZONE_REF}/ — reference clones do not "
            f"get manifests. If this is really your work, move it up a level "
            f"first, then adopt it.")
        out.ok = False
        return out
    if zone == zones.ZONE_ARCHIVE:
        out.warnings.append(f"{directory.name} is in {zones.ZONE_ARCHIVE}/ — "
                            f"adopting an archived project is unusual; check "
                            f"you meant this")

    # git handling comes before the manifest: if a directory is about to be
    # version-controlled, the manifest should land inside the repo, not beside
    # one that does not exist yet.
    out.steps.extend(_settle_git(directory, git, out.warnings))

    existing = pm.manifest_path_for(directory)
    plan = _plan_for(directory)
    if plan is None:
        return Outcome.failed("adopt", f"could not build a manifest plan for "
                                       f"{directory}")

    out.warnings.extend(plan.warnings)
    for f in plan.findings:
        out.steps.append(f"found: {f}")

    if plan.action == "unchanged":
        out.steps.append(f"{existing} is already up to date — nothing written")
        return out

    written = pm.write_manifest(plan)
    out.steps.append(f"{'updated' if plan.action == 'update' else 'wrote'} "
                     f"{written}")
    if is_git_repo(directory):
        out.warnings.append("review the manifest before committing it — it goes "
                            "to whatever remote this repo has")
    return out


def _resolve_target(target: str | Path, base: Path) -> Path:
    """Accept a bare name, a zone-relative path, or an absolute path."""
    p = Path(target).expanduser()
    if p.is_absolute():
        return p
    return (base / p)


def _plan_for(directory: Path):
    """The adoption plan for a directory, from work.db when it knows about it."""
    project = _project_for_directory(directory)
    if project is not None:
        plans = pm.plan_adoption(project["id"])
        for pl in plans:
            if pl.manifest_path:
                return pl
    return pm.plan_adoption_for_directory(directory)


def _settle_git(directory: Path, git: bool | None,
                warnings: list[str]) -> list[str]:
    """Apply the caller's git intent, with the deny marker outranking it."""
    steps: list[str] = []
    marked = zones.has_no_git_marker(directory)
    versioned = is_git_repo(directory)

    if git is False:
        if versioned:
            warnings.append(
                f"{directory.name} is already a git repo, so --no-git cannot "
                f"un-version it. Marker written anyway to record the intent; "
                f"removing the existing .git is your call, not this tool's.")
        if not marked:
            zones.write_no_git_marker(
                directory, "adopted with --no-git; too large or too private for git")
            steps.append("wrote .aos/no-git marker")
        else:
            steps.append(".aos/no-git marker already present")
        return steps

    if marked:
        if git is True:
            warnings.append(
                f"REFUSED to git init: {directory.name} carries a .aos/no-git "
                f"marker. The marker outranks the flag — that is what makes it a "
                f"deny rather than a default. Delete "
                f"{zones.marker_path(directory, zones.NO_GIT_MARKER)} if the "
                f"decision has genuinely changed.")
        else:
            steps.append(".aos/no-git marker present — left unversioned, as declared")
        return steps

    if versioned:
        steps.append("already a git repo")
        return steps

    if git is True:
        code, _stdout, stderr = _git(directory, "init")
        if code == 0:
            steps.append("git init")
        else:
            warnings.append(f"git init failed: {stderr}")
        return steps

    warnings.append(
        f"{directory.name} has no version control and no .aos/no-git marker, so "
        f"nothing records whether that is a decision or an oversight. Re-run "
        f"with --git to version it, or --no-git to declare it off-limits.")
    return steps


# ── survey (`project list`) ─────────────────────────────────────────

def survey(root: Path | None = None) -> Survey:
    """What is actually under ``~/project/``, grouped by zone, drift called out.

    Zone counts and rows are read straight off the filesystem rather than taken
    from the reconciler, so the listing stays honest on a machine where work.db
    is unavailable — the layout is a property of the disk, and a listing that
    cannot be produced without a database would be a listing of the wrong thing.

    **Drift is not.** That comes from ``project_reconcile._detect_drift``, the
    same call the steward check makes, because this used to be a second
    implementation of the same rules and two implementations of "what counts as
    drift" is how ``project list`` and the health check end up telling the
    operator different things about the same directory. If the reconciler cannot
    be imported the drift list is empty and says so — a listing is still useful
    without it, and inventing a fallback would recreate the divergence.
    """
    base = Path(root) if root else PROJECT_ROOT
    s = Survey(root_exists=base.exists())
    if not s.root_exists:
        return s

    for zone, d in zones.zone_dirs(base).items():
        s.zones_present[zone] = d.exists()
        s.zone_counts[zone] = len(
            [c for c in d.iterdir() if c.is_dir()]) if d.is_dir() else 0

    by_dir: dict[str, str] = {}
    for p in _live_projects():
        if p.get("path"):
            try:
                by_dir[str(Path(p["path"]).expanduser().resolve())] = p["id"]
            except OSError:
                continue

    for child in sorted((c for c in base.iterdir()
                         if c.is_dir() and not c.name.startswith(".")),
                        key=lambda p: p.name.lower()):
        if child.name in zones.ZONES:
            continue
        manifest, errs = pm.load_manifest(child) if pm else (None, [])
        try:
            resolved = str(child.resolve())
        except OSError:
            resolved = str(child)
        s.rows.append(SurveyRow(
            name=child.name,
            project_id=(manifest.id if manifest else by_dir.get(resolved)),
            has_manifest=manifest is not None,
            is_git=is_git_repo(child),
            branch=_branch(child) if is_git_repo(child) else None,
            dirty=is_dirty(child),
            archived=zones.read_archived_marker(child) is not None,
            no_git_marker=zones.has_no_git_marker(child),
            manifest_invalid=list(errs),
        ))

    s.drift = _drift_lines(base)
    return s


def _drift_lines(base: Path) -> list[str]:
    """The reconciler's typed drift rows, rendered one per line for the CLI."""
    try:
        import project_reconcile
        report = project_reconcile.reconcile(drift_only=True, root=base)
    except Exception as e:
        return [f"drift could not be evaluated ({e}) — the listing above is "
                f"still accurate; run `work projects reconcile` for detail"]
    return [f"{d.name}: {d.evidence}  [{d.kind}]" for d in report.drift]


# ── archive ─────────────────────────────────────────────────────────

def eligibility(directory: Path, project_id: str | None = None) -> Entanglement:
    """What still holds this project in place. Empty means the move is safe.

    Two blockers, both from the council's list of things a move silently breaks
    and this module cannot repair:

      * **registered git worktrees** store the main checkout's absolute path in
        their ``.git`` file. Moving the main checkout orphans every one of them.
      * **open tasks** mean the project is not finished, whatever the operator
        typed. Archiving live work is not a filesystem operation gone wrong; it
        is the wrong operation.

    work.db being unavailable yields no task blockers, which is the correct
    failure direction only because the worktree check still runs and the CLI
    reports which checks it was able to make.
    """
    e = Entanglement()
    e.worktrees = _registered_worktrees(Path(directory))
    if project_id:
        e.open_tasks = _open_tasks(project_id)
    return e


def archive(name: str, *, reason: str | None = None, root: Path | None = None,
            run_qmd: bool = True) -> Outcome:
    """Archive a finished project: move it when clean, flip it when entangled.

    Never deletes, never forces. When entanglement blocks the move, the marker
    is still written — the operator's judgement that the work is done is
    recorded either way — but the directory stays exactly where it is and the
    outcome carries the specifics of what blocked it.
    """
    base = Path(root) if root else PROJECT_ROOT
    directory = _resolve_target(name, base)
    if not directory.exists():
        return Outcome.failed("archive", f"{directory} does not exist")
    if zones.zone_of(directory, base) == zones.ZONE_ARCHIVE:
        return Outcome.failed(
            "archive", f"{directory} is already in {zones.ZONE_ARCHIVE}/")

    out = Outcome(ok=True, action="archive", path=str(directory))

    project = _project_for_directory(directory)
    manifest, _errs = pm.load_manifest(directory) if pm else (None, [])
    project_id = (project or {}).get("id") or (manifest.id if manifest else None)
    db_project = project or _writable_project(manifest, directory)

    ent = eligibility(directory, project_id)
    if engine is None:
        out.warnings.append("work engine unavailable — open tasks could not be "
                            "checked, so eligibility rests on worktrees alone")

    # The marker goes down before the commit so it lands *in* the final commit
    # rather than dangling as the one uncommitted change in an archived repo.
    zones.write_archived_marker(directory, reason, entangled=not ent.clean)
    out.steps.append("wrote .aos/archived marker")

    if not ent.clean:
        out.ok = True
        out.action = "archive-in-place"
        out.warnings.append(_entangled_warning(directory, ent))
        return out

    if is_git_repo(directory):
        if is_dirty(directory):
            _git(directory, "add", "-A")
            code, _stdout, stderr = _git(directory, "commit", "-m",
                                         ARCHIVE_COMMIT_MESSAGE)
            if code == 0:
                out.steps.append(f"committed working tree ({ARCHIVE_COMMIT_MESSAGE!r})")
            else:
                out.warnings.append(f"final commit failed: {stderr}")
        tag = f"archived/{date.today().strftime('%Y-%m')}"
        code, _stdout, stderr = _git(directory, "tag", "-f", tag)
        if code == 0:
            out.steps.append(f"tagged {tag}")
        else:
            out.warnings.append(f"tagging {tag} failed: {stderr}")
    else:
        out.steps.append("not a git repo — no final commit or tag")

    zones.ensure_zones(base)
    destination = base / zones.ZONE_ARCHIVE / directory.name
    if destination.exists():
        out.warnings.append(
            f"{destination} already exists — the move was skipped and the "
            f"project stays put. Rename one of them and re-run.")
        out.action = "archive-in-place"
        return out

    try:
        _move(directory, destination)
    except Exception as e:
        out.ok = False
        out.errors.append(f"move failed ({e}) — the marker is written but the "
                          f"project is still at {directory}")
        return out
    out.steps.append(f"moved to {destination}")
    out.path = str(destination)

    if db_project is not None and engine is not None:
        pid = db_project["id"]
        try:
            engine.update_project(pid, path=str(destination), status="archived")
            out.steps.append(f"work.db: '{pid}' path updated, status archived")
        except Exception as e:
            out.warnings.append(
                f"work.db update failed ({e}) — projects.path still points at "
                f"{directory}, which no longer exists. Fix with "
                f"`work projects path {pid} {destination}`")
    elif project_id and engine is None:
        out.warnings.append(f"work engine unavailable — '{project_id}'.path may "
                            f"still point at the old location")
    elif project_id:
        out.steps.append("no live work project owns this directory — work.db "
                         "left untouched")

    if run_qmd:
        out.steps.append(_reindex())
    return out


def _entangled_warning(directory: Path, ent: Entanglement) -> str:
    """The loud warning. Names exactly what blocked the move, path by path."""
    lines = [
        f"NOT MOVED — {directory.name} is entangled.",
        "",
        "It is marked archived, but it stays where it is because moving it "
        "would break the things below. Until they are cleared, the marker is "
        "the only signal that this project is finished, and nothing that walks "
        "~/project/ will notice it.",
        "",
    ]
    if ent.worktrees:
        lines.append(f"  {len(ent.worktrees)} registered git worktree(s) — each "
                     f"stores this directory's absolute path:")
        lines += [f"    {w}" for w in ent.worktrees]
        lines.append("    clear with: git worktree remove <path>  (or `git worktree prune`)")
    if ent.open_tasks:
        lines.append(f"  {len(ent.open_tasks)} open task(s) — the work is not done:")
        lines += [f"    {t}" for t in ent.open_tasks]
        lines.append("    close or cancel them, then re-run `project archive`")
    return "\n".join(lines)


def _move(src: Path, dst: Path) -> None:
    """Move a whole project directory, ``.git`` and all.

    ``rename`` first because it is atomic within a filesystem and moves the
    repository wholesale — git needs no help when its own ``.git`` directory
    travels with the tree. ``shutil.move`` is the cross-device fallback.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dst)
    except OSError:
        shutil.move(str(src), str(dst))


def _reindex() -> str:
    """Fire ``qmd update`` so search stops serving the pre-move path.

    Best-effort by design: a machine without QMD is not a broken machine, and an
    archive that succeeded should not report failure because a search index it
    does not own is stale.
    """
    if not QMD_BIN.exists():
        return f"qmd not installed at {QMD_BIN} — search index not refreshed"
    try:
        out = subprocess.run((str(QMD_BIN), "update"), capture_output=True,
                             text=True, timeout=300)
    except Exception as e:
        return f"qmd update did not run ({e}) — reindex manually"
    if out.returncode != 0:
        return f"qmd update exited {out.returncode} — reindex manually"
    return "qmd update: search index refreshed"
