"""
Tests for the project layer — zones, markers, the lifecycle verbs, zone-aware
reconciliation, the steward check, and migration 119.

Everything here runs against a tmp_path standing in for ``~/project/``. Nothing
touches the operator's real projects directory, real work.db, real
``~/.claude/rules`` or real ``~/.local/bin``: the modules take a ``root``
argument where they can, and where they read a module global (the reconciler,
the check, the migration) the fixtures monkeypatch it.

The bias in what is covered: the things that would be expensive to get wrong.
Archive eligibility, because a wrong answer moves a directory that something
still points at. The ``.aos/no-git`` deny marker, because it guards 30GB
directories against a well-meaning ``git init``. Drift detection, because a
health check that miscounts is worse than no health check. Cosmetics are left
to the reader.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

# conftest.py already puts core/engine/work on sys.path; be explicit too.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core" / "engine" / "work"))

import project_lifecycle as lifecycle  # noqa: E402
import project_manifest as pm  # noqa: E402
import project_reconcile as reconcile  # noqa: E402
import project_zones as zones  # noqa: E402

# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    """Give git an identity so `create` and `archive` can actually commit.

    Without this the suite passes or fails depending on whether the machine
    running it has a global git config — which is exactly the kind of hidden
    dependency that makes a test suite lie in CI.
    """
    for var, val in (("GIT_AUTHOR_NAME", "AOS Test"),
                     ("GIT_AUTHOR_EMAIL", "test@example.invalid"),
                     ("GIT_COMMITTER_NAME", "AOS Test"),
                     ("GIT_COMMITTER_EMAIL", "test@example.invalid")):
        monkeypatch.setenv(var, val)


@pytest.fixture(autouse=True)
def no_work_db(monkeypatch, tmp_path):
    """Detach the lifecycle AND the reconciler from live instance state.

    Most of these tests are about the filesystem, and a verb that quietly
    consulted the operator's real tracker would make them both slow and
    non-deterministic. Tests that DO want the tracker opt back in with the
    `work_env` fixture, which rebinds the same module to an isolated DB.

    The reconciler is detached too because `survey()` now takes its drift from
    it, so `project list` reaches work.db and the instance dispositions file by
    a path it did not use before. Left live, a declaration in the operator's own
    `~/.aos/config/project-dispositions.yaml` could silence a finding a test is
    asserting.
    """
    monkeypatch.setattr(lifecycle, "engine", None)
    monkeypatch.setattr(reconcile, "engine", None)
    monkeypatch.setenv("AOS_CONFIG_DIR", str(tmp_path / "no-instance-config"))


@pytest.fixture()
def root(tmp_path):
    """A stand-in for ~/project/ that already has its zones."""
    base = tmp_path / "project"
    zones.ensure_zones(base)
    return base


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", "-C", str(cwd), *args),
                          capture_output=True, text=True)


def _repo(directory: Path, *, commit: bool = True) -> Path:
    """A real git repo on disk. Real, not mocked — git's own answers are the
    thing under test (worktree registration, dirty trees, tags)."""
    directory.mkdir(parents=True, exist_ok=True)
    _git(directory, "init", "-b", "main")
    if commit:
        (directory / "file.txt").write_text("hello")
        _git(directory, "add", "-A")
        _git(directory, "commit", "-m", "initial")
    return directory


# ── names ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["aos", "quran-garden", "hre2", "a1-b2-c3"])
def test_valid_names_accepted(name):
    assert zones.validate_name(name) is None


@pytest.mark.parametrize("name,fragment", [
    ("_ref", "reserved for zones"),
    ("_anything", "reserved for zones"),
    ("aos-wt", "-wt"),
    ("Quran_Garden", "kebab-case"),
    ("has spaces", "kebab-case"),
    ("trailing-", "kebab-case"),
    ("", "needs a name"),
])
def test_invalid_names_rejected_with_a_reason(name, fragment):
    err = zones.validate_name(name)
    assert err is not None, f"{name!r} should have been rejected"
    assert fragment in err


# ── zones ───────────────────────────────────────────────────────────

def test_ensure_zones_creates_structure_and_is_idempotent(tmp_path):
    base = tmp_path / "project"
    first = zones.ensure_zones(base)
    assert first, "first call should have created something"
    for zone in zones.ZONES:
        assert (base / zone).is_dir()
        assert (base / zone / "README.md").exists()
    assert (base / "CLAUDE.md").exists()

    assert zones.ensure_zones(base) == [], "second call should be a no-op"


def test_ensure_policy_never_overwrites_operator_edits(root):
    policy = root / "CLAUDE.md"
    policy.write_text("# my own conventions")
    assert zones.ensure_policy(root) is None
    assert policy.read_text() == "# my own conventions"


def test_zone_of_reads_location_only(root):
    assert zones.zone_of(root / "_ref" / "someclone", root) == "_ref"
    assert zones.zone_of(root / "_archive" / "old", root) == "_archive"
    assert zones.zone_of(root / "_scratch" / "tmp", root) == "_scratch"
    assert zones.zone_of(root / "live-project", root) is None
    assert zones.zone_of(Path("/somewhere/else"), root) is None


# ── markers ─────────────────────────────────────────────────────────

def test_no_git_marker_round_trips(tmp_path):
    d = tmp_path / "heavy"
    d.mkdir()
    assert not zones.has_no_git_marker(d)
    zones.write_no_git_marker(d, "26GB OneDrive mirror")
    assert zones.has_no_git_marker(d)
    body = yaml.safe_load((d / ".aos" / "no-git").read_text())
    assert body["reason"] == "26GB OneDrive mirror"


def test_archived_marker_round_trips(tmp_path):
    d = tmp_path / "done"
    d.mkdir()
    assert zones.read_archived_marker(d) is None
    zones.write_archived_marker(d, "shipped", when="2026-08-14")
    m = zones.read_archived_marker(d)
    assert m.date == "2026-08-14"
    assert m.reason == "shipped"
    assert m.entangled is False


def test_entangled_marker_records_that_it_did_not_move(tmp_path):
    d = tmp_path / "stuck"
    d.mkdir()
    zones.write_archived_marker(d, "done", entangled=True)
    assert zones.read_archived_marker(d).entangled is True


def test_reason_containing_a_colon_does_not_break_the_marker(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    zones.write_archived_marker(d, "superseded by: the new thing")
    assert zones.read_archived_marker(d).reason == "superseded by: the new thing"


def test_malformed_marker_still_counts_as_archived(tmp_path):
    """A formatting slip must not silently return a project to live work."""
    d = tmp_path / "x"
    (d / ".aos").mkdir(parents=True)
    (d / ".aos" / "archived").write_text("{{{ not yaml at all")
    m = zones.read_archived_marker(d)
    assert m is not None


# ── manifest: depends_on ────────────────────────────────────────────

def _manifest_dict(**over) -> dict:
    base = {"schema": 1, "id": "hre", "kind": "mixed", "title": "HRE"}
    base.update(over)
    return base


def test_depends_on_accepts_a_list_of_ids():
    assert pm.validate(_manifest_dict(
        depends_on=["quran-garden-data", "tafsir"])) == []


def test_depends_on_is_optional_and_schema_stays_1():
    """Additive: every manifest already on disk must keep validating."""
    raw = _manifest_dict()
    assert "depends_on" not in raw
    assert pm.validate(raw) == []
    assert raw["schema"] == pm.SCHEMA_VERSION == 1


@pytest.mark.parametrize("value,fragment", [
    (["../quran-garden-data"], "looks like a path"),
    (["~/project/data"], "looks like a path"),
    (["/Volumes/AOS-X/project/data"], "looks like a path"),
    (["sub/dir"], "looks like a path"),
    (["Not_Kebab"], "kebab-case"),
    (["hre"], "this project itself"),
    ("quran-garden-data", "must be a list"),
    ([42], "must be a project id string"),
])
def test_depends_on_rejections(value, fragment):
    errors = pm.validate(_manifest_dict(depends_on=value))
    assert errors, f"{value!r} should have been rejected"
    assert any(fragment in e for e in errors), errors


def test_depends_on_survives_render_and_reparse():
    """The renderer and the validator are one module's two halves; a manifest
    it writes must be one it can read back."""
    m = pm.Manifest(id="hre", title="HRE", depends_on=["quran-garden-data"])
    raw = yaml.safe_load(pm.render_manifest_yaml(m))
    assert pm.validate(raw) == []
    assert pm.parse(raw).depends_on == ["quran-garden-data"]


def test_shipped_template_validates():
    """config/templates/project.yaml is the documentation of the schema. If it
    stops validating, the example is teaching people something untrue."""
    raw = yaml.safe_load(
        (REPO_ROOT / "config" / "templates" / "project.yaml").read_text())
    assert pm.validate(raw) == []


# ── create ──────────────────────────────────────────────────────────

def test_create_produces_a_self_identifying_project(root):
    out = lifecycle.create("demo", title="Demo", description="A demo.",
                           root=root)
    assert out.ok
    d = root / "demo"
    assert (d / ".git").is_dir()
    assert (d / "README.md").exists()

    manifest, errors = pm.load_manifest(d)
    assert errors == []
    assert manifest.id == "demo"
    assert manifest.title == "Demo"

    log = _git(d, "log", "--oneline").stdout
    assert "init: demo" in log
    assert not out.warnings, out.warnings


def test_create_refuses_an_existing_directory(root):
    (root / "taken").mkdir()
    out = lifecycle.create("taken", root=root)
    assert not out.ok
    assert "already exists" in out.errors[0]
    assert "project adopt" in out.errors[0]


def test_create_refuses_a_reserved_name_before_touching_disk(root):
    out = lifecycle.create("_ref", root=root)
    assert not out.ok
    assert not (root / "_ref" / ".git").exists()


def test_create_no_git_writes_the_deny_marker_instead_of_a_repo(root):
    out = lifecycle.create("bulk", root=root, git=False)
    assert out.ok
    d = root / "bulk"
    assert not (d / ".git").exists()
    assert zones.has_no_git_marker(d)


def test_create_does_not_register_in_work_db_unless_asked(root, work_env):
    eng = work_env["engine"]
    lifecycle.create("unregistered", root=root)
    assert not [p for p in eng.get_all_projects() if p["id"] == "unregistered"]


def test_create_with_work_flag_registers_and_sets_path(root, work_env,
                                                       monkeypatch):
    eng = work_env["engine"]
    monkeypatch.setattr(lifecycle, "engine", eng)
    out = lifecycle.create("registered", title="Registered", root=root,
                           register_work=True)
    assert out.ok
    rows = [p for p in eng.get_all_projects() if p["id"] == "registered"]
    assert rows, "project should have been created"
    assert rows[0]["path"] == str(root / "registered")


# ── adopt ───────────────────────────────────────────────────────────

def test_adopt_writes_a_manifest_for_a_stray_directory(root):
    stray = root / "stray"
    stray.mkdir()
    (stray / "main.py").write_text("print(1)\n")

    out = lifecycle.adopt("stray", root=root)
    assert out.ok
    manifest, errors = pm.load_manifest(stray)
    assert errors == []
    assert manifest.id == "stray"
    assert manifest.kind == "python"


def test_adopt_flags_an_unversioned_directory_with_no_marker(root):
    (root / "silent").mkdir()
    out = lifecycle.adopt("silent", root=root)
    assert any("no .aos/no-git marker" in w for w in out.warnings)


def test_adopt_no_git_records_the_decision(root):
    (root / "bulk").mkdir()
    out = lifecycle.adopt("bulk", root=root, git=False)
    assert out.ok
    assert zones.has_no_git_marker(root / "bulk")
    assert not (root / "bulk" / ".git").exists()


def test_no_git_marker_outranks_the_git_flag(root):
    """The guard that stops an agent running `git init` inside a 30GB mirror.

    A deny a flag can override is not a deny, so this asserts the refusal is
    absolute rather than merely the default.
    """
    d = root / "mirror"
    d.mkdir()
    zones.write_no_git_marker(d, "30GB OneDrive mirror")

    out = lifecycle.adopt("mirror", root=root, git=True)
    assert not (d / ".git").exists(), "git init must not have run"
    assert any("REFUSED to git init" in w for w in out.warnings)


def test_adopt_refuses_reference_clones(root):
    clone = root / "_ref" / "someones-repo"
    clone.mkdir(parents=True)
    out = lifecycle.adopt(clone, root=root)
    assert not out.ok
    assert not pm.manifest_path_for(clone).exists()


def test_adopt_is_idempotent(root):
    (root / "twice").mkdir()
    lifecycle.adopt("twice", root=root)
    out = lifecycle.adopt("twice", root=root)
    assert any("already up to date" in s for s in out.steps)


def test_adopt_creates_the_zones_when_they_are_missing(tmp_path):
    """`adopt` is the first verb an operator with a pre-existing ~/project/
    reaches for, and it used to be the one verb that did not install the layer —
    `create` and `archive` both call ensure_zones and it did not, so a machine
    whose migration deferred stayed without zones or a policy file."""
    base = tmp_path / "project"
    (base / "already-here").mkdir(parents=True)

    out = lifecycle.adopt("already-here", root=base)
    assert out.ok
    for zone in zones.ZONES:
        assert (base / zone).is_dir()
    assert (base / zones.POLICY_FILENAME).exists()


def test_adopt_refuses_a_zone_directory(root):
    out = lifecycle.adopt("_scratch", root=root)
    assert not out.ok
    assert "is a zone" in out.errors[0]
    assert not pm.manifest_path_for(root / "_scratch").exists()


def test_adopting_inside_scratch_says_the_manifest_will_not_surface(root):
    """A manifest written under _scratch/ is real and permanently invisible:
    zone membership is the classification, so neither `project list` nor the
    reconciler will ever show it. The README used to advise exactly this."""
    d = root / "_scratch" / "promising"
    d.mkdir(parents=True)

    out = lifecycle.adopt(d, root=root)
    assert out.ok
    assert any("does not promote it" in w for w in out.warnings)
    assert any("mv " in w for w in out.warnings)

    # ...and the claim the warning makes is true.
    assert d.name not in {r.name for r in lifecycle.survey(root=root).rows}


# ── adopt: never regenerate over a human's declarations ─────────────

def _hand_edit(directory: Path) -> None:
    """Put declarations into a manifest that only a person could have written."""
    mp = pm.manifest_path_for(directory)
    raw = mp.read_text()
    raw = raw.replace("depends_on: []", "depends_on:\n  - quran-garden-data")
    raw = raw.replace("description: null", "description: The hand-written truth")
    raw = raw.replace("done_when: null", "done_when: when the curriculum ships")
    raw = raw.replace("goal: null", "goal: g-teaching")
    mp.write_text(raw)


def test_readopting_keeps_hand_written_declarations(root):
    """The destructive case: adoption infers, and inference has no source for a
    `depends_on` or a `done_when`, so regenerating them means blanking them. For
    a manifest not yet committed there is no way back."""
    d = root / "hre"
    d.mkdir()
    (d / "main.py").write_text("print(1)\n")
    assert lifecycle.adopt("hre", root=root).ok
    _hand_edit(d)

    out = lifecycle.adopt("hre", root=root)
    assert out.ok

    m, errors = pm.load_manifest(d)
    assert errors == []
    assert m.depends_on == ["quran-garden-data"]
    assert m.description == "The hand-written truth"
    assert m.done_when == "when the curriculum ships"
    assert m.goal == "g-teaching"


def test_readopting_still_fills_the_gaps_it_can(root):
    """Preserving is not freezing: a field the existing manifest leaves empty is
    still filled from what is on disk."""
    d = root / "gaps"
    d.mkdir()
    assert lifecycle.adopt("gaps", root=root).ok
    _hand_edit(d)
    (d / "app.py").write_text("x = 1\n")     # now unmistakably python

    lifecycle.adopt("gaps", root=root)
    m, _ = pm.load_manifest(d)
    assert m.depends_on == ["quran-garden-data"], "declaration still there"


def test_force_regenerates_and_names_what_it_discards(root):
    d = root / "forced"
    d.mkdir()
    (d / "main.py").write_text("print(1)\n")
    lifecycle.adopt("forced", root=root)
    _hand_edit(d)

    out = lifecycle.adopt("forced", root=root, force=True)
    assert out.ok
    m, _ = pm.load_manifest(d)
    assert m.depends_on == [], "--force is the destructive path, on purpose"
    assert any("DISCARDS" in w and "depends_on" in w for w in out.warnings), \
        "the operator must be told exactly what --force threw away"


def test_adopt_refuses_to_overwrite_an_invalid_manifest(root):
    """An invalid manifest cannot be merged — `load_manifest` returns None for
    it — so regenerating would preserve nothing at all. And the usual cause is a
    typo one edit away from a good manifest."""
    d = root / "broken"
    d.mkdir()
    lifecycle.adopt("broken", root=root)
    mp = pm.manifest_path_for(d)
    mp.write_text(mp.read_text()
                  + "\nstatus: active\nsecret_notes: do not publish\n")
    before = mp.read_text()

    out = lifecycle.adopt("broken", root=root)
    assert not out.ok
    assert mp.read_text() == before, "nothing may be written over it"
    assert any("does not validate" in e for e in out.errors)
    assert any("--force" in e for e in out.errors)

    assert lifecycle.adopt("broken", root=root, force=True).ok
    assert pm.load_manifest(d)[1] == [], "--force is the way through"


# ── archive: eligibility ────────────────────────────────────────────

def test_eligibility_is_clean_for_a_plain_repo(root):
    d = _repo(root / "finished")
    assert lifecycle.eligibility(d).clean


def test_eligibility_reports_registered_worktrees_by_path(root):
    """The blocker that cannot be repaired from here: a worktree's .git file
    stores the main checkout's absolute path."""
    d = _repo(root / "busy")
    wt = d / ".claude" / "worktrees" / "feat-x"
    _git(d, "worktree", "add", "-b", "feat/x", str(wt))

    ent = lifecycle.eligibility(d)
    assert not ent.clean
    assert any("feat-x" in w for w in ent.worktrees)


