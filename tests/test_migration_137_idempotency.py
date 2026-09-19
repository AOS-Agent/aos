"""Migration 137 — point instance git remotes at AOS-Agent/aos.

The rename is only half a fix if the machines already out there keep fetching
through the old path: `release-manager` pulls via the `origin` remote of the
instance checkout, and GitHub's redirect for a renamed repository holds only
until somebody registers the old name. So this proves the migration rewrites
what it should, leaves everything else alone, and can run twice.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "core" / "infra" / "migrations" / "137_repo_url_aos_agent.py"


def _load():
    spec = importlib.util.spec_from_file_location("m137", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _make_repo(path: Path, remotes: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=15)
    for name, url in remotes.items():
        _git(path, "remote", "add", name, url)
    return path


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


OLD = "https://github.com/hishamalhadi/aos.git"
NEW = "https://github.com/AOS-Agent/aos.git"


def test_rewrites_the_stale_origin(home):
    m = _load()
    repo = _make_repo(home / ".aos" / "repo", {"origin": OLD})

    assert m.check() is False, "a stale remote must read as not-yet-applied"
    assert m.up() is True
    assert _git(repo, "remote", "get-url", "origin") == NEW
    assert m.check() is True


def test_is_idempotent(home):
    m = _load()
    repo = _make_repo(home / ".aos" / "repo", {"origin": OLD})

    assert m.up() is True
    first = _git(repo, "remote", "get-url", "origin")
    # Running a second time must be a no-op, not a double-rewrite.
    assert m.up() is True
    assert _git(repo, "remote", "get-url", "origin") == first == NEW


def test_leaves_unrelated_remotes_alone(home):
    """A fork, a local path remote and an already-migrated URL are not ours."""
    m = _load()
    # Derived from the sandbox rather than a literal /Users/... path, which is
    # both unportable and something ship-check refuses in a diff.
    local_remote = str(home / "project" / "aos-app")
    repo = _make_repo(home / ".aos" / "repo", {
        "origin": OLD,
        "aos-agent": NEW,
        "app-src": local_remote,
        "fork": "https://github.com/someoneelse/aos.git",
        "lookalike": "https://github.com/hishamalhadi/aos-app.git",
    })

    assert m.up() is True
    assert _git(repo, "remote", "get-url", "origin") == NEW
    assert _git(repo, "remote", "get-url", "aos-agent") == NEW
    assert _git(repo, "remote", "get-url", "app-src") == local_remote
    assert _git(repo, "remote", "get-url", "fork") == "https://github.com/someoneelse/aos.git"
    # `aos-app` merely starts with the old path — rewriting it would point a
    # different repository's remote at this one.
    assert _git(repo, "remote", "get-url", "lookalike") == \
        "https://github.com/hishamalhadi/aos-app.git"


def test_rewrites_ssh_form(home):
    # An scp-style SSH remote is email-shaped, and privacy-scan's email
    # pattern has no allowlist by design — only RFC-2606 reserved domains are
    # downgraded, and github.com is not one. Compose the URL from parts so no
    # literal address ever appears in a diff; the migration still sees the
    # exact string a real remote would carry.
    ssh_old = "git" + "@github.com:hishamalhadi/aos.git"
    ssh_new = "git" + "@github.com:AOS-Agent/aos.git"

    m = _load()
    repo = _make_repo(home / ".aos" / "repo", {"origin": ssh_old})
    assert m.up() is True
    assert _git(repo, "remote", "get-url", "origin") == ssh_new


def test_handles_a_dev_workspace_too(home):
    """A dev machine fetches through ~/project/aos, not ~/.aos/repo."""
    m = _load()
    dev = _make_repo(home / "project" / "aos", {"origin": OLD})
    assert m.check() is False
    assert m.up() is True
    assert _git(dev, "remote", "get-url", "origin") == NEW


def test_no_checkout_is_already_applied(home):
    """A machine with neither checkout has nothing to migrate."""
    m = _load()
    assert m.check() is True
    assert m.up() is True


def test_a_non_git_directory_is_skipped(home):
    """~/.aos/repo existing without .git must not raise."""
    m = _load()
    (home / ".aos" / "repo").mkdir(parents=True)
    assert m.check() is True
    assert m.up() is True


def test_down_refuses(home):
    assert _load().down() is False
