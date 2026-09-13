"""Migration 119 — the project layer reaches the instance — run twice, against a
sandboxed HOME.

Why this file exists alongside `tests/test_project_layer.py`'s own migration
coverage: both suites now sandbox `Path.home()` persistently for the whole
test (matching `test_migrations_111_116_idempotency.py`'s pattern) rather than
patching the module's path constants after import — the migration no longer
HAS path constants to patch, only functions (`_home()`, `_project_root()`,
`_rule_link()`, …) that re-resolve `Path.home()` on every call. This file
covers the repoint/backup shapes end to end; `test_project_layer.py` covers
the same migration against the real framework tree in place, plus the
surrounding zone/reconcile/steward-check machinery.

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
    instance (`~/aos` is the runtime copy) and means `_rule_source()` and
    `_cli_source()` resolve to files that actually exist — without which the
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
    assert mod._home() == home, "the migration resolved HOME outside the sandbox"
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
    return {"rule": m._rule_link(), "cli": m._cli_link()}


def _points_at_runtime(m) -> None:
    """Both links are symlinks resolving to the framework copies under ~/aos."""
    rule_link, rule_source = m._rule_link(), m._rule_source()
    cli_link, cli_source = m._cli_link(), m._cli_source()
    assert rule_link.is_symlink()
    assert rule_link.resolve() == rule_source.resolve()
    assert cli_link.is_symlink()
    assert cli_link.resolve() == cli_source.resolve()
    # Stored literally as the runtime path, not the release directory it
    # happens to resolve to today: `aos update` swaps that out from under us.
    assert os.readlink(rule_link) == str(rule_source)
    assert os.readlink(cli_link) == str(cli_source)


# ── Idempotency: the contract the runner relies on ───────────────────────────


def test_runs_twice_with_no_project_dir(m):
    """A fresh machine: nothing to install zones into, links still get made."""
    assert not m._project_root().exists()
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True
    _points_at_runtime(m)
    assert not m._project_root().exists(), "a projects directory is never conjured"

    before = {k: os.readlink(v) for k, v in _links(m).items()}
    assert m.up() is True
    assert m.check() is True
    assert {k: os.readlink(v) for k, v in _links(m).items()} == before


def test_runs_twice_with_a_project_dir(m):
    m._project_root().mkdir(parents=True)
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True

    import project_zones
    project_root = m._project_root()
    for zone in project_zones.ZONES:
        assert (project_root / zone).is_dir()
        assert (project_root / zone / "README.md").exists()
    assert m._policy().exists()

    world = sorted(str(p.relative_to(project_root))
                   for p in project_root.rglob("*"))
    policy = m._policy().read_text()

    assert m.up() is True
    assert m.check() is True
    assert sorted(str(p.relative_to(project_root))
                  for p in project_root.rglob("*")) == world
    assert m._policy().read_text() == policy


def test_first_run_actually_does_something(m):
    """A migration that does nothing is trivially idempotent and useless."""
    m._project_root().mkdir(parents=True)
    assert not m._rule_link().exists() and not m._cli_link().exists()
    m.up()
    assert m._policy().exists()
    assert m._rule_link().is_symlink() and m._cli_link().is_symlink()


def test_an_edited_policy_file_survives_both_runs(m):
    m._project_root().mkdir(parents=True)
    m._policy().write_text("# my own conventions\n")
    m.up()
    m.up()
    assert m._policy().read_text() == "# my own conventions\n"


# ── The repoint: worktree → runtime ──────────────────────────────────────────


def test_repoints_links_from_a_dev_worktree(m):
    """The live-instance case. Both links point into a branch checkout that
    `aos update` does not own; after the migration both point at `~/aos/`."""
    wt_cli, wt_rule = _worktree(m._home())
    cli_link, rule_link = m._cli_link(), m._rule_link()
    cli_link.parent.mkdir(parents=True, exist_ok=True)
    cli_link.symlink_to(wt_cli)
    rule_link.symlink_to(wt_rule)

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
    cli_link, rule_link = m._cli_link(), m._rule_link()
    cli_link.parent.mkdir(parents=True, exist_ok=True)
    cli_link.symlink_to(m._cli_source())
    rule_link.symlink_to(m._rule_source())
    stamps = {k: v.lstat().st_ino for k, v in _links(m).items()}

    assert m.up() is True
    assert {k: v.lstat().st_ino for k, v in _links(m).items()} == stamps
    assert not list(rule_link.parent.glob("*.pre-reconcile*"))
    assert not list(cli_link.parent.glob("*.pre-reconcile*"))


def test_repoints_a_dangling_link(m):
    """The worktree was deleted. The link is broken, not absent — and a broken
    `project` command is the exact failure this repoint exists to prevent."""
    gone = m._home() / "project" / "aos" / ".claude" / "worktrees" / "deleted" / "project"
    cli_link = m._cli_link()
    cli_link.parent.mkdir(parents=True, exist_ok=True)
    cli_link.symlink_to(gone)
    assert cli_link.is_symlink() and not cli_link.exists()

    assert m.check() is False
    assert m.up() is True
    _points_at_runtime(m)


def test_a_real_file_is_backed_up_never_clobbered(m):
    """The operator wrote their own rule by hand. Nothing here is entitled to
    throw that away, so it is renamed rather than removed."""
    rule_link = m._rule_link()
    rule_link.write_text("# hand-written rule, not ours\n")
    assert not rule_link.is_symlink()

    assert m.up() is True
    _points_at_runtime(m)
    backups = list(rule_link.parent.glob("project-structure.md.pre-reconcile*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "# hand-written rule, not ours\n"


def test_a_second_relink_does_not_overwrite_the_first_backup(m):
    """Two passes, two hand-written files, two surviving backups. The obvious
    implementation deletes the old backup to make room — on exactly the file the
    backup exists to protect."""
    rule_link = m._rule_link()
    rule_link.write_text("first\n")
    m.up()
    rule_link.unlink()
    rule_link.write_text("second\n")
    m.up()

    bodies = sorted(p.read_text() for p in
                    rule_link.parent.glob("project-structure.md.pre-reconcile*"))
    assert bodies == ["first\n", "second\n"]
    _points_at_runtime(m)


def test_links_are_made_even_when_the_project_root_is_unreachable(m):
    """An unmounted volume blocks the zones, not the command on PATH."""
    m._project_root().symlink_to(m._home() / "nowhere" / "project")
    assert m.up() is False, "an unreachable root must leave the migration pending"
    _points_at_runtime(m)
    assert m.check() is False


# ── Unreachable ~/project stays pending rather than being recorded done ──────


def test_unreachable_root_retries_and_then_completes(m):
    """`Path.exists()` follows symlinks, so a dangling `~/project` looks exactly
    like a machine that never had one. The two want opposite answers, and
    answering "complete" for this one is unrecoverable: the runner records the
    watermark and never offers the migration again."""
    target = m._home() / "volume" / "project"
    m._project_root().symlink_to(target)

    assert m.check() is False
    assert m.up() is False

    target.mkdir(parents=True)                      # volume mounted
    assert m.up() is True
    assert m.check() is True

    import project_zones
    project_root = m._project_root()
    for zone in project_zones.ZONES:
        assert (project_root / zone).is_dir()
    assert m._policy().exists()

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
