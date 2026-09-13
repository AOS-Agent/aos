"""
Test suite for the migration runner's success/failure contract
(core/infra/migrations/runner.py cmd_migrate()).

The runner used to only treat a literal `up() -> False` as failure. Legacy
migrations that returned a human-readable error string on failure (instead
of raising or returning False) slipped past `result is False`, got logged
as "applied", and silently advanced the version watermark past a migration
that never actually ran. The fixed contract: success is `True` or `None`;
ANY other return value (False, a string, 0, ...) or a raised exception is a
failure, and the watermark must not advance.

Never touches ~/.aos/ — VERSION_FILE and MIGRATION_LOG are monkeypatched to
tmp_path, and find_migrations() is monkeypatched to return fake in-memory
migration modules instead of globbing the real migrations directory.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

RUNNER_PATH = Path(__file__).parent.parent / "core" / "infra" / "migrations" / "runner.py"


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """Load runner.py fresh per test, with VERSION_FILE/MIGRATION_LOG/find_migrations
    redirected to an isolated tmp_path so no test ever touches ~/.aos/.

    Path.home() is sandboxed *before* exec_module runs. runner.py resolves
    AOS_DIR/USER_DIR/MIGRATION_DIR (and the VERSION_FILE/MIGRATION_LOG default
    values below, before they're overridden) from Path.home() at module scope
    — the same class of bug default_off.py's docstring warns about. No test
    here currently calls a path through those two unpatched-by-default names
    for real, but that has been true by accident before (see migration 122's
    test file, which relied on it until this release): the patch belongs here,
    not on each test's memory to add it.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    spec = importlib.util.spec_from_file_location("migration_runner", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_runner"] = mod
    spec.loader.exec_module(mod)

    mod.VERSION_FILE = tmp_path / ".version"
    mod.MIGRATION_LOG = tmp_path / "logs" / "migrations.yaml"
    yield mod
    sys.modules.pop("migration_runner", None)


def fake_migration(num: int, name: str, up_result, checked: bool = False):
    """Build a fake migration module: check() -> checked, up() -> up_result
    (or raises if up_result is an Exception instance).
    """
    mod = types.SimpleNamespace()
    mod.DESCRIPTION = name

    def check():
        return checked
    mod.check = check

    if isinstance(up_result, Exception):
        def up():
            raise up_result
    else:
        def up():
            return up_result
    mod.up = up

    return (num, name, mod)


class TestMigrateContract:
    """cmd_migrate() success/failure classification for a single pending migration."""

    def test_success_true_advances_watermark(self, runner, monkeypatch):
        monkeypatch.setattr(runner, "find_migrations", lambda *a, **k: [fake_migration(1, "001_ok", True)])
        result = runner.cmd_migrate()
        assert result is True
        assert runner.load_version() == 1

    def test_success_none_advances_watermark(self, runner, monkeypatch):
        """Many migrations don't explicitly `return True` — falling off the
        end of up() returns None, which must also count as success."""
        monkeypatch.setattr(runner, "find_migrations", lambda *a, **k: [fake_migration(1, "001_ok_none", None)])
        result = runner.cmd_migrate()
        assert result is True
        assert runner.load_version() == 1

    def test_false_return_is_failure_watermark_unchanged(self, runner, monkeypatch):
        monkeypatch.setattr(runner, "find_migrations", lambda *a, **k: [fake_migration(1, "001_fail", False)])
        result = runner.cmd_migrate()
        assert result is False
        assert runner.load_version() == 0

    def test_string_return_is_failure_watermark_unchanged(self, runner, monkeypatch):
        """The regression this test guards: a legacy migration returning an
        error STRING (e.g. "Failed: no such table") must be treated as a
        failure, not silently recorded as applied."""
        monkeypatch.setattr(
            runner, "find_migrations",
            lambda *a, **k: [fake_migration(1, "001_fail_string", "Failed: no such table")],
        )
        result = runner.cmd_migrate()
        assert result is False
        assert runner.load_version() == 0

    def test_exception_is_failure_watermark_unchanged(self, runner, monkeypatch):
        monkeypatch.setattr(
            runner, "find_migrations",
            lambda *a, **k: [fake_migration(1, "001_raises", RuntimeError("boom"))],
        )
        result = runner.cmd_migrate()
        assert result is False
        assert runner.load_version() == 0

    def test_failure_stops_the_batch_before_later_migrations(self, runner, monkeypatch):
        """A failing migration must not let later, higher-numbered
        migrations run — the chain stops at the first failure."""
        calls = []

        def make(num, name, up_result):
            n, nm, mod = fake_migration(num, name, up_result)

            def up(_orig=mod.up):
                calls.append(name)
                return _orig()
            mod.up = up
            return (n, nm, mod)

        monkeypatch.setattr(
            runner, "find_migrations",
            lambda *a, **k: [
                make(1, "001_fail_string", "some error"),
                make(2, "002_would_run", True),
            ],
        )
        result = runner.cmd_migrate()
        assert result is False
        assert calls == ["001_fail_string"]
        assert runner.load_version() == 0

    def test_already_applied_check_skips_up_and_advances_watermark(self, runner, monkeypatch):
        n, name, mod = fake_migration(1, "001_already_applied", "would be an error if called", checked=True)
        monkeypatch.setattr(runner, "find_migrations", lambda *a, **k: [(n, name, mod)])
        result = runner.cmd_migrate()
        assert result is True
        assert runner.load_version() == 1

    def test_migration_log_records_failure_with_detail(self, runner, monkeypatch):
        import yaml
        monkeypatch.setattr(
            runner, "find_migrations",
            lambda *a, **k: [fake_migration(1, "001_fail_string", "Failed: no such table")],
        )
        runner.cmd_migrate()
        log = yaml.safe_load(runner.MIGRATION_LOG.read_text())
        assert log[-1]["status"] == "failed"
        assert "no such table" in log[-1]["details"]


class TestPendingCount:
    """cmd_pending_count() — the updater's migration trigger.

    The updater used to gate migrations on VERSION delta alone: if VERSION
    was unchanged between releases, migrations were skipped even when
    pending, because a release can ship migrations without bumping VERSION.
    The fix is for the updater to ask the runner directly, independent of
    version. These tests pin the runner-side half of that contract: pending
    count reflects migrations strictly above the watermark, regardless of
    what VERSION says (the runner has no notion of VERSION at all — it only
    tracks its own watermark file).
    """

    def test_zero_pending_when_no_migrations_above_watermark(self, runner, monkeypatch, capsys):
        monkeypatch.setattr(runner, "find_migrations", lambda *a, **k: [fake_migration(1, "001_applied", True)])
        runner.save_version(1)
        runner.cmd_pending_count()
        assert capsys.readouterr().out.strip() == "0"

    def test_nonzero_pending_reported_independent_of_version_bump(self, runner, monkeypatch, capsys):
        """Pins the exact wave-3 scenario: several migrations shipped above
        the watermark while the release's VERSION string never changed.
        pending-count must still report them, since the updater has no
        other way to know they exist."""
        monkeypatch.setattr(
            runner, "find_migrations",
            lambda *a, **k: [
                fake_migration(1, "001_applied", True),
                fake_migration(2, "002_pending", True),
                fake_migration(3, "003_pending", True),
                fake_migration(4, "004_pending", True),
                fake_migration(5, "005_pending", True),
            ],
        )
        runner.save_version(1)
        runner.cmd_pending_count()
        assert capsys.readouterr().out.strip() == "4"

    def test_pending_count_does_not_mutate_watermark(self, runner, monkeypatch):
        """Checking pending count must be side-effect free — it's called
        every update cycle by the updater purely to decide whether to run
        migrate, and must not itself advance state."""
        monkeypatch.setattr(runner, "find_migrations", lambda *a, **k: [fake_migration(1, "001_pending", True)])
        runner.save_version(0)
        runner.cmd_pending_count()
        assert runner.load_version() == 0


class TestNumberingGaps:
    """A missing migration number is not a missing migration.

    Migration 117 (an update freeze, dropped with the 0.7.7 reframe) was deleted
    before it ever ran
    anywhere, which leaves the directory numbered 116, 118, 119, … Discovery
    must treat that hole as nothing at all: it globs files and sorts them, so
    there is no "next number" to stall on. These tests pin that, because the
    alternative implementation — walk current+1, current+2, … until a file is
    missing — looks identical on a contiguous directory and silently stops
    migrating on this one.
    """

    def _write(self, d: Path, stem: str) -> None:
        (d / f"{stem}.py").write_text(
            "DESCRIPTION = 'fake'\n"
            "def check():\n    return False\n"
            "def up():\n    return True\n"
        )

    def test_discovery_spans_a_gap(self, runner, tmp_path, monkeypatch):
        d = tmp_path / "migrations"
        d.mkdir()
        self._write(d, "116_before_the_gap")
        self._write(d, "118_after_the_gap")
        monkeypatch.setattr(runner, "MIGRATION_DIR", d)

        assert [n for n, _, _ in runner.find_migrations()] == [116, 118]

    def test_migrate_applies_116_then_118_with_117_absent(self, runner, tmp_path, monkeypatch):
        """The sequence 116 → 118 applies, and the watermark lands on 118."""
        d = tmp_path / "migrations"
        d.mkdir()
        self._write(d, "116_before_the_gap")
        self._write(d, "118_after_the_gap")
        monkeypatch.setattr(runner, "MIGRATION_DIR", d)
        runner.save_version(115)

        assert runner.cmd_migrate() is True
        assert runner.load_version() == 118

    def test_real_migrations_directory_has_no_duplicate_numbers(self):
        """Numbers may skip; they may never collide — two modules claiming the
        same number means one of them never runs on a machine at that level."""
        real = Path(__file__).parent.parent / "core" / "infra" / "migrations"
        nums = [int(f.stem.split("_")[0]) for f in real.glob("[0-9][0-9][0-9]_*.py")]
        assert len(nums) == len(set(nums)), "duplicate migration numbers"


class TestLazyDiscovery:
    """The runner must never import a migration that has already been applied.

    Found by the 0.7.7 sequential dry-run: migration 093 imports
    `qareen.tracking.store` at module scope, and the Qareen decommission (108)
    deleted that package — so on any machine at watermark ≥ 93, discovery
    crashed with ModuleNotFoundError before a single pending migration ran.
    Applied migrations are history; only pending ones get imported, and a
    pending one that fails to import is logged as an error, not a crash.
    """

    def _write(self, mig_dir: Path, name: str, body: str) -> None:
        mig_dir.mkdir(parents=True, exist_ok=True)
        (mig_dir / f"{name}.py").write_text(body)

    def test_applied_migrations_are_never_imported(self, runner, tmp_path, monkeypatch):
        mig_dir = tmp_path / "migrations"
        self._write(mig_dir, "001_ok", "DESCRIPTION='ok'\ndef check(): return True\ndef up(): return True\n")
        self._write(mig_dir, "002_broken", "import module_that_does_not_exist_anymore\n")
        monkeypatch.setattr(runner, "MIGRATION_DIR", mig_dir)
        runner.save_version(2)
        assert runner.cmd_migrate() is True  # 'already at version 2' — no import, no crash
        assert runner.load_version() == 2

    def test_pending_broken_import_is_an_error_not_a_crash(self, runner, tmp_path, monkeypatch):
        mig_dir = tmp_path / "migrations"
        self._write(mig_dir, "001_ok", "DESCRIPTION='ok'\ndef check(): return True\ndef up(): return True\n")
        self._write(mig_dir, "002_broken", "import module_that_does_not_exist_anymore\n")
        self._write(mig_dir, "003_never_reached", "DESCRIPTION='later'\ndef check(): return False\ndef up(): return True\n")
        monkeypatch.setattr(runner, "MIGRATION_DIR", mig_dir)
        runner.save_version(1)
        assert runner.cmd_migrate() is False
        assert runner.load_version() == 1
        log = runner.MIGRATION_LOG.read_text()
        assert "002_broken" in log and "error" in log
        assert "003_never_reached" not in log

    def test_pending_count_needs_no_imports(self, runner, tmp_path, monkeypatch, capsys):
        mig_dir = tmp_path / "migrations"
        self._write(mig_dir, "001_broken", "import module_that_does_not_exist_anymore\n")
        monkeypatch.setattr(runner, "MIGRATION_DIR", mig_dir)
        runner.save_version(0)
        runner.cmd_pending_count()
        assert capsys.readouterr().out.strip() == "1"


def test_093_survives_the_qareen_decommission(tmp_path, monkeypatch):
    """093 must import and report applied on a machine where qareen/ is gone."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    path = RUNNER_PATH.parent / "093_auto_tracker_init.py"
    spec = importlib.util.spec_from_file_location("mig_093", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # must not raise
    assert mod.check() is True
