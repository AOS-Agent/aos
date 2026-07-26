#!/usr/bin/env python3
"""Worktree audit and normalisation planner — branches belong inside projects.

Three incompatible conventions are in use, which is most of why ``~/project/``
looks like a mess:

  * ``/private/tmp/aos-*`` — ephemeral **by accident**, not by design. macOS
    periodically deletes *files* under ``/private/tmp`` while leaving the
    directory tree standing, so a worktree there does not vanish cleanly: it
    rots into an empty skeleton that git still has registered.
  * ``<project>/.claude/worktrees/agent-<hash>/`` — the right *place* (inside the
    project) but named by opaque agent hash, so no human can tell what any of
    them is without asking git.
  * top-level siblings (``quran-tools-islah-qg1``, ``deenoverdunya-falak``) —
    correct-ish and durable, but they pollute ``~/project/`` and break the
    one-directory-one-project model the disposition table assumes.

The canonical layout this module plans toward is ``<project>/.claude/worktrees/``
— the Claude Code harness's own convention, adopted rather than fought, with a
readable index so a human can tell what is what. See ``LAYOUT_PROPOSAL``.

**Report-only.** ``git worktree move``/``prune``/``rm`` touch real checkouts, so
every action is rendered as a command for the operator to read and approve.
Nothing here executes git mutations.
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
    import backend as engine
except ImportError:  # pragma: no cover
    engine = None

HOME = Path.home()
PROJECT_ROOT = HOME / "project"

# The harness's own convention. Adopted deliberately: Claude Code creates
# worktrees here for worktree-isolated agents, so declaring a competing scheme
# would mean fighting the tool that generates most of them.
CANONICAL_SUBDIR = Path(".claude") / "worktrees"

# Locations that are wiped by the operating system. A worktree here WILL be
# destroyed; the only question is when.
EPHEMERAL_PREFIXES = ("/private/tmp/", "/tmp/", "/var/tmp/", "/private/var/tmp/")


LAYOUT_PROPOSAL = """\
CANONICAL WORKTREE LAYOUT
=========================

  <project>/.claude/worktrees/<branch-slug>/     the checkout
  <project>/.claude/worktrees/INDEX.md           generated, human-readable

Why `.claude/worktrees/`:
  It is the Claude Code harness's own convention — the harness creates worktrees
  there for worktree-isolated agents, and dod already has 20 of them. Adopting it
  costs nothing and means the tool generating most worktrees needs no changes.
  Inventing a competing directory would guarantee a fourth convention.

Naming — branch-slug, not agent hash:
  `agent-a0ce6349b088395e1` is unreadable; `feat-doctrine-decisions` is not.
  The harness picks the hash, so AOS cannot rename at creation time. Therefore:

  RECOMMENDATION: an INDEX.md, not symlinks.

  Symlinks were considered and rejected. A `feat-doctrine-decisions ->
  agent-a0ce.../` symlink inside the worktrees directory is a path git can
  resolve, so tooling can end up with two live paths for one worktree, and
  `git worktree move` on the target silently breaks the link. An index file
  cannot be mistaken for a checkout, cannot break git, is diffable, and is
  regenerable from `git worktree list --porcelain` at any time — so it can never
  drift for long. Generate it; never hand-maintain it.

  A worktree the OPERATOR creates by hand should still be named for its branch.
  Hashes are the harness's business, not a convention to imitate.

Rule — never `/private/tmp` (or any tmp):
  macOS deletes files under /private/tmp on a periodic timer and the volume is
  reboot-cleared. It does NOT delete the directories, so a rotted worktree leaves
  a full directory skeleton with zero files that git still lists as registered.
  That is precisely how 19 aos worktrees died. `~/.aos/work/runner/worktrees/`
  (which the AOS work runner already uses) is the correct home for machine-owned
  worktrees; project-owned worktrees go in `.claude/worktrees/`.