def test_eligibility_reports_open_tasks(root, work_env, monkeypatch):
    eng = work_env["engine"]
    monkeypatch.setattr(lifecycle, "engine", eng)
    eng.add_project("Busy", project_id="busy")
    eng.add_task("Still to do", project="busy")

    ent = lifecycle.eligibility(_repo(root / "busy"), "busy")
    assert not ent.clean
    assert any("Still to do" in t for t in ent.open_tasks)


def test_done_tasks_do_not_block_an_archive(root, work_env, monkeypatch):
    eng = work_env["engine"]
    monkeypatch.setattr(lifecycle, "engine", eng)
    eng.add_project("Shipped", project_id="shipped")
    task = eng.add_task("Ship it", project="shipped")
    eng.complete_task(task["id"])

    assert lifecycle.eligibility(_repo(root / "shipped"), "shipped").clean


# ── archive: the two paths ──────────────────────────────────────────

def test_archive_clean_moves_commits_tags_and_marks(root):
    d = _repo(root / "finished")
    (d / "unsaved.txt").write_text("work in progress")

    out = lifecycle.archive("finished", reason="shipped", root=root,
                            run_qmd=False)
    assert out.ok
    assert out.action == "archive"

    moved = root / "_archive" / "finished"
    assert moved.is_dir()
    assert not d.exists(), "the directory must not be left behind"

    marker = zones.read_archived_marker(moved)
    assert marker is not None and marker.reason == "shipped"
    assert marker.entangled is False

    assert lifecycle.ARCHIVE_COMMIT_MESSAGE in _git(moved, "log", "--oneline").stdout
    assert _git(moved, "tag").stdout.strip().startswith("archived/")
    # The marker rides in the final commit rather than dangling as the one
    # uncommitted change in an archived repo.
    assert not lifecycle.is_dirty(moved)


