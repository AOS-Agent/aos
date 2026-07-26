"""Project-directory reconciler tests.

The reconciler's whole value is that no directory is unaccounted for and every
reason is explicit, so these tests are mostly about the *honesty* properties
rather than happy-path plumbing:

  * total accounting — every top-level directory gets exactly one disposition
  * worktrees are detected from git, never from a name suffix (a directory
    called `foo-wt` that is not a worktree must NOT be called one, and a
    worktree named after its branch must be)
  * nested repos are found, bounded, and skip vendor/build directories
  * a prose mention is not a dependency; hardcoded paths in code are
  * empty directories are settled before any reference rule can claim them
  * an operator declaration is never re-litigated — except when it contradicts
    live state, where the truth wins and the contradiction is reported
  * nothing is guessed: unknown stays `unclassified`
  * the framework template never supplies declarations
  * degrades on a fresh machine (no ~/project, no config, no yaml)

Isolated: builds real git repos in tmp_path and monkeypatches the module's
PROJECT_ROOT and project records. Never touches ~/project or the real work.db.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "engine" / "work"))

import project_reconcile as pr  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(cwd), *args), check=True,
                   capture_output=True, text=True)


def _repo(path: Path, *, remote: str | None = None, files: dict | None = None) -> Path:
    """A real git repo with one commit. Real git, because the reconciler's whole
    worktree contract is 'ask git, don't parse names'."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    for rel, body in (files or {"README": "x"}).items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A synthetic ~/project plus a set of project records to reconcile against."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(pr, "PROJECT_ROOT", root)
    monkeypatch.setattr(pr, "HOME", tmp_path)
    monkeypatch.setattr(pr, "CLAUDE_PROJECTS", tmp_path / ".claude" / "projects")
    pr._GIT_CACHE.clear()

    state: dict = {"projects": []}

    class _FakeEngine:
        @staticmethod
        def load_all():
            return {"projects": state["projects"]}

    monkeypatch.setattr(pr, "engine", _FakeEngine)
    # Default: no declarations at all.
    monkeypatch.setattr(pr, "load_declared", lambda: ({}, {}, "none"))
    return {"root": root, "state": state}


def _by_name(report) -> dict:
    return {e.name: e for e in report.entries}


# ── total accounting ────────────────────────────────────────────────────────

def test_every_directory_gets_exactly_one_disposition(env):
    root = env["root"]
    _repo(root / "app", remote="https://github.com/me/app.git")
    (root / "scratch").mkdir()
    (root / "scratch" / "note.txt").write_text("x")
    (root / "empty").mkdir()
    env["state"]["projects"] = [{"id": "app", "title": "App", "status": "active",
                                 "path": str(root / "app")}]

    r = pr.reconcile()
    tops = [e for e in r.entries if not e.nested]
    assert len(tops) == 3, "every top-level directory must appear exactly once"
    assert {e.name for e in tops} == {"app", "scratch", "empty"}
    for e in r.entries:
        assert e.disposition in pr.DISPOSITIONS
        assert e.evidence, f"{e.name} has a disposition with no stated reason"


def test_every_non_project_disposition_carries_a_reason(env):
    root = env["root"]
    (root / "husk").mkdir()
    r = pr.reconcile()
    e = _by_name(r)["husk"]
    assert e.disposition == "not_a_project"
    assert "empty" in e.evidence.lower()


# ── worktrees: git is the authority, not the name ───────────────────────────

def test_worktree_detected_from_git_not_from_name(env):
    """A worktree named after its branch is found; a `-wt` husk is not."""
    root = env["root"]
    main = _repo(root / "main-repo", remote="https://github.com/me/main.git")
    _git(main, "branch", "feature")
    _git(main, "worktree", "add", "-q", str(root / "totally-unrelated-name"), "feature")
    (root / "main-repo-wt").mkdir()          # looks like a worktree, is empty
    env["state"]["projects"] = [{"id": "proj", "title": "P", "status": "active",
                                 "path": str(main)}]

    r = pr.reconcile()
    got = _by_name(r)

    assert got["totally-unrelated-name"].disposition == "worktree_of"
    assert got["totally-unrelated-name"].target == "proj"
    assert "--git-common-dir" in got["totally-unrelated-name"].evidence

    # The naming convention must carry no weight whatsoever.
    assert got["main-repo-wt"].disposition == "not_a_project"
    assert got["main-repo-wt"].label != "worktree_of:proj"


