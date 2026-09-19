"""Mechanical proof: every migration >=108 resolves `Path.home()` per call.

Background (see ec9cf22, 026d3c1, and this same release's follow-up): a
migration that froze `HOME = Path.home()` — or any path derived from it — into
a MODULE-LEVEL CONSTANT at import time keeps returning whichever machine's (or
whichever sandboxed test's) home happened to be current the moment the module
was first imported, for the rest of the process. On 2026-09-13 that class of
bug wrote a test run's migration output into the operator's real
~/.aos/config/services.yaml. The fix converts every such constant into a
small zero-argument helper (`_home()`, `_work_db()`, `_rule_link()`, …) that
calls `Path.home()` itself, fresh, every time it is called — matching the
pattern `core/infra/lib/default_off.py` already used.

This file does not re-test each migration's up()/check() behavior — that is
each migration's own idempotency test's job. It tests exactly the property the
conversion is FOR: import a migration once under one sandboxed home, then move
`Path.home()` to a SECOND sandbox without re-importing, and call the same
helper again. A frozen constant would keep answering with the first sandbox;
a per-call helper must answer with the second. Re-importing under the second
sandbox would not catch the bug at all — a fresh exec always sees whatever
`Path.home()` currently is — so the whole point is to reuse the same loaded
module object across the switch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO / "core" / "infra" / "migrations"

# migration file stem -> the zero-argument helpers to exercise. Each entry
# must be a `def _name() -> Path` (or a container of Path — list/dict) that
# does nothing but Path arithmetic off Path.home(): no file I/O, no
# subprocess, no argument. Helpers that read or write the filesystem
# (`_present()`, `_backup()`, `_addable()`, `_orphans_present()`, …) are
# deliberately excluded — this test proves resolution, not migration
# behavior, and must never touch a disk itself beyond the two sandboxes.
HELPERS: dict[str, list[str]] = {
    "108_decommission_qareen": [
        "_home", "_la_dir", "_service_dir", "_state_yaml", "_qareen_db", "_skills_dir",
    ],
    "109_retire_halfbaked_services": ["_home", "_la_dir", "_services", "_state_yaml"],
    "110_retire_memory_mcp": ["_home", "_venv_dir", "_data_dir", "_mcp_files", "_plist"],
    "111_work_runner_default_off": [
        "_services_config_path", "_runner_config_path", "_plist_path",
    ],
    "112_comms_arms_default_off": ["_services_config_path"],
    "113_purge_stale_db_backups": ["_data_dir", "_backup_dir"],
    "114_purge_test_fixture_tasks": ["_work_db", "_backup_dir"],
    "115_close_stale_explore_threads": ["_work_db", "_backup_dir"],
    "116_qren_readiness": ["_home", "_aos_root", "_data_dir", "_report", "_config_dir"],
    "118_remove_aos_desktop_app": ["_cache"],
    "119_project_layer": [
        "_home", "_aos_dir", "_project_root", "_rule_source", "_rule_link",
        "_cli_source", "_cli_link", "_policy",
    ],
    "120_retire_dead_mcp_entries_and_orphans": [
        "_la_dir", "_services_dir", "_backup_dir", "_claude_json_path",
        "_integrations_config_path", "_obsidian_check_path",
    ],
    "121_thread_cwd_column": ["_work_db"],
    "122_retire_fleet_registry": ["_config_dir", "_archive_dir"],
    "123_sessions_into_work_db": ["_work_db", "_qareen_db", "_backup_dir"],
    "124_bridge_conversation_store": ["_bridge_db"],
    "125_qmd_collection_backfill": ["_qmd", "_aos_collections"],
    "126_inbox_reconcile_columns": ["_work_db"],
    "127_comms_crons_default_off": [
        "_crons_yaml", "_accounts_yaml", "_goals_yaml", "_state_yaml",
    ],
    "128_work_runner_decommission": [
        "_plist", "_launcher", "_runner_config", "_services_config",
    ],
    "129_close_cwd_keyed_threads": ["_work_db", "_backup_dir"],
    "130_trust_log_dispatch_hook": ["_settings_file"],
    "131_comms_nightly_park_and_retire": ["_crons_yaml", "_stale_report", "_backups_dir"],
    "133_revert_last_boot_directory_mitigation": ["_last_boot_file"],
    "134_google_credentials_hardening": ["_old_creds_dir", "_new_creds_dir"],
    "135_claude_profile_launchers": [
        "_home", "_local_bin", "_cld_source", "_cld2_link", "_cld3_link", "_aos_link",
    ],
    "136_comms_scope_config": ["_home", "_instance_config", "_template"],
    "137_repo_url_aos_agent": ["_checkouts"],
}


def _migration_path(stem: str) -> Path:
    matches = sorted(MIGRATIONS_DIR.glob(f"{stem}.py"))
    assert len(matches) == 1, f"expected exactly one file for {stem!r}, found {matches}"
    return matches[0]


def _load(stem: str):
    """Import the migration fresh. Path.home() must already be sandboxed by
    the caller — several of these (111, 112, 119) resolve a Path.home()-
    derived import path at module scope."""
    path = _migration_path(stem)
    spec = importlib.util.spec_from_file_location(f"mig_resolve_{stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _assert_under(value, home: Path, where: str) -> None:
    """*value* is a Path, or a list/dict/tuple/set of Path, and every one of
    them sits under *home*."""
    if isinstance(value, Path):
        assert value == home or home in value.parents, (
            f"{where} did not resolve under {home}: {value!r}")
        return
    if isinstance(value, dict):
        for v in value.values():
            _assert_under(v, home, where)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            _assert_under(v, home, where)
        return
    raise AssertionError(f"{where} returned a non-Path value: {value!r}")


@pytest.mark.parametrize("stem", sorted(HELPERS))
def test_helper_tracks_home_after_it_moves_post_import(stem, tmp_path, monkeypatch):
    """Import once under sandbox A; move Path.home() to sandbox B without
    re-importing; call the same helper again and require it to report B.

    This is the mechanical proof no path constant was frozen at import: a
    module-level `HOME = Path.home()` (or anything built from it) would still
    answer with sandbox A here, because the assignment ran once, back when
    Path.home() was A, and never runs again.
    """
    home_a = tmp_path / "home_a"
    home_b = tmp_path / "home_b"
    home_a.mkdir()
    home_b.mkdir()
    # A couple of these migrations (111, 112 at module scope; 119 for its
    # zone-module sys.path bootstrap) resolve a Path.home()-derived import
    # path at exec time — give both sandboxes a working ~/aos.
    (home_a / "aos").symlink_to(REPO)
    (home_b / "aos").symlink_to(REPO)

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_a))
    mod = _load(stem)

    helpers = HELPERS[stem]
    assert helpers, f"{stem} has no helpers registered in this test"

    for name in helpers:
        fn = getattr(mod, name, None)
        assert callable(fn), f"{stem}.{name} is not defined, or not callable"
        first = fn()
        _assert_under(first, home_a, f"{stem}.{name}() (before the switch)")

    # Move Path.home() to a second sandbox. No re-import.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_b))

    for name in helpers:
        fn = getattr(mod, name)
        second = fn()
        _assert_under(second, home_b, (
            f"{stem}.{name}() (after Path.home() moved to a second sandbox — "
            f"a frozen constant would still report the first one)"
        ))


def test_every_migration_from_108_up_is_registered_here():
    """Guards this file's own coverage: a new migration >=108 that ships
    without an entry above would otherwise silently get zero mechanical
    proof. 117 is a deliberate gap (an update freeze dropped before it ever
    ran anywhere — see tests/test_migration_runner.py's TestNumberingGaps)."""
    on_disk = {
        p.stem for p in MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.py")
        if int(p.stem.split("_")[0]) >= 108
    }
    assert on_disk == set(HELPERS), (
        f"missing: {on_disk - set(HELPERS)}, stale: {set(HELPERS) - on_disk}")
