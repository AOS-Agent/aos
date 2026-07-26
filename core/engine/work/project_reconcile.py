#!/usr/bin/env python3
"""Project-directory reconciler — account for every directory under ~/project/.

The question this answers is not "are the directories and the work projects a
1:1 mirror" (they are not, and should not be). It is the stricter, more useful
one: **is any directory unaccounted for, and is the reason always explicit?**

Every top-level directory under ``~/project/`` resolves to exactly one
disposition:

======================  ====================================================
``linked``              is the primary repo of a work project
``worktree_of:<id>``    a git worktree / branch checkout of a linked project
``component_of:<id>``   a supporting repo of a project (data, sources,
                        sub-app, nested repo)
``not_a_project``       deliberately excluded — always carries a stated reason
``unclassified``        genuinely unknown → SURFACED for the operator to
                        triage. Never auto-guessed.
======================  ====================================================

Detection order — first match wins, and every match records citable evidence:

1. **declared** — an entry in the instance dispositions file. A decision the
   operator already made is never re-litigated. One exception: if a declaration
   contradicts live state (it says ``not_a_project`` about a directory that IS
   a live project's ``path``), the work record wins and the contradiction is
   reported. A stale declaration must never make the report state something
   false; the operator settles it by deleting one side or the other.
2. **manifest** — the directory contains ``.aos/project.yaml`` declaring an
   ``id`` that matches a live work project. This is the *primary* mechanism and
   the only deterministic one: the directory states its own identity, so no
   matching is involved. Everything below it is fallback, kept for
   not-yet-adopted directories. See ``project_manifest.py``.
3. **linked** — the directory's resolved realpath equals a work project's
   resolved ``path``. ``~/project`` is a symlink to the AOS-X volume, so paths
   are compared *resolved*, never as strings — half the live project records
   spell the same directory ``/Users/agentalhadi/project/x`` and half
   ``/Volumes/AOS-X/project/x``.
4. **worktree_of** — asked of git, not inferred from a name. In a worktree,
   ``git rev-parse --git-common-dir`` points into the *main* checkout's
   ``.git`` while ``--git-dir`` points at the worktree's own admin dir. The
   common dir's parent is then mapped back to the project that owns it. The
   ``-wt`` naming convention is deliberately NOT used as a signal — it drifts
   (see the module's live findings: the three ``*-wt`` directories are empty
   husks and are not worktrees at all, while the two real worktrees are named
   after their branches).
5. **component_of / submodule** — the directory's git remote matches a
   ``.gitmodules`` url declared by a linked project's repo.
6. **component_of / nested** — the directory *is* a repo nested inside a
   linked project's tree (bounded walk, depth ≤ 3).
7. **filesystem-evident non-project** — an empty directory. Not a guess: a
   directory with zero entries has nothing to track. Reason states the fact.
   Checked early, because an empty directory cannot be a worktree or a
   component no matter what any document says about it.
8. **component_of / path reference** — the directory's absolute path is
   hardcoded in a linked project's tracked **code or config**, cited
   ``file:line`` so the claim is checkable. Prose is excluded on purpose
   (``*.md``, ``*.txt``, ``docs/``): a design doc *mentioning* a path is a
   citation, not a dependency. Letting prose count here was measurably wrong on
   live data — it bound ``qul`` and ``tafsir`` to projects that merely name them
   in a handoff note.
9. **unclassified** — everything left, emitted with its full evidence bundle
   (git remote, branch, last commit, CLAUDE.md, session count, nested repos) so
   the operator can triage in one pass instead of going digging.

The reverse gap is reported too: work projects with no directory, each with a
stated reason drawn from the instance file. A project whose absence has no
recorded reason is reported as such — an unexplained gap is a finding, not a
blank.

**This module reports. It does not apply.** ``apply_dispositions()`` exists and
writes exactly one file — the instance dispositions YAML. It is never called
from anywhere in this module, touches no task data, and never writes
``project.path`` (that is ``detect_projects.apply_path_matches``, likewise
explicit). Proposals for new projects are printed, never created.

Two-layer config, per the component-lifecycle rule (same shape as
``apps_registry``):

  * FRAMEWORK ``config/project-dispositions.yaml`` — a TEMPLATE, and nothing
    more. The framework tree is git-tracked and shipped to every machine, so no
    real directory names or paths live in it, and — unlike ``apps_registry`` —
    its ``example-*`` entries are **never** loaded as active declarations. A
    disposition records a decision a human made about a directory on *this*
    machine; falling back to shipped examples would invent decisions nobody
    made, which is the exact failure this module exists to prevent.
  * INSTANCE ``~/.aos/config/project-dispositions.yaml`` — the only source of
    declarations. Plainly hand-editable; seed a first draft with
    ``--propose-instance``.

No instance file → nothing declared, everything falls through to derived
evidence or an honest ``unclassified``. Missing config is never a crash.

Usage::

    python3 project_reconcile.py                 # full disposition report
    python3 project_reconcile.py --json
    python3 project_reconcile.py --gaps-only     # just the reverse gap
    python3 project_reconcile.py --propose-instance   # print a ready-to-review
                                                      # instance YAML to stdout
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import yaml
except Exception:  # pragma: no cover — yaml is a hard dep; defensive only
    yaml = None

try:
    import backend as engine
except ImportError:  # pragma: no cover
    engine = None

try:
    import project_manifest as _pm
except Exception:  # pragma: no cover — reconciler still works without manifests
    _pm = None

HOME = Path.home()
PROJECT_ROOT = HOME / "project"
CLAUDE_PROJECTS = HOME / ".claude" / "projects"

# Bounded walk for nested repos. Depth is counted from PROJECT_ROOT, so
# depth 3 reaches e.g. quran-tools/shared/quran-data.
MAX_NEST_DEPTH = 3
SKIP_DIRS = {
    "node_modules", ".venv", "venv", "_archive", "dist", "build", "__pycache__",
    "vendor", ".git", "Pods", ".next", "target", "DerivedData",
}

DISPOSITIONS = ("linked", "worktree_of", "component_of", "not_a_project", "unclassified")


# ── data ────────────────────────────────────────────────────────────

@dataclass
class Entry:
    """One directory's disposition, with the evidence that produced it."""
    name: str                       # display name, relative to ~/project/
    path: str                       # resolved absolute path
    disposition: str                # one of DISPOSITIONS
    target: str | None = None       # project id (or repo path) for the *_of forms
    evidence: str = ""              # citable, mechanical
    source: str = ""                # declared | work-record | git | filesystem | reference
    notes: list[str] = field(default_factory=list)
    nested: bool = False            # discovered by the nested walk, not top-level
    # Structured facts. Triage decisions read THESE, never the evidence string —
    # substring-matching human prose is how "not a git repo" once scored as
    # "git repo" and proposed six non-repos as new projects.
    facts: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.disposition in ("worktree_of", "component_of") and self.target:
            return f"{self.disposition}:{self.target}"
        return self.disposition


