"""Bounded async git runner + project resolver + HEAD-sha TTL cache.

The load-bearing primitive of the cockpit. Every git invocation goes through
``_git`` which:

  * runs under a global Semaphore(4) so a burst of polls can't fork-bomb,
  * is timeout-bounded and KILLS the child on expiry (shell ``timeout`` is not
    available in this env — the subprocess timeout param is the only bound),
  * sets ``GIT_OPTIONAL_LOCKS=0`` + ``--no-optional-locks`` so read ops never
    take ``index.lock`` and won't fight a running IDE,
  * sets ``GIT_TERMINAL_PROMPT=0`` so nothing can ever block on auth, and
  * does NO network operation — there is no fetch/pull anywhere in this module.

git on the external SSD is slow; everything here stays bounded and read-only.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Read ops only. No auth prompts, no optional locks, no system config bleed-in.
GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
}

# Refuse to run git on arbitrary records — only repos under these roots.
# ~/project is a symlink to /Volumes/AOS-X/project, so both resolve to the same
# real path; we keep both for clarity and for machines without the symlink.
_ALLOWED_ROOTS = [
    Path("/Volumes/AOS-X/project"),
    Path.home() / "project",
]

_GIT_SEM = asyncio.Semaphore(4)

# Field/record separators for machine-parseable log output. Subjects, author
# names and refs cannot contain these control bytes, so splitting is unambiguous.
US = "\x1f"  # unit separator — between fields
RS = "\x1e"  # record separator — between commits

# 5s TTL in-memory cache keyed by (repo, HEAD-sha, kind, params). HEAD moving on
# a commit changes the key, so a fresh commit auto-invalidates everything.
_CACHE_TTL = 5.0
_CACHE: dict[tuple, tuple[float, Any]] = {}


class GitError(RuntimeError):
    """A git command exited non-zero where success was required."""


class GitTimeout(RuntimeError):
    """A git command exceeded its timeout and was killed."""


# ---------------------------------------------------------------------------
# The bounded runner
# ---------------------------------------------------------------------------


async def _git(repo: Path | str, *args: str, timeout: float = 5.0) -> tuple[int, str, str]:
    """Run ``git -C <repo> --no-optional-locks <args>`` bounded by ``timeout``.

    Returns ``(returncode, stdout, stderr)``. Never raises on a non-zero exit
    (callers decide) — only raises :class:`GitTimeout` when the child overruns.
    """
    async with _GIT_SEM:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo),
            "--no-optional-locks",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=GIT_ENV,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            proc.kill()
            await proc.wait()
            raise GitTimeout(f"git {' '.join(args)} timed out after {timeout}s")
        return (
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )


async def _git_text(repo: Path, *args: str, timeout: float = 5.0) -> str:
    """Run git and return stripped stdout, or "" on a non-zero exit."""
    code, out, _ = await _git(repo, *args, timeout=timeout)
    return out.strip() if code == 0 else ""


# A very short TTL memo for HEAD itself. head_sha is the cache KEY source and is
# called many times per request (status alone derives it ~7×); without this every
# cache *hit* still paid one `rev-parse HEAD` shell. 1s is short enough that a new
# commit is reflected almost immediately, long enough to collapse the open burst.
_HEAD_CACHE: dict[str, tuple[float, str]] = {}
_HEAD_TTL = 1.0


async def head_sha(repo: Path) -> str:
    """Full HEAD sha (the cache key). "" if it can't be read. Memoized ~1s."""
    rp = str(repo)
    now = time.monotonic()
    hit = _HEAD_CACHE.get(rp)
    if hit and (now - hit[0]) < _HEAD_TTL:
        return hit[1]
    val = await _git_text(repo, "rev-parse", "HEAD")
    # Don't cache empty (transient failure) — let the next call retry.
    if val:
        _HEAD_CACHE[rp] = (now, val)
    return val