def test_archive_entangled_does_not_move_and_says_why(root):
    d = _repo(root / "tangled")
    wt = d / ".claude" / "worktrees" / "feat-y"
    _git(d, "worktree", "add", "-b", "feat/y", str(wt))

    out = lifecycle.archive("tangled", root=root, run_qmd=False)
    assert out.action == "archive-in-place"
    assert d.exists(), "an entangled project must stay where it is"
    assert not (root / "_archive" / "tangled").exists()

    marker = zones.read_archived_marker(d)
    assert marker is not None and marker.entangled is True

    warning = "\n".join(out.warnings)
    assert "NOT MOVED" in warning
    assert "feat-y" in warning, "the warning must name the blocking path"
    assert "git worktree remove" in warning, "and say how to clear it"


def test_archive_of_a_non_repo_still_moves(root):
    d = root / "notes"
    d.mkdir()
    (d / "a.md").write_text("stuff")
    out = lifecycle.archive("notes", root=root, run_qmd=False)
    assert out.ok and out.action == "archive"
    assert (root / "_archive" / "notes" / "a.md").exists()


def test_archive_refuses_a_name_collision_rather_than_clobbering(root):
    _repo(root / "dup")
    (root / "_archive" / "dup").mkdir(parents=True)
    (root / "_archive" / "dup" / "older.txt").write_text("previous archive")

    out = lifecycle.archive("dup", root=root, run_qmd=False)
    assert out.action == "archive-in-place"
    assert (root / "dup").exists()
    assert (root / "_archive" / "dup" / "older.txt").read_text() == "previous archive"


