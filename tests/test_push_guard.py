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
