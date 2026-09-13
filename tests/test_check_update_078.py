"""
Tests for core/bin/crons/check-update's phase-2 verification and downgrade
guard (aos#2347).

Two failure modes, same script, both `exit 0`:

  A. The update pipeline can report `last_apply.result: ok` (success) when
     the deployed VERSION/hash never actually moved — release-manager
     `activate` (or a git reset) can exit 0 without ~/aos actually switching.
  B. The pipeline can apply a candidate that is *behind* what's deployed (a
     `stable` tag rolled back upstream) and report it as a plain "Update",
     with no operator-visible signal that it was a downgrade.

Fix under test: phase 2 (`deploy()`) now verifies the actual on-disk
VERSION/hash match what phase 1 resolved before writing `last_apply.result:
ok`, recording `noop` (with a reason) when nothing moved instead; and
`apply_release`/`apply_git` refuse to build/apply a candidate that is not
strictly ahead of the deployed commit+version, recording
`refused_downgrade` and notifying once, unless invoked with
`--allow-downgrade` (or `ALLOW_DOWNGRADE=1` in the environment).

Technique
---------
check-update is a flat bash script: function definitions followed by a
`case "${1:-}"` dispatch that always ends in `exit 0`. To call its bash
functions directly (`apply_release`, `apply_git`, `_is_downgrade`, ...) this
sources everything ABOVE the `# ── Main` marker into a throwaway script and
calls the function by name — the same "extract the real shipped logic and
drive it" approach test_install_cmux_detection.py uses for install.sh.

Every test runs under a fully isolated `$HOME` (tmp_path) and a git remote
that is itself a tmp repo — never the operator's real ~/aos or ~/project/aos.
`release-manager` is stubbed at the exact absolute path check-update invokes
it from (it is never looked up via PATH, so a PATH-based stub would never be
seen); `git` is the real system binary run only against these tmp repos —
real git is what makes `merge-base --is-ancestor` (the ancestry half of the
downgrade guard) meaningful to test at all. Per the task brief: never run
`check-update --apply`/`--continue` against the real ~/aos.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
CHECK_UPDATE = REPO_ROOT / "core" / "bin" / "crons" / "check-update"
CHANNELS_PY = REPO_ROOT / "core" / "lib" / "channels.py"

_MAIN_MARKER = "# ── Main "


def _functions_only_text() -> str:
    """Everything in check-update above the case/exit dispatch: every
    function definition and top-level variable, none of the "run something
    based on argv" tail."""
    text = CHECK_UPDATE.read_text()
    idx = text.index(_MAIN_MARKER)
    return text[:idx]


# ── tiny git helpers (real git, always against tmp_path repos) ──────────────


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, f"git {args} in {cwd} failed: {result.stderr}"
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _commit_version(path: Path, version: str) -> str:
    (path / "VERSION").write_text(version + "\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", f"v{version}")
    return _git(path, "rev-parse", "HEAD")


# ── fake runtime layout (just enough for phase 2 to run in isolation) ───────

_NOTIFY_STUB = """#!/usr/bin/env bash
mkdir -p "$HOME/.aos"
printf '%s\\n' "$1" >> "$HOME/.aos/notify.log"
exit 0
"""

_RECONCILE_STUB = "import sys\nsys.exit(0)\n"

_MIGRATIONS_STUB = (
    "import sys\n"
    "if 'pending-count' in sys.argv:\n"
    "    print(0)\n"
    "sys.exit(0)\n"
)


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _install_stub_runtime(release_dir: Path) -> None:
    """Populate a fake AOS_DIR (a release directory, or a git-clone root) with
    the minimum real layout check-update's phase 2 and channel resolution
    need: a real python3, the real channels.py (symlinked, unmodified — pure
    logic, safe to reuse), no-op reconcile/migrations runners, a no-op `aos`
    CLI for sync-*, an aos-notify stub that logs to a file instead of
    reaching Telegram, and check-update itself symlinked back to the file
    under test so phase 2 always runs the real, current code."""
    internal = release_dir / "core" / "bin" / "internal"
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "aos-python").symlink_to(sys.executable)

    lib = release_dir / "core" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "channels.py").symlink_to(CHANNELS_PY)

    crons = release_dir / "core" / "bin" / "crons"
    crons.mkdir(parents=True, exist_ok=True)
    (crons / "check-update").symlink_to(CHECK_UPDATE)

    cli = release_dir / "core" / "bin" / "cli"
    cli.mkdir(parents=True, exist_ok=True)
    _write_exec(cli / "aos", "#!/usr/bin/env bash\nexit 0\n")
    _write_exec(cli / "aos-notify", _NOTIFY_STUB)

    reconcile = release_dir / "core" / "infra" / "reconcile"
    reconcile.mkdir(parents=True, exist_ok=True)
    (reconcile / "runner.py").write_text(_RECONCILE_STUB)

    migrations = release_dir / "core" / "infra" / "migrations"
    migrations.mkdir(parents=True, exist_ok=True)
    (migrations / "runner.py").write_text(_MIGRATIONS_STUB)


