#!/usr/bin/env python3
"""Project manifest — a project's declarations, stored in the project itself.

Discovery today is heuristic: a project's identity lives only in ``work.db``, so
a directory on disk has no idea it is an AOS project and the reconciler is
reduced to slug-matching (``hre`` → ``hre-prototype`` → ``hre``). A manifest
ends the guessing. Discovery becomes::

    find ~/project -name project.yaml -path '*/.aos/*'

Location: ``~/project/<dir>/.aos/project.yaml``.

Ownership — what goes where
---------------------------

The manifest owns **declarations**: things only a human can state. ``work.db``
owns **tasks and derivations**. The test for whether a field belongs in the
manifest is *could you delete the DB and rebuild this from the manifest?* —
declarations yes, tasks no.

``state`` MUST NOT appear in a manifest, and neither may ``status``,
``progress``, ``pct``, ``last_activity`` or task data. This is enforced by
``validate()``, not left to discipline. A hand-maintained status field is the
bug this whole workstream exists to kill: ``dod`` claimed ``active`` while
untouched, four HRE vault docs still say ``shaping`` over started work, 21 tasks
have claimed ``active`` since March. State is *derived*, by ``brief.py``, from
signals that cannot lie.

One correction to the brief that shaped this design
---------------------------------------------------

"The manifest and work.db must never hold the same field" is not achievable, and
pretending otherwise would hide a real divergence class rather than remove it.
``title`` cannot leave ``work.db``: every task list, board and CLI row needs it,
and the five directory-less projects have nowhere else to keep it.

So the rule is about **authority**, not storage:

  * the manifest is *authoritative* for declarations;
  * ``work.db`` may hold a **projection** of them for query convenience;
  * where the two disagree, that is a reportable ``Conflict``
    (``kind="manifest_divergence"``), never an auto-resolution.

That is strictly better than a silent overlap, because the disagreement becomes
visible on the project page next to ``status_disagreement``.

Safety by schema, not by discipline
-----------------------------------

Manifests are committed to git, and ``aos``, ``deenoverdunya``, ``quran-tools``
and ``hre/app`` all have GitHub remotes — ``ahhs-quran`` is public-facing. So the
schema is built so there is nowhere for sensitive data to go:

  * **Closed key set.** Unknown keys are *rejected*, not ignored, so nobody can
    quietly add ``students:`` or ``notes:``.
  * **No absolute paths.** Anything starting with ``/`` or ``~`` is rejected: it
    would leak the operator's username and break on their Pi and MacBook. All
    paths are relative to the project root; vault paths are relative to the
    vault root.
  * **One-line, length-capped description.** Detail belongs in the vault, which
    is private; the manifest carries only vault *paths*.

Directory-less projects — an explicit case, not an omission
-----------------------------------------------------------

``p1``, ``p2``, ``unified-comms``, ``hackrf-sdr`` and ``auto-tracker`` have no
directory, therefore no manifest, and that is correct. Their declarations stay in
``work.db``. ``missing_manifest`` is never reported for them.

Two layers, per the component-lifecycle rule
--------------------------------------------

  * FRAMEWORK ``config/templates/project.yaml`` — the schema template, EXAMPLE
    values only. Never read as live data.
  * OPERATOR DATA — the real manifests, in each project directory, git-tracked
    with the project they describe (not in ``~/.aos/``: a manifest travels with
    its repo, which is the entire point).

This module reports and plans. ``write_manifest()`` is the single write path and
is never called from ``plan_adoption()`` or from ``main()``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

try:
    from brief_types import Conflict
except Exception:  # pragma: no cover — degrade if the compiler isn't installed
    @dataclass
    class Conflict:  # type: ignore[no-redef]
        kind: str
        severity: str = "warn"
        message: str = ""
        refs: list = field(default_factory=list)

try:
    import backend as engine
except ImportError:  # pragma: no cover
    engine = None

HOME = Path.home()
PROJECT_ROOT = HOME / "project"
VAULT_ROOT = HOME / "vault"
MANIFEST_REL = Path(".aos") / "project.yaml"

SCHEMA_VERSION = 1
DESCRIPTION_MAX = 160

KINDS = ("ios", "web", "python", "content", "mixed")

# The closed key set. Anything else is rejected — that is what makes "nowhere
# for sensitive data to go" a property of the schema rather than a hope.
TOP_LEVEL_KEYS = {
    "schema", "id", "kind", "title", "description", "done_when", "appetite",
    "initiative", "goal", "docs", "repos", "worktrees",
}
REPOS_KEYS = {"root", "components"}
COMPONENT_KEYS = {"path", "role", "remote", "note"}
WORKTREE_KEYS = {"location", "naming"}
COMPONENT_ROLES = ("sub-app", "data", "sources", "vendored", "docs", "tooling")

# Fields that must never appear: all of them are derived, and a hand-written
# copy is guaranteed to rot. The error message names the deriving component so
# the rejection teaches instead of just refusing.
FORBIDDEN_KEYS = {
    "state": "derived by brief.py from task/git/session signals",
    "status": "derived — a project's status is its compiled state",
    "progress": "derived from task counts in work.db",
    "pct": "derived from task counts in work.db",
    "last_activity": "derived as the max over all live signals",
    "last_updated": "derived — git and the DB already know",
    "tasks": "tasks live in work.db, never in a file",
    "task_count": "derived from work.db",
    "done_count": "derived from work.db",
    "active_count": "derived from work.db",
    "conflicts": "emitted by the compiler, never declared",
    "tags": "derived — see BRIEF-CONTRACT.md tag derivation",
    "narrative": "agent-written, cached in the brief store",
    "path": "a manifest already knows where it is; absolute paths are banned",
}

# Declaration fields the DB currently projects. Divergence here is reported,
# never auto-resolved. Maps manifest key → project-record key.
PROJECTED_FIELDS = {
    "title": "title",
    "done_when": "done_when",
    "goal": "goal",
}

WORKTREE_DEFAULT_LOCATION = ".claude/worktrees"


# ── data ────────────────────────────────────────────────────────────

@dataclass
class Component:
    path: str
    role: str
    remote: str | None = None
    note: str | None = None


@dataclass
class Manifest:
    id: str
    kind: str = "mixed"
    title: str = ""
    description: str | None = None
    done_when: str | None = None
    appetite: str | None = None
    initiative: str | None = None
    goal: str | None = None
    docs: list[str] = field(default_factory=list)
    repo_root: str = "."
    components: list[Component] = field(default_factory=list)
    worktree_location: str = WORKTREE_DEFAULT_LOCATION
    worktree_naming: str = "branch-slug"
    schema: int = SCHEMA_VERSION
    source_path: str | None = None      # where it was loaded from


@dataclass
class AdoptionPlan:
    """What ``project init`` would write for one project. Nothing is written."""
    project_id: str
    directory: str | None
    manifest_path: str | None
    action: str                 # create | update | skip_no_directory | unchanged
    manifest_yaml: str          # exactly what would land on disk
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── validation — the schema is the safety mechanism ──────────────────

def _is_absolute_ish(p: str) -> bool:
    return p.startswith("/") or p.startswith("~") or p.startswith("$")


def validate(raw: dict, *, project_ids: set[str] | None = None) -> list[str]:
    """Return a list of plain-English errors. Empty list == valid.

    Rejects, rather than ignores: unknown keys, derived fields, absolute paths,
    multi-line or over-long descriptions. A permissive validator would defeat
    the point — the schema is how sensitive data is kept out, so an unknown key
    must be an error.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["manifest is not a mapping"]

    for key in raw:
        if key in FORBIDDEN_KEYS:
            errors.append(f"'{key}' must not appear in a manifest — it is "
                          f"{FORBIDDEN_KEYS[key]}")
        elif key not in TOP_LEVEL_KEYS:
            errors.append(f"unknown key '{key}' — the schema is a closed set "
                          f"({', '.join(sorted(TOP_LEVEL_KEYS))})")

    if raw.get("schema") != SCHEMA_VERSION:
        errors.append(f"schema must be {SCHEMA_VERSION}, got {raw.get('schema')!r}")

    pid = raw.get("id")
    if not pid or not isinstance(pid, str):
        errors.append("'id' is required and must be a string")
    elif project_ids is not None and pid not in project_ids:
        errors.append(f"id '{pid}' is not a known work project — create the "
                      f"project first, or fix the id")

    if raw.get("kind") not in KINDS:
        errors.append(f"'kind' must be one of {', '.join(KINDS)}, "
                      f"got {raw.get('kind')!r}")

    if not raw.get("title"):
        errors.append("'title' is required")

    desc = raw.get("description")
    if desc is not None:
        if not isinstance(desc, str):
            errors.append("'description' must be a string")
        else:
            if "\n" in desc.strip():
                errors.append("'description' must be ONE line — detail belongs "
                              "in a vault doc, referenced from 'docs'")
            if len(desc) > DESCRIPTION_MAX:
                errors.append(f"'description' is {len(desc)} chars; max is "
                              f"{DESCRIPTION_MAX}. Manifests are committed to "
                              f"public remotes — put detail in the vault")

    for d in raw.get("docs") or []:
        if not isinstance(d, str):
            errors.append(f"docs entry {d!r} must be a string")
        elif _is_absolute_ish(d):
            errors.append(f"docs path '{d}' is absolute — use a path relative "
                          f"to the vault root (it leaks the username and "
                          f"breaks on other machines)")

    repos = raw.get("repos")
    if repos is not None:
        if not isinstance(repos, dict):
            errors.append("'repos' must be a mapping")
        else:
            for k in repos:
                if k not in REPOS_KEYS:
                    errors.append(f"unknown key 'repos.{k}' "
                                  f"(allowed: {', '.join(sorted(REPOS_KEYS))})")
            root = repos.get("root", ".")
            if isinstance(root, str) and _is_absolute_ish(root):
                errors.append(f"repos.root '{root}' is absolute — use a path "
                              f"relative to the project root")
            for i, c in enumerate(repos.get("components") or []):
                if not isinstance(c, dict):
                    errors.append(f"repos.components[{i}] must be a mapping")
                    continue
                for k in c:
                    if k not in COMPONENT_KEYS:
                        errors.append(
                            f"unknown key 'repos.components[{i}].{k}' "
                            f"(allowed: {', '.join(sorted(COMPONENT_KEYS))})")
                cp = c.get("path")
                if not cp:
                    errors.append(f"repos.components[{i}] needs a 'path'")
                elif _is_absolute_ish(str(cp)):
                    errors.append(f"repos.components[{i}].path '{cp}' is "
                                  f"absolute — use a project-relative path")
                if c.get("role") and c["role"] not in COMPONENT_ROLES:
                    errors.append(f"repos.components[{i}].role {c['role']!r} "
                                  f"must be one of {', '.join(COMPONENT_ROLES)}")

    wt = raw.get("worktrees")
    if wt is not None:
        if not isinstance(wt, dict):
            errors.append("'worktrees' must be a mapping")
        else:
            for k in wt:
                if k not in WORKTREE_KEYS:
                    errors.append(f"unknown key 'worktrees.{k}' "
                                  f"(allowed: {', '.join(sorted(WORKTREE_KEYS))})")
            loc = wt.get("location")
            if loc and _is_absolute_ish(str(loc)):
                errors.append(
                    f"worktrees.location '{loc}' is absolute. Worktrees must "
                    f"live inside the project; /private/tmp in particular is "
                    f"wiped by the OS (see project_worktrees.py)")
    return errors