@dataclass
class Gap:
    """A work project with no directory on disk."""
    project_id: str
    title: str
    reason: str
    explained: bool                 # False → nobody has recorded why


@dataclass
class Proposal:
    """A directory that looks like real untracked work. Reported, never created."""
    name: str
    path: str
    confidence: str                 # high | medium | low
    reasoning: str


@dataclass
class ReconcileReport:
    entries: list[Entry]
    gaps: list[Gap]
    proposals: list[Proposal]
    conflicts: list[str]
    declared_stale: list[str]       # declared names that no longer exist on disk
    # Typed conflicts in the brief_types.Conflict shape, so the project page
    # renders manifest divergence exactly like status_disagreement.
    brief_conflicts: list = field(default_factory=list)

    @property
    def unclassified(self) -> list[Entry]:
        return [e for e in self.entries if e.disposition == "unclassified"]


# ── config: two layers ──────────────────────────────────────────────

def _framework_config_path() -> Path:
    # …/core/engine/work/project_reconcile.py → repo root is parents[3].
    return Path(__file__).resolve().parents[3] / "config" / "project-dispositions.yaml"


def _instance_config_path() -> Path | None:
    override = os.environ.get("AOS_CONFIG_DIR")
    base = Path(override) if override else (HOME / ".aos" / "config")
    p = base / "project-dispositions.yaml"
    return p if p.exists() else None


def _load_raw(path: Path | None) -> dict:
    if path is None or yaml is None or not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def load_declared() -> tuple[dict, dict, str]:
    """Resolve the effective dispositions config.

    Returns ``(directories, projects, layer)``:
      * ``directories`` — {dir name: {disposition, target, reason}}
      * ``projects``    — {project id: {no_directory_reason: ...}}
      * ``layer``       — "instance" | "none (framework template only)"

    **Only the instance layer supplies declarations.** Unlike ``apps_registry``,
    the framework file here is a template and nothing more: its ``example-*``
    entries must never become active dispositions, because a disposition is a
    record of a decision a human made about a directory that exists on *this*
    machine. Falling back to shipped examples would invent decisions nobody
    made — the exact failure the reconciler exists to prevent.

    No instance file → no declarations, and every directory is classified from
    live evidence or reported ``unclassified``. Never a crash.
    """
    instance = _load_raw(_instance_config_path())
    if instance.get("directories") or instance.get("projects"):
        return (instance.get("directories") or {},
                instance.get("projects") or {}, "instance")
    return {}, {}, "none (framework template only — nothing declared yet)"


# ── git helpers — ask git, never guess from names ────────────────────