# check-update invokes release-manager as `bash "$rm" create ...` — an
# explicit interpreter, not an exec-by-shebang — so the stub must itself be a
# bash script (a python one would be parsed as bash and fail immediately).
_RELEASE_MANAGER_STUB = """#!/usr/bin/env bash
# Proof-of-invocation marker (asserted absent in the downgrade-refused
# tests): a real release-manager must never be reached once the guard fires.
mkdir -p "$HOME/.aos"
echo "$*" >> "$HOME/.aos/rm-called"

cmd="${1:-}"
case "$cmd" in
    create)
        [[ "${STUB_FAIL_CREATE:-}" == "1" ]] && exit 1
        exit 0
        ;;
    activate)
        [[ "${STUB_FAIL_ACTIVATE:-}" == "1" ]] && exit 1
        name="${2:-}"
        new_hash="${3:-}"
        if [[ "${STUB_NOOP_ACTIVATE:-}" == "1" ]]; then
            # Simulate the exact bug: report success without ~/aos (or the
            # deployed-hash file) actually moving.
            exit 0
        fi
        target="$STUB_RELEASES_ROOT/$name"
        [[ -d "$target" ]] || exit 1
        ln -sfn "$target" "$STUB_AOS_LINK"
        printf '%s' "$new_hash" > "$STUB_HASH_FILE"
        exit 0
        ;;
    *)
        exit 2
        ;;
esac
"""


def _base_env(home: Path) -> dict:
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
    }


def _run(home: Path, call: str, env_extra: dict | None = None, timeout: int = 30):
    """Source the functions-only slice of check-update in a fresh bash
    process (HOME=home) and invoke `call`. The function's own exit code
    becomes the process exit code (even across the `exec ... --continue`
    hop in apply_release/apply_git's success path, since exec replaces the
    process image in place)."""
    functions_file = home / "_check_update_functions.sh"
    functions_file.write_text(_functions_only_text())
    script = (
        "set -uo pipefail\n"
        f"source '{functions_file}'\n"
        f"{call}\n"
        "EXIT=$?\n"
        'exit "$EXIT"\n'
    )
    env = _base_env(home)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _state(home: Path) -> dict:
    path = home / ".aos" / "data" / "update-state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _notify_log(home: Path) -> list[str]:
    path = home / ".aos" / "notify.log"
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line]


# ── fixture: a release-install layout with a controllable release-manager ───