# ── load ────────────────────────────────────────────────────────────

def manifest_path_for(directory: Path) -> Path:
    return directory / MANIFEST_REL


def parse(raw: dict, *, source: str | None = None) -> Manifest:
    """Build a Manifest from validated raw yaml. Call ``validate`` first."""
    repos = raw.get("repos") or {}
    wt = raw.get("worktrees") or {}
    return Manifest(
        id=raw["id"],
        kind=raw.get("kind", "mixed"),
        title=raw.get("title", ""),
        description=raw.get("description"),
        done_when=raw.get("done_when"),
        appetite=raw.get("appetite"),
        initiative=raw.get("initiative"),
        goal=raw.get("goal"),
        docs=list(raw.get("docs") or []),
        repo_root=repos.get("root", "."),
        components=[Component(path=c["path"], role=c.get("role", "sub-app"),
                              remote=c.get("remote"), note=c.get("note"))
                    for c in (repos.get("components") or []) if c.get("path")],
        worktree_location=wt.get("location", WORKTREE_DEFAULT_LOCATION),
        worktree_naming=wt.get("naming", "branch-slug"),
        schema=raw.get("schema", SCHEMA_VERSION),
        source_path=source,
    )


def load_manifest(directory: Path) -> tuple[Manifest | None, list[str]]:
    """Load one project's manifest. Returns ``(manifest, errors)``.

    A manifest that fails validation yields ``(None, errors)`` — an invalid
    manifest is never partially trusted.
    """
    p = manifest_path_for(directory)
    if not p.exists() or yaml is None:
        return None, []
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        return None, [f"{p}: unreadable ({e})"]
    errors = validate(raw)
    if errors:
        return None, [f"{p}: {e}" for e in errors]
    return parse(raw, source=str(p)), []


