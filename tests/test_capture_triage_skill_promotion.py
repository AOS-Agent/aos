"""
Tests for promoting `capture` and `triage` from operator-local skills
(~/.claude/skills/capture, ~/.claude/skills/triage on this machine) into
core/skills/, so every install gets them (aos#237, faisal-mini parity audit:
`ls -la ~/.claude/skills/capture ~/.claude/skills/triage` on this machine
showed real directories, not symlinks — meaning they were never shipped by
the framework at all).

Both the ongoing `skill_symlinks` reconcile check
(core/infra/reconcile/checks/symlinks.py) and migration 004
(core/infra/migrations/004_symlink_skills.py) auto-discover every directory
under core/skills/ — neither has a hardcoded allowlist standing between a
new core/skills/<name>/ and every machine getting it linked. These tests
pin that: the promoted skills are picked up with zero changes to either
file, which is also why 004's CORE_SKILLS list is untouched by this change
(it only gates 004's own `check()` sufficiency test, not what `up()` links).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CAPTURE_DIR = REPO / "core" / "skills" / "capture"
TRIAGE_DIR = REPO / "core" / "skills" / "triage"
MIGRATION_004 = REPO / "core" / "infra" / "migrations" / "004_symlink_skills.py"
SYMLINKS_CHECK = REPO / "core" / "infra" / "reconcile" / "checks" / "symlinks.py"

DESC_CAP = 1500  # ship-check's own budget for a skill's frontmatter description


def _frontmatter_description(skill_md: Path) -> str:
    text = skill_md.read_text()
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    assert m, f"{skill_md} has no frontmatter"
    fm = m.group(1)
    dm = re.search(r"^description:\s*(.*?)(?=^\S|\Z)", fm, re.S | re.M)
    assert dm, f"{skill_md} frontmatter has no description"
    return dm.group(1)


# ── The promoted files exist and are within budget ──────────────────────────


def test_capture_promoted_with_skill_md():
    assert (CAPTURE_DIR / "SKILL.md").is_file()


def test_triage_promoted_with_its_reference_docs_and_agents_manifest():
    assert (TRIAGE_DIR / "SKILL.md").is_file()
    assert (TRIAGE_DIR / "AGENT-BRIEF.md").is_file()
    assert (TRIAGE_DIR / "OUT-OF-SCOPE.md").is_file()
    assert (TRIAGE_DIR / "agents" / "openai.yaml").is_file()


@pytest.mark.parametrize("skill_md", [CAPTURE_DIR / "SKILL.md", TRIAGE_DIR / "SKILL.md"])
def test_description_within_ship_check_budget(skill_md):
    assert len(_frontmatter_description(skill_md)) <= DESC_CAP


@pytest.mark.parametrize("skill_dir,name", [(CAPTURE_DIR, "capture"), (TRIAGE_DIR, "triage")])
def test_frontmatter_name_matches_directory(skill_dir, name):
    text = (skill_dir / "SKILL.md").read_text()
    m = re.search(r"^name:\s*(\S+)", text, re.M)
    assert m and m.group(1) == name


# ── Migration 004 auto-discovers them with zero list changes ────────────────


def _load_migration_004(home: Path):
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        loader = importlib.machinery.SourceFileLoader(f"mig004_{id(home)}", str(MIGRATION_004))
        spec = importlib.util.spec_from_file_location(loader.name, MIGRATION_004, loader=loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


@pytest.fixture
def home_with_capture_and_triage(tmp_path):
    """A sandbox HOME whose core/skills/ holds only the two promoted skills —
    isolates the auto-discovery claim from the rest of the real skill set."""
    h = tmp_path / "home"
    skills_src = h / "aos" / "core" / "skills"
    skills_src.mkdir(parents=True)
    for src in (CAPTURE_DIR, TRIAGE_DIR):
        dest = skills_src / src.name
        dest.mkdir()
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src)
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_bytes(f.read_bytes())
    return h


def test_migration_004_core_skills_list_does_not_need_them():
    """The hardcoded-list smell the audit flagged — confirmed harmless: it
    only gates check()'s sufficiency test, never what up() links (that's a
    live iterdir() over core/skills/, below)."""
    mod = _load_migration_004(Path.home())
    assert "capture" not in mod.CORE_SKILLS
    assert "triage" not in mod.CORE_SKILLS


def test_migration_004_links_capture_and_triage_without_being_told_to(home_with_capture_and_triage):
    mod = _load_migration_004(home_with_capture_and_triage)
    assert mod.up() is True

    target = home_with_capture_and_triage / ".claude" / "skills"
    for name in ("capture", "triage"):
        link = target / name
        assert link.is_symlink(), f"{name} was not symlinked"
        assert link.resolve() == (home_with_capture_and_triage / "aos" / "core" / "skills" / name).resolve()


# ── The ongoing reconcile check auto-discovers them too ─────────────────────


def _load_symlinks_check(home: Path):
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    for name in ("base",):
        sys.modules.pop(name, None)
    try:
        loader = importlib.machinery.SourceFileLoader(f"symlinks_{id(home)}", str(SYMLINKS_CHECK))
        spec = importlib.util.spec_from_file_location(loader.name, SYMLINKS_CHECK, loader=loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        Path.home = real_home  # type: ignore[method-assign]


def test_skill_symlink_check_discovers_capture_and_triage(home_with_capture_and_triage):
    mod = _load_symlinks_check(home_with_capture_and_triage)
    check = mod.SkillSymlinkCheck()
    names = check._framework_skills()
    assert "capture" in names
    assert "triage" in names


def test_skill_symlink_check_converts_an_existing_real_copy_into_a_symlink(
    home_with_capture_and_triage,
):
    """The exact path this operator's own machine takes: `capture` and
    `triage` exist there as real directories (pre-dating this promotion),
    and the reconcile check's job is to back the copy up and replace it
    with a symlink to the newly-framework-owned source — never deleting
    the original content outright."""
    live_skills = home_with_capture_and_triage / ".claude" / "skills"
    live_skills.mkdir(parents=True)
    real_capture = live_skills / "capture"
    real_capture.mkdir()
    (real_capture / "SKILL.md").write_text("an operator's real, pre-existing copy\n")

    mod = _load_symlinks_check(home_with_capture_and_triage)
    check = mod.SkillSymlinkCheck()
    assert check.check() is False

    result = check.fix()

    link = live_skills / "capture"
    assert link.is_symlink()
    assert result.status is mod.Status.FIXED

    backups = list((home_with_capture_and_triage / ".aos" / "backups" / "pre-reconcile" / "skills").glob("*capture"))
    assert backups, "the operator's original copy must be backed up, not deleted"
    assert (backups[0] / "SKILL.md").read_text() == "an operator's real, pre-existing copy\n"
