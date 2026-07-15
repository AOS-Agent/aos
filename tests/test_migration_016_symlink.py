"""Migration 016 must never treat the post-reorg core/lib SYMLINK as the
v1-era dead directory.

Found by the clean-box VM (2026-07-15): on a fresh install of current main,
016's up() called shutil.rmtree() on core/lib — which is now a live symlink
to core/infra/lib — raising and halting the entire migration batch at 16,
blocking 17-81 on every new machine.

Contract pinned here:
- core/lib is a symlink  -> check() sees no issue, up() leaves it alone.
- core/lib is a real dir -> still detected and removed (original behavior).
- core/lib missing       -> no issue.
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "core" / "infra" / "migrations" / "016_cleanup.py"


def _load(tmp_home: Path):
    spec = importlib.util.spec_from_file_location("mig016", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HOME = tmp_home
    mod.AOS_DIR = tmp_home / "aos"
    mod.LA_DIR = tmp_home / "Library" / "LaunchAgents"
    return mod


def _scaffold(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "aos" / "core").mkdir(parents=True)
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    return home


def test_symlink_lib_is_not_an_issue(tmp_path):
    home = _scaffold(tmp_path)
    target = home / "aos" / "core" / "infra" / "lib"
    target.mkdir(parents=True)
    (target / "notify.py").write_text("# live module\n")
    (home / "aos" / "core" / "lib").symlink_to(target)

    mod = _load(home)
    assert mod.check() is True, "live symlink must not be flagged for cleanup"


def test_symlink_lib_survives_up(tmp_path):
    home = _scaffold(tmp_path)
    target = home / "aos" / "core" / "infra" / "lib"
    target.mkdir(parents=True)
    (target / "notify.py").write_text("# live module\n")
    link = home / "aos" / "core" / "lib"
    link.symlink_to(target)

    mod = _load(home)
    assert mod.up() is True
    assert link.is_symlink(), "up() must not touch the live symlink"
    assert (target / "notify.py").exists(), "symlink target must be intact"


def test_real_dead_dir_still_removed(tmp_path):
    home = _scaffold(tmp_path)
    dead = home / "aos" / "core" / "lib"
    dead.mkdir(parents=True)
    (dead / "config.py").write_text("# v1 artifact\n")

    mod = _load(home)
    assert mod.check() is False, "real dead dir must still be detected"
    assert mod.up() is True
    assert not dead.exists(), "real dead dir must still be removed"


def test_missing_lib_is_fine(tmp_path):
    home = _scaffold(tmp_path)
    mod = _load(home)
    assert mod.check() is True
    assert mod.up() is True