def discover(root: Path | None = None, max_depth: int = 2) -> dict[str, Manifest]:
    """Find every manifest under ``root``. This replaces slug heuristics.

    Bounded walk: a manifest lives at ``<project>/.aos/project.yaml``, so depth 2
    from the project root is all that is ever needed.
    """
    base = root or PROJECT_ROOT
    out: dict[str, Manifest] = {}
    if not base.exists():
        return out
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        m, _errors = load_manifest(child)
        if m:
            out[m.id] = m
    return out


# ── divergence, as a Conflict the project page already knows how to render ──

def divergence_conflicts(manifest: Manifest, project: dict) -> list[Conflict]:
    """Compare the manifest's declarations against work.db's projection.

    Reports, never resolves. Uses the existing ``Conflict`` shape from
    ``brief_types`` so this surfaces on the project page exactly like
    ``status_disagreement``.

    Note for the compiler workstream: ``"manifest_divergence"`` and
    ``"missing_manifest"`` should be appended to ``CONFLICT_KINDS`` in
    ``brief_types.py``. That tuple is documentation, not enforcement
    (``Conflict.kind`` is a plain ``str``), so this module does not need to edit
    a file it does not own in order to work.
    """
    out: list[Conflict] = []
    for mkey, dbkey in PROJECTED_FIELDS.items():
        mval = getattr(manifest, mkey, None)
        dval = project.get(dbkey)
        if mval in (None, "") and dval in (None, ""):
            continue
        if (mval or None) != (dval or None):
            out.append(Conflict(
                kind="manifest_divergence",
                severity="warn",
                message=(f'"{mkey}" differs: the manifest declares '
                         f'{mval!r} but the tracker holds {dval!r}. The manifest '
                         f'is authoritative for declarations — update the '
                         f'tracker, or fix the manifest if it is wrong.'),
                refs=[manifest.source_path or manifest.id, f"project:{project.get('id')}"],
            ))
    return out


