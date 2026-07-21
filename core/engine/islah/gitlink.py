#!/usr/bin/env python3
"""
Iṣlāḥ git-link — connect issues to the git tree, automatically.

For each issue, scans its app's repo(s) for:
  - commits whose message references the issue's display id (e.g. "QG-1")
  - a branch whose name references the issue (e.g. islah/qg1-...)
and writes branch + commits (sha, subject, files, +add/-del) onto the record.

Convention (same as Linear's "magic words"): put the display id (QG-1) in the
commit subject or the branch name and it links itself. Idempotent — safe to poll.

    islah git-link                 # link all apps
    islah git-link --app quran-garden
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Ledger  # noqa: E402

REGISTRY = Path("/Volumes/AOS-X/project/aos/config/islah-apps.yaml")


def disp_id(bid):
    if "#" in bid:
        p, n = bid.split("#", 1)
        return f"{p.upper()}-{n}"
    return bid.upper()


def slug_token(bid):
    # qg#1 -> "qg1" (matches branch names like islah/qg1-...)
    return bid.replace("#", "").lower()


def _git(repo, *args, timeout=20):
    for _ in range(3):  # AOS-X intermittent EPERM — retry
        try:
            r = subprocess.run(["git", "-C", repo, *args],
                               capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip()
        except Exception:
            continue
    return ""


def load_repos():
    repos = {}
    try:
        cfg = yaml.safe_load(REGISTRY.read_text()) or {}
        for slug, meta in (cfg.get("apps") or {}).items():
            repos[slug] = meta.get("repo")
    except Exception:
        pass
    # include any adjacent worktrees so branch/commit scans see agent branches
    return repos


def find_commits(repo, did):
    out = _git(repo, "log", "--all", f"--grep={did}", "--regexp-ignore-case",
               "--pretty=format:%H\x1f%h\x1f%s")
    commits = []
    for line in filter(None, out.splitlines()):
        full, short, subj = (line.split("\x1f") + ["", "", ""])[:3]
        stat = _git(repo, "show", "--numstat", "--format=", full)
        add = dele = 0
        files = []
        for row in filter(None, stat.splitlines()):
            parts = row.split("\t")
            if len(parts) == 3:
                a, d, f = parts
                add += int(a) if a.isdigit() else 0
                dele += int(d) if d.isdigit() else 0
                files.append(f)
        commits.append({"sha": short, "full": full, "subject": subj,
                        "add": add, "del": dele, "files": files})
    return commits


def find_branch(repo, token):
    out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
    for ref in out.splitlines():
        if token in ref.lower():
            return ref
    return None


def link(app_filter=None, verbose=True):
    repos = load_repos()
    lg = Ledger()
    touched = 0
    for b in lg.list(app=app_filter):
        repo = repos.get(b.app)
        if not repo or not Path(repo).exists():
            continue
        did, tok = disp_id(b.id), slug_token(b.id)
        commits = find_commits(repo, did)
        branch = find_branch(repo, tok)
        fields = {}
        if repo:
            fields["repo"] = repo
        if branch:
            fields["branch"] = branch
        if commits:
            fields["commits"] = commits
        if fields:
            lg.update(b.id, **fields)
            touched += 1
            if verbose:
                cs = ", ".join(c["sha"] for c in commits) or "—"
                print(f"  {did}: branch={branch or '—'}  commits=[{cs}]")
    if verbose:
        print(f"linked {touched} issue(s)")
    return touched


def main():
    ap = argparse.ArgumentParser(prog="islah git-link")
    ap.add_argument("--app")
    a = ap.parse_args()
    print("Scanning git tree for issue references…")
    link(app_filter=a.app)


if __name__ == "__main__":
    main()