def test_worktree_of_untracked_repo_says_target_is_not_a_project(env):
    root = env["root"]
    main = _repo(root / "loose", remote="https://github.com/me/loose.git")
    _git(main, "branch", "wip")
    _git(main, "worktree", "add", "-q", str(root / "loose-wip"), "wip")
    env["state"]["projects"] = []

    r = pr.reconcile()
    e = _by_name(r)["loose-wip"]
    assert e.disposition == "worktree_of"
    assert any("not a tracked project" in n for n in e.notes)


# ── nested repos ────────────────────────────────────────────────────────────

def test_nested_repo_inside_linked_project_is_a_component(env):
    """The live case that motivated this: a project whose real code sits in a
    nested repo the parent gitignores."""
    root = env["root"]
    parent = _repo(root / "platform", files={".gitignore": "app/\n"})
    _repo(parent / "app", remote="https://github.com/me/inner.git")
    env["state"]["projects"] = [{"id": "plat", "title": "Platform", "status": "active",
                                "path": str(parent)}]

    r = pr.reconcile()
    nested = [e for e in r.entries if e.nested]
    assert len(nested) == 1
    assert nested[0].disposition == "component_of"
    assert nested[0].target == "plat"
    assert "app/" in nested[0].evidence          # cites the gitignore proof


def test_nested_walk_skips_vendor_dirs_and_is_depth_bounded(env):
    root = env["root"]
    parent = _repo(root / "big")
    _repo(parent / "node_modules" / "dep")                     # must be skipped
    _repo(parent / ".hidden" / "dep")                          # must be skipped
    _repo(parent / "a" / "b" / "c" / "d" / "deep")             # beyond depth
    _repo(parent / "shared" / "data")                          # must be found
    env["state"]["projects"] = [{"id": "big", "title": "Big", "status": "active",
                                "path": str(parent)}]

    r = pr.reconcile()
    names = {e.name for e in r.entries if e.nested}
    assert any(n.endswith("shared/data") for n in names)
    assert not any("node_modules" in n for n in names)
    assert not any(".hidden" in n for n in names)
    assert not any("deep" in n for n in names)


# ── component detection: code counts, prose does not ────────────────────────

def test_submodule_url_binds_a_sibling_directory_as_component(env):
    root = env["root"]
    url = "https://github.com/me/data.git"
    consumer = _repo(root / "consumer", files={
        ".gitmodules": f'[submodule "shared/data"]\n\tpath = shared/data\n\turl = {url}\n'})
    _repo(root / "data", remote=url)
    env["state"]["projects"] = [{"id": "cons", "title": "C", "status": "active",
                                "path": str(consumer)}]

    r = pr.reconcile()
    e = _by_name(r)["data"]
    assert e.disposition == "component_of"
    assert e.target == "cons"
    assert "gitmodules" in e.evidence


def test_hardcoded_path_in_code_binds_component_but_prose_does_not(env):
    """A design doc naming a path is a citation; code hardcoding it is a
    dependency. Conflating the two mis-bound two real directories."""
    root = env["root"]
    used = root / "sources"
    used.mkdir()
    (used / "f.txt").write_text("data")
    mentioned = root / "just-discussed"
    mentioned.mkdir()
    (mentioned / "f.txt").write_text("data")

    app = _repo(root / "app", files={
        "src/db.mjs": f"const SRC = '{used}';\n",
        "docs/HANDOFF.md": f"We should look at {mentioned} some day.\n",
    })
    env["state"]["projects"] = [{"id": "app", "title": "App", "status": "active",
                                "path": str(app)}]

    r = pr.reconcile()
    got = _by_name(r)
    assert got["sources"].disposition == "component_of"
    assert got["sources"].target == "app"
    assert "src/db.mjs" in got["sources"].evidence

    assert got["just-discussed"].disposition == "unclassified", \
        "a prose mention must not create a component relationship"


def test_empty_directory_wins_over_a_stale_reference(env):
    """An empty directory cannot be a component no matter what a doc says."""
    root = env["root"]
    (root / "husk").mkdir()
    app = _repo(root / "app", files={"cfg.py": f"OLD = '{root / 'husk'}'\n"})
    env["state"]["projects"] = [{"id": "app", "title": "App", "status": "active",
                                "path": str(app)}]

    r = pr.reconcile()
    assert _by_name(r)["husk"].disposition == "not_a_project"


# ── nothing is guessed ──────────────────────────────────────────────────────