def missing_manifest_conflict(project: dict) -> Conflict | None:
    """A linked project with no manifest. Directory-less projects are exempt."""
    if not project.get("path"):
        return None                      # explicitly correct, not a gap
    d = Path(project["path"]).expanduser()
    if manifest_path_for(d).exists():
        return None
    return Conflict(
        kind="missing_manifest",
        severity="warn",
        message=(f"{project['id']} has a directory but no "
                 f".aos/project.yaml, so the directory cannot identify itself "
                 f"as an AOS project and discovery falls back to slug guessing. "
                 f"Run `work projects adopt {project['id']}` to write one."),
        refs=[str(d)],
    )


# ── adoption — the important half ───────────────────────────────────

def _split_description(desc: str | None) -> tuple[str | None, str | None, str | None]:
    """Unpack the ``"appetite:x, initiative:y"`` string crammed into
    ``project.description`` in the live data, returning
    ``(clean_description, appetite, initiative)``.

    This is why adoption is worth doing: those are declarations wearing a
    trench coat. ``hre.description`` is literally
    ``"appetite:no-deadline, initiative:hre-platform"`` and ``aos.description``
    is ``"This was created via the API test"``.
    """
    if not desc:
        return None, None, None
    appetite = initiative = None
    leftovers: list[str] = []
    for part in desc.split(","):
        part = part.strip()
        low = part.lower()
        if low.startswith("appetite:"):
            appetite = part.split(":", 1)[1].strip()
        elif low.startswith("initiative:"):
            initiative = part.split(":", 1)[1].strip()
        elif low.startswith("short_id:"):
            continue                     # a DB concern, not a declaration
        elif part:
            leftovers.append(part)
    return (", ".join(leftovers) or None), appetite, initiative


