"""
Idempotency and safety tests for migration 125 (QMD collection backfill).

Second-operator machines install `qmd` via install.sh's `prereq_qmd` and get
nothing further — no collection is ever registered, so `qmd status` reports
zero documents until someone runs `qmd collection add` by hand (see aos#237,
the faisal-mini parity audit). This migration backfills the curated AOS
collection set (log, knowledge, skills, skills-core, agents, aos-docs) that a
reference machine actually runs, adding only whatever is MISSING and never
touching a collection that already exists — including an operator's own,
unrelated collections (a hand-added flat `vault`, a business knowledge base,
etc.), which are not this migration's to disturb.

Every test drives a stubbed `qmd` binary that records its own invocations
instead of a real install, and runs the migration twice — the same contract
as every other v0.8.0 migration: `up()` must be safe to replay.
"""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "core" / "infra" / "migrations"

STUB_QMD = """#!/bin/bash
# Records every invocation as one space-joined line, then fakes just enough
# of `qmd collection show/add` + `update`/`embed` for the migration under
# test. State (which collection names "exist") persists in $QMD_STUB_STATE
# across invocations within a test, the same way a real qmd index would.
echo "$*" >> "$QMD_STUB_CALLS"
touch "$QMD_STUB_STATE"

if [ "$1" = "collection" ] && [ "$2" = "show" ]; then
    grep -qxF "$3" "$QMD_STUB_STATE" && exit 0 || exit 1
elif [ "$1" = "collection" ] && [ "$2" = "add" ]; then
    echo "$3" >> "$QMD_STUB_STATE"
    exit 0
fi
exit 0
"""


def load_migration(name: str, home: Path):
    """Import a migration with Path.home() already pointing at the sandbox.

    Migrations resolve their paths at import time (HOME = Path.home() at
    module scope), so the patch has to be in place before exec_module, and
    the module has to be re-imported per test rather than cached.
    """
    path = next(MIGRATIONS.glob(f"{name}*.py"))
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        spec = importlib.util.spec_from_file_location(f"mig_{name}_{home.name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A sandbox HOME with the stub qmd wired up and the six AOS source dirs
    present (vault/log, vault/knowledge, .claude/skills, aos/core/skills,
    aos/core/agents, aos/docs) so the migration finds real paths to add.
    """
    h = tmp_path / "home"
    qmd_bin = h / ".bun" / "bin" / "qmd"
    qmd_bin.parent.mkdir(parents=True)
    qmd_bin.write_text(STUB_QMD)
    qmd_bin.chmod(qmd_bin.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    for rel in (
        "vault/log", "vault/knowledge", ".claude/skills",
        "aos/core/skills", "aos/core/agents", "aos/docs",
    ):
        (h / rel).mkdir(parents=True)

    # Path objects can't carry extra attributes (no __dict__), so the calls
    # log and state file live as fixed siblings of `home` under the same
    # tmp_path rather than being stashed on it.
    monkeypatch.setenv("QMD_STUB_CALLS", str(_calls_log(h)))
    monkeypatch.setenv("QMD_STUB_STATE", str(_state_file(h)))
    return h


def _calls_log(home: Path) -> Path:
    return home.parent / "calls.log"


def _state_file(home: Path) -> Path:
    return home.parent / "state.txt"


def _calls(home: Path) -> list[str]:
    log = _calls_log(home)
    if not log.exists():
        return []
    return log.read_text().splitlines()


def _state(home: Path) -> set[str]:
    state = _state_file(home)
    if not state.exists():
        return set()
    return set(state.read_text().split())


AOS_NAMES = {"log", "knowledge", "skills", "skills-core", "agents", "aos-docs"}


# ── The migration does the thing ─────────────────────────────────────────────


def test_125_adds_every_missing_collection_and_reindexes_once(home):
    m = load_migration("125", home)

    assert m.check() is False
    assert m.up() is True

    adds = [c for c in _calls(home) if c.startswith("collection add ")]
    added_names = {c.split()[2] for c in adds}
    assert added_names == AOS_NAMES

    # Each add names the framework-declared path, not a placeholder.
    log_add = next(c for c in adds if c.split()[2] == "log")
    assert str(home / "vault" / "log") in log_add

    assert _calls(home).count("update") == 1
    assert _calls(home).count("embed") == 1

    assert m.check() is True


def test_125_second_run_is_a_pure_noop(home):
    m = load_migration("125", home)
    m.up()
    first_run_calls = list(_calls(home))

    # Replay, as the runner would on a machine already past this migration —
    # or an operator running `aos migrate` a second time by hand.
    m2 = load_migration("125", home)
    assert m2.check() is True
    assert m2.up() is True

    second_run_calls = _calls(home)[len(first_run_calls):]
    assert not any(c.startswith("collection add ") for c in second_run_calls), \
        "replay re-added a collection that already exists"
    assert "update" not in second_run_calls
    assert "embed" not in second_run_calls, "replay re-triggered a reindex for nothing"


def test_125_never_touches_an_operators_own_collection(home):
    """A hand-added `vault` collection (or any name outside the AOS set) is
    not this migration's to add, remove, or rename."""
    _state_file(home).write_text("vault\n")

    m = load_migration("125", home)
    assert m.up() is True

    assert not any(
        c.split()[2] == "vault"
        for c in _calls(home)
        if c.startswith("collection add ")
    )
    assert not any(c.startswith("collection remove") for c in _calls(home))
    assert not any(c.startswith("collection rename") for c in _calls(home))
    assert "vault" in _state(home), "the operator's own collection must survive untouched"


def test_125_skips_a_collection_whose_source_path_does_not_exist(home):
    """A partial install (e.g. no ~/aos/docs yet) must not crash the
    migration or block it forever — that collection is simply not addable
    yet, and the migration says so rather than failing."""
    import shutil
    shutil.rmtree(home / "aos" / "docs")

    m = load_migration("125", home)
    assert m.up() is True

    adds = [c for c in _calls(home) if c.startswith("collection add ")]
    added_names = {c.split()[2] for c in adds}
    assert "aos-docs" not in added_names
    assert added_names == AOS_NAMES - {"aos-docs"}


def test_125_is_a_noop_when_qmd_is_not_installed(home):
    """QMD not installed at all — skip cleanly, never treat it as failure."""
    (home / ".bun" / "bin" / "qmd").unlink()

    m = load_migration("125", home)
    assert m.check() is True
    assert m.up() is True
    assert _calls(home) == []


def test_125_reports_a_description_and_refuses_to_reverse(home):
    m = load_migration("125", home)
    assert isinstance(m.DESCRIPTION, str) and m.DESCRIPTION
    assert m.down() is False


def test_125_declares_exactly_the_reference_machine_collection_set(home):
    """The canonical set this migration backfills, pinned so a future edit to
    it is a deliberate diff and not an accidental add/removal."""
    m = load_migration("125", home)
    assert set(m.AOS_COLLECTIONS.keys()) == AOS_NAMES