def test_unknown_directory_stays_unclassified_with_triage_evidence(env):
    root = env["root"]
    _repo(root / "mystery", remote="https://github.com/someone-else/x.git")
    (root / "mystery" / "CLAUDE.md").write_text("# m")
    env["state"]["projects"] = []

    r = pr.reconcile()
    e = _by_name(r)["mystery"]
    assert e.disposition == "unclassified"
    assert e.facts["is_git"] is True
    assert "CLAUDE.md" in e.evidence
    assert r.unclassified and r.unclassified[0].name == "mystery"


def test_third_party_clone_is_never_proposed_as_a_project(env):
    """Owner namespace is derived from the linked projects' remotes, not a
    hardcoded username."""
    root = env["root"]
    mine = _repo(root / "mine", remote="https://github.com/me/mine.git")
    _repo(root / "theirs", remote="https://github.com/vendor/theirs.git")
    (root / "theirs" / "CLAUDE.md").write_text("# t")
    _repo(root / "also-mine", remote="https://github.com/me/other.git")
    (root / "also-mine" / "CLAUDE.md").write_text("# o")
    env["state"]["projects"] = [{"id": "mine", "title": "M", "status": "active",
                                 "path": str(mine)}]

    r = pr.reconcile()
    proposed = {p.name for p in r.proposals}
    assert "also-mine" in proposed
    assert "theirs" not in proposed
    assert _by_name(r)["theirs"].facts["third_party_remote"] is True


def test_directory_with_no_version_control_is_not_proposed_alone(env):
    root = env["root"]
    d = root / "data-dump"
    d.mkdir()
    (d / "x.csv").write_text("a,b")
    env["state"]["projects"] = []

    r = pr.reconcile()
    assert _by_name(r)["data-dump"].disposition == "unclassified"
    assert "data-dump" not in {p.name for p in r.proposals}


# ── declarations ────────────────────────────────────────────────────────────

def test_declared_not_a_project_is_never_re_litigated(env, monkeypatch):
    root = env["root"]
    _repo(root / "vendor-clone", remote="https://github.com/v/c.git")
    (root / "vendor-clone" / "CLAUDE.md").write_text("# v")
    monkeypatch.setattr(pr, "load_declared", lambda: (
        {"vendor-clone": {"disposition": "not_a_project", "reason": "upstream clone"}},
        {}, "instance"))

    r = pr.reconcile()
    e = _by_name(r)["vendor-clone"]
    assert e.disposition == "not_a_project"
    assert e.source == "declared"
    assert e.evidence == "upstream clone"
    assert "vendor-clone" not in {p.name for p in r.proposals}


def test_declaration_contradicting_live_state_loses_but_is_reported(env, monkeypatch):
    """A stale declaration must never make the report state something false."""
    root = env["root"]
    app = _repo(root / "app")
    env["state"]["projects"] = [{"id": "app", "title": "App", "status": "active",
                                "path": str(app)}]
    monkeypatch.setattr(pr, "load_declared", lambda: (
        {"app": {"disposition": "not_a_project", "reason": "stale"}}, {}, "instance"))

    r = pr.reconcile()
    e = _by_name(r)["app"]
    assert e.disposition == "linked", "live work record is current state and must win"
    assert any("contradict" in n.lower() for n in e.notes)
    assert r.conflicts and "app" in r.conflicts[0]


def test_invalid_declared_disposition_is_rejected_not_trusted(env, monkeypatch):
    root = env["root"]
    _repo(root / "x", remote="https://github.com/me/x.git")
    monkeypatch.setattr(pr, "load_declared", lambda: (
        {"x": {"disposition": "nonsense", "reason": "?"}}, {}, "instance"))

    r = pr.reconcile()
    assert _by_name(r)["x"].disposition == "unclassified"
    assert any("not one of" in c for c in r.conflicts)


def test_declaration_for_a_vanished_directory_is_reported_as_drift(env, monkeypatch):
    monkeypatch.setattr(pr, "load_declared", lambda: (
        {"deleted-long-ago": {"disposition": "not_a_project", "reason": "gone"}},
        {}, "instance"))
    r = pr.reconcile()
    assert r.declared_stale == ["deleted-long-ago"]


# ── the reverse gap ─────────────────────────────────────────────────────────