async def _cached(repo: Path, head: str, key: tuple, factory: Callable[[], Awaitable[Any]]) -> Any:
    """5s TTL cache keyed by (repo, head, key), with SINGLE-FLIGHT.

    Opening the cockpit fires 5 endpoints concurrently; several call the same
    factory (git_status, resolve_base, the subject map …). Without single-flight
    each concurrent miss ran the full factory because the result is only stored
    *after* the await — N misses = N git shells. We now store an in-flight Future
    BEFORE awaiting, so concurrent callers for the same key await one computation.
    The store/get pair runs with no await between them, so it's atomic on the loop.
    """
    ck = (str(repo), head, key)
    now = time.monotonic()
    hit = _CACHE.get(ck)
    if hit is not None:
        ts, val = hit
        if isinstance(val, asyncio.Future):
            return await val  # another caller is computing this key — join it
        if (now - ts) < _CACHE_TTL:
            return val

    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _CACHE[ck] = (now, fut)
    try:
        val = await factory()
    except BaseException as exc:
        # Fail the waiters and drop the entry so the next call retries cleanly.
        if not fut.done():
            fut.set_exception(exc)
        _CACHE.pop(ck, None)
        raise
    _CACHE[ck] = (time.monotonic(), val)
    if not fut.done():
        fut.set_result(val)

    # Opportunistic prune so the dict can't grow without bound across branches.
    if len(_CACHE) > 256:
        for k, (ts, v) in list(_CACHE.items()):
            if not isinstance(v, asyncio.Future) and (now - ts) >= _CACHE_TTL:
                _CACHE.pop(k, None)
    return val


# ---------------------------------------------------------------------------
# Project resolution (shared, graceful — never a 500 from an unlinked repo)
# ---------------------------------------------------------------------------


@dataclass
class RepoResolution:
    """Outcome of resolving a project_id to a usable repo path.

    Exactly one of ``repo`` (proceed) or ``payload`` (early return) is set.
    """

    repo: Path | None = None
    payload: dict | None = None
    status_code: int = 200


def _is_allowed(path: Path) -> bool:
    try:
        rp = path.resolve()
    except Exception:
        return False
    for root in _ALLOWED_ROOTS:
        try:
            rp.relative_to(root.resolve())
            return True
        except Exception:
            continue
    return False


async def resolve_repo(request, project_id: str) -> RepoResolution:
    """Resolve a project to a git repo path, gracefully.

    Returns a :class:`RepoResolution`. When ``repo`` is None the caller returns
    ``payload`` with ``status_code``. Outcomes:
      * 404                                   — no such project (or no ontology)
      * {linked: false}                       — project has no path
      * {linked: true, is_repo: false}        — path missing / off-allowlist / not a work tree
    """
    from ..ontology.types import ObjectType  # local import: avoid import cycles

    ontology = getattr(request.app.state, "ontology", None)
    if ontology is None:
        return RepoResolution(payload={"detail": "ontology unavailable"}, status_code=404)

    proj = None
    try:
        proj = ontology.get(ObjectType.PROJECT, project_id)
    except Exception:
        proj = None
    if proj is None:
        return RepoResolution(
            payload={"detail": "project not found", "project_id": project_id},
            status_code=404,
        )

    path = getattr(proj, "path", None)
    if not path:
        return RepoResolution(payload={"linked": False}, status_code=200)

    repo = Path(str(path))
    if not _is_allowed(repo):
        return RepoResolution(
            payload={"linked": True, "is_repo": False, "reason": "path not under an allowlisted root"},
            status_code=200,
        )
    if not repo.exists():
        return RepoResolution(
            payload={"linked": True, "is_repo": False, "reason": "path does not exist"},
            status_code=200,
        )

    try:
        inside = await _git_text(repo, "rev-parse", "--is-inside-work-tree", timeout=4.0)
    except GitTimeout:
        return RepoResolution(payload={"linked": True, "is_repo": False, "reason": "git timed out"}, status_code=200)
    if inside != "true":
        return RepoResolution(payload={"linked": True, "is_repo": False}, status_code=200)

    return RepoResolution(repo=repo)


