"""Migration 131 — comms-intelligence nightly parked, two dead crons retired.

Same contract as 127 (which this mirrors): idempotent, respects an explicit
operator opt-in, never touches a job or file it wasn't named for. Two
independent concerns share this migration number the same way 127 combined
the comms default-off with four dead config keys:

  1. enrich-comms/comms-patterns/comms-graduation default-off in crons.yaml
     (backup-comms is never touched — the fixture below includes it as a
     canary for exactly that).
  2. stale-detector's orphaned ~/.aos/work/stale-report.yaml archived to
     ~/.aos/backups/ (never deleted outright) now that the cron and script
     are gone from this release.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIG = REPO / "core" / "infra" / "migrations" / "131_comms_nightly_park_and_retire.py"

SAMPLE = """\
jobs:
  # ── Tier 1 ───────────────────────────────────────────
  watchdog:
    command: bash ~/aos/core/bin/crons/watchdog
    every: 5m
    tier: 1
    description: Checks services are running

  backup-comms:
    command: bash ~/aos/core/bin/crons/backup-comms
    at: "03:15"
    timeout: 300
    tier: 4
    description: Snapshots comms.db to AOS-X (rotating 7) before nightly enrichment

  enrich-comms:
    command: bash ~/aos/core/bin/crons/enrich-comms
    at: "03:30"
    timeout: 3000
    tier: 4
    description: Nightly Haiku entity extraction into comms.db message_entities

  comms-patterns:
    command: python3 ~/aos/core/engine/comms/patterns/compute.py
    at: "05:30"
    timeout: 180
    tier: 4
    description: Computes communication patterns and trends

  comms-graduation:
    command: python3 ~/aos/core/engine/comms/graduation/runner.py
    at: "06:00"
    timeout: 120
    tier: 4
    description: Graduates contacts through trust levels
"""


@pytest.fixture
def m(tmp_path, monkeypatch):
    """The migration with HOME sandboxed for the whole test.

    Patched before exec, held for the duration — same contract every other
    migration test in this repo keeps (see test_migration_127_idempotency.py).
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    spec = importlib.util.spec_from_file_location("mig_131", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._crons_yaml() == tmp_path / "aos" / "config" / "crons.yaml"
    assert mod._stale_report() == tmp_path / ".aos" / "work" / "stale-report.yaml"
    return mod


def _write_crons(m, text: str) -> None:
    m._crons_yaml().parent.mkdir(parents=True, exist_ok=True)
    m._crons_yaml().write_text(text)


# ── Part A: comms-nightly default-off ────────────────────────────────────


def test_missing_crons_yaml_and_no_stale_report_is_already_applied(m):
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_inserts_enabled_false_for_the_three_comms_jobs_and_is_idempotent(m):
    _write_crons(m, SAMPLE)
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True

    text = m._crons_yaml().read_text()
    assert "enrich-comms:\n    enabled: false\n" in text
    assert "comms-patterns:\n    enabled: false\n" in text
    assert "comms-graduation:\n    enabled: false\n" in text

    # backup-comms is the canary: never touched, keeps running.
    assert "backup-comms:\n    command: bash ~/aos/core/bin/crons/backup-comms\n" in text
    assert "backup-comms:\n    enabled" not in text

    # watchdog block is untouched too — no stray edits leaked across blocks.
    assert "watchdog:\n    command: bash ~/aos/core/bin/crons/watchdog\n    every: 5m\n" in text

    before = m._crons_yaml().read_text()
    assert m.up() is True
    after = m._crons_yaml().read_text()
    assert before == after
    assert after.count("enabled: false") == 3


def test_already_shipped_enabled_false_is_a_noop(m):
    text = SAMPLE
    for job in ("enrich-comms", "comms-patterns", "comms-graduation"):
        text = text.replace(f"  {job}:\n    command:", f"  {job}:\n    enabled: false\n    command:")
    _write_crons(m, text)
    assert m.check() is True
    before = m._crons_yaml().read_text()
    assert m.up() is True
    assert m._crons_yaml().read_text() == before


def test_explicit_operator_opt_in_is_respected_and_never_re_disabled(m):
    _write_crons(m, SAMPLE.replace(
        "  enrich-comms:\n    command:",
        "  enrich-comms:\n    enabled: true\n    command:",
    ))
    assert m.check() is False  # the other two still need patching
    assert m.up() is True
    text = m._crons_yaml().read_text()
    assert "enrich-comms:\n    enabled: true\n    command:" in text
    assert m.check() is True

    assert m.up() is True
    assert "enrich-comms:\n    enabled: true\n    command:" in m._crons_yaml().read_text()


def test_job_missing_from_crons_yaml_is_skipped_not_blocking(m):
    text = SAMPLE.replace(
        '  comms-graduation:\n    command: python3 ~/aos/core/engine/comms/graduation/runner.py\n'
        '    at: "06:00"\n    timeout: 120\n    tier: 4\n'
        '    description: Graduates contacts through trust levels\n',
        "",
    )
    _write_crons(m, text)
    assert "comms-graduation" not in text
    assert m.check() is False  # enrich-comms / comms-patterns still unpatched
    assert m.up() is True
    assert m.check() is True


# ── Part B: archive stale-detector's orphaned output ─────────────────────


def test_no_stale_report_is_already_applied(m):
    assert m._stale_report().exists() is False
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_archives_stale_report_to_backups_and_never_deletes(m):
    report = m._stale_report()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("generated: '2026-09-12T08:00:00'\nstale_tasks: []\n")

    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    assert report.exists() is False  # gone from the old path...

    backups = m._backups_dir()
    archived = list(backups.glob("stale-report-retired-*.yaml"))
    assert len(archived) == 1, f"expected exactly one archived copy, found {archived}"
    assert "stale_tasks: []" in archived[0].read_text()  # ...content preserved, not dropped


def test_archive_step_is_idempotent(m):
    report = m._stale_report()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("generated: '2026-09-12T08:00:00'\nstale_tasks: []\n")

    assert m.up() is True
    backups = m._backups_dir()
    first_pass = list(backups.glob("stale-report-retired-*.yaml"))
    assert len(first_pass) == 1

    # Second run: nothing left at the old path, nothing new archived.
    assert m.up() is True
    second_pass = list(backups.glob("stale-report-retired-*.yaml"))
    assert len(second_pass) == 1
    assert second_pass == first_pass