def test_project_without_directory_is_reported_and_unexplained_gaps_are_flagged(env, monkeypatch):
    env["state"]["projects"] = [
        {"id": "sub", "title": "Sub-scope", "status": "active"},
        {"id": "nocode", "title": "Not started", "status": "active"},
    ]
    monkeypatch.setattr(pr, "load_declared", lambda: (
        {}, {"sub": {"no_directory_reason": "a scope inside the monorepo"}}, "instance"))

    r = pr.reconcile()
    gaps = {g.project_id: g for g in r.gaps}
    assert gaps["sub"].explained is True
    assert "monorepo" in gaps["sub"].reason
    assert gaps["nocode"].explained is False, "an unexplained gap must be visible"


def test_cancelled_projects_are_not_reported_as_gaps(env):
    env["state"]["projects"] = [
        {"id": "dead", "title": "Dupe", "status": "cancelled"},
        {"id": "live", "title": "Live", "status": "active"},
    ]
    r = pr.reconcile()
    assert {g.project_id for g in r.gaps} == {"live"}


def test_symlinked_and_real_paths_resolve_to_the_same_directory(env):
    """Live project records spell the same directory two ways (~/project vs the
    volume realpath); string comparison would report a false gap."""
    root = env["root"]
    real = _repo(root / "app")
    link = root.parent / "link-to-project"
    link.symlink_to(root)
    env["state"]["projects"] = [{"id": "app", "title": "App", "status": "active",
                                "path": str(link / "app")}]

    r = pr.reconcile()
    assert _by_name(r)["app"].disposition == "linked"
    assert r.gaps == []
    assert str(real) == _by_name(r)["app"].path


# ── the instance-file proposal, and the single write path ───────────────────

def test_proposed_instance_yaml_round_trips_and_leaves_unknowns_commented(env):
    yaml = pytest.importorskip("yaml")
    root = env["root"]
    app = _repo(root / "app")
    _repo(root / "mystery", remote="https://github.com/x/y.git")
    env["state"]["projects"] = [
        {"id": "app", "title": "App", "status": "active", "path": str(app)},
        {"id": "ghost", "title": "Ghost", "status": "active"},
    ]

    r = pr.reconcile()
    text = pr.propose_instance_yaml(r)
    parsed = yaml.safe_load(text)

    assert parsed["version"] == 1
    assert parsed["directories"]["app"]["disposition"] == "linked"
    for name, spec in parsed["directories"].items():
        assert spec["disposition"] in pr.DISPOSITIONS
        assert spec.get("reason"), f"{name} declared with no reason"
    assert "mystery" not in parsed["directories"], \
        "an unknown must be left for the operator, not auto-declared"
    assert "# mystery:" in text, "the unknown should still be scaffolded, commented"
    assert "ghost" in parsed["projects"]


def test_reconcile_writes_nothing(env, tmp_path):
    """The reconciler is report-only; apply_dispositions is the sole write path
    and must never be reachable from reconcile()."""
    root = env["root"]
    app = _repo(root / "app")
    env["state"]["projects"] = [{"id": "app", "title": "App", "status": "active",
                                "path": str(app)}]
    cfg = tmp_path / ".aos" / "config" / "project-dispositions.yaml"

    r = pr.reconcile()
    pr.render_report(r)
    pr.report_to_dict(r)
    assert not cfg.exists(), "reconcile/render must not create instance config"

    written = pr.apply_dispositions(r, path=cfg)
    assert written.exists(), "the explicit write path does work when called"


# ── degradation ─────────────────────────────────────────────────────────────

def test_framework_template_supplies_no_declarations(monkeypatch, tmp_path):
    """The shipped examples must never become active dispositions."""
    monkeypatch.setattr(pr, "HOME", tmp_path)
    monkeypatch.setenv("AOS_CONFIG_DIR", str(tmp_path / "empty-cfg"))
    dirs, projects, layer = pr.load_declared()
    assert dirs == {} and projects == {}
    assert "framework template only" in layer

    fw = pr._framework_config_path()
    if fw.exists():                     # shipped template must still be valid
        yaml = pytest.importorskip("yaml")
        parsed = yaml.safe_load(fw.read_text()) or {}
        for name, spec in (parsed.get("directories") or {}).items():
            assert name.startswith("example-"), \
                f"framework template must not name a real directory: {name}"
            assert spec["disposition"] in pr.DISPOSITIONS
            assert spec.get("reason")


def test_missing_project_root_does_not_crash(env):
    env["root"].rmdir()
    env["state"]["projects"] = [{"id": "a", "title": "A", "status": "active"}]
    r = pr.reconcile()
    assert r.entries == []
    assert len(r.gaps) == 1


