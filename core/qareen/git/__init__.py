"""Qareen git package — the Git/Ship cockpit backend.

Three concerns, kept strictly separate (the model's backbone):

  runner.py  DERIVED, ephemeral git state — a bounded async git runner plus the
             project resolver and a HEAD-sha TTL cache. NEVER mutates git, NEVER
             hits the network. Every call is timeout-bounded and killed on expiry.
  store.py   DURABLE, operator-owned ship state — the per-branch ship plan yaml
             under ~/.aos/ship/. Pure NEW instance data, graceful when absent.
  seed.py    The join — parses the operator's commit-triage spec into batches and
             pins them to live commit SHAs (the stable join between the two layers).

No code here ever pushes, merges, or fetches. Read-only by construction.
"""

from .runner import (
    GitError,
    GitTimeout,
    RepoResolution,
    resolve_repo,
    git_status,
    git_commits,
    git_below_base,
    git_worktrees,
    commit_subjects,
    head_sha,
    ordered_unmerged_shas,
    resolve_base,
)

__all__ = [
    "GitError",
    "GitTimeout",
    "RepoResolution",
    "resolve_repo",
    "git_status",
    "git_commits",
    "git_below_base",
    "git_worktrees",
    "commit_subjects",
    "head_sha",
    "ordered_unmerged_shas",
    "resolve_base",
]
