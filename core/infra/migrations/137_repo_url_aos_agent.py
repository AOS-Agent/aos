"""
Migration 137: point instance git remotes at AOS-Agent/aos.

Background
----------
The repository was renamed from `hishamalhadi/aos` to `AOS-Agent/aos` (same
repository — GitHub id 1188268574, confirmed by the API redirect). This
release rewrites the framework's own references: `install.sh`, `bootstrap.sh`,
`README.md` and `core/engine/work/backend.py`.

That covers every NEW install. It does not cover the machines already out
there, and those are the ones that matter: `release-manager` fetches through
the `origin` remote of the instance's source checkout (`~/.aos/repo`, or
`~/project/aos` on a dev machine), and that remote still holds the old URL.

Nothing is broken today — GitHub keeps serving the old name, `git push`
prints "This repository moved" and proceeds. But that alias is a courtesy,
not a guarantee: it holds only while nobody registers a new repository at
`hishamalhadi/aos`. The day somebody does, every machine still carrying the
old remote silently fetches a stranger's code and deploys it at 4am. A
rename is cheap to follow now and unrecoverable to have ignored later.

So the framework change ships with the bridge that carries the instance
across, per the atomic migration rule.

What this migration does
------------------------
  1. For each known instance checkout, read `git remote get-url <remote>`
     for every remote it has.
  2. Rewrite ONLY a URL whose path is exactly the old `hishamalhadi/aos`
     (any host spelling: https, ssh, git@, with or without `.git`).
  3. Leave every other remote alone — a fork, a mirror, a local path
     remote (`app-src` on the reference machine points at
     `~/project/aos-app`) and anything already on AOS-Agent.

Idempotent: `check()` is true once no known checkout has a remote on the old
path, which is also the state on a machine that never had one. Reversible:
`down()` returns False — pointing a remote back at a name that may by then
belong to someone else is not a thing a migration should offer.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

DESCRIPTION = "Point instance git remotes at AOS-Agent/aos (repo renamed)"

OLD_PATH = "hishamalhadi/aos"
NEW_PATH = "AOS-Agent/aos"

# Matches the old path only as a whole repo path, so a remote that merely
# contains the string (a directory named after it, say) is not rewritten.
_OLD_RE = re.compile(r"(?<![\w.-])" + re.escape(OLD_PATH) + r"(?=(\.git)?/?$)")


def _checkouts() -> list[Path]:
    """Instance checkouts release-manager may fetch through."""
    home = Path.home()
    return [home / ".aos" / "repo", home / "project" / "aos"]


def _remotes(repo: Path) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "remote"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:  # noqa: BLE001
        return []
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def _url(repo: Path, remote: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", remote],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:  # noqa: BLE001
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _stale(repo: Path) -> list[tuple[str, str, str]]:
    """(remote, old_url, new_url) for every remote still on the old path."""
    out: list[tuple[str, str, str]] = []
    for remote in _remotes(repo):
        url = _url(repo, remote)
        if not url:
            continue
        new = _OLD_RE.sub(NEW_PATH, url)
        if new != url:
            out.append((remote, url, new))
    return out


def check() -> bool:
    """Applied once no known checkout still points a remote at the old path."""
    for repo in _checkouts():
        if not (repo / ".git").exists():
            continue
        if _stale(repo):
            return False
    return True


def up() -> bool:
    touched = False
    for repo in _checkouts():
        if not (repo / ".git").exists():
            continue
        for remote, old, new in _stale(repo):
            try:
                r = subprocess.run(
                    ["git", "-C", str(repo), "remote", "set-url", remote, new],
                    capture_output=True, text=True, timeout=15,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"       ✗ {repo}: {remote}: {exc}")
                continue
            if r.returncode == 0:
                print(f"       - {repo}: {remote} {old} → {new}")
                touched = True
            else:
                print(f"       ✗ {repo}: {remote}: {r.stderr.strip()}")

    if not touched:
        print("       - no remote on the old path — nothing to rewrite")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 137 already applied" if check() else ("Done" if up() else "Failed"))