def test_missing_yaml_module_degrades(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "yaml", None)
    monkeypatch.setattr(pr, "HOME", tmp_path)
    dirs, projects, layer = pr.load_declared()
    assert (dirs, projects) == ({}, {})


# ── manifests as the primary discovery mechanism ────────────────────

def test_manifest_links_a_directory_whose_name_matches_nothing(env, monkeypatch):
    """The point of the manifest: identity is declared, so slug matching is not
    needed at all. `project.path` is deliberately left unset here."""
    yaml = pytest.importorskip("yaml")
    root = env["root"]
    d = _repo(root / "totally-unrelated-name")
    (d / ".aos").mkdir()
    (d / ".aos" / "project.yaml").write_text(yaml.safe_dump({
        "schema": 1, "id": "demo", "kind": "python", "title": "Demo",
        "repos": {"root": ".", "components": [{"path": "app", "role": "sub-app"}]},
    }))
    env["state"]["projects"] = [{"id": "demo", "title": "Demo", "status": "active"}]

    r = pr.reconcile()
    e = _by_name(r)["totally-unrelated-name"]
    assert e.disposition == "linked"
    assert e.target == "demo"
    assert e.source == "manifest"
    assert e.facts["manifest"] is True
    assert "declares id 'demo'" in e.evidence
    assert r.gaps == [], "a manifest-linked project is not a directory-less gap"


def test_linked_without_a_manifest_says_so(env):
    root = env["root"]
    app = _repo(root / "app")
    env["state"]["projects"] = [{"id": "app", "title": "App", "status": "active",
                                 "path": str(app)}]
    r = pr.reconcile()
    e = _by_name(r)["app"]
    assert e.source == "work-record"
    assert e.facts["manifest"] is False
    assert any("no .aos/project.yaml" in n for n in e.notes)


def test_manifest_naming_an_unknown_project_is_a_conflict(env):
    yaml = pytest.importorskip("yaml")
    root = env["root"]
    d = _repo(root / "orphan")
    (d / ".aos").mkdir()
    (d / ".aos" / "project.yaml").write_text(yaml.safe_dump({
        "schema": 1, "id": "ghost", "kind": "python", "title": "Ghost",
    }))
    env["state"]["projects"] = []

    r = pr.reconcile()
    assert any("not a live work project" in c for c in r.conflicts)


def test_invalid_manifest_is_reported_and_the_dir_falls_back(env):
    yaml = pytest.importorskip("yaml")
    root = env["root"]
    d = _repo(root / "bad", remote="https://github.com/me/bad.git")
    (d / ".aos").mkdir()
    (d / ".aos" / "project.yaml").write_text(yaml.safe_dump({
        "schema": 1, "id": "bad", "kind": "python", "title": "Bad",
        "state": "moving",              # forbidden
    }))
    env["state"]["projects"] = [{"id": "bad", "title": "Bad", "status": "active"}]

    r = pr.reconcile()
    assert any("must not appear in a manifest" in c for c in r.conflicts)
    assert _by_name(r)["bad"].disposition == "unclassified", \
        "an invalid manifest must not silently link the directory"


def test_missing_manifest_is_a_typed_conflict_only_for_linked_projects(env):
    root = env["root"]
    app = _repo(root / "app")
    env["state"]["projects"] = [
        {"id": "app", "title": "App", "status": "active", "path": str(app)},
        {"id": "nodir", "title": "No dir", "status": "active"},
    ]
    r = pr.reconcile()
    kinds = {(c.kind, tuple(c.refs)) for c in r.brief_conflicts}
    assert any(k == "missing_manifest" for k, _ in kinds)
    assert not any("nodir" in " ".join(refs) for _k, refs in kinds), \
        "a directory-less project has nowhere to put a manifest"


def test_manifest_divergence_surfaces_as_a_brief_conflict(env):
    yaml = pytest.importorskip("yaml")
    root = env["root"]
    d = _repo(root / "app")
    (d / ".aos").mkdir()
    (d / ".aos" / "project.yaml").write_text(yaml.safe_dump({
        "schema": 1, "id": "app", "kind": "python",
        "title": "Title as declared in the manifest",
    }))
    env["state"]["projects"] = [{"id": "app", "title": "Title as held by the tracker",
                                 "status": "active", "path": str(d)}]

    r = pr.reconcile()
    div = [c for c in r.brief_conflicts if c.kind == "manifest_divergence"]
    assert len(div) == 1
    assert "manifest" in div[0].message and "tracker" in div[0].message