def test_archive_updates_work_db_path_and_status(root, work_env, monkeypatch):
    eng = work_env["engine"]
    monkeypatch.setattr(lifecycle, "engine", eng)
    d = _repo(root / "shipped")
    eng.add_project("Shipped", project_id="shipped")
    eng.update_project("shipped", path=str(d))

    out = lifecycle.archive("shipped", root=root, run_qmd=False)
    assert out.ok and out.action == "archive"

    row = [p for p in eng.get_all_projects() if p["id"] == "shipped"][0]
    assert row["path"] == str(root / "_archive" / "shipped")
    assert row["status"] == "archived"


def test_archive_will_not_rewrite_an_unrelated_projects_path(root, work_env,
                                                             monkeypatch):
    """A manifest id is a claim, not a licence to relocate somebody else's row.

    If a directory's manifest says `id: hre` while the live `hre` project points
    somewhere else entirely, writing the archive path into that row would
    relocate a project that was never archived.
    """
    eng = work_env["engine"]
    monkeypatch.setattr(lifecycle, "engine", eng)
    elsewhere = root / "the-real-hre"
    elsewhere.mkdir()
    eng.add_project("HRE", project_id="hre")
    eng.update_project("hre", path=str(elsewhere))

    impostor = _repo(root / "impostor")
    pm.write_manifest(pm.plan_adoption_for_directory(impostor, project_id="hre"))

    out = lifecycle.archive("impostor", root=root, run_qmd=False)
    assert out.ok

    row = [p for p in eng.get_all_projects() if p["id"] == "hre"][0]
    assert row["path"] == str(elsewhere), "the real project must not have moved"
    assert row["status"] != "archived"


def test_archive_refuses_something_already_archived(root):
    d = root / "_archive" / "old"
    d.mkdir(parents=True)
    out = lifecycle.archive(d, root=root, run_qmd=False)
    assert not out.ok
    assert "already in" in out.errors[0]


# ── archive: what must never be archived ────────────────────────────

@pytest.mark.parametrize("zone", zones.ZONES)
def test_archive_refuses_a_zone_by_name(root, zone):
    """A zone is the filing cabinet, not a thing that can be filed. Without the
    guard `project archive _scratch` passes every other check — the directory
    exists, it is not inside _archive/, nothing is registered against it — and
    moves the entire zone, contents and all, into _archive/_scratch."""
    inhabitant = root / zone / "contents"
    inhabitant.mkdir(parents=True)

    out = lifecycle.archive(zone, root=root, run_qmd=False)
    assert not out.ok
    assert (root / zone).is_dir(), "the zone must still be where it was"
    assert inhabitant.is_dir()
    assert not (root / "_archive" / zone).exists()
    assert not zones.marker_path(root / zone, zones.ARCHIVED_MARKER).exists()


def test_archive_refuses_a_zone_by_absolute_path(root):
    out = lifecycle.archive(root / "_ref", root=root, run_qmd=False)
    assert not out.ok
    assert "is a zone" in out.errors[0]
    assert (root / "_ref").is_dir()


def test_archive_refuses_a_name_the_layer_cannot_address(root):
    """Archive moves the directory into _archive/ under the same name, so the
    name has to be one every other verb can name back."""
    (root / "Legacy_Thing").mkdir()
    out = lifecycle.archive("Legacy_Thing", root=root, run_qmd=False)
    assert not out.ok
    assert "kebab-case" in out.errors[0]
    assert "Rename it first" in out.errors[0]
    assert (root / "Legacy_Thing").is_dir()


# ── archive: the worktree guard must fail CLOSED ────────────────────

@pytest.fixture()
def worktree_query_broken(monkeypatch):
    """Make `git worktree list` unanswerable, the way a timeout would."""
    import project_worktrees as pw

    def boom(repo):
        raise pw.WorktreeQueryFailed("timed out after 30s")

    monkeypatch.setattr(pw, "list_worktrees_checked", boom)


def test_eligibility_records_that_worktrees_could_not_be_enumerated(
        root, worktree_query_broken):
    d = _repo(root / "opaque")
    ent = lifecycle.eligibility(d)
    assert ent.worktrees == []
    assert ent.worktrees_unknown
    assert not ent.clean, "'could not tell' must never read as 'nothing found'"


def test_archive_refuses_when_worktrees_cannot_be_enumerated(
        root, worktree_query_broken):
    """The guard failed OPEN: enumeration collapsed to [], which reads exactly
    like 'no worktrees registered', and the move went ahead — orphaning every
    checkout whose .git file stores this absolute path."""
    d = _repo(root / "opaque")

    out = lifecycle.archive("opaque", root=root, run_qmd=False)
    assert not out.ok
    assert "could not be asked" in out.errors[0]
    assert d.is_dir(), "nothing may have moved"
    assert not (root / "_archive" / "opaque").exists()
    assert not zones.marker_path(d, zones.ARCHIVED_MARKER).exists(), \
        "not even the marker: the project is not known to be safe to call done"


def test_a_healthy_repo_still_archives(root):
    """The companion to the test above: fail-closed must not mean fail-always."""
    _repo(root / "healthy")
    out = lifecycle.archive("healthy", root=root, run_qmd=False)
    assert out.ok and out.action == "archive"
    assert (root / "_archive" / "healthy").is_dir()


def test_reindex_does_not_block_the_verb(root, monkeypatch, tmp_path):
    """`qmd update` used to run synchronously with a 300s timeout, so a verb
    that had already finished its durable work held the terminal waiting on a
    search index it does not own."""
    # A stand-in qmd that takes its time. Nothing is mocked: the point is what
    # the real spawn does, and a mocked Popen would only prove the call shape.
    fake_qmd = tmp_path / "qmd"
    fake_qmd.write_text("#!/bin/sh\nsleep 20\n")
    fake_qmd.chmod(0o755)
    monkeypatch.setattr(lifecycle, "QMD_BIN", fake_qmd)

    _repo(root / "indexed")
    started = time.monotonic()
    out = lifecycle.archive("indexed", root=root, run_qmd=True)
    elapsed = time.monotonic() - started

    assert out.ok
    assert elapsed < 5, (f"archive waited {elapsed:.1f}s on the reindex; the "
                         f"move is already durable by then")
    assert any("background" in s for s in out.steps)


