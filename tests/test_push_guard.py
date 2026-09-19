"""pre-push-guard (aos#192): the three failure classes of 2026-07-21 stay dead.

Exercises the hook script against fixture repos: a push deleting a shipped
migration is blocked (stale-tree clobber), an oversized binary is blocked,
a clean push passes, and FORCE_GUARD=1 overrides.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "core" / "bin" / "internal" / "pre-push-guard"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True)


def _mk_repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "core/infra/migrations").mkdir(parents=True)
    (r / "core/infra/migrations/001_seed.py").write_text("# migration\n")
    (r / "README.md").write_text("hi\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def _run_guard(repo, base_sha, tip_sha, env_extra=None):
    import os
    env = dict(os.environ)
    env.pop("FORCE_GUARD", None)
    if env_extra:
        env.update(env_extra)
    line = f"refs/heads/main {tip_sha} refs/heads/main {base_sha}\n"
    return subprocess.run(["bash", str(GUARD)], input=line, text=True,
                          capture_output=True, cwd=str(repo), env=env)


def _shas(repo):
    base = _git(repo, "rev-parse", "HEAD~1").stdout.strip()
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return base, tip


def test_migration_deletion_blocked(tmp_path):
    r = _mk_repo(tmp_path)
    _git(r, "rm", "-q", "core/infra/migrations/001_seed.py")
    _git(r, "commit", "-qm", "clobber")
    base, tip = _shas(r)
    res = _run_guard(r, base, tip)
    assert res.returncode != 0
    assert "clobber" in res.stderr.lower()


def test_oversized_binary_blocked(tmp_path):
    r = _mk_repo(tmp_path)
    (r / "big.bin").write_bytes(b"\0" * (6 * 1024 * 1024))
    _git(r, "add", "big.bin")
    _git(r, "commit", "-qm", "big binary")
    base, tip = _shas(r)
    res = _run_guard(r, base, tip)
    assert res.returncode != 0
    assert ">5MB" in res.stderr


def test_clean_push_passes(tmp_path):
    r = _mk_repo(tmp_path)
    (r / "feature.py").write_text("x = 1\n")
    _git(r, "add", "feature.py")
    _git(r, "commit", "-qm", "clean feature")
    base, tip = _shas(r)
    res = _run_guard(r, base, tip)
    assert res.returncode == 0, res.stderr


def test_force_guard_overrides(tmp_path):
    r = _mk_repo(tmp_path)
    _git(r, "rm", "-q", "core/infra/migrations/001_seed.py")
    _git(r, "commit", "-qm", "intended removal")
    base, tip = _shas(r)
    res = _run_guard(r, base, tip, {"FORCE_GUARD": "1"})
    assert res.returncode == 0


# ── A ref that lands on a commit the remote already has ──────────────────────
#
# `aos promote` moves the `stable` tag onto the commit that IS origin/main.
# The range is computed against the tag's OLD value — months back — so every
# migration and test legitimately deleted in between read as a stale-tree
# clobber and the promotion was refused (2026-09-19: a friend machine sat on
# v0.7.1 while stable could not be advanced). Nothing is being introduced:
# the server already has that commit, and it passed this guard on its way to
# main. What must NOT happen is the guard going quiet for unpushed work that
# merely has a tag pointed at it.

def _mk_repo_with_remote(tmp_path):
    """Fixture repo whose main is already published to a bare remote."""
    r = _mk_repo(tmp_path)
    bare = tmp_path / "remote.git"
    _git(r, "init", "-q", "--bare", str(bare))
    _git(r, "remote", "add", "origin", str(bare))
    _git(r, "push", "-q", "origin", "main")
    _git(r, "fetch", "-q", "origin")
    return r


def _run_guard_ref(repo, ref, tip_sha, remote_sha, env_extra=None):
    import os
    env = dict(os.environ)
    env.pop("FORCE_GUARD", None)
    if env_extra:
        env.update(env_extra)
    line = f"{ref} {tip_sha} {ref} {remote_sha}\n"
    return subprocess.run(["bash", str(GUARD)], input=line, text=True,
                          capture_output=True, cwd=str(repo), env=env)


ZERO = "0" * 40


def test_tag_promotion_onto_published_commit_passes(tmp_path):
    """The promote case: tag moves onto a commit origin/main already carries."""
    r = _mk_repo_with_remote(tmp_path)
    old_tag_target = _git(r, "rev-parse", "HEAD").stdout.strip()

    # A later commit legitimately retires a shipped migration, and is pushed.
    _git(r, "rm", "-q", "core/infra/migrations/001_seed.py")
    _git(r, "commit", "-qm", "retire 001 deliberately")
    _git(r, "push", "-q", "origin", "main")
    _git(r, "fetch", "-q", "origin")
    tip = _git(r, "rev-parse", "HEAD").stdout.strip()

    res = _run_guard_ref(r, "refs/tags/stable", tip, old_tag_target)
    assert res.returncode == 0, (
        "moving a tag onto a commit the remote already has introduces nothing "
        f"and must not be refused: {res.stderr}")


def test_tag_on_unpushed_work_is_still_checked(tmp_path):
    """The hole that must stay shut: a tag is not a way past the guard."""
    r = _mk_repo_with_remote(tmp_path)
    _git(r, "rm", "-q", "core/infra/migrations/001_seed.py")
    _git(r, "commit", "-qm", "unpushed clobber")
    tip = _git(r, "rev-parse", "HEAD").stdout.strip()

    res = _run_guard_ref(r, "refs/tags/rogue", tip, ZERO)
    assert res.returncode != 0, "unpushed work must be inspected, tag or not"
    assert "clobber" in res.stderr.lower()


def test_new_branch_is_checked_on_what_it_introduces(tmp_path):
    """A brand-new ref used to be diffed against the WORKING TREE, which is
    empty right after committing — so new branches sailed past every check."""
    r = _mk_repo_with_remote(tmp_path)
    _git(r, "checkout", "-q", "-b", "feature")
    (r / "big.bin").write_bytes(b"\0" * (6 * 1024 * 1024))
    _git(r, "add", "big.bin")
    _git(r, "commit", "-qm", "big binary on a new branch")
    tip = _git(r, "rev-parse", "HEAD").stdout.strip()

    res = _run_guard_ref(r, "refs/heads/feature", tip, ZERO)
    assert res.returncode != 0, "a new branch must be checked, not waved through"
    assert ">5MB" in res.stderr


def test_clean_new_branch_still_passes(tmp_path):
    """Checking new refs must not mean refusing ordinary ones."""
    r = _mk_repo_with_remote(tmp_path)
    _git(r, "checkout", "-q", "-b", "feature")
    (r / "feature.py").write_text("x = 1\n")
    _git(r, "add", "feature.py")
    _git(r, "commit", "-qm", "clean feature")
    tip = _git(r, "rev-parse", "HEAD").stdout.strip()

    res = _run_guard_ref(r, "refs/heads/feature", tip, ZERO)
    assert res.returncode == 0, res.stderr
