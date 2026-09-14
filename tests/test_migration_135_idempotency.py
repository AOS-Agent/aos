"""Migration 135 — per-profile Claude Code launchers (cld2/cld3) reach the
instance — run twice, against a sandboxed HOME.

Same harness shape as `tests/test_migration_119_idempotency.py`: `Path.home()`
is sandboxed persistently for the whole test rather than patching module path
constants after import, because the migration has no path constants to patch
— only functions (`_home()`, `_cld_source()`, `_cld2_link()`, …) that
re-resolve `Path.home()` on every call.

Every shape the two links can arrive in gets a test: absent, already correct,
pointing somewhere else (dangling or not), and a real file in the way. A
dedicated pair of tests covers the two things the migration is explicit about
NOT doing: it never creates a profile directory, and it never repairs
`~/.local/bin/aos` — only reports on it.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "core" / "infra" / "migrations" / "135_claude_profile_launchers.py"


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def home(tmp_path, monkeypatch):
    """A sandbox HOME with the framework tree symlinked in as `~/aos`, and
    nothing else real — matching the shape a real instance has: `~/aos` is
    the runtime copy, and `~/aos/core/bin/cld` (itself a checked-in symlink to
    `cli/cld`) is what `_cld_source()` must resolve to for this migration to
    have anything to link against."""
    h = tmp_path / "home"
    (h / ".local" / "bin").mkdir(parents=True)
    (h / "aos").symlink_to(REPO)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


@pytest.fixture
def m(home):
    """Import migration 135 with `Path.home()` already pointing at the sandbox."""
    spec = importlib.util.spec_from_file_location(f"mig_135_{home.parent.name}",
                                                    MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._home() == home, "the migration resolved HOME outside the sandbox"
    return mod


def _links(m) -> dict[str, Path]:
    return {name: m._launcher_link(name) for name in m.LAUNCHER_NAMES}


def _points_at_runtime(m) -> None:
    source = m._cld_source()
    for name in m.LAUNCHER_NAMES:
        link = m._launcher_link(name)
        assert link.is_symlink(), f"{name} is not a symlink"
        assert link.resolve() == source.resolve(), f"{name} points at the wrong target"
        # Stored literally as the runtime path, not whatever it happens to
        # resolve to today — `aos update` swaps the release dir out from
        # under it.
        assert os.readlink(link) == str(source)


# ── Idempotency: the contract the runner relies on ───────────────────────────

def test_runs_twice_from_nothing(m):
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True
    _points_at_runtime(m)

    before = {k: os.readlink(v) for k, v in _links(m).items()}
    assert m.up() is True
    assert m.check() is True
    assert {k: os.readlink(v) for k, v in _links(m).items()} == before


def test_first_run_actually_does_something(m):
    """A migration that does nothing is trivially idempotent and useless."""
    assert not m._cld2_link().exists() and not m._cld3_link().exists()
    m.up()
    assert m._cld2_link().is_symlink() and m._cld3_link().is_symlink()


def test_already_correct_links_are_not_touched(m):
    """The idempotent path: no unlink, no relink, no backup, same inode."""
    for name in m.LAUNCHER_NAMES:
        m._launcher_link(name).symlink_to(m._cld_source())
    stamps = {k: v.lstat().st_ino for k, v in _links(m).items()}

    assert m.up() is True
    assert {k: v.lstat().st_ino for k, v in _links(m).items()} == stamps
    assert not list(m._local_bin().glob("cld2.pre-135*"))
    assert not list(m._local_bin().glob("cld3.pre-135*"))


def test_repoints_a_link_pointing_elsewhere(m):
    """A stale target (e.g. a worktree checkout) is repointed, not left."""
    stray = m._home() / "somewhere" / "cld"
    stray.parent.mkdir(parents=True)
    stray.write_text("#!/bin/sh\necho stray\n")
    m._cld2_link().symlink_to(stray)

    assert m.check() is False
    assert m.up() is True
    _points_at_runtime(m)
    assert m.up() is True
    _points_at_runtime(m)


def test_repoints_a_dangling_link(m):
    gone = m._home() / "nowhere" / "cld"
    m._cld3_link().symlink_to(gone)
    assert m._cld3_link().is_symlink() and not m._cld3_link().exists()

    assert m.check() is False
    assert m.up() is True
    _points_at_runtime(m)


def test_a_real_file_is_backed_up_never_clobbered(m):
    """The operator's own script sitting at ~/.local/bin/cld2. Nothing here is
    entitled to throw it away, so it is renamed rather than removed."""
    link = m._cld2_link()
    link.write_text("#!/bin/sh\necho hand-written\n")
    assert not link.is_symlink()

    assert m.up() is True
    _points_at_runtime(m)
    backups = list(m._local_bin().glob("cld2.pre-135*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "#!/bin/sh\necho hand-written\n"


def test_a_second_backup_does_not_overwrite_the_first(m):
    link = m._cld2_link()
    link.write_text("first\n")
    m.up()
    link.unlink()
    link.write_text("second\n")
    m.up()

    bodies = sorted(p.read_text() for p in m._local_bin().glob("cld2.pre-135*"))
    assert bodies == ["first\n", "second\n"]
    _points_at_runtime(m)


def test_incomplete_framework_tree_stays_pending(m):
    """No ~/aos/core/bin/cld to link to — the migration must not claim success."""
    (m._home() / "aos").unlink()
    assert m.check() is False
    assert m.up() is False
    assert not m._cld2_link().exists()
    assert not m._cld3_link().exists()


# ── What this migration deliberately does not do ─────────────────────────────

def test_never_creates_a_profile_directory(m):
    m.up()
    assert not (m._home() / ".aos" / "claude-profiles").exists()


def test_reports_but_does_not_repair_a_broken_aos_link(m, capsys):
    """~/.local/bin/aos is install.sh's symlink, not this migration's — a
    dangling one is reported, never touched."""
    aos_link = m._aos_link()
    aos_link.symlink_to(m._home() / "nowhere")
    assert aos_link.is_symlink() and not aos_link.exists()

    assert m.up() is True  # cld2/cld3 still install fine
    out = capsys.readouterr().out
    assert "aos" in out and "no longer resolves" in out
    # Untouched — still the same dangling target, not repaired or removed.
    assert os.readlink(aos_link) == str(m._home() / "nowhere")


def test_a_healthy_aos_link_is_left_alone_and_unremarked(m, capsys):
    aos_source = m._home() / "aos" / "core" / "bin" / "cli" / "aos"
    m._aos_link().symlink_to(aos_source)

    assert m.up() is True
    out = capsys.readouterr().out
    assert "no longer resolves" not in out
    assert m._aos_link().resolve() == aos_source.resolve()


def test_absent_aos_link_is_not_flagged(m, capsys):
    """A machine that never had ~/.local/bin/aos (or this test's own sandbox,
    which never creates one) is not this migration's problem to report on."""
    assert not m._aos_link().exists()
    assert m.up() is True
    out = capsys.readouterr().out
    assert "no longer resolves" not in out