# ── survey ──────────────────────────────────────────────────────────

def test_survey_reports_zone_counts_and_drift(root):
    lifecycle.create("clean-one", root=root)
    (root / "no-manifest").mkdir()
    # Not empty: an empty directory has nothing to identify, and is deliberately
    # exempt from the unmanifested finding (see the husk tests below).
    (root / "no-manifest" / "notes.md").write_text("stuff")
    (root / "_ref" / "a-clone").mkdir(parents=True)
    (root / "_scratch" / "junk").mkdir(parents=True)

    s = lifecycle.survey(root=root)
    names = {r.name for r in s.rows}
    assert names == {"clean-one", "no-manifest"}, "zones are not projects"
    assert s.zone_counts["_ref"] == 1
    assert s.zone_counts["_scratch"] == 1
    assert any("no-manifest" in d for d in s.drift)


def test_survey_on_a_machine_with_no_project_dir(tmp_path):
    s = lifecycle.survey(root=tmp_path / "nothing-here")
    assert s.root_exists is False
    assert s.rows == []


# ── reconcile: zones and drift ──────────────────────────────────────

@pytest.fixture()
def reconcile_root(tmp_path, monkeypatch):
    """Point the reconciler at a throwaway ~/project/ with no work.db and no
    instance dispositions file — so every finding comes from the tree itself."""
    base = tmp_path / "project"
    zones.ensure_zones(base)
    monkeypatch.setattr(reconcile, "PROJECT_ROOT", base)
    monkeypatch.setattr(reconcile, "engine", None)
    monkeypatch.setenv("AOS_CONFIG_DIR", str(tmp_path / "no-config"))
    reconcile._GIT_CACHE.clear()
    yield base
    reconcile._GIT_CACHE.clear()


def _drift_kinds(report, name: str) -> set:
    return {d.kind for d in report.drift if d.name == name}


def test_zone_directories_are_not_themselves_classified(reconcile_root):
    r = reconcile.reconcile(drift_only=True)
    assert {e.name for e in r.entries} & set(zones.ZONES) == set()


def test_reference_clones_are_expected_not_drift(reconcile_root):
    _repo(reconcile_root / "_ref" / "someones-lib")
    r = reconcile.reconcile(drift_only=True)

    entry = next(e for e in r.entries if e.name == "_ref/someones-lib")
    assert entry.disposition == "not_a_project"
    assert entry.zone == "_ref"
    assert _drift_kinds(r, "_ref/someones-lib") == set()


def test_scratch_is_exempt(reconcile_root):
    (reconcile_root / "_scratch" / "junk").mkdir(parents=True)
    r = reconcile.reconcile(drift_only=True)
    assert _drift_kinds(r, "_scratch/junk") == set()


def test_a_real_archive_is_quiet(reconcile_root):
    """Produced by the actual verb, not a hand-built approximation — the point
    is that what `project archive` leaves behind reports nothing."""
    _repo(reconcile_root / "old")
    assert lifecycle.archive("old", root=reconcile_root, run_qmd=False).ok
    reconcile._GIT_CACHE.clear()

    r = reconcile.reconcile(drift_only=True)
    entry = next(e for e in r.entries if e.name == "_archive/old")
    assert entry.disposition == "archived"
    assert _drift_kinds(r, "_archive/old") == set()


def test_archived_but_active_is_detected(reconcile_root):
    """The skeptic lens's non-negotiable: a claim checked against the tree."""
    _repo(reconcile_root / "zombie")
    lifecycle.archive("zombie", root=reconcile_root, run_qmd=False)
    reconcile._GIT_CACHE.clear()
    (reconcile_root / "_archive" / "zombie" / "still-working.py").write_text("x")

    r = reconcile.reconcile(drift_only=True)
    assert "archived_but_active" in _drift_kinds(r, "_archive/zombie")


def test_a_commit_after_the_archive_point_is_detected(reconcile_root):
    """The other half: work that was committed, so the tree looks clean."""
    d = _repo(reconcile_root / "resumed")
    lifecycle.archive("resumed", root=reconcile_root, run_qmd=False)
    moved = reconcile_root / "_archive" / "resumed"
    zones.write_archived_marker(moved, "done", when="2020-01-01")
    (moved / "more.py").write_text("x = 1")
    _git(moved, "add", "-A")
    _git(moved, "commit", "-m", "carried on regardless")
    reconcile._GIT_CACHE.clear()
    assert not lifecycle.is_dirty(moved), "the tree must look clean"

    r = reconcile.reconcile(drift_only=True)
    kinds = _drift_kinds(r, "_archive/resumed")
    assert "archived_but_active" in kinds
    assert d is not None


def test_mtime_is_only_consulted_where_there_is_no_git(reconcile_root):
    """A repo answers precisely, so its mtimes are ignored — Time Machine and
    `chmod -R` bump those without anybody doing work, and a check that cries
    wolf on a backup run is a check the operator learns to skip.

    A directory with no git has no better signal, so there mtime does count.
    """
    repo = _repo(reconcile_root / "quiet-repo")
    zones.write_archived_marker(repo, "done", when="2020-01-01")
    _git(repo, "add", "-A")
    # Backdated so the commit precedes the archive point. That leaves mtime as
    # the only thing that could still fire — which is exactly what this asserts
    # does not happen for a repo.
    subprocess.run(("git", "-C", str(repo), "commit", "-m", "archive: final state"),
                   capture_output=True, text=True,
                   env={**os.environ,
                        "GIT_AUTHOR_DATE": "2019-01-01T00:00:00Z",
                        "GIT_COMMITTER_DATE": "2019-01-01T00:00:00Z"})
    os.utime(repo / "file.txt", None)          # a backup touching a file

    plain = reconcile_root / "quiet-notes"
    plain.mkdir()
    (plain / "a.md").write_text("stuff")       # mtime = now
    zones.write_archived_marker(plain, "done", when="2020-01-01")

    reconcile._GIT_CACHE.clear()
    r = reconcile.reconcile(drift_only=True)
    # Both carry a marker at the top level, so both are archived_not_moved. The
    # question this test asks is the other one: did mtime alone make either of
    # them look active?
    assert "archived_but_active" not in _drift_kinds(r, "quiet-repo")
    assert "archived_but_active" in _drift_kinds(r, "quiet-notes")


def test_archived_in_place_but_active_is_detected(reconcile_root):
    """Flip-in-place has no location to corroborate it, so the marker alone
    must still be checked."""
    d = _repo(reconcile_root / "flipped")
    zones.write_archived_marker(d, "done", entangled=True, when="2020-01-01")
    (d / "new-work.py").write_text("x = 1")

    r = reconcile.reconcile(drift_only=True)
    assert "archived_but_active" in _drift_kinds(r, "flipped")