def _infer_kind(directory: Path,
                components: list[Component] | None = None) -> tuple[str, str]:
    """Guess ``kind`` from what is on disk. Returns ``(kind, why)``.

    A guess, and labelled as one in the plan's findings — the operator confirms
    it. Inference here is cheap and reversible; unlike a disposition, a wrong
    ``kind`` misfiles nothing.

    Looks two levels deep, because the marker file is often not at the root:
    ``aos`` has no root ``pyproject.toml`` but is unambiguously a Python system
    (``core/**/*.py``), and a depth-1-only check called it ``content``.
    """
    def has(*patterns: str) -> bool:
        for pat in patterns:
            try:
                if any(directory.glob(pat)):
                    return True
            except Exception:
                continue
        return False

    ios = has("*.xcodeproj", "*/*.xcodeproj", "*.xcworkspace", "*/*.xcworkspace",
              "*/Info.plist")
    web = has("package.json", "*/package.json")
    py = has("pyproject.toml", "setup.py", "requirements.txt",
             "*.py", "*/*.py", "*/*/*.py")
    signals = [n for n, v in (("ios", ios), ("web", web), ("python", py)) if v]

    # A project whose real code lives in a nested sub-app repo is mixed by
    # construction — hre is markdown curriculum plus a whole web/iOS client.
    sub_apps = [c for c in (components or []) if c.role == "sub-app"]
    if sub_apps and signals != ["ios"]:
        which = ", ".join(c.path for c in sub_apps)
        extra = f" plus {', '.join(signals)} at the root" if signals else ""
        return "mixed", f"content/config at the root with a sub-app repo ({which}){extra}"

    if len(signals) > 1:
        return "mixed", f"multiple stacks on disk ({', '.join(signals)})"
    if signals:
        return signals[0], f"detected {signals[0]} project files"
    if has("*.md", "*/*.md"):
        return "content", "markdown content, no build system"
    return "mixed", "no clear stack signal — please confirm"


def _relative_docs(project_id: str, initiative: str | None) -> list[str]:
    """Vault docs for this project, as vault-relative paths. Never absolute."""
    out: list[str] = []
    init_dir = VAULT_ROOT / "knowledge" / "initiatives"
    if not init_dir.exists():
        return out
    slugs = {s for s in (initiative, project_id) if s}
    try:
        for f in sorted(init_dir.glob("*.md")):
            stem = f.stem
            if stem in slugs or any(stem.startswith(f"{s}-") for s in slugs):
                out.append(str(f.relative_to(VAULT_ROOT)))
    except Exception:
        pass
    return out


def _nested_components(directory: Path) -> list[Component]:
    """Nested repos, as declared components with project-relative paths."""
    try:
        from project_reconcile import git_info, nested_repos
    except Exception:
        return []
    out: list[Component] = []
    for repo in nested_repos(directory):
        try:
            rel = repo.relative_to(directory).as_posix()
        except ValueError:
            continue
        gi = git_info(repo)
        role = "vendored"
        note = None
        gitignore = directory / ".gitignore"
        ignored = False
        try:
            ignored = gitignore.exists() and any(
                line.strip().rstrip("/") == rel.rstrip("/")
                for line in gitignore.read_text().splitlines())
        except Exception:
            pass
        if ignored:
            role, note = "sub-app", "separate repo; parent gitignores this path"
        elif "/" in rel:
            role = "data"
        out.append(Component(path=rel, role=role,
                             remote=gi.get("remote"), note=note))
    return out