class ReleaseFixture:
    """A fake release install: $HOME/aos -> releases/<old>, a source repo at
    $HOME/project/aos (find_repo()'s first candidate) cloned from a tmp
    "origin" repo, and a release-manager stub whose behavior is steered via
    environment variables passed to `apply()`."""

    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"
        self.origin = tmp_path / "origin"
        self.releases_root = self.home / "aos-releases"
        self.aos_link = self.home / "aos"
        self.hash_file = self.home / ".aos" / "data" / "deployed-hash"

        _init_repo(self.origin)
        self.hashes = {}
        for version in ("0.7.0", "0.7.1", "0.7.2"):
            self.hashes[version] = _commit_version(self.origin, version)

        (self.home / "project").mkdir(parents=True, exist_ok=True)
        self.source_repo = self.home / "project" / "aos"
        _git(self.home / "project", "clone", "-q", str(self.origin), "aos")

        (self.home / ".aos" / "data").mkdir(parents=True, exist_ok=True)
        (self.home / ".aos" / "config").mkdir(parents=True, exist_ok=True)
        (self.home / ".aos" / "config" / "channel").write_text("edge\n")

        # Deployed release: v0.7.0
        self.old_dir = self.releases_root / "v0.7.0-old0000"
        _install_stub_runtime(self.old_dir)
        (self.old_dir / "VERSION").write_text("0.7.0\n")
        _write_exec(self.old_dir / "core" / "bin" / "internal" / "release-manager", _RELEASE_MANAGER_STUB)
        self.aos_link.symlink_to(self.old_dir)
        self.hash_file.write_text(self.hashes["0.7.0"])

    def move_origin_main(self, version: str) -> None:
        """Force origin's main branch to an arbitrary commit — simulates a
        `stable`/`main` ref moving backward upstream."""
        _git(self.origin, "update-ref", "refs/heads/main", self.hashes[version])

    def pre_stage_release(self, version: str, tag: str) -> Path:
        """Pre-build the target release dir release-manager's real `create`
        would have produced, keyed the same way apply_release computes
        release_name (<version>-<7-char hash>)."""
        release_name = f"{version}-{self.hashes[version][:7]}"
        release_dir = self.releases_root / release_name
        _install_stub_runtime(release_dir)
        (release_dir / "VERSION").write_text(version + "\n")
        _write_exec(
            release_dir / "core" / "bin" / "internal" / "release-manager",
            _RELEASE_MANAGER_STUB,
        )
        assert tag == release_name  # sanity — caller must match the real formula
        return release_dir

    def env(self, **extra) -> dict:
        env = {
            "STUB_RELEASES_ROOT": str(self.releases_root),
            "STUB_AOS_LINK": str(self.aos_link),
            "STUB_HASH_FILE": str(self.hash_file),
        }
        env.update(extra)
        return env


@pytest.fixture
def release_fixture(tmp_path):
    return ReleaseFixture(tmp_path)


# ── (a) phase 2 verification: noop vs success ───────────────────────────────


class TestPhase2VerifiesTheDeploy:
    def test_activation_that_does_not_move_disk_is_recorded_as_noop(self, release_fixture):
        """release-manager activate exits 0 but never swaps ~/aos or writes
        the hash file — the exact aos#2347 shape. Phase 2 must not record
        this as success."""
        rf = release_fixture
        rf.move_origin_main("0.7.1")
        release_name = f"0.7.1-{rf.hashes['0.7.1'][:7]}"
        rf.pre_stage_release("0.7.1", release_name)

        result = _run(rf.home, "apply_release", rf.env(STUB_NOOP_ACTIVATE="1"))

        state = _state(rf.home)
        assert state.get("last_apply", {}).get("result") == "noop", state
        assert state.get("status") == "noop", state
        assert state.get("last_apply", {}).get("result") != "ok"
        # And the disk really didn't move — the assertion isn't just trusting
        # the state file.
        assert os.path.realpath(rf.aos_link) == os.path.realpath(rf.old_dir)
        assert rf.hash_file.read_text() == rf.hashes["0.7.0"]
        assert result.returncode == 0  # phase 2 still exits 0 — noop isn't a crash

    def test_real_activation_is_recorded_as_success(self, release_fixture):
        """The mirror image: release-manager activate really does swap
        ~/aos and the hash file — this must land as `ok`, not noop."""
        rf = release_fixture
        rf.move_origin_main("0.7.1")
        release_name = f"0.7.1-{rf.hashes['0.7.1'][:7]}"
        rf.pre_stage_release("0.7.1", release_name)

        result = _run(rf.home, "apply_release", rf.env())

        assert result.returncode == 0, result.stderr
        state = _state(rf.home)
        assert state.get("last_apply", {}).get("result") == "ok", state
        assert state.get("status") == "up_to_date", state
        assert state.get("version") == "0.7.1"
        assert os.path.realpath(rf.aos_link) != os.path.realpath(rf.old_dir)
        assert rf.hash_file.read_text() == rf.hashes["0.7.1"]

    def test_nothing_to_apply_is_recorded_as_noop_not_silence(self, release_fixture):
        """Candidate hash already equals deployed hash — previously this
        branch returned 0 without writing anything to the state file at
        all, leaving whatever last_apply happened to be there before."""
        rf = release_fixture
        # Deployed already matches origin/main's tip (0.7.2, the last commit
        # the fixture made) — nothing to move.
        rf.hash_file.write_text(rf.hashes["0.7.2"])
        (rf.old_dir / "VERSION").write_text("0.7.2\n")

        result = _run(rf.home, "apply_release", rf.env())

        assert result.returncode == 0
        state = _state(rf.home)
        assert state.get("last_apply", {}).get("result") == "noop", state
        assert state.get("last_apply", {}).get("step") == "nothing_to_apply"