def test_unmanifested_and_unversioned_are_reported(reconcile_root):
    (reconcile_root / "orphan").mkdir()
    (reconcile_root / "orphan" / "notes.md").write_text("stuff")

    r = reconcile.reconcile(drift_only=True)
    assert _drift_kinds(r, "orphan") == {"unmanifested", "no_git_unmarked"}


def test_the_no_git_marker_silences_the_version_control_finding(reconcile_root):
    d = reconcile_root / "bulk"
    d.mkdir()
    (d / "big.bin").write_text("x")
    zones.write_no_git_marker(d, "too large for git")

    r = reconcile.reconcile(drift_only=True)
    kinds = _drift_kinds(r, "bulk")
    assert "no_git_unmarked" not in kinds
    assert "unmanifested" in kinds, "the marker answers one question, not both"


def test_a_dirty_tree_is_reported(reconcile_root):
    d = _repo(reconcile_root / "wip")
    (d / "uncommitted.txt").write_text("only on this disk")

    r = reconcile.reconcile(drift_only=True)
    assert "dirty_tree" in _drift_kinds(r, "wip")


def test_an_operator_declaration_silences_housekeeping(reconcile_root, tmp_path,
                                                       monkeypatch):
    """A decision already made is never re-litigated — the whole reason the
    dispositions file exists."""
    (reconcile_root / "deliberate").mkdir()
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "project-dispositions.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "directories": {"deliberate": {"disposition": "not_a_project",
                                       "reason": "a scratch dir, on purpose"}},
    }))
    monkeypatch.setenv("AOS_CONFIG_DIR", str(cfg))

    r = reconcile.reconcile(drift_only=True)
    assert _drift_kinds(r, "deliberate") == set()


@pytest.fixture()
def pacific_time(monkeypatch):
    """Run the body west of UTC, where the timezone bug actually bites."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_a_same_day_archive_west_of_utc_is_not_reported_active(reconcile_root,
                                                               pacific_time):
    """`date.today()` writes a LOCAL date; `git log %cI` carries a real offset.
    Reading the bare marker date as UTC midnight shifts the reference back by
    the machine's offset, so on UTC-7 every archive made after 17:00 local
    produced a final commit whose UTC timestamp landed past the reference — and
    the project was reported archived_but_active from the moment it was
    archived, permanently.
    """
    d = _repo(reconcile_root / "same-day")
    zones.write_archived_marker(d, "done", when="2026-08-15")
    (d / "final.txt").write_text("last thing")
    _git(d, "add", "-A")
    # 20:00 local on the marker's own date — 03:00Z the following day.
    subprocess.run(("git", "-C", str(d), "commit", "-m", "archive: final state"),
                   capture_output=True, text=True,
                   env={**os.environ,
                        "GIT_AUTHOR_DATE": "2026-08-15T20:00:00-07:00",
                        "GIT_COMMITTER_DATE": "2026-08-15T20:00:00-07:00"})
    reconcile._GIT_CACHE.clear()

    r = reconcile.reconcile(drift_only=True)
    assert "archived_but_active" not in _drift_kinds(r, "same-day")


def test_a_commit_a_week_after_the_marker_is_still_caught(reconcile_root,
                                                          pacific_time):
    """The companion: the timezone fix widens the reference by hours, not days,
    so real post-archive work is still a finding."""
    d = _repo(reconcile_root / "carried-on")
    zones.write_archived_marker(d, "done", when="2026-08-15")
    (d / "more.txt").write_text("a week later")
    _git(d, "add", "-A")
    subprocess.run(("git", "-C", str(d), "commit", "-m", "more work"),
                   capture_output=True, text=True,
                   env={**os.environ,
                        "GIT_AUTHOR_DATE": "2026-08-22T09:00:00-07:00",
                        "GIT_COMMITTER_DATE": "2026-08-22T09:00:00-07:00"})
    reconcile._GIT_CACHE.clear()

    r = reconcile.reconcile(drift_only=True)
    assert "archived_but_active" in _drift_kinds(r, "carried-on")


# ── an invalid manifest is not a missing one ────────────────────────

def _manifest_file(directory: Path, body: str) -> Path:
    p = pm.manifest_path_for(directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_an_invalid_manifest_is_its_own_finding(reconcile_root):
    """Reporting it as `unmanifested` sends the operator to `project adopt` —
    which would then regenerate over the file whose typo is the whole problem."""
    d = reconcile_root / "typo"
    d.mkdir()
    _manifest_file(d, "schema: 1\nid: typo\nkind: mixed\ntitle: Typo\n"
                      "status: active\n")

    r = reconcile.reconcile(drift_only=True)
    kinds = _drift_kinds(r, "typo")
    assert "manifest_invalid" in kinds
    assert "unmanifested" not in kinds, "it is not missing; it is broken"

    row = next(d_ for d_ in r.drift if d_.name == "typo"
               and d_.kind == "manifest_invalid")
    assert "status" in row.evidence, "name the validator's actual complaint"
    assert "adopt" not in row.evidence, "adoption is the wrong advice here"


def test_survey_flags_an_invalid_manifest_separately(root):
    d = root / "typo"
    d.mkdir()
    _manifest_file(d, "schema: 1\nid: typo\nkind: mixed\ntitle: Typo\n"
                      "progress: 40\n")

    row = next(r for r in lifecycle.survey(root=root).rows if r.name == "typo")
    assert row.has_manifest is False
    assert row.manifest_invalid, "the errors travel with the row"


# ── third-party is a heuristic, and a manifest outranks it ──────────

def test_a_manifested_repo_in_another_namespace_is_not_third_party(
        reconcile_root, work_env, monkeypatch):
    """Owner namespaces are derived only from the remotes of work.db projects
    that have a path, so an operator's own repo published under a second GitHub
    org looks exactly like somebody else's clone. Adopting it is the answer to
    that, and it must not still be third-party afterwards."""
    eng = work_env["engine"]
    monkeypatch.setattr(reconcile, "engine", eng)

    known = _repo(reconcile_root / "known")
    _git(known, "remote", "add", "origin",
         "https://github.com/operator/known.git")
    eng.add_project("Known", project_id="known")
    eng.update_project("known", path=str(known))

    other = _repo(reconcile_root / "other-org")
    _git(other, "remote", "add", "origin",
         "https://github.com/operators-second-org/other-org.git")
    reconcile._GIT_CACHE.clear()

    r = reconcile.reconcile(drift_only=True)
    assert "third_party_at_top_level" in _drift_kinds(r, "other-org"), \
        "unadopted, the heuristic is all there is and it should still fire"

    _manifest_file(other, "schema: 1\nid: other-org\nkind: mixed\n"
                          "title: Other Org\n")
    reconcile._GIT_CACHE.clear()
    r = reconcile.reconcile(drift_only=True)
    assert "third_party_at_top_level" not in _drift_kinds(r, "other-org"), \
        "a manifest is the operator saying 'this one is mine'"


# ── declarations, husks, and the layer itself ───────────────────────

def test_a_zone_name_declaration_is_not_reported_stale(reconcile_root, tmp_path,
                                                       monkeypatch):
    """`top_level_dirs()` filters ZONE_NAMES, so a pre-existing declaration
    keyed by a bare zone name looks like it names a directory that has gone.
    It has not gone; the layer now answers for it."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "project-dispositions.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "directories": {
            "_scratch": {"disposition": "not_a_project",
                         "reason": "predates the zones"},
            "genuinely-gone": {"disposition": "not_a_project",
                               "reason": "deleted last year"},
        },
    }))
    monkeypatch.setenv("AOS_CONFIG_DIR", str(cfg))

    r = reconcile.reconcile(drift_only=True)
    assert "_scratch" not in r.declared_stale
    assert "genuinely-gone" in r.declared_stale, "real staleness still reported"