def render_manifest_yaml(m: Manifest) -> str:
    """Render a manifest as commented YAML a human would want to edit.

    Hand-rolled rather than ``yaml.dump`` so the comments survive — this file is
    read by people far more often than by code, and the comments are where the
    "do not put state here" rule lives at the point of temptation.
    """
    def q(v) -> str:
        if v is None:
            return "null"
        s = str(v)
        return f'"{s}"' if any(c in s for c in ':#"\'{}[]|>&*!%@`,') else s

    L = [
        f"# AOS project manifest — declarations for '{m.id}'.",
        "#",
        "# This file is COMMITTED TO GIT with the project, so it holds no secrets,",
        "# no absolute paths, and no personal detail — only a one-line description",
        "# and PATHS into the (private) vault.",
        "#",
        "# It declares; it never reports. There is deliberately no status, state,",
        "# progress or last-updated field: those are derived by brief.py from tasks,",
        "# git and sessions, and a hand-written copy would start rotting today.",
        f"schema: {m.schema}",
        f"id: {m.id}",
        f"kind: {m.kind}                 # {' | '.join(KINDS)}",
        f"title: {q(m.title)}",
    ]
    L.append(f"description: {q(m.description)}"
             + "   # ONE line; detail goes in the vault docs below")
    L.append(f"done_when: {q(m.done_when)}")
    L.append(f"appetite: {q(m.appetite)}")
    L.append(f"initiative: {q(m.initiative)}       # vault initiative key, not a path")
    L.append(f"goal: {q(m.goal)}")
    L.append("")
    L.append("# Vault documents, RELATIVE to the vault root. Paths only — the vault")
    L.append("# stays private, this file does not.")
    if m.docs:
        L.append("docs:")
        L += [f"  - {d}" for d in m.docs]
    else:
        L.append("docs: []")
    L.append("")
    L.append("# Repo layout. Every path is RELATIVE to this project directory.")
    L.append("repos:")
    L.append(f"  root: {m.repo_root}")
    if m.components:
        L.append("  components:")
        for c in m.components:
            L.append(f"    - path: {c.path}")
            L.append(f"      role: {c.role}                # {' | '.join(COMPONENT_ROLES)}")
            if c.remote:
                L.append(f"      remote: {c.remote}")
            if c.note:
                L.append(f"      note: {q(c.note)}")
    else:
        L.append("  components: []")
    L.append("")
    L.append("# Where branch checkouts live. Relative to this project, always —")
    L.append("# /private/tmp is wiped by the OS and has already destroyed 19 of them.")
    L.append("worktrees:")
    L.append(f"  location: {m.worktree_location}")
    L.append(f"  naming: {m.worktree_naming}")
    return "\n".join(L) + "\n"


def plan_adoption(project_id: str | None = None) -> list[AdoptionPlan]:
    """Plan a manifest for every linked project (or just one). Writes nothing.

    This is adoption, not scaffolding: the manifest is built from what
    ``work.db`` and the filesystem already know, so running it against ``hre``
    recovers the nested ``app/`` repo, the vault docs and the crammed
    ``appetite``/``initiative`` fields without anyone retyping them.
    """
    if engine is None:
        return []
    projects = [p for p in engine.load_all().get("projects", [])
                if p.get("status") not in ("cancelled", "archived")]
    if project_id:
        projects = [p for p in projects if p["id"] == project_id]

    plans: list[AdoptionPlan] = []
    for p in projects:
        pid = p["id"]
        findings: list[str] = []
        warnings: list[str] = []

        if not p.get("path"):
            plans.append(AdoptionPlan(
                project_id=pid, directory=None, manifest_path=None,
                action="skip_no_directory", manifest_yaml="",
                findings=["no directory, therefore no manifest — correct, not a "
                          "gap. Its declarations stay in work.db."]))
            continue

        d = Path(p["path"]).expanduser().resolve()
        if not d.exists():
            warnings.append(f"project.path {p['path']} does not exist on disk")
            plans.append(AdoptionPlan(project_id=pid, directory=str(d),
                                      manifest_path=None, action="skip_no_directory",
                                      manifest_yaml="", warnings=warnings))
            continue

        desc, appetite, initiative = _split_description(p.get("description"))
        if appetite or initiative:
            findings.append(f"recovered {'appetite ' if appetite else ''}"
                            f"{'initiative ' if initiative else ''}"
                            f"from the description string, which had them "
                            f"crammed in as 'key:value' text")
        if desc:
            findings.append(f"description kept as {desc!r} — review it; the live "
                            f"records contain junk like 'created via the API test'")

        docs = _relative_docs(pid, initiative)
        if docs:
            findings.append(f"linked {len(docs)} vault doc(s) by path")

        # Components first: a nested sub-app repo changes what `kind` should be.
        components = _nested_components(d)
        kind, why = _infer_kind(d, components)
        findings.append(f"kind={kind} ({why}) — inferred, please confirm")
        for c in components:
            findings.append(f"found nested repo '{c.path}' (role={c.role})"
                            + (f", remote {c.remote}" if c.remote else ""))

        wt_loc, wt_note = _declared_worktree_location(d)
        if wt_note:
            findings.append(wt_note)

        m = Manifest(id=pid, kind=kind, title=p.get("title", pid),
                     description=desc, done_when=p.get("done_when"),
                     appetite=appetite, initiative=initiative, goal=p.get("goal"),
                     docs=docs, components=components, worktree_location=wt_loc)

        mp = manifest_path_for(d)
        existing, errs = load_manifest(d)
        if errs:
            warnings.append(f"existing manifest is invalid: {'; '.join(errs)}")
        rendered = render_manifest_yaml(m)
        if existing and mp.exists() and mp.read_text() == rendered:
            action = "unchanged"          # idempotent: re-running is a no-op
        elif mp.exists():
            action = "update"
        else:
            action = "create"

        # Committed-to-git caution, stated per project rather than in general.
        try:
            from project_reconcile import git_info
            gi = git_info(d)
            if gi.get("remote"):
                warnings.append(f"this repo has remote {gi['remote']} — the "
                                f"manifest will be pushed there once committed")
            if gi.get("branch") and gi["branch"] != "main":
                warnings.append(f"currently on branch '{gi['branch']}', not main "
                                f"— decide deliberately which branch gets it")
            if gi.get("commit_count") is not None and gi["commit_count"] < 5:
                warnings.append(f"only {gi['commit_count']} commit(s) in this "
                                f"repo — young history, tread carefully")
        except Exception:
            pass

        plans.append(AdoptionPlan(project_id=pid, directory=str(d),
                                  manifest_path=str(mp), action=action,
                                  manifest_yaml=rendered,
                                  findings=findings, warnings=warnings))
    return plans