# ---------------------------------------------------------------------------
# Base resolution + the unmerged set
# ---------------------------------------------------------------------------


async def resolve_base(repo: Path, preferred: str = "origin/main") -> tuple[str | None, bool]:
    """Resolve the merge base ref. Returns (base, base_missing).

    Tries the preferred ref, then origin/main, then local main. base_missing is
    True only when nothing resolves (ahead/behind become null).
    """
    candidates = [
        preferred,
        "origin/main",
        "main",
        "origin/master",
        "master",
        "@{upstream}",
        "origin/HEAD",
    ]
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        # --end-of-options: never let a candidate that looks like a flag (e.g.
        # "--output=...") be parsed as an option by rev-parse. The only raw-base
        # sink in the module; everything downstream uses the value it returns.
        code, _, _ = await _git(
            repo, "rev-parse", "--verify", "--quiet", "--end-of-options", cand, timeout=4.0
        )
        if code == 0:
            return cand, False
    return None, True


async def ordered_unmerged_shas(repo: Path, base: str) -> list[str]:
    """Full SHAs of ``base..HEAD`` in oldest→newest order (for SHA assignment).

    The heaviest read in the cockpit (one rev-list over ~150 commits, ~50ms on
    the external SSD). Cached by HEAD-sha so it runs once per commit, not per
    render. Still bounded — no ``--all``.
    """
    head = await head_sha(repo)

    async def _factory() -> list[str]:
        out = await _git_text(
            repo, "rev-list", "--reverse", "--end-of-options", f"{base}..HEAD", timeout=8.0
        )
        return [ln for ln in out.splitlines() if ln.strip()]

    return await _cached(repo, head, ("unmerged-shas", base), _factory)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _categorize_status(porcelain: str) -> dict:
    staged = unstaged = untracked = 0
    lines = [ln for ln in porcelain.splitlines() if ln]
    for ln in lines:
        xy = ln[:2]
        if xy == "??":
            untracked += 1
            continue
        # XY: index/staged col, worktree/unstaged col.
        if len(ln) >= 1 and ln[0] not in (" ", "?"):
            staged += 1
        if len(ln) >= 2 and ln[1] not in (" ", "?"):
            unstaged += 1
    return {
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "total": len(lines),
    }