def test_an_empty_husk_is_not_told_to_adopt_itself(reconcile_root):
    """The empty guard `no_git_unmarked` already had. A husk left by the retired
    worktree convention has nothing to identify, and telling the operator to
    adopt it on every run forever is the noise that makes the report skippable."""
    (reconcile_root / "aos-wt").mkdir()

    r = reconcile.reconcile(drift_only=True)
    assert _drift_kinds(r, "aos-wt") == set()

    entry = next(e for e in r.entries if e.name == "aos-wt")
    assert entry.disposition == "not_a_project", "still accounted for, though"


def test_missing_zones_and_policy_are_reported(tmp_path, monkeypatch):
    """Migration 119 defers zone creation when ~/project cannot be written into.
    Every verb lazily creates what it needs, so a half-installed layer is
    otherwise completely silent."""
    bare = tmp_path / "project"
    bare.mkdir()
    monkeypatch.setattr(reconcile, "PROJECT_ROOT", bare)
    reconcile._GIT_CACHE.clear()

    r = reconcile.reconcile(drift_only=True)
    missing = {d.name for d in r.drift if d.kind == "layer_not_installed"}
    assert missing == set(zones.ZONES) | {zones.POLICY_FILENAME}

    zones.ensure_zones(bare)
    r = reconcile.reconcile(drift_only=True)
    assert [d for d in r.drift if d.kind == "layer_not_installed"] == []


def test_an_archived_marker_that_never_moved_is_reported(reconcile_root):
    """The flip-in-place fallback. `project archive` shouts about it once, and
    then the warning scrolls away while the condition stays."""
    d = _repo(reconcile_root / "flipped")
    zones.write_archived_marker(d, "done", entangled=True, when="2020-01-01")
    reconcile._GIT_CACHE.clear()

    r = reconcile.reconcile(drift_only=True)
    assert "archived_not_moved" in _drift_kinds(r, "flipped")


def test_survey_and_the_reconciler_report_the_same_drift(root):
    """One implementation, two readers. `project list` and the steward check
    disagreeing about the same directory in front of the operator is worse than
    either being wrong alone."""
    _repo(root / "wip")
    (root / "wip" / "uncommitted.txt").write_text("x")
    (root / "orphan").mkdir()
    (root / "orphan" / "notes.md").write_text("stuff")
    reconcile._GIT_CACHE.clear()

    s = lifecycle.survey(root=root)
    reconcile._GIT_CACHE.clear()
    r = reconcile.reconcile(drift_only=True, root=root)

    assert len(s.drift) == len(r.drift)
    for row in r.drift:
        assert any(row.name in line and row.kind in line for line in s.drift)


def test_drift_only_and_the_full_pass_agree_on_drift(reconcile_root):
    """drift_only skips expensive passes, not findings. If the two ever
    disagree, the cheap mode the steward check runs is lying."""
    _repo(reconcile_root / "wip")
    (reconcile_root / "wip" / "dirty.txt").write_text("x")
    (reconcile_root / "orphan").mkdir()

    fast = reconcile.reconcile(drift_only=True)
    reconcile._GIT_CACHE.clear()
    full = reconcile.reconcile()

    def fingerprint(r):
        return sorted((d.kind, d.name) for d in r.drift)

    assert fingerprint(fast) == fingerprint(full)


# ── the steward check ───────────────────────────────────────────────

@pytest.fixture()
def check_cls(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "core" / "infra" / "reconcile"))
    sys.path.insert(0, str(REPO_ROOT / "core" / "infra" / "reconcile" / "checks"))
    from project_layer import ProjectLayerCheck
    return ProjectLayerCheck


def test_check_skips_on_a_machine_with_no_project_dir(check_cls, tmp_path,
                                                      monkeypatch):
    """A fresh install has no ~/project/ until the first `project new`, and this
    check must SKIP there rather than report OK.

    "No directories, therefore no misfiled directory, therefore healthy" is true
    and still wrong to report: it is indistinguishable from a check that looked
    at nothing, which is the failure tests/test_reconcile_blindness.py exists to
    ratchet against.
    """
    c = check_cls()
    monkeypatch.setattr(c, "PROJECT_ROOT", tmp_path / "absent")
    assert c.precondition() is False


def test_check_is_ok_on_an_empty_project_dir(check_cls, reconcile_root,
                                             monkeypatch):
    c = check_cls()
    monkeypatch.setattr(c, "PROJECT_ROOT", reconcile_root)
    assert c.check() is True


def test_check_notifies_with_counts_and_never_corrects(check_cls,
                                                       reconcile_root,
                                                       monkeypatch):
    (reconcile_root / "orphan").mkdir()
    (reconcile_root / "orphan" / "a.md").write_text("x")

    c = check_cls()
    monkeypatch.setattr(c, "PROJECT_ROOT", reconcile_root)
    assert c.check() is False

    result = c.fix()
    assert result.status.value == "notify"
    assert "unmanifested=1" in result.message
    assert "orphan" in result.detail
    # Reports, never corrects: the tree is exactly as it was.
    assert not pm.manifest_path_for(reconcile_root / "orphan").exists()
    assert not (reconcile_root / "orphan" / ".git").exists()