# ── The runner's contract ────────────────────────────────────────────────────

def test_has_the_runner_contract(m):
    assert isinstance(m.DESCRIPTION, str) and m.DESCRIPTION
    assert callable(m.check) and callable(m.up)
    assert m.up() in (True, None), "anything else is read as a failure"


def test_the_number_is_not_already_taken():
    numbers = [p.name.split("_")[0]
               for p in MIGRATION.parent.glob("[0-9][0-9][0-9]_*.py")]
    assert numbers.count("135") == 1
    assert len(numbers) == len(set(numbers)), "two migrations share a number"


# ── The live instance is never touched ───────────────────────────────────────

def test_live_instance_is_untouched_by_this_suite():
    """Guard the guard — same shape as migration 119's own version of this
    test. A leaked Path.home() patch would point the operator's real
    ~/.local/bin/cld2 (or cld3) at a directory under /tmp macOS deletes on a
    timer."""
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"

    real_home = Path("~").expanduser()
    for name in ("cld2", "cld3"):
        link = real_home / ".local" / "bin" / name
        if link.is_symlink():
            assert "/tmp" not in os.readlink(link), (
                f"{link} points into a test sandbox — the suite wrote outside it")

    strays = list((real_home / ".local" / "bin").glob("cld*.pre-135*"))
    assert not strays, f"the suite backed up a live launcher: {strays}"