async def current_branch(repo: Path) -> str:
    """Abbrev-ref of HEAD ("HEAD" when detached). Memoized ~1s.

    Resolved OUTSIDE the head-keyed status cache and folded into its key: a branch
    switch at the SAME commit (``git switch -c foo``) leaves HEAD unchanged, so
    without this the cached status — and the plan filename derived from it — would
    serve the OLD branch for up to the TTL and route decisions to the wrong yaml.
    """
    rp = str(repo)
    now = time.monotonic()
    hit = _BRANCH_TTL_CACHE.get(rp)
    if hit and (now - hit[0]) < _HEAD_TTL:
        return hit[1]
    val = await _git_text(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if val:
        _BRANCH_TTL_CACHE[rp] = (now, val)
    return val


_BRANCH_TTL_CACHE: dict[str, tuple[float, str]] = {}


async def git_status(repo: Path, preferred_base: str = "origin/main") -> dict:
    """Compute the derived status block. Cached 5s by (HEAD-sha, branch)."""
    head = await head_sha(repo)
    branch0 = await current_branch(repo)

    async def _factory() -> dict:
        branch = branch0
        short = await _git_text(repo, "rev-parse", "--short", "HEAD")
        detached = branch == "HEAD"
        if detached:
            branch = short

        base, base_missing = await resolve_base(repo, preferred_base)

        ahead = behind = 0
        if base is not None:
            lr = await _git_text(
                repo, "rev-list", "--left-right", "--count", "--end-of-options", f"{base}...HEAD"
            )
            # "<behind>\t<ahead>" — left side is base (behind), right side is HEAD (ahead).
            parts = lr.split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                except ValueError:
                    behind = ahead = 0

        porcelain = await _git_text(repo, "status", "--porcelain=v1")
        dirty = _categorize_status(porcelain)

        wt = await _git_text(repo, "worktree", "list", "--porcelain")
        worktree_count = sum(1 for ln in wt.splitlines() if ln.startswith("worktree "))

        return {
            "linked": True,
            "is_repo": True,
            "branch": branch,
            "detached": detached,
            "head": short,
            "head_sha": head,
            "base": base,
            "ahead": ahead,
            "behind": behind,
            "base_missing": base_missing,
            "dirty": dirty,
            "worktree_count": worktree_count,
        }

    return await _cached(repo, head, ("status", preferred_base, branch0), _factory)


# ---------------------------------------------------------------------------
# Commits (the unmerged set — also the graph data the frontend renders lanes from)
# ---------------------------------------------------------------------------


def _parse_commits(raw: str) -> list[dict]:
    commits: list[dict] = []
    for record in raw.split(RS):
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split(US)
        if len(fields) < 7:
            continue
        sha, short, parents, author, ts, refs, subject = fields[:7]
        parent_list = [p for p in parents.split(" ") if p]
        ref_list = [r.strip() for r in refs.split(",") if r.strip()] if refs else []
        try:
            ts_int = int(ts)
        except ValueError:
            ts_int = 0
        commits.append(
            {
                "sha": sha,
                "short": short,
                "parents": parent_list,
                "author": author,
                "ts": ts_int,
                "refs": ref_list,
                "subject": subject,
            }
        )
    return commits


async def git_commits(repo: Path, preferred_base: str = "origin/main", limit: int = 60) -> dict:
    """The unmerged commit set ``base..HEAD``, newest→oldest (date-order).

    %P parents are included so the graph session can render lanes with no second
    backend pass. ``total`` is the TRUE count even when the list is capped.
    """
    limit = max(1, min(int(limit), 200))  # HARD CAP 200, never --all
    head = await head_sha(repo)

    async def _factory() -> dict:
        base, base_missing = await resolve_base(repo, preferred_base)
        if base is None:
            return {"commits": [], "total": 0, "truncated": False, "base": None, "base_missing": True}

        fmt = US.join(["%H", "%h", "%P", "%an", "%at", "%D", "%s"]) + RS
        raw = await _git_text(
            repo,
            "log",
            f"--max-count={limit}",
            "--date-order",
            f"--pretty=format:{fmt}",
            "--end-of-options",
            f"{base}..HEAD",
            timeout=8.0,
        )
        commits = _parse_commits(raw)

        total_str = await _git_text(
            repo, "rev-list", "--count", "--end-of-options", f"{base}..HEAD"
        )
        try:
            total = int(total_str)
        except ValueError:
            total = len(commits)

        return {
            "commits": commits,
            "total": total,
            "truncated": total > len(commits),
            "base": base,
            "base_missing": False,
        }

    return await _cached(repo, head, ("commits", preferred_base, limit), _factory)


# ---------------------------------------------------------------------------
# Below the ship line — bounded merged context under origin/main
# ---------------------------------------------------------------------------


async def git_below_base(
    repo: Path, preferred_base: str = "origin/main", limit: int = 6
) -> dict:
    """A few commits AT/BELOW the base ref — the merged ground beneath the ship line.

    Same {sha, parents[], …} shape as :func:`git_commits` so the graph can dim
    these below the line without a second parser. Hard-capped, read-only, no
    ``--all`` — just the most recent ``limit`` commits reachable from the base.
    """
    limit = max(1, min(int(limit), 30))
    head = await head_sha(repo)

    async def _factory() -> dict:
        base, base_missing = await resolve_base(repo, preferred_base)
        if base is None:
            return {"commits": [], "base": None, "base_missing": True}

        fmt = US.join(["%H", "%h", "%P", "%an", "%at", "%D", "%s"]) + RS
        raw = await _git_text(
            repo,
            "log",
            f"--max-count={limit}",
            "--date-order",
            f"--pretty=format:{fmt}",
            "--end-of-options",
            base,
            timeout=8.0,
        )
        return {"commits": _parse_commits(raw), "base": base, "base_missing": False}

    return await _cached(repo, head, ("below", preferred_base, limit), _factory)


# ---------------------------------------------------------------------------
# Worktrees — read-only porcelain parse
# ---------------------------------------------------------------------------


async def git_worktrees(repo: Path) -> dict:
    """Every worktree attached to this repo, parsed from ``worktree list --porcelain``.

    Records carry path, branch (or detached/bare), and short HEAD. The block for
    ``repo`` itself is flagged ``is_current`` so the UI can mark "this checkout".
    Purely informational — nothing here touches or creates a worktree.
    """
    head = await head_sha(repo)

    async def _factory() -> dict:
        out = await _git_text(repo, "worktree", "list", "--porcelain", timeout=5.0)
        worktrees: list[dict] = []
        cur: dict = {}

        def flush() -> None:
            if cur:
                worktrees.append(dict(cur))

        for ln in out.splitlines():
            if not ln.strip():
                flush()
                cur.clear()
                continue
            if ln.startswith("worktree "):
                flush()
                cur.clear()
                cur["path"] = ln[len("worktree ") :]
            elif ln.startswith("HEAD "):
                full = ln[len("HEAD ") :]
                cur["head_sha"] = full
                cur["head"] = full[:7]
            elif ln.startswith("branch "):
                cur["branch"] = ln[len("branch ") :].replace("refs/heads/", "")
            elif ln.strip() == "detached":
                cur["detached"] = True
            elif ln.strip() == "bare":
                cur["bare"] = True
            elif ln.startswith("locked"):
                cur["locked"] = True
        flush()

        try:
            repo_real = str(repo.resolve())
        except Exception:
            repo_real = str(repo)
        for i, w in enumerate(worktrees):
            w["primary"] = i == 0
            try:
                w["is_current"] = str(Path(w["path"]).resolve()) == repo_real
            except Exception:
                w["is_current"] = False

        return {"worktrees": worktrees, "count": len(worktrees)}

    return await _cached(repo, head, ("worktrees",), _factory)


# ---------------------------------------------------------------------------
# Subjects — a lightweight sha → subject map for the whole unmerged set
# ---------------------------------------------------------------------------


async def commit_subjects(
    repo: Path, preferred_base: str = "origin/main", limit: int = 400
) -> dict:
    """A ``{sha: subject}`` map for every commit in ``base..HEAD`` (bounded).

    The ship ledger lists each batch's commits by SHA; without this it could only
    show a subject for commits inside the graph's loaded window (newest 60),
    falling back to "(beyond loaded window)" for older ones. This one cheap log
    (SHAs + subjects only, no graph data) lets every batch row show its message
    regardless of how much of the graph is loaded. Hard-capped, read-only.
    """
    limit = max(1, min(int(limit), 600))
    head = await head_sha(repo)

    async def _factory() -> dict:
        base, _ = await resolve_base(repo, preferred_base)
        if base is None:
            return {}
        raw = await _git_text(
            repo,
            "log",
            f"--max-count={limit}",
            f"--pretty=format:%H{US}%s",
            "--end-of-options",
            f"{base}..HEAD",
            timeout=8.0,
        )
        out: dict[str, str] = {}
        for ln in raw.splitlines():
            if US in ln:
                sha, subj = ln.split(US, 1)
                out[sha] = subj
        return out

    return await _cached(repo, head, ("subjects", preferred_base, limit), _factory)