def test_check_runs_the_reconciler_once_per_run(check_cls, reconcile_root,
                                                monkeypatch):
    """check() then fix() must cost one pass, not two — this is the slowest
    check in the suite."""
    (reconcile_root / "orphan").mkdir()
    calls = []
    real = reconcile.reconcile

    def counting(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(reconcile, "reconcile", counting)
    c = check_cls()
    monkeypatch.setattr(c, "PROJECT_ROOT", reconcile_root)
    c.check()
    c.fix()
    assert len(calls) == 1
    assert calls[0] == {"drift_only": True}


def test_check_is_registered(check_cls):
    """ship-check enforces this too; asserting it here fails faster.

    Compared by name rather than identity: the check module is importable both
    flat (as the runner's fallback loader does it) and as `checks.project_layer`,
    which produces two distinct class objects for the same file.
    """
    from checks import ALL_CHECKS
    assert check_cls.name in {c.name for c in ALL_CHECKS}


# ── migration 119 ───────────────────────────────────────────────────

@pytest.fixture()
def migration(tmp_path, monkeypatch):
    """Load migration 119 with Path.home() sandboxed to tmp_path/home.

    The migration's paths are all functions now (`_home()`, `_project_root()`,
    `_rule_link()`, …) that re-resolve `Path.home()` on every call — there are
    no more module-level path constants to overwrite after import. So this
    fixture sandboxes `Path.home()` itself, persistently, for the whole test
    (monkeypatch reverts it at teardown), the same shape as
    `tests/test_migration_119_idempotency.py`'s `home` fixture. `~/aos` is
    symlinked to REPO_ROOT (the framework tree under test) so `_rule_source()`
    and `_cli_source()` resolve to real files.
    """
    import importlib.util
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "aos").symlink_to(REPO_ROOT)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    path = REPO_ROOT / "core" / "infra" / "migrations" / "119_project_layer.py"
    spec = importlib.util.spec_from_file_location("m119", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_is_idempotent(migration):
    migration._project_root().mkdir(parents=True)
    assert migration.check() is False
    assert migration.up() is True
    assert migration.check() is True
    assert migration.up() is True, "re-running must be safe"
    assert migration.check() is True


def test_migration_installs_zones_rule_and_cli(migration):
    migration._project_root().mkdir(parents=True)
    migration.up()

    for zone in zones.ZONES:
        assert (migration._project_root() / zone).is_dir()
    assert migration._policy().exists()
    assert migration._rule_link().is_symlink()
    assert migration._rule_link().resolve() == migration._rule_source().resolve()
    assert migration._cli_link().is_symlink()
    assert os.access(migration._cli_source(), os.X_OK)


def test_migration_skips_zones_when_there_is_no_project_dir(migration):
    """A machine that has never had a projects directory does not get one
    created for it; the first `project new` does that."""
    assert not migration._project_root().exists()
    assert migration.up() is True
    assert not migration._project_root().exists()


def test_migration_touches_no_existing_directory(migration):
    """The council's lock: migrating contents is an operator-supervised triage
    session, never automated. Here that means a 30GB unversioned directory
    comes out the other side unversioned, unmoved and unmarked."""
    migration._project_root().mkdir(parents=True)
    heavy = migration._project_root() / "elora-greens"
    heavy.mkdir()
    (heavy / "mirror.dat").write_text("30GB, pretend")
    repo = _repo(migration._project_root() / "real-project")
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    migration.up()

    assert heavy.is_dir(), "must not have been moved"
    assert not (heavy / ".git").exists(), "must not have been git-initialised"
    assert not pm.manifest_path_for(heavy).exists(), "must not have been adopted"
    assert (heavy / "mirror.dat").exists()
    assert repo.is_dir()
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before


def test_migration_preserves_an_existing_policy_file(migration):
    migration._project_root().mkdir(parents=True)
    migration._policy().write_text("# the operator's own version")
    migration.up()
    assert migration._policy().read_text() == "# the operator's own version"


def test_migration_backs_up_a_handwritten_rule(migration):
    rule_link = migration._rule_link()
    rule_link.parent.mkdir(parents=True)
    rule_link.write_text("# my own rule, written by hand")
    migration.up()

    backup = rule_link.with_name(rule_link.name + ".pre-reconcile")
    assert backup.read_text() == "# my own rule, written by hand"
    assert rule_link.is_symlink()


def test_migration_never_deletes_an_earlier_backup(migration):
    """Nothing in this repo auto-deletes, and the old version broke that rule on
    exactly the file the backup exists to protect: it unlinked any existing
    `.pre-reconcile` to make room for a new one."""
    rule_link = migration._rule_link()
    rule_link.parent.mkdir(parents=True)
    first = rule_link.with_name(rule_link.name + ".pre-reconcile")
    first.write_text("# the version I actually care about")
    rule_link.write_text("# a later hand-edit")

    migration.up()

    assert first.read_text() == "# the version I actually care about"
    second = rule_link.with_name(rule_link.name + ".pre-reconcile.2")
    assert second.read_text() == "# a later hand-edit"
    assert rule_link.is_symlink()


def test_migration_stays_pending_when_project_root_is_unreachable(migration):
    """`~/project` is a symlink onto an external volume. Unmounted, it dangles —
    and `exists()` follows symlinks, so it reports exactly what a machine with
    no projects directory reports. Answering "complete" there is unrecoverable:
    the runner records the watermark and never offers the migration again.
    """
    project_root = migration._project_root()
    project_root.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(migration._home() / "not-mounted", project_root)
    assert project_root.is_symlink()
    assert not project_root.exists(), "the link dangles"

    assert migration.check() is False, "nothing was installed there"
    # runner.py:112-133 — anything other than True/None leaves the watermark
    # where it is, so the migration is retried on the next update cycle.
    assert migration.up() is False

    # The parts that live on the internal disk still went in: the operator gets
    # the CLI and the rule now, and the zones when the volume comes back.
    assert migration._rule_link().is_symlink()
    assert migration._cli_link().is_symlink()


def test_migration_completes_once_the_volume_is_back(migration):
    """The retry has to actually converge, or 'stays pending' is just broken."""
    project_root = migration._project_root()
    project_root.parent.mkdir(parents=True, exist_ok=True)
    target = migration._home() / "volume" / "project"
    os.symlink(target, project_root)
    assert migration.up() is False

    target.mkdir(parents=True)                  # volume mounted
    assert migration.up() is True
    assert migration.check() is True
    for zone in zones.ZONES:
        assert (project_root / zone).is_dir()
    assert migration._policy().exists()


def test_migration_imports_under_the_system_python(migration):
    """The runner loads migrations with whatever interpreter is running `aos
    update`, which on this machine is /usr/bin/python3 — 3.9.6. A `str | None`
    annotation is evaluated at def time without `from __future__ import
    annotations`, so the module raised TypeError on import and the migration
    could never run at all.
    """
    system_python = Path("/usr/bin/python3")
    if not system_python.exists():              # pragma: no cover — Linux CI
        pytest.skip("no /usr/bin/python3 on this machine")

    path = REPO_ROOT / "core" / "infra" / "migrations" / "119_project_layer.py"
    proc = subprocess.run(
        (str(system_python), "-c",
         "import importlib.util, sys;"
         f"spec = importlib.util.spec_from_file_location('m119', {str(path)!r});"
         "mod = importlib.util.module_from_spec(spec);"
         "spec.loader.exec_module(mod);"
         "print(mod.DESCRIPTION)"),
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "Project layer" in proc.stdout