def _git(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(("git", "-C", str(path), *args),
                             capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


_GIT_CACHE: dict[str, dict] = {}


def git_info(path: Path) -> dict:
    """Everything we need to know about a directory's git identity.

    ``is_worktree`` is decided the only reliable way: a linked worktree's
    ``--git-common-dir`` resolves somewhere other than its ``--git-dir``.

    Memoised: one reconcile pass asks about the same repo from several angles
    (owner namespace, remote index, per-directory classification) and each
    answer costs five subprocess spawns.
    """
    key = str(path)
    if key in _GIT_CACHE:
        return _GIT_CACHE[key]
    info = {
        "is_git": (path / ".git").exists(),
        "git_dir": None, "common_dir": None, "is_worktree": False,
        "main_repo": None, "remote": None, "branch": None, "last_commit": None,
        "commit_count": None,
    }
    if not info["is_git"]:
        _GIT_CACHE[key] = info
        return info

    def _abs(rel: str | None) -> Path | None:
        if not rel:
            return None
        p = Path(rel)
        if not p.is_absolute():
            p = path / p
        try:
            return p.resolve()
        except Exception:
            return p

    gd = _abs(_git(path, "rev-parse", "--git-dir"))
    cd = _abs(_git(path, "rev-parse", "--git-common-dir"))
    info["git_dir"] = str(gd) if gd else None
    info["common_dir"] = str(cd) if cd else None
    if gd and cd and gd != cd:
        info["is_worktree"] = True
        # The main checkout is the parent of the common .git directory.
        info["main_repo"] = str(cd.parent)
    info["remote"] = _git(path, "config", "--get", "remote.origin.url")
    info["branch"] = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    info["last_commit"] = _git(path, "log", "-1", "--format=%cI")
    count = _git(path, "rev-list", "--count", "HEAD")
    info["commit_count"] = int(count) if count and count.isdigit() else None
    _GIT_CACHE[key] = info
    return info


def submodule_urls(repo: Path) -> dict[str, str]:
    """{submodule path: url} declared in the repo's .gitmodules. {} if none."""
    gm = repo / ".gitmodules"
    if not gm.exists():
        return {}
    out: dict[str, str] = {}
    cur_path = None
    try:
        for raw in gm.read_text().splitlines():
            line = raw.strip()
            if line.startswith("[submodule"):
                cur_path = None
            elif line.startswith("path"):
                cur_path = line.split("=", 1)[1].strip() if "=" in line else None
            elif line.startswith("url") and "=" in line:
                url = line.split("=", 1)[1].strip()
                out[cur_path or url] = url
    except Exception:
        return {}
    return out


def _norm_remote(url: str | None) -> str | None:
    """Compare remotes ignoring protocol, .git suffix and trailing slash."""
    if not url:
        return None
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    for pre in ("https://", "http://", "ssh://", "git://"):
        if u.startswith(pre):
            u = u[len(pre):]
    u = u.replace("git@", "").replace(":", "/", 1) if u.startswith("git@") else u
    return u.lower()


# Prose is not a dependency. A design doc that names a path is citing it; only
# code and config that hardcodes a path actually depends on it.
_PROSE_PATHSPECS = (
    ":!*.md", ":!*.markdown", ":!*.txt", ":!*.rst", ":!*.log",
    ":!docs/*", ":!*/docs/*", ":!CHANGELOG*", ":!*.lock",
)


def _namespace(remote: str | None) -> str | None:
    """The owner segment of a remote — 'github.com/alice/x' → 'github.com/alice'."""
    n = _norm_remote(remote)
    if not n:
        return None
    parts = n.split("/")
    return "/".join(parts[:2]) if len(parts) >= 3 else None


def _owner_namespaces(proj_path: dict[str, Path]) -> set[str]:
    """Namespaces the operator publishes to, derived from their linked projects.

    Discovered, never hardcoded: whoever owns the remotes of the repos the work
    system already points at is the operator. An empty set (no linked project
    has a remote) means "unknown", and callers must then judge nothing
    third-party rather than assume.
    """
    out: set[str] = set()
    for ppath in proj_path.values():
        ns = _namespace(git_info(ppath).get("remote"))
        if ns:
            out.add(ns)
    return out


def _references_any(repo: Path, needles: list[str]) -> dict[str, str]:
    """{needle: "file:line"} for needles hardcoded in `repo`'s tracked CODE.

    One batched ``git grep`` for all needles rather than one call per needle —
    on this machine the per-needle version took 77s (openclaw alone is 22.5k
    commits); batched it is ~2s. ``git grep`` only searches tracked content, so
    a stray file in a build directory cannot fool it, and ``_PROSE_PATHSPECS``
    keeps documentation from counting as a dependency.
    """
    if not needles:
        return {}
    args = ["git", "-C", str(repo), "grep", "-n", "-I", "-F"]
    for n in needles:
        args += ["-e", n]
    args += ["--", *_PROSE_PATHSPECS]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=90)
    except Exception:
        return {}
    if out.returncode != 0 or not out.stdout:
        return {}
    hits: dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        loc, body = f"{parts[0]}:{parts[1]}", parts[2]
        # git grep does not say which -e matched; attribute by containment, and
        # prefer the longest needle so a path never steals its own parent's hit.
        for n in sorted(needles, key=len, reverse=True):
            if n in body:
                hits.setdefault(n, loc)
                break
    return hits


# ── filesystem scanning ─────────────────────────────────────────────

def _resolve(p: str | Path) -> Path:
    try:
        return Path(p).expanduser().resolve()
    except Exception:
        return Path(p).expanduser()


def top_level_dirs() -> list[Path]:
    if not PROJECT_ROOT.exists():
        return []
    return sorted((c for c in PROJECT_ROOT.iterdir()
                   if c.is_dir() and not c.name.startswith(".")),
                  key=lambda p: p.name.lower())


def nested_repos(root: Path, max_depth: int = MAX_NEST_DEPTH) -> list[Path]:
    """Bounded walk for git repos below `root`. Skips build/vendor/hidden dirs.

    ``.claude/worktrees/agent-*`` and the like are excluded because hidden
    directories are never descended into.
    """
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = list(d.iterdir())
        except (PermissionError, OSError):
            return
        for c in children:
            if not c.is_dir() or c.is_symlink() or c.name.startswith("."):
                continue
            if c.name in SKIP_DIRS:
                continue
            if (c / ".git").exists():
                found.append(c)
                # A repo inside a repo is a boundary; deeper nesting below a
                # found repo is that repo's business, not ours.
                continue
            walk(c, depth + 1)

    walk(root, 1)
    return found


def _is_empty(path: Path) -> bool:
    try:
        return not any(c.name not in (".DS_Store",) for c in path.iterdir())
    except (PermissionError, OSError):
        return False


def _session_count(path: Path) -> int:
    """Claude session count for a directory, via ~/.claude/projects/<slug>."""
    if not CLAUDE_PROJECTS.exists():
        return 0
    total = 0
    slug = str(path).replace("/", "-")
    for cand in (slug, str(path).replace("/Volumes/AOS-X", str(HOME)).replace("/", "-")):
        d = CLAUDE_PROJECTS / cand
        if d.is_dir():
            total = max(total, len(list(d.glob("*.jsonl"))))
    return total


# ── the reconciler ──────────────────────────────────────────────────

def reconcile() -> ReconcileReport:
    """Classify every directory under ~/project/. Report-only; writes nothing."""
    declared_dirs, declared_projects, _layer = load_declared()

    projects = engine.load_all().get("projects", []) if engine else []
    live = [p for p in projects if p.get("status") not in ("cancelled", "archived")]

    # project id → resolved path, for the projects that have one
    proj_path: dict[str, Path] = {}
    for p in live:
        if p.get("path"):
            proj_path[p["id"]] = _resolve(p["path"])
    path_to_proj: dict[Path, str] = {v: k for k, v in proj_path.items()}

    # remote → owning project, and submodule url → owning project
    remote_to_proj: dict[str, str] = {}
    submodule_to_proj: dict[str, tuple[str, str]] = {}
    for pid, ppath in proj_path.items():
        gi = git_info(ppath)
        nr = _norm_remote(gi.get("remote"))
        if nr:
            remote_to_proj.setdefault(nr, pid)
        for sub_path, url in submodule_urls(ppath).items():
            nu = _norm_remote(url)
            if nu:
                submodule_to_proj.setdefault(nu, (pid, sub_path))

    # Which git namespaces are the operator's own? Derived from the remotes of
    # the repos their tracked projects already point at — never a hardcoded
    # username list. If no linked project has a remote, nothing is judged
    # third-party and the report says so by omission rather than by guessing.
    owners = _owner_namespaces(proj_path)

    def _is_third_party(remote: str | None) -> bool:
        ns = _namespace(remote)
        return bool(ns and owners and ns not in owners)

    # Manifests, keyed by directory name — the deterministic discovery layer.
    manifests: dict[str, object] = {}
    manifest_errors: list[str] = []
    brief_conflicts: list = []
    if _pm is not None:
        for d in top_level_dirs():
            m, errs = _pm.load_manifest(d)
            manifest_errors.extend(errs)
            if m:
                manifests[d.name] = m
        by_id = {p["id"]: p for p in live}
        for m in manifests.values():
            if m.id in by_id:
                brief_conflicts.extend(_pm.divergence_conflicts(m, by_id[m.id]))
        for p in live:
            c = _pm.missing_manifest_conflict(p)
            if c:
                brief_conflicts.append(c)

    tops = top_level_dirs()
    entries: list[Entry] = []
    conflicts: list[str] = list(manifest_errors)
    suppressed_nested: dict[str, list[str]] = {}
    deferred: list[tuple[Path, Path, str, dict]] = []

    # ---- pass 1: nested repos inside LINKED projects → component_of ----
    nested_entries: list[Entry] = []
    for pid, ppath in proj_path.items():
        for repo in nested_repos(ppath):
            # ~/project is a symlink to the AOS-X volume; compare resolved.
            root = _resolve(PROJECT_ROOT)
            rel = repo.relative_to(root) if repo.is_relative_to(root) else repo
            gi = git_info(repo)
            why = [f"git repo nested inside {pid}'s tree"]
            if gi.get("remote"):
                why.append(f"own remote {gi['remote']}")
            gitignore = ppath / ".gitignore"
            try:
                rel_in_parent = repo.relative_to(ppath).as_posix()
                if gitignore.exists() and any(
                        line.strip().rstrip("/") == rel_in_parent.rstrip("/")
                        for line in gitignore.read_text().splitlines()):
                    why.append(f"parent .gitignore excludes {rel_in_parent}/")
            except Exception:
                pass
            notes = [f"last commit {gi['last_commit'][:10]}"] if gi.get("last_commit") else []
            if _is_third_party(gi.get("remote")):
                notes.append("third-party remote — vendored dependency, not the "
                             "operator's code")
            nested_entries.append(Entry(
                name=str(rel), path=str(repo), disposition="component_of",
                target=pid, source="git", evidence="; ".join(why), notes=notes,
                nested=True,
                facts={"is_git": True,
                       "third_party_remote": _is_third_party(gi.get("remote"))},
            ))

    # ---- pass 2: top-level directories ----
    for d in tops:
        rp = _resolve(d)
        name = d.name
        gi = git_info(d)

        # 1. declared — an operator decision, never re-litigated
        dec = declared_dirs.get(name)
        if dec:
            disp = (dec.get("disposition") or "").strip()
            target = dec.get("target")
            reason = dec.get("reason") or "declared in instance config"
            if disp not in DISPOSITIONS:
                conflicts.append(
                    f"{name}: declared disposition '{disp}' is not one of "
                    f"{', '.join(DISPOSITIONS)} — entry ignored, falling through to detection")
            elif rp in path_to_proj and disp != "linked":
                # A stale declaration must never make the table state something
                # false. The work record is live, checkable state; the
                # declaration is a decision that has since been overtaken. Show
                # the truth, and report the contradiction for the operator to
                # settle (probably by deleting the declaration).
                pid = path_to_proj[rp]
                conflicts.append(
                    f"{name}: declared '{disp}' but it IS the path of live project "
                    f"'{pid}'. Showing 'linked' (the work record is current state); "
                    f"the declaration is stale — remove it or clear {pid}.path")
                entries.append(Entry(
                    name=name, path=str(rp), disposition="linked", target=pid,
                    source="work-record",
                    evidence=f"project '{pid}'.path resolves to this directory",
                    notes=[f"a declaration says '{disp}' ({reason}) — contradicted "
                           f"by the live project record, see conflicts"]))
                continue
            else:
                entries.append(Entry(name=name, path=str(rp), disposition=disp,
                                     target=target, source="declared", evidence=reason))
                continue

        # 2. manifest — the directory declares its own identity. Deterministic:
        #    no matching, no heuristic. This is the mechanism; everything below
        #    is fallback for directories not yet adopted.
        man = manifests.get(name)
        if man is not None:
            pid = man.id
            notes = [f"declared kind={man.kind}"]
            if man.components:
                notes.append(f"declares {len(man.components)} component(s): "
                             + ", ".join(c.path for c in man.components))
            if pid not in {p["id"] for p in live}:
                conflicts.append(
                    f"{name}: manifest declares id '{pid}', which is not a live "
                    f"work project — create the project or fix the manifest")
                notes.append(f"id '{pid}' matches no live project")
            entries.append(Entry(
                name=name, path=str(rp), disposition="linked", target=pid,
                source="manifest",
                evidence=f".aos/project.yaml declares id '{pid}'",
                notes=notes, facts={"is_git": gi["is_git"], "manifest": True}))
            continue

        # 3. linked — resolved path equals a live project's resolved path
        if rp in path_to_proj:
            pid = path_to_proj[rp]
            notes = []
            if not gi["is_git"]:
                notes.append("WARNING: linked but not a git repo")
            if gi.get("branch"):
                notes.append(f"on branch {gi['branch']}")
            notes.append("no .aos/project.yaml — identity known only to the "
                         "tracker; `work projects adopt` would fix that")
            entries.append(Entry(
                name=name, path=str(rp), disposition="linked", target=pid,
                source="work-record",
                evidence=f"project '{pid}'.path resolves to this directory",
                notes=notes, facts={"is_git": gi["is_git"], "manifest": False}))
            continue

        # 3. worktree_of — asked of git
        if gi["is_worktree"] and gi["main_repo"]:
            main = _resolve(gi["main_repo"])
            owner = path_to_proj.get(main)
            ev = (f"git --git-common-dir points at {gi['common_dir']} "
                  f"(≠ --git-dir {gi['git_dir']}); branch '{gi['branch']}'")
            if owner:
                entries.append(Entry(name=name, path=str(rp), disposition="worktree_of",
                                     target=owner, source="git", evidence=ev))
            else:
                entries.append(Entry(
                    name=name, path=str(rp), disposition="worktree_of",
                    target=main.name, source="git", evidence=ev,
                    notes=[f"main checkout {main} is not a tracked project — "
                           f"target is a directory, not a project id"]))
            continue

        # 4. component_of — remote matches a linked project's submodule url
        nr = _norm_remote(gi.get("remote"))
        if nr and nr in submodule_to_proj:
            pid, sub_path = submodule_to_proj[nr]
            e = Entry(name=name, path=str(rp), disposition="component_of", target=pid,
                      source="git",
                      evidence=f"remote {gi['remote']} == {pid} .gitmodules "
                               f"url for submodule '{sub_path}'")
            # A component can serve more than one project. Say so; don't hide it
            # behind the single owner the table is able to show.
            for other_pid, other_path in proj_path.items():
                if other_pid == pid:
                    continue
                found = _references_any(other_path, [str(rp), str(HOME / "project" / name)])
                if found:
                    loc = next(iter(found.values()))
                    e.notes.append(f"also referenced by {other_pid} at {loc} — "
                                   f"shared component; one owner shown, not exclusive")
            entries.append(e)
            continue

        # 4b. component_of — same remote as a linked project (a second clone)
        if nr and nr in remote_to_proj:
            pid = remote_to_proj[nr]
            entries.append(Entry(
                name=name, path=str(rp), disposition="component_of", target=pid,
                source="git",
                evidence=f"same git remote as project '{pid}' ({gi['remote']}) "
                         f"but a separate clone, not a worktree",
                notes=["second clone of a tracked repo — verify it is not a stale copy"]))
            continue

        # 5. filesystem-evident non-project — an empty directory is a fact, not
        #    a guess. Checked BEFORE the reference rule: an empty directory
        #    cannot be a component no matter what a stale handoff note says
        #    about it (live case: quran-tools-wt, named in a backup HANDOFF.md).
        if _is_empty(d):
            entries.append(Entry(
                name=name, path=str(rp), disposition="not_a_project",
                source="filesystem",
                evidence="directory is empty (no entries) — nothing to track",
                notes=["if this was a worktree parent, git no longer knows about it"],
                facts={"empty": True, "is_git": False}))
            continue

        # 6/7. Needs a code search across the linked projects. Deferred so all
        #      candidates can be batched into one grep per project.
        deferred.append((d, rp, name, gi))

    # ---- pass 3: batched reference search over the deferred candidates ----
    # Two spellings per candidate, because half the repo records use the
    # /Users symlink path and half the /Volumes realpath.
    needle_owner: dict[str, tuple[str, Path]] = {}
    for _d, rp, name, _gi in deferred:
        for needle in {str(rp), str(HOME / "project" / name)}:
            needle_owner[needle] = (name, rp)
    ref_hits: dict[str, tuple[str, str]] = {}   # dir name → (project id, file:line)
    for pid, ppath in proj_path.items():
        for needle, loc in _references_any(ppath, list(needle_owner)).items():
            dname, _rp = needle_owner[needle]
            ref_hits.setdefault(dname, (pid, loc))

    for d, rp, name, gi in deferred:
        # 6. component_of — path hardcoded in a linked project's tracked code
        if name in ref_hits:
            pid, loc = ref_hits[name]
            entries.append(Entry(
                name=name, path=str(rp), disposition="component_of", target=pid,
                source="reference",
                evidence=f"absolute path hardcoded in {pid}'s tracked code at {loc}",
                notes=["binding is a code-level dependency, not a doc mention"],
                facts={"is_git": gi["is_git"]}))
            continue

        # 7. unclassified — surfaced with everything needed to triage
        third_party = _is_third_party(gi.get("remote"))
        has_claude = (d / "CLAUDE.md").exists()
        sc = _session_count(rp)
        inner = nested_repos(d)
        ev_bits = []
        if gi["is_git"]:
            ev_bits.append(f"git repo, branch {gi['branch']}")
            ev_bits.append(f"remote {gi['remote']}" if gi.get("remote") else "no remote")
            if gi.get("last_commit"):
                ev_bits.append(f"last commit {gi['last_commit'][:10]}")
            if gi.get("commit_count") is not None:
                ev_bits.append(f"{gi['commit_count']} commits")
        else:
            ev_bits.append("not a git repo")
        if has_claude:
            ev_bits.append("has CLAUDE.md")
        if sc:
            ev_bits.append(f"{sc} Claude sessions")
        notes = []
        if inner:
            names = ", ".join(p.relative_to(d).as_posix() for p in inner[:6])
            notes.append(f"contains {len(inner)} nested repo(s): {names}")
            suppressed_nested[name] = [p.relative_to(d).as_posix() for p in inner]
        if third_party:
            notes.append("remote is not the operator's — looks like a clone of "
                         "someone else's repo")
        entries.append(Entry(
            name=name, path=str(rp), disposition="unclassified",
            source="", evidence="; ".join(ev_bits), notes=notes,
            facts={"is_git": gi["is_git"], "third_party_remote": third_party,
                   "has_claude_md": has_claude, "sessions": sc,
                   "nested_repos": len(inner), "empty": False,
                   "last_commit": gi.get("last_commit"),
                   "commit_count": gi.get("commit_count")}))

    # Nested rows come after their parents, so the table reads as a tree. Top
    # level is re-sorted because the deferred reference pass classifies out of
    # order; the report must still read as the directory listing does.
    by_parent: dict[str, list[Entry]] = {}
    for ne in nested_entries:
        top = ne.name.split("/")[0]
        by_parent.setdefault(top, []).append(ne)
    ordered: list[Entry] = []
    for e in sorted(entries, key=lambda x: x.name.lower()):
        ordered.append(e)
        for ne in sorted(by_parent.get(e.name, []), key=lambda x: x.name):
            ordered.append(ne)

    # ---- reverse gap: projects with no directory ----
    # "Has a directory" means path set OR a manifest claims it. A project whose
    # manifest names it but whose project.path is unset is NOT a gap — that is
    # the normal state immediately after adoption, and calling it a gap would
    # send the operator hunting for a directory that is right there.
    located: dict[str, str] = {pid: "project.path" for pid in proj_path}
    for dname, m in manifests.items():
        located.setdefault(m.id, f"manifest at {dname}/.aos/project.yaml")

    gaps: list[Gap] = []
    for p in live:
        pid = p["id"]
        if pid in located:
            if not p.get("path"):
                conflicts.append(
                    f"{pid}: located by {located[pid]} but project.path is unset "
                    f"— the tracker cannot resolve this project from a cwd. "
                    f"Set it with `work projects path {pid} <dir>`")
            continue
        dec = declared_projects.get(pid) or {}
        reason = dec.get("no_directory_reason") if isinstance(dec, dict) else str(dec)
        gaps.append(Gap(project_id=pid, title=p.get("title", ""),
                        reason=reason or "no recorded reason — operator input needed",
                        explained=bool(reason)))

    # ---- declared entries for directories that no longer exist ----
    on_disk = {d.name for d in tops}
    stale = sorted(n for n in declared_dirs if n not in on_disk)

    # ---- proposals: unclassified dirs that look like real untracked work ----
    proposals = _propose(ordered)

    return ReconcileReport(entries=ordered, gaps=gaps, proposals=proposals,
                           conflicts=conflicts, declared_stale=stale,
                           brief_conflicts=brief_conflicts)


def _propose(entries: list[Entry]) -> list[Proposal]:
    """Which unclassified directories look like the operator's own live work?

    Reads ``Entry.facts``, never the evidence prose. Deliberately conservative:
    the signal is a repo in the operator's own namespace that they have driven
    with Claude sessions or marked with a CLAUDE.md. A clone of someone else's
    repo is never proposed, and a directory with no version control at all is
    never proposed on its own — those are the two shapes that make up most of
    the untriaged pile, and neither is evidence of tracked work.
    """
    out: list[Proposal] = []
    for e in entries:
        if e.disposition != "unclassified":
            continue
        f = e.facts
        is_git = bool(f.get("is_git"))
        third_party = bool(f.get("third_party_remote"))
        has_claude = bool(f.get("has_claude_md"))
        sessions = int(f.get("sessions") or 0)
        commits = f.get("commit_count") or 0

        reasons: list[str] = []
        score = 0
        if is_git and not third_party:
            score += 2
            reasons.append(f"git repo in the operator's own namespace ({commits} commits)")
        if has_claude:
            score += 2
            reasons.append("has CLAUDE.md (explicit project marker)")
        if sessions:
            score += 2
            reasons.append(f"{sessions} Claude session(s) — the operator has worked here")
        if third_party:
            score -= 4
            reasons.append("clone of someone else's repo, not the operator's work")
        if not is_git:
            score -= 3
            reasons.append("no version control — more likely scratch or a data dump")

        if score >= 4:
            conf = "high"
        elif score >= 2:
            conf = "medium"
        else:
            continue
        out.append(Proposal(name=e.name, path=e.path, confidence=conf,
                            reasoning="; ".join(reasons)))
    return out


# ── rendering ───────────────────────────────────────────────────────

_ICON = {"linked": "=", "worktree_of": "w", "component_of": "c",
         "not_a_project": "x", "unclassified": "?"}


def render_report(r: ReconcileReport) -> str:
    _dirs, _projs, layer = load_declared()
    L: list[str] = []
    L.append("  Project directory reconciliation (report only — nothing written)")
    L.append(f"  dispositions config layer: {layer}")
    L.append("")

    counts: dict[str, int] = {}
    for e in r.entries:
        counts[e.disposition] = counts.get(e.disposition, 0) + 1
    L.append("  " + "   ".join(f"{k}={counts.get(k, 0)}" for k in DISPOSITIONS))
    L.append("")

    for e in r.entries:
        icon = _ICON.get(e.disposition, "?")
        indent = "    " if e.nested else "  "
        L.append(f"{indent}{icon} {e.name:<28} {e.label}")
        L.append(f"{indent}    {e.evidence}"
                 + (f"   [{e.source}]" if e.source else ""))
        for n in e.notes:
            L.append(f"{indent}    - {n}")
        L.append("")

    L.append("  Reverse gap — work projects with no directory:")
    if not r.gaps:
        L.append("    (none)")
    for g in r.gaps:
        mark = " " if g.explained else "!"
        L.append(f"    {mark} {g.project_id:<20} {g.title}")
        L.append(f"        {g.reason}")
    L.append("")

    if r.proposals:
        L.append("  Would propose as new projects (NOT created):")
        for p in r.proposals:
            L.append(f"    [{p.confidence}] {p.name}")
            L.append(f"        {p.reasoning}")
        L.append("")

    if r.unclassified:
        L.append(f"  Unclassified — {len(r.unclassified)} directories need an operator")
        L.append("  decision. None were guessed. Record each in")
        L.append("  ~/.aos/config/project-dispositions.yaml to stop re-litigating them:")
        for e in r.unclassified:
            L.append(f"    ? {e.name}")
        L.append("")

    if r.brief_conflicts:
        L.append(f"  Manifest conflicts ({len(r.brief_conflicts)}) — reported as "
                 f"brief_types.Conflict, so these surface on the project page:")
        for c in r.brief_conflicts:
            L.append(f"    [{c.severity}] {c.kind}: {c.message}")
        L.append("")

    if r.conflicts:
        L.append("  Conflicts (operator must resolve — nothing auto-resolved):")
        for c in r.conflicts:
            L.append(f"    ! {c}")
        L.append("")

    if r.declared_stale:
        L.append("  Declared but no longer on disk (config drift):")
        for n in r.declared_stale:
            L.append(f"    - {n}")
        L.append("")

    return "\n".join(L)


def report_to_dict(r: ReconcileReport) -> dict:
    return {
        "entries": [{
            "name": e.name, "path": e.path, "disposition": e.disposition,
            "target": e.target, "label": e.label, "evidence": e.evidence,
            "source": e.source, "notes": e.notes, "nested": e.nested,
        } for e in r.entries],
        "gaps": [{"project_id": g.project_id, "title": g.title,
                  "reason": g.reason, "explained": g.explained} for g in r.gaps],
        "proposals": [{"name": p.name, "path": p.path,
                       "confidence": p.confidence, "reasoning": p.reasoning}
                      for p in r.proposals],
        "conflicts": r.conflicts,
        "declared_stale": r.declared_stale,
        "brief_conflicts": [{"kind": c.kind, "severity": c.severity,
                             "message": c.message, "refs": list(c.refs)}
                            for c in r.brief_conflicts],
    }


def propose_instance_yaml(r: ReconcileReport) -> str:
    """Render a hand-editable instance dispositions file from this run.

    Derived findings become declarations so they stop being recomputed;
    ``unclassified`` entries are emitted **commented out** with their evidence,
    because turning an unknown into a declaration is the operator's call, not
    the reconciler's. Nothing is written — this returns text.
    """
    L = ["# Project dispositions — INSTANCE layer (this machine's real decisions).",
         "# Hand-editable. Read by core/engine/work/project_reconcile.py.",
         "# Replaces the framework example map wholesale.",
         "version: 1",
         "",
         "directories:"]
    for e in r.entries:
        if e.nested or e.disposition == "unclassified":
            continue
        L.append(f"  {e.name}:")
        L.append(f"    disposition: {e.disposition}")
        if e.target:
            L.append(f"    target: {e.target}")
        L.append(f"    reason: {json.dumps(e.evidence)}")
    if r.unclassified:
        L += ["", "  # ---- UNTRIAGED: uncomment and set a disposition + reason. ----",
              "  # Left commented on purpose: the reconciler will not decide these."]
        for e in r.unclassified:
            L.append(f"  # {e.name}:")
            L.append(f"  #   disposition: not_a_project   # or component_of / linked")
            L.append(f"  #   reason: \"\"   # evidence: {e.evidence}")
    L += ["", "projects:"]
    for g in r.gaps:
        L.append(f"  {g.project_id}:")
        L.append(f"    no_directory_reason: {json.dumps(g.reason)}"
                 + ("" if g.explained else "   # TODO: replace with the real reason"))
    return "\n".join(L) + "\n"


# ── the one write path — never called from this module ───────────────

def apply_dispositions(r: ReconcileReport, *, path: Path | None = None) -> Path:
    """Freeze this run's derived dispositions into the instance config.

    The ONLY write this module can perform. It touches one YAML file in the
    instance layer: no task data, no ``work.db``, no ``project.path``.
    Deliberately not wired into ``main()`` — a human reviews
    ``render_report()`` and calls this explicitly, or (better) hand-edits the
    file that ``propose_instance_yaml()`` prints.
    """
    target = path or (HOME / ".aos" / "config" / "project-dispositions.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(propose_instance_yaml(r))
    return target


def cli_entry(args: list[str]) -> None:
    """``work projects reconcile [--json|--gaps-only|--propose-instance]``.

    Lives here rather than in cli.py so the CLI's registration stays a
    four-line delegation — cli.py is another workstream's file.
    """
    if engine is None:
        print("Work engine not available")
        return
    r = reconcile()
    if "--json" in args:
        print(json.dumps(report_to_dict(r), indent=2, default=str))
    elif "--propose-instance" in args:
        print(propose_instance_yaml(r))
    elif "--gaps-only" in args:
        for g in r.gaps:
            print(f"  {' ' if g.explained else '!'} {g.project_id:<20} {g.reason}")
    else:
        print(render_report(r))
    # Deliberately no --apply. apply_dispositions() exists and must be invoked
    # explicitly, after a human has read the report above.


if __name__ == "__main__":
    cli_entry(sys.argv[1:])
