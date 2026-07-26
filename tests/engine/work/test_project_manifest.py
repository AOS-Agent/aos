"""Project manifest tests.

The manifest's job is to make discovery deterministic and to be *safe to commit
to a public repo*. So the tests concentrate on the schema being a real barrier
rather than a suggestion:

  * derived fields (state/status/progress/...) are REJECTED, not ignored
  * unknown keys are REJECTED — that is what keeps sensitive data out
  * absolute paths are REJECTED everywhere they could appear
  * description is one line and length-capped
  * adoption recovers what work.db already knows, including the appetite and
    initiative crammed into the description string
  * adoption is idempotent
  * directory-less projects are an explicit, documented case
  * divergence is reported as a brief_types.Conflict, never resolved
  * planning writes nothing

Isolated: synthetic project records and tmp_path directories throughout.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "engine" / "work"))

import project_manifest as pm  # noqa: E402

yaml = pytest.importorskip("yaml")


def _valid() -> dict:
    return {
        "schema": 1, "id": "demo", "kind": "python", "title": "Demo",
        "description": "A short summary.", "done_when": "It ships.",
        "appetite": "2-weeks", "initiative": "demo-init", "goal": None,
        "docs": ["knowledge/initiatives/demo-init.md"],
        "repos": {"root": ".", "components": [
            {"path": "app", "role": "sub-app", "remote": "https://x/y.git"}]},
        "worktrees": {"location": ".claude/worktrees", "naming": "branch-slug"},
    }


# ── the schema is a barrier, not a suggestion ───────────────────────

def test_a_complete_valid_manifest_passes():
    assert pm.validate(_valid()) == []


@pytest.mark.parametrize("key", sorted(pm.FORBIDDEN_KEYS))
def test_every_derived_field_is_rejected(key):
    """state/status/progress/... must be impossible to declare, not merely
    discouraged. This is the bug the whole workstream exists to kill."""
    raw = _valid()
    raw[key] = "whatever"
    errors = pm.validate(raw)
    assert any(key in e for e in errors), f"{key} was accepted"


def test_unknown_keys_are_rejected_not_ignored():
    """A permissive validator would defeat the privacy design — an unknown key
    is exactly how `students:` or `notes:` would end up in a public repo."""
    raw = _valid()
    raw["students"] = ["a name that must never reach GitHub"]
    errors = pm.validate(raw)
    assert any("students" in e and "unknown key" in e for e in errors)


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.__setitem__("docs", ["/Users/someone/vault/x.md"]), "docs path"),
    (lambda r: r.__setitem__("docs", ["~/vault/x.md"]), "docs path"),
    (lambda r: r["repos"].__setitem__("root", "/abs/repo"), "repos.root"),
    (lambda r: r["repos"]["components"][0].__setitem__("path", "/abs/app"), "path"),
    (lambda r: r["worktrees"].__setitem__("location", "/private/tmp/wt"), "worktrees.location"),
])
def test_absolute_paths_are_rejected_everywhere(mutate, needle):
    """Absolute paths leak the username and break on the Pi and the MacBook."""
    raw = _valid()
    mutate(raw)
    errors = pm.validate(raw)
    assert any(needle in e for e in errors), errors


def test_tmp_worktree_location_is_rejected_with_the_reason():
    raw = _valid()
    raw["worktrees"]["location"] = "/private/tmp/aos-x"
    errors = pm.validate(raw)
    assert any("wiped by the OS" in e for e in errors)


def test_description_must_be_one_short_line():
    multi = _valid()
    multi["description"] = "line one\nline two"
    assert any("ONE line" in e for e in pm.validate(multi))

    long = _valid()
    long["description"] = "x" * (pm.DESCRIPTION_MAX + 1)
    assert any("chars" in e for e in pm.validate(long))


def test_unknown_nested_keys_and_bad_enums_are_rejected():
    raw = _valid()
    raw["kind"] = "rust"
    raw["repos"]["extra"] = 1
    raw["repos"]["components"][0]["role"] = "mystery"
    raw["worktrees"]["extra"] = 1
    errors = " ".join(pm.validate(raw))
    assert "'kind' must be one of" in errors
    assert "repos.extra" in errors
    assert "role" in errors
    assert "worktrees.extra" in errors


def test_id_must_name_a_real_project_when_ids_are_supplied():
    errors = pm.validate(_valid(), project_ids={"other"})
    assert any("not a known work project" in e for e in errors)
    assert pm.validate(_valid(), project_ids={"demo"}) == []


def test_wrong_schema_version_is_rejected():
    raw = _valid()
    raw["schema"] = 99
    assert any("schema must be 1" in e for e in pm.validate(raw))


# ── load / discover ─────────────────────────────────────────────────

def test_an_invalid_manifest_is_never_partially_trusted(tmp_path):
    d = tmp_path / "proj"
    (d / ".aos").mkdir(parents=True)
    raw = _valid()
    raw["state"] = "moving"
    (d / ".aos" / "project.yaml").write_text(yaml.safe_dump(raw))

    m, errors = pm.load_manifest(d)
    assert m is None, "a manifest with a forbidden field must not load at all"
    assert errors


def test_discover_finds_manifests_without_any_slug_matching(tmp_path):
    """The whole point: a directory named nothing like its project id is still
    found, because it declares its own identity."""
    root = tmp_path / "project"
    for dirname, pid in (("totally-unrelated-name", "demo"), ("other-dir", "second")):
        d = root / dirname / ".aos"
        d.mkdir(parents=True)
        raw = _valid()
        raw["id"] = pid
        (d / "project.yaml").write_text(yaml.safe_dump(raw))

    found = pm.discover(root)
    assert set(found) == {"demo", "second"}
    assert found["demo"].id == "demo"


def test_render_then_reload_round_trips(tmp_path):
    m = pm.parse(_valid())
    text = pm.render_manifest_yaml(m)
    d = tmp_path / "p"
    (d / ".aos").mkdir(parents=True)
    (d / ".aos" / "project.yaml").write_text(text)

    back, errors = pm.load_manifest(d)
    assert errors == []
    assert back is not None
    assert back.id == m.id and back.title == m.title
    assert [c.path for c in back.components] == [c.path for c in m.components]
    assert back.worktree_location == m.worktree_location


def test_rendered_manifest_contains_no_absolute_paths(tmp_path):
    m = pm.parse(_valid())
    text = pm.render_manifest_yaml(m)
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue                      # comments may mention /private/tmp
        assert "/Users/" not in line, line
        assert "/Volumes/" not in line, line


# ── adoption ────────────────────────────────────────────────────────

def test_description_string_is_unpacked_into_real_fields():
    """Live data crams declarations into prose:
    hre.description == "appetite:no-deadline, initiative:hre-platform"."""
    desc, appetite, initiative = pm._split_description(
        "appetite:no-deadline, initiative:hre-platform")
    assert appetite == "no-deadline"
    assert initiative == "hre-platform"
    assert desc is None

    desc, appetite, initiative = pm._split_description("short_id:dod")
    assert (desc, appetite, initiative) == (None, None, None)

    desc, _, _ = pm._split_description("This was created via the API test")
    assert desc == "This was created via the API test"


def test_kind_inference_looks_deeper_than_the_root(tmp_path):
    """aos has no root pyproject.toml but is unambiguously Python; a
    depth-1-only check called it 'content'."""
    d = tmp_path / "sys"
    (d / "core" / "engine").mkdir(parents=True)
    (d / "core" / "engine" / "x.py").write_text("x = 1")
    (d / "README.md").write_text("# sys")
    kind, why = pm._infer_kind(d)
    assert kind == "python", why


def test_a_sub_app_component_makes_the_project_mixed(tmp_path):
    d = tmp_path / "platform"
    d.mkdir()
    (d / "README.md").write_text("# curriculum")
    kind, why = pm._infer_kind(d, [pm.Component(path="app", role="sub-app")])
    assert kind == "mixed"
    assert "sub-app" in why


def _fake_engine(monkeypatch, projects):
    class _E:
        @staticmethod
        def load_all():
            return {"projects": projects}
    monkeypatch.setattr(pm, "engine", _E)


def test_adoption_recovers_declarations_and_nested_repos(tmp_path, monkeypatch):
    d = tmp_path / "hre"
    (d / "app").mkdir(parents=True)
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d / "app"), "init", "-q"], check=True,
                   capture_output=True)
    (d / ".gitignore").write_text("app/\n")
    (d / "README.md").write_text("# hre")
    monkeypatch.setattr(pm, "VAULT_ROOT", tmp_path / "vault")

    _fake_engine(monkeypatch, [{
        "id": "hre", "title": "HRE", "status": "active", "path": str(d),
        "description": "appetite:no-deadline, initiative:hre-platform",
        "done_when": "Grade 9 running",
    }])

    plan = pm.plan_adoption("hre")[0]
    assert plan.action == "create"
    raw = yaml.safe_load(plan.manifest_yaml)
    assert raw["appetite"] == "no-deadline"
    assert raw["initiative"] == "hre-platform"
    assert raw["done_when"] == "Grade 9 running"
    assert [c["path"] for c in raw["repos"]["components"]] == ["app"]
    assert raw["repos"]["components"][0]["role"] == "sub-app"
    assert pm.validate(raw) == []


def test_adoption_is_idempotent(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "x.py").write_text("x=1")
    monkeypatch.setattr(pm, "VAULT_ROOT", tmp_path / "vault")
    _fake_engine(monkeypatch, [{"id": "proj", "title": "P", "status": "active",
                                "path": str(d)}])

    first = pm.plan_adoption("proj")[0]
    assert first.action == "create"
    pm.write_manifest(first)                       # explicit write, as a human would

    second = pm.plan_adoption("proj")[0]
    assert second.action == "unchanged", "re-running must be a no-op"
    assert second.manifest_yaml == first.manifest_yaml


def test_directory_less_projects_are_an_explicit_case(monkeypatch):
    _fake_engine(monkeypatch, [
        {"id": "p1", "title": "Sub-scope", "status": "active"},
        {"id": "unified-comms", "title": "Comms", "status": "active"},
    ])
    plans = {p.project_id: p for p in pm.plan_adoption()}
    for pid in ("p1", "unified-comms"):
        assert plans[pid].action == "skip_no_directory"
        assert plans[pid].manifest_path is None
        assert any("correct, not a gap" in f for f in plans[pid].findings)


def test_adoption_warns_when_the_repo_has_a_public_remote(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "x.py").write_text("x=1")
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                    "https://github.com/me/public.git"], check=True, capture_output=True)
    monkeypatch.setattr(pm, "VAULT_ROOT", tmp_path / "vault")
    _fake_engine(monkeypatch, [{"id": "proj", "title": "P", "status": "active",
                                "path": str(d)}])

    plan = pm.plan_adoption("proj")[0]
    assert any("will be pushed there" in w for w in plan.warnings)


def test_cancelled_projects_are_not_adopted(monkeypatch):
    _fake_engine(monkeypatch, [
        {"id": "dead", "title": "D", "status": "cancelled", "path": "/nope"},
        {"id": "live", "title": "L", "status": "active"},
    ])
    assert {p.project_id for p in pm.plan_adoption()} == {"live"}


def test_planning_writes_nothing(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "x.py").write_text("x=1")
    monkeypatch.setattr(pm, "VAULT_ROOT", tmp_path / "vault")
    _fake_engine(monkeypatch, [{"id": "proj", "title": "P", "status": "active",
                                "path": str(d)}])

    plans = pm.plan_adoption()
    pm.render_plan(plans, show_yaml=True)
    assert not (d / ".aos").exists(), "planning must not create anything on disk"


# ── divergence, in the shape the project page already renders ───────

def test_divergence_is_reported_as_a_conflict_not_resolved():
    m = pm.parse(_valid())
    m.title = "Demo (manifest wording)"
    project = {"id": "demo", "title": "Demo (tracker wording)",
               "done_when": "It ships.", "goal": None}

    conflicts = pm.divergence_conflicts(m, project)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.kind == "manifest_divergence"
    assert c.severity == "warn"
    assert "manifest wording" in c.message and "tracker wording" in c.message
    assert any("project:demo" in r for r in c.refs)


def test_no_divergence_when_declarations_agree():
    m = pm.parse(_valid())
    project = {"id": "demo", "title": "Demo", "done_when": "It ships.", "goal": None}
    assert pm.divergence_conflicts(m, project) == []


def test_empty_and_missing_are_treated_as_equal_not_divergent():
    m = pm.parse(_valid())
    m.goal = None
    project = {"id": "demo", "title": "Demo", "done_when": "It ships.", "goal": ""}
    assert pm.divergence_conflicts(m, project) == []


def test_missing_manifest_conflict_exempts_directory_less_projects(tmp_path):
    assert pm.missing_manifest_conflict(
        {"id": "p1", "title": "Sub-scope"}) is None, \
        "a project with no directory has nowhere to put a manifest"

    d = tmp_path / "proj"
    d.mkdir()
    c = pm.missing_manifest_conflict({"id": "proj", "path": str(d)})
    assert c is not None and c.kind == "missing_manifest"

    (d / ".aos").mkdir()
    (d / ".aos" / "project.yaml").write_text("x")
    assert pm.missing_manifest_conflict({"id": "proj", "path": str(d)}) is None


def test_conflict_type_comes_from_brief_types():
    """Reuses the existing model rather than inventing a parallel one."""
    from brief_types import Conflict as BriefConflict
    m = pm.parse(_valid())
    c = pm.divergence_conflicts(m, {"id": "demo", "title": "different"})[0]
    assert isinstance(c, BriefConflict)


def test_emitting_a_new_conflict_kind_does_not_depend_on_the_vocabulary_tuple():
    """CONFLICT_KINDS in brief_types.py is owned by the compiler workstream and
    is documentation, not enforcement (``Conflict.kind`` is a plain ``str``). So
    these kinds must work whether or not that tuple has been updated yet — this
    test must not fail when its owner appends them."""
    import brief_types
    assert isinstance(brief_types.CONFLICT_KINDS, tuple)
    m = pm.parse(_valid())
    c = pm.divergence_conflicts(m, {"id": "demo", "title": "different"})[0]
    assert c.kind == "manifest_divergence"
    assert isinstance(c.kind, str)


# ── degradation ─────────────────────────────────────────────────────

def test_no_manifest_returns_cleanly(tmp_path):
    assert pm.load_manifest(tmp_path) == (None, [])


def test_unreadable_manifest_reports_rather_than_raises(tmp_path):
    d = tmp_path / "p"
    (d / ".aos").mkdir(parents=True)
    (d / ".aos" / "project.yaml").write_text("{{{ not yaml")
    m, errors = pm.load_manifest(d)
    assert m is None and errors


def test_discover_on_a_missing_root_is_empty(tmp_path):
    assert pm.discover(tmp_path / "nope") == {}


def test_framework_template_is_valid_and_uses_example_values_only():
    tpl = Path(__file__).resolve().parents[3] / "config" / "templates" / "project.yaml"
    if not tpl.exists():
        pytest.skip("framework template not present")
    raw = yaml.safe_load(tpl.read_text())
    assert pm.validate(raw) == [], "the shipped template must satisfy its own schema"
    assert raw["id"].startswith("example-")
    text = tpl.read_text()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "/Users/" not in line and "/Volumes/" not in line