"""


# ── data ────────────────────────────────────────────────────────────

@dataclass
class WorktreeRow:
    project_id: str | None
    repo: str                       # the main checkout this belongs to
    path: str
    branch: str
    is_main: bool = False
    exists: bool = False
    registered: bool = True         # git still lists it
    prunable: bool = False
    prunable_reason: str = ""
    file_count: int | None = None   # None when the directory is gone
    ahead: int | None = None        # commits ahead of the default branch
    dirty: int | None = None        # changed/untracked entries
    location_kind: str = ""         # canonical | sibling | ephemeral | main | other
    verdict: str = ""               # plain English: what this is
    safe: bool | None = None        # is the proposed action safe?


@dataclass
class Action:
    kind: str                       # prune | move | rmdir | rm_husk | rename_note
    command: str                    # exactly what the operator would run
    why: str
    safe: bool
    caveat: str = ""


@dataclass
class WorktreeReport:
    rows: list[WorktreeRow] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ── git plumbing ────────────────────────────────────────────────────

def _git(cwd: Path, *args: str, timeout: int = 30) -> str | None:
    try:
        out = subprocess.run(("git", "-C", str(cwd), *args),
                             capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _default_branch(repo: Path) -> str:
    """The branch to measure 'ahead' against. Falls back sensibly."""
    for cand in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", cand) is not None:
            return cand
    return "HEAD"


def list_worktrees(repo: Path) -> list[dict]:
    """Parse ``git worktree list --porcelain`` including prunable reasons.

    The reason matters: ``prunable gitdir file points to non-existent location``
    means the worktree's ``.git`` file is gone, which is a very different
    situation from the directory being gone.
    """
    raw = _git(repo, "worktree", "list", "--porcelain")
    if not raw:
        return []
    out: list[dict] = []
    cur: dict = {}
    for line in raw.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
                cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree" and cur:
            out.append(cur)
            cur = {}
        cur[key] = val
    if cur:
        out.append(cur)
    return out


def _classify_location(path: Path, repo: Path) -> str:
    s = str(path)
    if path == repo:
        return "main"
    if s.startswith(EPHEMERAL_PREFIXES):
        return "ephemeral"
    try:
        if path.is_relative_to(repo / ".claude" / "worktrees"):
            return "canonical"
    except AttributeError:  # pragma: no cover - py<3.9
        pass
    if path.parent == repo.parent:
        return "sibling"
    return "other"


def audit(repos: dict[str, Path] | None = None) -> WorktreeReport:
    """Audit every worktree of every linked project. Mutates nothing.

    For each worktree this establishes, from git rather than from its name:
    branch, commits ahead of the default branch, dirty entry count, whether the
    directory exists, and whether it holds any files at all. That last check is
    the one that matters most: a directory can exist, be registered, and still
    contain nothing.
    """
    if repos is None:
        repos = {}
        if engine:
            for p in engine.load_all().get("projects", []):
                if p.get("path") and p.get("status") not in ("cancelled", "archived"):
                    d = Path(p["path"]).expanduser().resolve()
                    if (d / ".git").exists():
                        repos[p["id"]] = d

    rep = WorktreeReport()
    for pid, repo in sorted(repos.items()):
        default = _default_branch(repo)
        for wt in list_worktrees(repo):
            path = Path(wt.get("worktree", ""))
            branch = (wt.get("branch") or "").replace("refs/heads/", "") or "(detached)"
            is_main = path.resolve() == repo
            prunable = "prunable" in wt
            row = WorktreeRow(
                project_id=pid, repo=str(repo), path=str(path), branch=branch,
                is_main=is_main, exists=path.is_dir(), prunable=prunable,
                prunable_reason=wt.get("prunable", ""),
                location_kind=_classify_location(path, repo),
            )
            if row.exists:
                try:
                    row.file_count = sum(1 for _ in path.rglob("*") if _.is_file())
                except Exception:
                    row.file_count = None
                if (path / ".git").exists():
                    ahead = _git(path, "rev-list", "--count", f"{default}..HEAD")
                    row.ahead = int(ahead) if ahead and ahead.isdigit() else None
                    st = _git(path, "status", "--short")
                    row.dirty = len(st.splitlines()) if st else 0
            _verdict(row)
            rep.rows.append(row)

    _plan(rep)
    return rep


def _verdict(row: WorktreeRow) -> None:
    """Say in plain English what this worktree actually is."""
    if row.is_main:
        row.verdict = "the main checkout — not a worktree to move"
        row.safe = None
        return
    if not row.exists:
        row.verdict = "directory is gone; only a stale git registration remains"
        row.safe = True
        return
    if row.file_count == 0:
        row.verdict = ("EMPTY SKELETON — directory tree survives but holds zero "
                       "files; the OS tmp cleaner deleted the contents. No work "
                       "is recoverable here")
        row.safe = True
        return
    bits = [f"live worktree on '{row.branch}'"]
    if row.ahead:
        bits.append(f"{row.ahead} commit(s) NOT in the default branch")
    elif row.ahead == 0:
        bits.append("fully merged")
    if row.dirty:
        bits.append(f"{row.dirty} uncommitted entr(y/ies)")
    row.verdict = ", ".join(bits)
    # `safe` here means "safe to MOVE". Only dirty state blocks `git worktree
    # move` (it refuses without --force); committed-but-unmerged work travels
    # with the worktree perfectly well, so `ahead` must NOT make a move look
    # risky. It matters for *deletion*, which is why it stays in the verdict.
    row.safe = not row.dirty


def _plan(rep: WorktreeReport) -> None:
    """Turn the audit into commands the operator can read and approve."""
    by_repo: dict[str, list[WorktreeRow]] = {}
    for r in rep.rows:
        by_repo.setdefault(r.repo, []).append(r)

    for repo, rows in sorted(by_repo.items()):
        stale = [r for r in rows if r.prunable or (not r.exists and not r.is_main)]
        husks = [r for r in rows if r.exists and r.file_count == 0 and not r.is_main]

        if stale:
            rep.actions.append(Action(
                kind="prune",
                command=f"git -C {repo} worktree prune -v",
                why=(f"{len(stale)} stale registration(s). `prune` removes only the "
                     f"admin records under .git/worktrees/ — it does NOT delete any "
                     f"working directory, so it cannot lose work."),
                safe=True,
            ))
        if husks:
            # One reviewable command rather than 17 near-identical lines. The
            # paths are listed in full so the operator reads exactly what goes.
            paths = " \\\n      ".join(sorted(h.path for h in husks))
            rep.actions.append(Action(
                kind="rm_husk",
                command=f"rm -rf {paths}",
                why=(f"{len(husks)} empty skeleton(s): each verified to contain "
                     f"ZERO regular files — only a directory tree the OS tmp "
                     f"cleaner left behind. Nothing is recoverable from them."),
                safe=True,
                caveat="run the prune above first, so git is not left pointing at them",
            ))

        for r in rows:
            if r.is_main or not r.exists or r.file_count == 0:
                continue
            if r.location_kind in ("canonical",):
                continue
            if r.location_kind == "ephemeral":
                dest = Path(repo) / CANONICAL_SUBDIR / _slug(r.branch)
                rep.actions.append(Action(
                    kind="move",
                    command=f"git -C {repo} worktree move {r.path} {dest}",
                    why=(f"'{r.branch}' is live ({r.file_count} files) but sits in a "
                         f"tmp location the OS wipes. Move it inside the project."),
                    safe=bool(r.safe),
                    caveat=_move_caveat(r),
                ))
            elif r.location_kind == "sibling":
                dest = Path(repo) / CANONICAL_SUBDIR / _slug(r.branch)
                rep.actions.append(Action(
                    kind="move",
                    command=f"git -C {repo} worktree move {r.path} {dest}",
                    why=(f"top-level sibling pollutes ~/project/ and breaks "
                         f"one-directory-one-project; branch '{r.branch}'"),
                    safe=bool(r.safe),
                    caveat=_move_caveat(r),
                ))

        hashed = [r for r in rows
                  if r.location_kind == "canonical" and r.path.rsplit("/", 1)[-1].startswith("agent-")]
        if hashed:
            rep.actions.append(Action(
                kind="rename_note",
                command=(f"python3 ~/aos/core/engine/work/project_worktrees.py "
                         f"--index {repo}"),
                why=(f"{len(hashed)} worktree(s) named by agent hash. These are in "
                     f"the right place; they are just unreadable. Generate "
                     f"INDEX.md rather than renaming — the harness owns these "
                     f"directory names and will keep creating them."),
                safe=True,
            ))


def _move_caveat(row: WorktreeRow) -> str:
    """What actually needs care before moving this worktree.

    Dirty state is the only blocker (``git worktree move`` refuses without
    ``--force``). Unmerged commits are called out separately because they mean
    "do not delete this one", not "do not move it".
    """
    parts = []
    if row.dirty:
        parts.append(f"{row.dirty} uncommitted entr(y/ies) — commit or stash "
                     f"first; `worktree move` refuses a dirty worktree without "
                     f"--force, and --force here would be reckless")
    if row.ahead:
        parts.append(f"holds {row.ahead} commit(s) not in the default branch, "
                     f"which move preserves — but do not delete this worktree")
    return "; ".join(parts)


def _slug(branch: str) -> str:
    return branch.replace("/", "-").replace("_", "-").strip("-") or "detached"


# ── the readable index (the alternative to symlinks) ────────────────

def render_index(repo: Path) -> str:
    """A human-readable map of a repo's worktrees, generated from git.

    Regenerable at any time, therefore it can never drift for long. Cannot be
    mistaken for a checkout and cannot break ``git worktree move`` — which is
    exactly why this is preferred over branch-named symlinks.
    """
    default = _default_branch(repo)
    L = [f"# Worktrees — {repo.name}", "",
         "Generated from `git worktree list --porcelain`. Do not hand-edit;",
         "regenerate with `project_worktrees.py --index <repo>`.", "",
         "| Branch | Directory | Ahead | Dirty |",
         "|---|---|---|---|"]
    for wt in list_worktrees(repo):
        path = Path(wt.get("worktree", ""))
        branch = (wt.get("branch") or "").replace("refs/heads/", "") or "(detached)"
        if path.resolve() == repo:
            continue
        ahead = dirty = "-"
        if path.is_dir() and (path / ".git").exists():
            a = _git(path, "rev-list", "--count", f"{default}..HEAD")
            ahead = a if a else "-"
            st = _git(path, "status", "--short")
            dirty = str(len(st.splitlines())) if st else "0"
        name = path.name if path.is_dir() else f"{path.name} (missing)"
        L.append(f"| `{branch}` | `{name}` | {ahead} | {dirty} |")
    return "\n".join(L) + "\n"


def write_index(repo: Path) -> Path:
    """Write INDEX.md. A generated file inside the project — the one write here."""
    target = repo / CANONICAL_SUBDIR / "INDEX.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_index(repo))
    return target


# ── rendering ───────────────────────────────────────────────────────

def render_report(rep: WorktreeReport) -> str:
    L = ["  Worktree audit (report only — no git mutation performed)", ""]
    by_repo: dict[str, list[WorktreeRow]] = {}
    for r in rep.rows:
        by_repo.setdefault(r.repo, []).append(r)

    for repo, rows in sorted(by_repo.items()):
        pid = rows[0].project_id
        live = [r for r in rows if r.exists and r.file_count and not r.is_main]
        husk = [r for r in rows if r.exists and r.file_count == 0 and not r.is_main]
        gone = [r for r in rows if not r.exists]
        L.append(f"  {pid}  ({repo})")
        L.append(f"    {len(rows) - 1} worktree(s): {len(live)} live, "
                 f"{len(husk)} empty skeleton, {len(gone)} directory gone")
        for r in sorted(rows, key=lambda x: (x.is_main is False, x.location_kind, x.branch)):
            if r.is_main:
                continue
            flag = {"canonical": "ok", "sibling": "SIBLING",
                    "ephemeral": "TMP", "other": "?"}.get(r.location_kind, "?")
            L.append(f"      [{flag:<8}] {r.branch:<34} {Path(r.path).name}")
            L.append(f"                 {r.verdict}")
        L.append("")

    L.append("  Proposed actions — nothing has been run:")
    if not rep.actions:
        L.append("    (none)")
    for a in rep.actions:
        L.append(f"    [{'safe' if a.safe else 'NEEDS CARE'}] {a.kind}")
        L.append(f"        $ {a.command}")
        L.append(f"        {a.why}")
        if a.caveat:
            L.append(f"        caveat: {a.caveat}")
        L.append("")
    for n in rep.notes:
        L.append(f"  note: {n}")
    return "\n".join(L)


def report_to_dict(rep: WorktreeReport) -> dict:
    return {
        "rows": [vars(r) for r in rep.rows],
        "actions": [vars(a) for a in rep.actions],
        "notes": rep.notes,
    }


def cli_entry(args: list[str]) -> None:
    """``work projects worktrees [--json|--layout|--index <repo>]`` — reports."""
    if "--layout" in args:
        print(LAYOUT_PROPOSAL)
        return
    if "--index" in args:
        i = args.index("--index")
        if i + 1 < len(args):
            print(render_index(Path(args[i + 1]).expanduser().resolve()))
        else:
            print("Usage: --index <repo path>")
        return
    if engine is None:
        print("Work engine not available")
        return
    rep = audit()
    if "--json" in args:
        print(json.dumps(report_to_dict(rep), indent=2, default=str))
        return
    print(render_report(rep))
    # No --apply. Every mutation is printed as a command for the operator.


if __name__ == "__main__":
    cli_entry(sys.argv[1:])