# ── (b) downgrade refusal ────────────────────────────────────────────────────


class TestDowngradeRefusal:
    def test_release_path_refuses_downgrade_by_ancestry_and_version(self, release_fixture):
        rf = release_fixture
        # Deployed is 0.7.2; candidate the channel resolves to is 0.7.0 — an
        # ancestor commit AND a lower VERSION. Simulates a `stable`/edge ref
        # rolled backward upstream (aos#2347 case B).
        rf.hash_file.write_text(rf.hashes["0.7.2"])
        (rf.old_dir / "VERSION").write_text("0.7.2\n")
        rf.move_origin_main("0.7.0")

        result = _run(rf.home, "apply_release", rf.env())

        assert result.returncode == 1
        state = _state(rf.home)
        assert state.get("last_apply", {}).get("result") == "refused_downgrade", state
        assert state.get("status") == "refused_downgrade"
        assert "downgrade" in state["last_apply"]["detail"].lower()
        # Nothing was touched: no build, no activation, no disk movement.
        assert not (rf.home / ".aos" / "rm-called").exists()
        assert os.path.realpath(rf.aos_link) == os.path.realpath(rf.old_dir)
        assert rf.hash_file.read_text() == rf.hashes["0.7.2"]
        # Exactly one notification for this candidate.
        assert len(_notify_log(rf.home)) == 1

    def test_allow_downgrade_env_overrides_the_refusal(self, release_fixture):
        rf = release_fixture
        rf.hash_file.write_text(rf.hashes["0.7.2"])
        (rf.old_dir / "VERSION").write_text("0.7.2\n")
        rf.move_origin_main("0.7.0")
        rf.pre_stage_release("0.7.0", f"0.7.0-{rf.hashes['0.7.0'][:7]}")

        result = _run(rf.home, "apply_release", rf.env(ALLOW_DOWNGRADE="1"))

        assert result.returncode == 0, result.stderr
        state = _state(rf.home)
        assert state.get("last_apply", {}).get("result") == "ok", state
        assert state.get("version") == "0.7.0"
        assert (rf.home / ".aos" / "rm-called").exists()  # release-manager WAS invoked
        assert os.path.realpath(rf.aos_link) != os.path.realpath(rf.old_dir)

    def test_repeated_refusal_notifies_only_once(self, release_fixture):
        """The every-2h cron re-runs apply() on a stuck channel; the operator
        should hear about the refusal once, not every cycle."""
        rf = release_fixture
        rf.hash_file.write_text(rf.hashes["0.7.2"])
        (rf.old_dir / "VERSION").write_text("0.7.2\n")
        rf.move_origin_main("0.7.0")

        r1 = _run(rf.home, "apply_release", rf.env())
        r2 = _run(rf.home, "apply_release", rf.env())

        assert r1.returncode == 1 and r2.returncode == 1
        assert len(_notify_log(rf.home)) == 1

    def test_git_path_refuses_downgrade(self, tmp_path):
        """Same guard, git-clone install: ~/aos IS the git clone, no
        release-manager involved at all."""
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        origin = tmp_path / "origin"
        _init_repo(origin)
        hashes = {}
        for version in ("0.7.0", "0.7.1", "0.7.2"):
            hashes[version] = _commit_version(origin, version)

        aos_dir = home / "aos"
        _git(home, "clone", "-q", str(origin), "aos")
        _git(aos_dir, "checkout", "-q", hashes["0.7.2"])
        _install_stub_runtime(aos_dir)
        (home / ".aos" / "config").mkdir(parents=True, exist_ok=True)
        (home / ".aos" / "config" / "channel").write_text("edge\n")

        # Roll the remote main back to 0.7.0 — a downgrade relative to the
        # checked-out 0.7.2.
        _git(origin, "update-ref", "refs/heads/main", hashes["0.7.0"])

        before_head = _git(aos_dir, "rev-parse", "HEAD")
        result = _run(home, "apply_git", _base_env(home) | {})

        assert result.returncode == 1
        state = _state(home)
        assert state.get("last_apply", {}).get("result") == "refused_downgrade", state
        # The working tree was never touched.
        assert _git(aos_dir, "rev-parse", "HEAD") == before_head == hashes["0.7.2"]

    def test_git_path_allow_downgrade_proceeds(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        origin = tmp_path / "origin"
        _init_repo(origin)
        hashes = {}
        for version in ("0.7.0", "0.7.1", "0.7.2"):
            hashes[version] = _commit_version(origin, version)

        aos_dir = home / "aos"
        _git(home, "clone", "-q", str(origin), "aos")
        _git(aos_dir, "checkout", "-q", hashes["0.7.2"])
        _install_stub_runtime(aos_dir)
        (home / ".aos" / "config").mkdir(parents=True, exist_ok=True)
        (home / ".aos" / "config" / "channel").write_text("edge\n")

        _git(origin, "update-ref", "refs/heads/main", hashes["0.7.0"])

        result = _run(home, "apply_git", {"ALLOW_DOWNGRADE": "1"})

        assert result.returncode == 0, result.stderr
        state = _state(home)
        assert state.get("last_apply", {}).get("result") == "ok", state
        assert _git(aos_dir, "rev-parse", "HEAD") == hashes["0.7.0"]


# ── pure-logic unit coverage for the two helper predicates ───────────────────


class TestDowngradePredicates:
    """_version_lt/_is_downgrade both shell out to $AOS_PY, which defaults to
    $HOME/aos/core/bin/internal/aos-python — irrelevant to what's being
    tested here, so it's pointed straight at the real interpreter."""

    def _run_predicate(self, tmp_path, call: str) -> int:
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        (home / ".aos").mkdir(parents=True, exist_ok=True)
        result = _run(home, call, {"AOS_PY": sys.executable})
        return result.returncode

    def test_version_lt_true(self, tmp_path):
        assert self._run_predicate(tmp_path, '_version_lt "0.7.1" "0.7.2"') == 0

    def test_version_lt_false_when_equal_or_ahead(self, tmp_path):
        assert self._run_predicate(tmp_path, '_version_lt "0.7.2" "0.7.2"') == 1
        assert self._run_predicate(tmp_path, '_version_lt "0.7.3" "0.7.2"') == 1

    def test_version_lt_unparseable_is_never_a_downgrade(self, tmp_path):
        assert self._run_predicate(tmp_path, '_version_lt "unknown" "0.7.2"') == 1
        assert self._run_predicate(tmp_path, '_version_lt "0.7.2" "unknown"') == 1

    def test_is_downgrade_by_version_alone_ignores_missing_ancestry(self, tmp_path):
        """A candidate can be flagged purely on the VERSION signal even when
        its commit isn't reachable from the deployed one at all (orphan
        branch with a hand-rolled-back VERSION file) — the two signals are
        independent, either is sufficient."""
        home = tmp_path / "home"
        repo = home / "repo"
        _init_repo(repo)
        c_deployed = _commit_version(repo, "0.7.2")
        _git(repo, "checkout", "-q", "--orphan", "side")
        _git(repo, "rm", "-rf", "-q", ".")
        c_candidate = _commit_version(repo, "0.7.1")

        (home / ".aos").mkdir(parents=True, exist_ok=True)
        result = _run(
            home,
            f'_is_downgrade "{repo}" "{c_deployed}" "{c_candidate}" "0.7.2" "0.7.1"',
            {"AOS_PY": sys.executable},
        )
        assert result.returncode == 0

    def test_is_downgrade_false_for_a_real_advance(self, tmp_path):
        home = tmp_path / "home"
        repo = home / "repo"
        _init_repo(repo)
        c_old = _commit_version(repo, "0.7.1")
        c_new = _commit_version(repo, "0.7.2")

        (home / ".aos").mkdir(parents=True, exist_ok=True)
        result = _run(
            home,
            f'_is_downgrade "{repo}" "{c_old}" "{c_new}" "0.7.1" "0.7.2"',
            {"AOS_PY": sys.executable},
        )
        assert result.returncode == 1
