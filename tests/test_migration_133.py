"""Migration 133 — revert the `.last-boot` directory mitigation for aos#2356.

Same contract as sibling migrations (see test_migration_131.py): idempotent,
never touches anything it wasn't named for, one-way (down() is False, same as
131's archive step — there is nothing safe to automatically re-mitigate).
"""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIG = REPO / "core" / "infra" / "migrations" / "133_revert_last_boot_directory_mitigation.py"


@pytest.fixture
def m(tmp_path, monkeypatch):
    """The migration with HOME sandboxed for the whole test — same contract
    every other migration test in this repo keeps."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    loader = SourceFileLoader("mig_133", str(MIG))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert mod._last_boot_file() == tmp_path / ".aos" / "logs" / "crons" / ".last-boot"
    return mod


def test_nothing_present_is_already_applied(m):
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_ordinary_file_is_left_alone(m):
    f = m._last_boot_file()
    f.parent.mkdir(parents=True)
    f.write_text("1757764800")

    assert m.check() is True  # not a directory — already converged
    assert m.up() is True
    assert f.read_text() == "1757764800"


def test_marked_directory_is_removed(m):
    d = m._last_boot_file()
    d.mkdir(parents=True)
    (d / m.MARKER_NAME).write_text("see aos#2356 — safe to rm -rf once fixed")

    assert m.check() is False

    assert m.up() is True
    assert m.check() is True
    assert not d.exists()


def test_unmarked_directory_is_left_alone_not_this_mitigation(m):
    """A directory at this exact path that isn't the documented #2356
    workaround (no marker) must never be assumed to be it and removed."""
    d = m._last_boot_file()
    d.mkdir(parents=True)
    (d / "some-other-file.txt").write_text("unrelated")

    assert m.check() is True  # not this migration's concern
    assert m.up() is True
    assert d.exists()  # untouched
    assert (d / "some-other-file.txt").exists()


def test_idempotent_second_run_is_a_noop(m):
    d = m._last_boot_file()
    d.mkdir(parents=True)
    (d / m.MARKER_NAME).write_text("see aos#2356")

    assert m.up() is True
    assert not d.exists()
    assert m.up() is True  # second pass: nothing left to remove
    assert m.check() is True


def test_down_is_a_one_way_migration():
    """Recreating a deliberate operator workaround automatically would be
    exactly the kind of silent, unrequested action the migration and
    destructive-ops rules both forbid — down() refuses, same as 131."""
    loader = SourceFileLoader("mig_133_down", str(MIG))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert mod.down() is False
