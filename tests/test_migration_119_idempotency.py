"""Migration 119 — the project layer reaches the instance — run twice, against a
sandboxed HOME.

Why this file exists when `tests/test_project_layer.py` already exercises the
migration: that suite patches the module's path constants *after* import. This
one patches `Path.home()` *before* `exec_module`, the way
`test_migrations_111_116_idempotency.py` does, so the test covers the real
failure mode of a migration that resolves `HOME = Path.home()` at module scope.
If a future edit moves a path out of the patched set — a new `~/.claude/...`
target, say — the attribute-patching fixture would quietly write to the
operator's own home and pass. This one cannot: the only home it knows is in
`tmp_path`, and the last test in the file asserts the live instance is clean
afterwards.

The other half of the job here is the **repoint**. On the machine this feature
was developed on, `~/.local/bin/project` and
`~/.claude/rules/project-structure.md` were hand-linked into a git worktree so
the CLI could be used while it was still being written. The migration has to
move both onto the runtime paths under `~/aos/`, idempotently, and has to do it
without destroying a real file that happens to be sitting at either path. Every
shape those two links can arrive in gets a test: absent, already correct,
pointing at a worktree, dangling, and a real file.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "core" / "infra" / "migrations" / "119_project_layer.py"


# ── Harness ──────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A sandbox HOME with the framework tree symlinked in, and nothing else real.

    `~/aos` is a symlink to the repo under test, which is the shape of a real
    instance (`~/aos` is the runtime copy) and means `RULE_SOURCE` and
    `CLI_SOURCE` resolve to files that actually exist — without which the
    migration's `if source.exists()` guards would make every assertion here
    vacuously true.
    """
    h = tmp_path / "home"
    (h / ".aos" / "config").mkdir(parents=True)
    (h / ".claude" / "rules").mkdir(parents=True)
    (h / "aos").symlink_to(REPO)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    # The migration appends to sys.path and imports project_zones. Give both a
    # scope: a copy of sys.path that monkeypatch restores, and a project_zones
    # that cannot be served from cache with some other test's HOME baked in.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "project_zones", raising=False)
    return h