def _declared_worktree_location(directory: Path) -> tuple[str, str | None]:
    """Where this project's branches already live, as a project-relative path.

    Prefers observed reality over the default: if the project already keeps
    worktrees in ``.claude/worktrees`` (the Claude Code harness's own
    convention), declare that rather than imposing a competing scheme.
    """
    harness = directory / ".claude" / "worktrees"
    if harness.is_dir() and any(harness.iterdir()):
        n = len([c for c in harness.iterdir() if c.is_dir()])
        return WORKTREE_DEFAULT_LOCATION, (
            f"already keeps {n} worktree(s) in .claude/worktrees — adopting the "
            f"harness convention rather than inventing one")
    return WORKTREE_DEFAULT_LOCATION, None


# ── the single write path — never called from planning ──────────────

def write_manifest(plan: AdoptionPlan) -> Path:
    """Write one planned manifest to disk. The ONLY write in this module.

    Never called by ``plan_adoption()``, ``render_plan()`` or ``main()``. A human
    reviews the plan and calls this, because it writes into a git repo that has a
    public remote.
    """
    if not plan.manifest_path or not plan.manifest_yaml:
        raise ValueError(f"{plan.project_id}: nothing to write ({plan.action})")
    p = Path(plan.manifest_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plan.manifest_yaml)
    return p


# ── rendering ───────────────────────────────────────────────────────

def render_plan(plans: list[AdoptionPlan], *, show_yaml: bool = False) -> str:
    L = ["  Manifest adoption plan (report only — nothing written)", ""]
    for pl in plans:
        L.append(f"  [{pl.action}] {pl.project_id}"
                 + (f"  -> {pl.manifest_path}" if pl.manifest_path else ""))
        for f in pl.findings:
            L.append(f"      + {f}")
        for w in pl.warnings:
            L.append(f"      ! {w}")
        if show_yaml and pl.manifest_yaml:
            L.append("")
            L += [f"      | {line}" for line in pl.manifest_yaml.splitlines()]
        L.append("")
    counts: dict[str, int] = {}
    for pl in plans:
        counts[pl.action] = counts.get(pl.action, 0) + 1
    L.append("  " + "   ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "\n".join(L)


def cli_entry(args: list[str]) -> None:
    """``work projects adopt [<project>] [--yaml] [--json]`` — plans only."""
    if engine is None:
        print("Work engine not available")
        return
    rest = [a for a in args if not a.startswith("--")]
    plans = plan_adoption(rest[0] if rest else None)
    if "--json" in args:
        import json
        print(json.dumps([{
            "project_id": p.project_id, "directory": p.directory,
            "manifest_path": p.manifest_path, "action": p.action,
            "findings": p.findings, "warnings": p.warnings,
            "manifest_yaml": p.manifest_yaml,
        } for p in plans], indent=2))
        return
    print(render_plan(plans, show_yaml="--yaml" in args))
    # Deliberately no --apply. write_manifest() must be called explicitly, per
    # project, after a human has read the YAML that would land in their repo.


if __name__ == "__main__":
    cli_entry(sys.argv[1:])
