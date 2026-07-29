"""
Tests for `aos snapshot` — the live system inventory.

The snapshot exists so that documentation-shaped things (the onboard skill, the
whats-new skill, Chief) stop asserting facts about the system inline and start
reading them. Its whole value is being *derived*, so these tests check derivation
rather than values: the automation count must equal what config/crons.yaml
declares, the service list must equal the registry, the skill count must equal
the directories on disk.

They also pin robustness. The snapshot is called at the very start of
onboarding, on a machine that may be half-configured; a section whose source is
missing must degrade, never raise.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SNAPSHOT = REPO / "core" / "bin" / "cli" / "aos-snapshot"


def _mod():
    # The CLI ships without a .py extension, so the loader must be explicit —
    # spec_from_file_location cannot infer one from the suffix.
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader("aos_snapshot_under_test", str(SNAPSHOT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aos_snapshot_under_test"] = mod
    loader.exec_module(mod)
    return mod


snap = _mod()


def _yaml():
    import yaml
    return yaml


# ── derivation ───────────────────────────────────────────────────────────────

def test_automation_count_matches_crons_yaml():
    """The count must come from crons.yaml, not from a number someone typed."""
    data = _yaml().safe_load((REPO / "config" / "crons.yaml").read_text()) or {}
    jobs = data.get("jobs") or {}
    expected = sum(1 for j in jobs.values() if j.get("enabled", True) is not False)

    result = snap.automation()
    assert result["enabled"] == expected
    assert result["total"] == len(jobs)


def test_services_match_the_registry():
    """Service identity comes from the registry — the same source install.sh uses."""
    sys.path.insert(0, str(REPO / "core" / "infra" / "lib"))
    from service_registry import load_registry

    expected = {m.name for m in load_registry()}
    got = {i["name"] for i in snap.services()["items"]}
    assert got == expected


def test_skill_count_matches_disk():
    """Skills are counted from directories that actually contain a SKILL.md."""
    on_disk = {
        d.name for d in (REPO / "core" / "skills").iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    }
    names = set(snap.skills()["names"])
    # The snapshot also picks up installed skills from ~/.claude/skills, so the
    # framework's own set must be a subset — never missing one.
    assert on_disk <= names


def test_integrations_come_from_the_registry():
    reg = _yaml().safe_load(
        (REPO / "core" / "infra" / "integrations" / "registry.yaml").read_text()
    ) or {}
    expected = {
        key
        for tier in reg.values() if isinstance(tier, dict)
        for key, spec in tier.items()
        if isinstance(spec, dict) and "status" in spec
    }
    got = {i["key"] for i in snap.integrations(probe=False)["items"]}
    assert got == expected


def test_integration_states_are_honest():
    """
    An integration we cannot assess must be `unknown`, never `needs_setup`.

    Most integrations are permission-based and declare no secrets; reporting
    those as unconfigured would have onboarding talk an operator into
    re-connecting things that already work.
    """
    for item in snap.integrations(probe=False)["items"]:
        assert item["state"] in {"connected", "needs_setup", "unknown"}
        if not item["requires_secrets"]:
            # With probing off, nothing else can establish state.
            assert item["state"] == "unknown"


# ── robustness ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(snap.SECTIONS))
def test_every_section_returns_a_dict_without_raising(name):
    """Onboarding calls this first, on possibly half-built machines."""
    fn = snap.SECTIONS[name]
    result = fn(probe=False) if name == "integrations" else fn()
    assert isinstance(result, dict)


def test_missing_sources_degrade_rather_than_raise(monkeypatch, tmp_path):
    """Point every source at an empty directory; sections must still return."""
    monkeypatch.setattr(snap, "VAULT", tmp_path / "no-vault")
    monkeypatch.setattr(snap, "USER_DIR", tmp_path / "no-user-dir")
    monkeypatch.setattr(snap, "CLAUDE_DIR", tmp_path / "no-claude")

    assert snap.vault()["exists"] is False
    assert isinstance(snap.machine(), dict)
    assert isinstance(snap.subsystems(), dict)
    assert snap.skills()["count"] >= 0


# ── CLI contract ─────────────────────────────────────────────────────────────

def test_json_output_is_valid_and_complete():
    result = subprocess.run(
        [sys.executable, str(SNAPSHOT), "--json", "--no-probe"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data) == set(snap.SECTIONS)


def test_human_output_renders():
    result = subprocess.run(
        [sys.executable, str(SNAPSHOT), "--no-probe"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    for heading in ("SERVICES", "AGENTS", "SKILLS", "AUTOMATION", "INTEGRATIONS", "VAULT"):
        assert heading in result.stdout


def test_unknown_section_is_rejected():
    result = subprocess.run(
        [sys.executable, str(SNAPSHOT), "--section", "nonsense"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2


# ── the guard that gives this file its point ─────────────────────────────────

def test_onboard_skill_reads_the_snapshot():
    """
    The onboard skill must derive system facts, not state them.

    It previously hardcoded "12+ automated jobs", seven of 22 integrations, and
    a vault tour naming folders that no longer existed.
    """
    text = (REPO / "core" / "skills" / "onboard" / "SKILL.md").read_text()
    assert "aos snapshot" in text
    # The specific dead folder names from the old vault tour.
    assert "ideas/, materials/" not in text
