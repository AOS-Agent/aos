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
    """
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
        monkeypatch.setattr(runner, "find_migrations", lambda: [fake_migration(1, "001_ok", True)])
        result = runner.cmd_migrate()
        assert result is True
        assert runner.load_version() == 1

    def test_success_none_advances_watermark(self, runner, monkeypatch):
        """Many migrations don't explicitly `return True` — falling off the
        end of up() returns None, which must also count as success."""
        monkeypatch.setattr(runner, "find_migrations", lambda: [fake_migration(1, "001_ok_none", None)])
        result = runner.cmd_migrate()
        assert result is True
        assert runner.load_version() == 1

    def test_false_return_is_failure_watermark_unchanged(self, runner, monkeypatch):
        monkeypatch.setattr(runner, "find_migrations", lambda: [fake_migration(1, "001_fail", False)])
        result = runner.cmd_migrate()
        assert result is False
        assert runner.load_version() == 0

    def test_string_return_is_failure_watermark_unchanged(self, runner, monkeypatch):
        """The regression this test guards: a legacy migration returning an
        error STRING (e.g. "Failed: no such table") must be treated as a
        failure, not silently recorded as applied."""
        monkeypatch.setattr(
            runner, "find_migrations",
            lambda: [fake_migration(1, "001_fail_string", "Failed: no such table")],
        )
        result = runner.cmd_migrate()
        assert result is False
        assert runner.load_version() == 0

    def test_exception_is_failure_watermark_unchanged(self, runner, monkeypatch):
        monkeypatch.setattr(
            runner, "find_migrations",
            lambda: [fake_migration(1, "001_raises", RuntimeError("boom"))],
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
            lambda: [
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
        monkeypatch.setattr(runner, "find_migrations", lambda: [(n, name, mod)])
        result = runner.cmd_migrate()
        assert result is True
        assert runner.load_version() == 1

    def test_migration_log_records_failure_with_detail(self, runner, monkeypatch):
        import yaml
        monkeypatch.setattr(
            runner, "find_migrations",
            lambda: [fake_migration(1, "001_fail_string", "Failed: no such table")],
        )
        runner.cmd_migrate()
        log = yaml.safe_load(runner.MIGRATION_LOG.read_text())
        assert log[-1]["status"] == "failed"
        assert "no such table" in log[-1]["details"]