@pytest.fixture
def m(home):
    """Import migration 119 with `Path.home()` already pointing at the sandbox."""
    spec = importlib.util.spec_from_file_location(f"mig_119_{home.parent.name}",
                                                  MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.HOME == home, "the migration resolved HOME outside the sandbox"
    return mod


def _worktree(home: Path) -> tuple[Path, Path]:
    """A stand-in for the dev worktree the live instance was linked into."""
    wt = home / "project" / "aos" / ".claude" / "worktrees" / "feat-project-layer"
    cli = wt / "core" / "bin" / "cli" / "project"
    rule = wt / ".claude" / "rules" / "project-structure.md"
    cli.parent.mkdir(parents=True, exist_ok=True)
    rule.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("#!/usr/bin/env python3\n# the branch copy\n")
    rule.write_text("# the branch copy of the rule\n")
    return cli, rule


def _links(m) -> dict[str, Path]:
    return {"rule": m.RULE_LINK, "cli": m.CLI_LINK}


def _points_at_runtime(m) -> None:
    """Both links are symlinks resolving to the framework copies under ~/aos."""
    assert m.RULE_LINK.is_symlink()
    assert m.RULE_LINK.resolve() == m.RULE_SOURCE.resolve()
    assert m.CLI_LINK.is_symlink()
    assert m.CLI_LINK.resolve() == m.CLI_SOURCE.resolve()
    # Stored literally as the runtime path, not the release directory it
    # happens to resolve to today: `aos update` swaps that out from under us.
    assert os.readlink(m.RULE_LINK) == str(m.RULE_SOURCE)
    assert os.readlink(m.CLI_LINK) == str(m.CLI_SOURCE)


# ── Idempotency: the contract the runner relies on ───────────────────────────


def test_runs_twice_with_no_project_dir(m):
    """A fresh machine: nothing to install zones into, links still get made."""
    assert not m.PROJECT_ROOT.exists()
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True
    _points_at_runtime(m)
    assert not m.PROJECT_ROOT.exists(), "a projects directory is never conjured"

    before = {k: os.readlink(v) for k, v in _links(m).items()}
    assert m.up() is True
    assert m.check() is True
    assert {k: os.readlink(v) for k, v in _links(m).items()} == before


def test_runs_twice_with_a_project_dir(m):
    m.PROJECT_ROOT.mkdir(parents=True)
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True

    import project_zones
    for zone in project_zones.ZONES:
        assert (m.PROJECT_ROOT / zone).is_dir()
        assert (m.PROJECT_ROOT / zone / "README.md").exists()
    assert m.POLICY.exists()

    world = sorted(str(p.relative_to(m.PROJECT_ROOT))
                   for p in m.PROJECT_ROOT.rglob("*"))
    policy = m.POLICY.read_text()

    assert m.up() is True
    assert m.check() is True
    assert sorted(str(p.relative_to(m.PROJECT_ROOT))
                  for p in m.PROJECT_ROOT.rglob("*")) == world
    assert m.POLICY.read_text() == policy


def test_first_run_actually_does_something(m):
    """A migration that does nothing is trivially idempotent and useless."""
    m.PROJECT_ROOT.mkdir(parents=True)
    assert not m.RULE_LINK.exists() and not m.CLI_LINK.exists()
    m.up()
    assert m.POLICY.exists()
    assert m.RULE_LINK.is_symlink() and m.CLI_LINK.is_symlink()


def test_an_edited_policy_file_survives_both_runs(m):
    m.PROJECT_ROOT.mkdir(parents=True)
    m.POLICY.write_text("# my own conventions\n")
    m.up()
    m.up()
    assert m.POLICY.read_text() == "# my own conventions\n"


# ── The repoint: worktree → runtime ──────────────────────────────────────────


def test_repoints_links_from_a_dev_worktree(m):
    """The live-instance case. Both links point into a branch checkout that
    `aos update` does not own; after the migration both point at `~/aos/`."""
    wt_cli, wt_rule = _worktree(m.HOME)
    m.CLI_LINK.parent.mkdir(parents=True, exist_ok=True)
    m.CLI_LINK.symlink_to(wt_cli)
    m.RULE_LINK.symlink_to(wt_rule)

    assert m.check() is False, "a worktree link is not the installed state"
    assert m.up() is True
    _points_at_runtime(m)
    assert m.check() is True

    # The worktree itself is not the migration's property to tidy up.
    assert wt_cli.exists() and wt_rule.exists()

    assert m.up() is True
    _points_at_runtime(m)


def test_already_correct_links_are_not_touched(m):
    """The idempotent path: no unlink, no relink, no backup, same inode."""
    m.CLI_LINK.parent.mkdir(parents=True, exist_ok=True)
    m.CLI_LINK.symlink_to(m.CLI_SOURCE)
    m.RULE_LINK.symlink_to(m.RULE_SOURCE)
    stamps = {k: v.lstat().st_ino for k, v in _links(m).items()}

    assert m.up() is True
    assert {k: v.lstat().st_ino for k, v in _links(m).items()} == stamps
    assert not list(m.RULE_LINK.parent.glob("*.pre-reconcile*"))
    assert not list(m.CLI_LINK.parent.glob("*.pre-reconcile*"))


def test_repoints_a_dangling_link(m):
    """The worktree was deleted. The link is broken, not absent — and a broken
    `project` command is the exact failure this repoint exists to prevent."""
    gone = m.HOME / "project" / "aos" / ".claude" / "worktrees" / "deleted" / "project"
    m.CLI_LINK.parent.mkdir(parents=True, exist_ok=True)
    m.CLI_LINK.symlink_to(gone)
    assert m.CLI_LINK.is_symlink() and not m.CLI_LINK.exists()

    assert m.check() is False
    assert m.up() is True
    _points_at_runtime(m)


def test_a_real_file_is_backed_up_never_clobbered(m):
    """The operator wrote their own rule by hand. Nothing here is entitled to
    throw that away, so it is renamed rather than removed."""
    m.RULE_LINK.write_text("# hand-written rule, not ours\n")
    assert not m.RULE_LINK.is_symlink()

    assert m.up() is True
    _points_at_runtime(m)
    backups = list(m.RULE_LINK.parent.glob("project-structure.md.pre-reconcile*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "# hand-written rule, not ours\n"


def test_a_second_relink_does_not_overwrite_the_first_backup(m):
    """Two passes, two hand-written files, two surviving backups. The obvious
    implementation deletes the old backup to make room — on exactly the file the
    backup exists to protect."""
    m.RULE_LINK.write_text("first\n")
    m.up()
    m.RULE_LINK.unlink()
    m.RULE_LINK.write_text("second\n")
    m.up()

    bodies = sorted(p.read_text() for p in
                    m.RULE_LINK.parent.glob("project-structure.md.pre-reconcile*"))
    assert bodies == ["first\n", "second\n"]
    _points_at_runtime(m)


def test_links_are_made_even_when_the_project_root_is_unreachable(m):
    """An unmounted volume blocks the zones, not the command on PATH."""
    m.PROJECT_ROOT.symlink_to(m.HOME / "nowhere" / "project")
    assert m.up() is False, "an unreachable root must leave the migration pending"
    _points_at_runtime(m)
    assert m.check() is False


# ── Unreachable ~/project stays pending rather than being recorded done ──────


def test_unreachable_root_retries_and_then_completes(m):
    """`Path.exists()` follows symlinks, so a dangling `~/project` looks exactly
    like a machine that never had one. The two want opposite answers, and
    answering "complete" for this one is unrecoverable: the runner records the
    watermark and never offers the migration again."""
    target = m.HOME / "volume" / "project"
    m.PROJECT_ROOT.symlink_to(target)

    assert m.check() is False
    assert m.up() is False

    target.mkdir(parents=True)                      # volume mounted
    assert m.up() is True
    assert m.check() is True

    import project_zones
    for zone in project_zones.ZONES:
        assert (m.PROJECT_ROOT / zone).is_dir()
    assert m.POLICY.exists()

    assert m.up() is True                           # and still idempotent


def test_absent_root_is_a_success_not_a_deferral(m):
    """No `~/project` and nothing claiming to be one: the migration is genuinely
    complete, and the first `project new` builds the structure."""
    assert m.up() is True
    assert m.check() is True


# ── The runner's contract ────────────────────────────────────────────────────


def test_has_the_runner_contract(m):
    assert isinstance(m.DESCRIPTION, str) and m.DESCRIPTION
    assert callable(m.check) and callable(m.up)
    assert m.up() in (True, None), "anything else is read as a failure"


def test_the_number_is_not_already_taken():
    """119 had to be chosen: the branch shipped this as 102, which release had
    already used for the launcher-naming migration."""
    numbers = [p.name.split("_")[0]
               for p in MIGRATION.parent.glob("[0-9][0-9][0-9]_*.py")]
    assert numbers.count("119") == 1
    assert len(numbers) == len(set(numbers)), "two migrations share a number"


# ── The live instance is never touched ───────────────────────────────────────


def test_live_instance_is_untouched_by_this_suite():
    """Guard the guard. Every path in this file comes from `Path.home()`, so a
    leaked patch would point the operator's own `~/.local/bin/project` at a
    directory under `/tmp` that macOS deletes on a timer."""
    assert Path.home() == Path("~").expanduser(), "Path.home patch leaked out of a test"

    real_home = Path("~").expanduser()
    for link in (real_home / ".claude" / "rules" / "project-structure.md",
                 real_home / ".local" / "bin" / "project"):
        if link.is_symlink():
            assert "/tmp" not in os.readlink(link), (
                f"{link} points into a test sandbox — the suite wrote outside it")

    strays = list((real_home / ".claude" / "rules").glob("*.pre-reconcile*"))
    assert not strays, f"the suite backed up a live rule file: {strays}"
