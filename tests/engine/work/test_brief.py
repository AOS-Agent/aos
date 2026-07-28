"""
Tests for the Project Brief compiler (core/engine/work/brief.py).

Two kinds of test live here:

* **Pure-function tests** drive the derivation and detection helpers with
  plain dicts. They are the fast, exact ones — a duplicate-detection threshold
  can be pinned to a specific pair of real titles without touching a database.
* **End-to-end tests** compile a brief against the isolated work DB from
  ``tests/conftest.py`` (``work_env``), with the vault and project root
  redirected into ``tmp_path`` so nothing reads the operator's real files.

The titles in the duplicate-spine tests are the live ones from the ``hre``
project, which genuinely has two competing plans for the same work. Nothing in
the detector is special-cased for them; they are here because a real bug is a
better fixture than an invented one.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "engine" / "work"))

import brief as briefmod  # noqa: E402
from brief_types import Actor, Conflict, ProjectBrief  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture()
def brief_env(work_env, tmp_path, monkeypatch):
    """work_env plus a sandboxed vault, project root, and brief store."""
    vault = tmp_path / "vault"
    (vault / "knowledge" / "initiatives").mkdir(parents=True)
    (vault / "knowledge" / "decisions").mkdir(parents=True)
    (vault / "log" / "sessions").mkdir(parents=True)
    projects = tmp_path / "project"
    projects.mkdir()

    monkeypatch.setattr(briefmod, "VAULT", vault)
    monkeypatch.setattr(briefmod, "PROJECT_ROOT", projects)
    monkeypatch.setattr(briefmod, "BRIEF_DIR", tmp_path / "briefs")
    briefmod._FM_CACHE.clear()
    briefmod._DOC_CACHE.clear()

    # The work engine POSTs every mutation to the local dashboard; a test must
    # never reach a running service.
    monkeypatch.setattr(work_env["engine"], "_notify_dashboard", lambda *a, **k: None)

    work_env["vault"] = vault
    work_env["projects"] = projects
    return work_env


def _task(tid, title, status="todo", parent=None, **extra):
    """A task row shaped like the SQLite reader produces."""
    row = {"id": tid, "title": title, "status": status, "parent_id": parent,
           "priority": 3, "tags": [], "fields": {}, "created_at": "2026-07-13T05:00:00",
           "started_at": None, "completed_at": None, "description": None,
           "created_by": "manual"}
    row.update(extra)
    return row


# The real hre plan, both halves of it. hre#1 is a 13-part decomposition filed
# on Jul 25; hre#2..6 are five tasks filed Jul 13 covering the same ground.
HRE_SPINE = [
    _task("hre#1", "MVP scope — Grade 9, four surfaces"),
    _task("hre#1.1", "Part 0: Content velocity probe", "done", "hre#1"),
    _task("hre#1.2", "Part 1: Foundations & stack", "done", "hre#1"),
    _task("hre#1.3", "Part 2: The claim spine", "done", "hre#1"),
    _task("hre#1.4", "Part 3: Content to drill pipeline", parent="hre#1"),
    _task("hre#1.5", "Part 4: Learning engine", parent="hre#1"),
    _task("hre#1.6", "Part 5: Identity, privacy & RLS", parent="hre#1"),
    _task("hre#1.7", "Part 6: Design system & Arabic rendering", parent="hre#1"),
    _task("hre#1.8", "Part 7: Student surface", parent="hre#1"),
    _task("hre#1.9", "Part 8: Teacher surface", parent="hre#1"),
    _task("hre#1.10", "Part 9: Admin surface", parent="hre#1"),
    _task("hre#1.11", "Part 10: Parent surface", parent="hre#1"),
    _task("hre#1.12", "Part 11: Repo structure & agent docs", parent="hre#1"),
    _task("hre#1.13", "Part 12: Scope doc finalization", parent="hre#1"),
    _task("hre#2", "HRE: draft the constitution (manhaj, approved sources, "
                   "rules of representation)"),
    _task("hre#3", "HRE: build T0 extraction pipeline (roots, morphology, "
                   "tajwid spans) from quran-tools/qul"),
    _task("hre#4", "HRE: build T1 citation pipeline (Taleem al-Quran, Sadi, "
                   "Ibn Kathir) with provenance"),
    _task("hre#5", "HRE: produce 10 real ruku briefs and MEASURE velocity "
                   "(min per brief, pct human, scholar-min)"),
    _task("hre#6", "HRE: vertical slice — one week end to end (brief to render "
                   "to practice to quiz to parent email)"),
]


# ── State derivation ────────────────────────────────────────────────────

def _counts(total, done=0, active=0, todo=0, waiting=0):
    pct = int(round(done * 100 / total)) if total else 0
    return {"task_count": total, "done_count": done, "active_count": active,
            "todo_count": todo, "waiting_count": waiting, "pct": pct}


def test_state_done_when_project_record_says_completed():
    state, reason = briefmod._derive_state(
        {"status": "completed"}, _counts(5, done=1, active=1),
        "2026-07-26T10:00:00", "task", [], [])
    assert state == "done"
    assert "completed" in reason


def test_state_done_when_every_task_is_done():
    state, reason = briefmod._derive_state(
        {"status": "active"}, _counts(4, done=4), "2026-07-26T10:00:00",
        "task", [], [])
    assert state == "done"
    assert "4 tasks" in reason


def test_cancelled_project_does_not_read_as_about_to_start():
    """`deenoverdunya` is cancelled with zero tasks.

    It read "not started — no tasks have been filed yet", which implies work
    is pending on an abandoned project. The locked state vocabulary has no
    `cancelled` member, so the reason has to carry it.
    """
    state, reason = briefmod._derive_state(
        {"status": "cancelled"}, _counts(0), None, "unknown", [], [])
    assert state in briefmod.STATES if hasattr(briefmod, "STATES") else True
    assert state == "cold"
    assert "cancelled" in reason
    assert "moved elsewhere" in reason
    assert not reason.endswith(".")        # the summary supplies the period


def test_cancelled_project_is_not_told_to_link_a_repo(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "gone").mkdir(parents=True)
    monkeypatch.setattr(briefmod, "PROJECT_ROOT", root)
    assert briefmod._detect_untracked_repo("gone", None, None, "cancelled") == []
    assert briefmod._detect_untracked_repo("gone", None, None, "completed") == []
    assert briefmod._detect_untracked_repo("gone", None, None, "active")


def test_state_blocked_outranks_moving():
    """A blocked project is blocked even if it moved five minutes ago."""
    from brief_types import Blocker
    blockers = [Blocker(task_id="p#1", title="Ship the thing",
                        blocked_on="waiting on the vendor key")]
    state, reason = briefmod._derive_state(
        {"status": "active"}, _counts(3, done=1, active=1),
        briefmod._now_iso(), "task", blockers, [])
    assert state == "blocked"
    assert "vendor key" in reason


def test_state_not_started_names_the_count_and_age():
    tasks = [_task(f"p#{i}", f"Task {i}") for i in range(19)]
    state, reason = briefmod._derive_state(
        {"status": "active"}, _counts(19, todo=19), "2026-07-13T05:00:00",
        "task", [], tasks)
    assert state == "not_started"
    assert "19 tasks" in reason and "none started" in reason


@pytest.mark.parametrize("days,expected", [(0.5, "moving"), (2.9, "moving"),
                                           (5, "warm"), (9.9, "warm"),
                                           (11, "cold"), (400, "cold")])
def test_state_follows_the_activity_windows(days, expected):
    from datetime import datetime, timedelta
    when = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    state, _ = briefmod._derive_state(
        {"status": "active"}, _counts(5, done=2), when, "task", [], [])
    assert state == expected


def test_state_cold_when_nothing_is_dated():
    state, reason = briefmod._derive_state(
        {"status": "active"}, _counts(5, done=2), None, "unknown", [], [])
    assert state == "cold"
    assert "no dated activity" in reason


def test_blocker_needs_a_stated_reason():
    """A bare `waiting` status is not a blocker — inventing a reason would lie."""
    silent = [_task("p#1", "Mystery", "waiting")]
    assert briefmod._derive_blockers(silent, {}) == []

    explained = [_task("p#2", "Mystery", "waiting", description="waiting on legal")]
    found = briefmod._derive_blockers(explained, {})
    assert [b.task_id for b in found] == ["p#2"]
    assert found[0].blocked_on == "waiting on legal"


def test_blocker_reads_handoff_blockers():
    tasks = [_task("p#1", "Ship it", "waiting")]
    handoffs = {"p#1": {"blockers": ["Supabase project not provisioned"],
                        "timestamp": "2026-07-20T09:00:00"}}
    found = briefmod._derive_blockers(tasks, handoffs)
    assert found[0].blocked_on == "Supabase project not provisioned"
    assert found[0].since == "2026-07-20T09:00:00"


# ── Conflict detection: duplicate_spine ─────────────────────────────────

def test_duplicate_spine_finds_the_live_hre_bug():
    """hre#1.1 and hre#5 are the same task under two different plans.

    Nothing about them is hard-coded: the only lexical evidence is the word
    "velocity", which happens to appear in exactly these two of the project's
    nineteen titles. The detector has to decide that is enough on its own.
    """
    conflicts = briefmod._detect_duplicate_spine(HRE_SPINE)
    assert conflicts, "expected the duplicated plan to be detected"

    pairs = {frozenset(c.refs[:2]) for c in conflicts}
    assert frozenset({"hre#1.1", "hre#5"}) in pairs

    hit = next(c for c in conflicts if set(c.refs[:2]) == {"hre#1.1", "hre#5"})
    assert hit.kind == "duplicate_spine"
    assert hit.severity in ("warn", "error")
    assert "velocity" in hit.message
    # The message must be readable on its own, naming both sides.
    assert "Content velocity probe" in hit.message


def test_duplicate_spine_ignores_tasks_in_the_same_subtree():
    """Siblings of one plan are a decomposition, not a duplicate."""
    same_tree = [t for t in HRE_SPINE if t["id"].startswith("hre#1")]
    assert briefmod._detect_duplicate_spine(same_tree) == []


def test_duplicate_spine_stays_quiet_on_genuinely_different_work():
    tasks = [
        _task("p#1", "Set up Postgres row level security"),
        _task("p#2", "Design the onboarding illustration set"),
        _task("p#3", "Write the refund policy page"),
        _task("p#4", "Benchmark the search index"),
    ]
    assert briefmod._detect_duplicate_spine(tasks) == []


def test_duplicate_spine_flags_a_near_identical_pair_as_an_error():
    tasks = [
        _task("p#1", "Build lane analytics and predictive ETAs"),
        _task("p#2", "Build predictive ETAs from lane analytics"),
    ]
    conflicts = briefmod._detect_duplicate_spine(tasks)
    assert len(conflicts) == 1
    assert conflicts[0].severity == "error"


def test_duplicate_spine_ignores_a_shared_generic_word():
    """"Build" and "the" are not evidence; a rare shared term is."""
    tasks = [_task(f"p#{i}", f"Build the {noun} screen")
             for i, noun in enumerate(["settings", "billing", "profile",
                                       "search", "inbox", "archive"])]
    assert briefmod._detect_duplicate_spine(tasks) == []


def test_duplicate_spine_skips_pairs_where_either_side_is_cancelled():
    """Cancelling one of two duplicates IS the resolution.

    hre#5 was cancelled precisely because it had been folded into hre#1.1.
    Reporting it forever would mean every resolution leaves a permanent
    complaint behind.
    """
    live = _task("p#1", "Build lane analytics and predictive ETAs")
    other = _task("p#2", "Build predictive ETAs from lane analytics")
    assert briefmod._detect_duplicate_spine([live, other])       # sanity: it fires

    for cancelled_side in (live, other):
        pair = [dict(live), dict(other)]
        for t in pair:
            if t["id"] == cancelled_side["id"]:
                t["status"] = "cancelled"
        assert briefmod._detect_duplicate_spine(pair) == []


def test_duplicate_spine_is_silent_on_hre_now_that_hre5_is_cancelled():
    """The live hre resolution, end to end on the real titles."""
    resolved = [dict(t) for t in HRE_SPINE]
    for t in resolved:
        if t["id"] == "hre#5":
            t["status"] = "cancelled"
    assert briefmod._detect_duplicate_spine(resolved) == []


def test_duplicate_spine_reports_repeated_evidence_between_two_subtrees():
    tasks = [
        _task("a#1", "Plan A"),
        _task("a#1.1", "Import the morphology corpus", parent="a#1"),
        _task("a#1.2", "Render the tajwid overlay", parent="a#1"),
        _task("b#2", "Import morphology corpus from qul"),
        _task("b#3", "Tajwid overlay rendering"),
    ]
    conflicts = briefmod._detect_duplicate_spine(tasks)
    assert conflicts
    assert "2 such pairs" in conflicts[0].message
    assert set(conflicts[0].refs) >= {"a#1.1", "a#1.2", "b#2", "b#3"}


def test_duplicate_spine_is_bounded():
    """A brief that lists thirty problems reports none."""
    tasks = []
    for i in range(30):
        tasks.append(_task(f"p#{i}", f"Build the distinctword{i} subsystem"))
        tasks.append(_task(f"q#{i}", f"Rebuild distinctword{i} subsystem now"))
    conflicts = briefmod._detect_duplicate_spine(tasks)
    assert 0 < len(conflicts) <= briefmod.MAX_CONFLICTS_PER_KIND


# ── Conflict detection: the other three kinds ───────────────────────────

def test_orphan_task_detects_a_missing_parent():
    tasks = [_task("p#1.4", "Orphaned subtask", parent="p#1")]
    conflicts = briefmod._detect_orphan_tasks(tasks, {"p#1.4"})
    assert [c.kind for c in conflicts] == ["orphan_task"]
    assert conflicts[0].severity == "error"
    assert "p#1" in conflicts[0].refs


def test_orphan_task_detects_a_source_ref_that_is_not_on_disk():
    tasks = [_task("p#1", "Cited task",
                   description="<!-- meta: source_ref:vault/knowledge/nope.md -->")]
    conflicts = briefmod._detect_orphan_tasks(tasks, {"p#1"})
    assert [c.kind for c in conflicts] == ["orphan_task"]
    assert "nope.md" in conflicts[0].message


def test_orphan_task_quiet_when_parent_and_source_exist(tmp_path, monkeypatch):
    real = tmp_path / "real.md"
    real.write_text("hi")
    tasks = [_task("p#1", "Parent"),
             _task("p#1.1", "Child", parent="p#1",
                   description=f"<!-- meta: source_ref:{real} -->")]
    assert briefmod._detect_orphan_tasks(tasks, {"p#1", "p#1.1"}) == []


def test_stale_doc_flags_a_shaping_doc_over_started_work(tmp_path):
    path = tmp_path / "thing.md"
    path.write_text("---\nstatus: shaping\ndate: 2026-07-01\n---\nbody\n")
    docs = [{"path": path, "name": "thing.md", "slug": "thing",
             "fm": {"status": "shaping", "date": "2026-07-01"}, "excerpt": None}]
    tasks = [_task("p#1", "Done thing", "done", created_at="2026-07-02T00:00:00")]
    conflicts = briefmod._detect_stale_docs(docs, tasks)
    assert [c.kind for c in conflicts] == ["stale_doc"]
    assert "shaping" in conflicts[0].message


def test_stale_doc_flags_a_doc_that_predates_the_plan():
    docs = [{"path": Path("/tmp/old.md"), "name": "old.md", "slug": "old",
             "fm": {"status": "active", "date": "2026-06-01"}, "excerpt": None}]
    tasks = [_task("p#1", "New task", created_at="2026-07-20T00:00:00")]
    conflicts = briefmod._detect_stale_docs(docs, tasks)
    assert [c.kind for c in conflicts] == ["stale_doc"]
    assert "predates" in conflicts[0].message


def test_stale_doc_quiet_on_a_current_doc():
    docs = [{"path": Path("/tmp/ok.md"), "name": "ok.md", "slug": "ok",
             "fm": {"status": "active", "date": "2026-07-20"}, "excerpt": None}]
    tasks = [_task("p#1", "New task", created_at="2026-07-21T00:00:00")]
    assert briefmod._detect_stale_docs(docs, tasks) == []


def test_untracked_repo_finds_a_matching_directory(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "hre-prototype").mkdir(parents=True)
    (root / "something-else").mkdir()
    monkeypatch.setattr(briefmod, "PROJECT_ROOT", root)

    conflicts = briefmod._detect_untracked_repo("hre", None, None)
    assert [c.kind for c in conflicts] == ["untracked_repo"]
    assert "hre-prototype" in conflicts[0].message
    assert "something-else" not in conflicts[0].message


def test_the_fix_command_we_print_actually_exists(tmp_path, monkeypatch):
    """A brief that prints a command which doesn't exist is confident nonsense.

    This shipped wrong once: the message said `work project <id> --path <p>`,
    and `project` is not a command. Rather than scrape cli.py's source — which
    would couple this suite to the formatting of a file someone else owns —
    actually run the command and check the CLI recognises it.
    """
    import subprocess
    cli = Path(briefmod.__file__).parent / "cli.py"
    argv = briefmod.UNTRACKED_REPO_FIX.split()[1:]      # ["projects", "path"]

    env = dict(os.environ, AOS_WORK_DB=str(tmp_path / "throwaway.db"))
    result = subprocess.run([sys.executable, str(cli), *argv],
                            capture_output=True, text=True, timeout=60, env=env)
    output = result.stdout + result.stderr
    assert "Unknown command" not in output, (
        f"brief.py tells the operator to run '{briefmod.UNTRACKED_REPO_FIX}', "
        f"but the CLI does not recognise it:\n{output}")
    assert "usage" in output.lower()      # it asked for the missing arguments


def test_untracked_repo_silent_when_the_path_is_already_set(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "hre").mkdir(parents=True)
    monkeypatch.setattr(briefmod, "PROJECT_ROOT", root)
    assert briefmod._detect_untracked_repo("hre", None, str(root / "hre")) == []


# ── Phases, next_up ─────────────────────────────────────────────────────

def test_phases_follow_the_numbered_decomposition():
    phases = briefmod._derive_phases(HRE_SPINE)
    labels = [p.label for p in phases]
    assert "Phase 0 — Content velocity probe" in labels
    assert "Phase 2 — The claim spine" in labels
    assert [p.state for p in phases[:3]] == ["done", "done", "done"]
    assert phases[3].state == "not_started"
    # hre#2..6 belong to no numbered phase and must still be accounted for.
    catch_all = [p for p in phases if p.key == "unphased"]
    assert catch_all and set(catch_all[0].task_ids) >= {"hre#2", "hre#5"}


def test_declared_fields_phase_beats_every_inference():
    """An explicit `fields.phase` declaration always wins.

    The live hre shape: hre#3 and hre#4 declare `part-3` even though their
    titles say nothing about a part, so Phase 3 must hold three tasks, not
    the one whose title happens to match.
    """
    tasks = [dict(t) for t in HRE_SPINE]
    declared = {"hre#1.1": "part-0", "hre#1.2": "part-1", "hre#1.3": "part-2",
                "hre#1.4": "part-3", "hre#3": "part-3", "hre#4": "part-3",
                "hre#2": "foundation", "hre#6": "milestone"}
    for t in tasks:
        if t["id"] in declared:
            t["fields"] = {"phase": declared[t["id"]]}

    phases = {p.key: p for p in briefmod._derive_phases(tasks)}
    assert phases["phase-3"].total == 3
    assert set(phases["phase-3"].task_ids) == {"hre#1.4", "hre#3", "hre#4"}
    # The label is quoted from a member's own title, not invented.
    assert phases["phase-3"].label == "Phase 3 — Content to drill pipeline"
    # A declared phase with no numbered member still gets a readable name.
    assert phases["foundation"].label == "Foundation"
    assert phases["foundation"].task_ids == ["hre#2"]
    # Tasks that declare a phase must never land in the catch-all.
    unphased = phases.get("unphased")
    if unphased:
        assert not (set(unphased.task_ids) & set(declared))


def test_declared_phases_order_numbered_first_then_named():
    tasks = [_task("p#1", "A", fields={"phase": "milestone"}),
             _task("p#2", "B", fields={"phase": "part-10"}),
             _task("p#3", "C", fields={"phase": "part-2"}),
             _task("p#4", "D", fields={"phase": "foundation"})]
    keys = [p.key for p in briefmod._derive_phases(tasks)]
    assert keys == ["phase-2", "phase-10", "foundation", "milestone"]


def test_cancelled_work_is_not_counted_against_a_phase():
    """hre Phase 0 read "1/2 in_progress" with nothing actually left to do."""
    tasks = [_task("p#1", "Probe", "done", fields={"phase": "part-0"}),
             _task("p#2", "Folded into p#1", "cancelled", fields={"phase": "part-0"})]
    phases = briefmod._derive_phases(tasks)
    assert len(phases) == 1
    assert (phases[0].done, phases[0].total) == (1, 1)
    assert phases[0].state == "done"
    assert "p#2" not in phases[0].task_ids


def test_phase_disappears_when_all_its_work_was_cancelled():
    tasks = [_task("p#1", "Abandoned", "cancelled", fields={"phase": "part-0"}),
             _task("p#2", "Live", fields={"phase": "part-1"})]
    assert [p.key for p in briefmod._derive_phases(tasks)] == ["phase-1"]


def test_repeated_phase_numbers_are_not_project_structure():
    """aos has six different parents each owning a "Phase 1: …" subtask.

    Read as project structure that produced 100 phases with colliding keys.
    Repeated numbering means the numbering is per-parent and says nothing
    about the project, so it must not be used.
    """
    tasks = []
    for parent in ("a#1", "b#2", "c#3"):
        tasks.append(_task(parent, f"Spine {parent}"))
        for n in (1, 2):
            tasks.append(_task(f"{parent}.{n}", f"Phase {n}: do a thing",
                               parent=parent))
    phases = briefmod._derive_phases(tasks)
    keys = [p.key for p in phases]
    assert len(keys) == len(set(keys)), "phase keys must be unique"
    assert not any(k.startswith("phase-") for k in keys)


def test_inferred_phases_are_dropped_when_they_are_only_tree_shape():
    """552 tasks yielded ~100 single-task "phases" — noise, not structure."""
    tasks = []
    for i in range(40):
        tasks.append(_task(f"p#{i}", f"Parent {i}"))
        tasks.append(_task(f"p#{i}.1", f"Only child {i}", parent=f"p#{i}"))
    assert briefmod._derive_phases(tasks) == []


def test_declared_phases_are_never_suppressed_however_many():
    tasks = [_task(f"p#{i}", f"Thing {i}", fields={"phase": f"part-{i}"})
             for i in range(30)]
    assert len(briefmod._derive_phases(tasks)) == 30


def test_phases_fall_back_to_parent_tasks_when_nothing_is_numbered():
    tasks = [_task("p#1", "Backend"), _task("p#1.1", "Schema", "done", "p#1"),
             _task("p#1.2", "API", parent="p#1"), _task("p#2", "Loose one")]
    phases = briefmod._derive_phases(tasks)
    assert phases[0].label == "Backend"
    assert phases[0].done == 1 and phases[0].total == 3
    assert phases[0].state == "in_progress"
    assert phases[-1].task_ids == ["p#2"]


def test_next_up_will_not_jump_ahead_of_an_unfinished_phase():
    phases = briefmod._derive_phases(HRE_SPINE)
    next_up = briefmod._derive_next_up(HRE_SPINE, phases, {})
    assert next_up[0].task_id == "hre#1.4"           # Part 3, the first open one
    assert "earliest unfinished phase" in next_up[0].why
    assert "hre#1.9" not in [n.task_id for n in next_up]   # Part 8 waits its turn


def test_next_up_skips_a_parent_whose_children_are_open():
    tasks = [_task("p#1", "Parent"), _task("p#1.1", "Child", parent="p#1")]
    next_up = briefmod._derive_next_up(tasks, briefmod._derive_phases(tasks), {})
    assert [n.task_id for n in next_up] == ["p#1.1"]


def test_next_up_prefers_work_already_in_progress():
    tasks = [_task("p#1", "Someday", priority=1),
             _task("p#2", "Underway", "active", priority=4)]
    next_up = briefmod._derive_next_up(tasks, [], {})
    assert next_up[0].task_id == "p#2"
    assert "in progress" in next_up[0].why


def test_next_up_is_capped_and_explains_itself():
    tasks = [_task(f"p#{i}", f"Thing {i}") for i in range(10)]
    next_up = briefmod._derive_next_up(tasks, [], {})
    assert len(next_up) == briefmod.MAX_NEXT
    assert all(n.why for n in next_up)


# ── Attribution ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["manual", "subtask", "initiative", "inbox",
                                    "api", "cli", "seed", "", None])
def test_intake_sources_are_never_rendered_as_a_person(source):
    """`created_by` is an intake source, not a person.

    1,227 rows say "manual" and 679 say "subtask". Rendering either as "You"
    would re-introduce, at the render layer, exactly the false attribution
    the attribution workstream exists to remove.
    """
    actor = briefmod._created_actor(source, "2026-07-13T05:00:00")
    assert actor.kind == "unknown"
    assert briefmod._describe(actor, "filed", "Some task").startswith("Someone")


def test_import_sources_are_marked_as_imports_not_people():
    actor = briefmod._created_actor("islah-import", "2026-07-13T05:00:00")
    assert actor.kind == "import"
    assert not briefmod._describe(actor, "filed", "x").startswith("You")


@pytest.mark.parametrize("source,kind", [("operator", "operator"),
                                         ("cron", "cron"),
                                         ("chief", "agent"),
                                         ("advisor", "agent")])
def test_tokens_that_do_name_an_actor_still_resolve(source, kind):
    assert briefmod._created_actor(source, "2026-07-13T05:00:00").kind == kind


def test_actor_matches_on_the_field_not_just_the_timestamp():
    """hre#3 has a title and a description change in the same second.

    Attributing a status event to the actor of an unrelated field edit would
    be a coincidence dressed up as a fact.
    """
    at = "2026-07-26T15:49:51"
    history = {"p#1": [
        {"timestamp": at, "field_name": "title", "new_value": "x",
         "actor": "agent:chief", "actor_type": "agent", "session_id": None},
        {"timestamp": at, "field_name": "status", "new_value": "done",
         "actor": "agent:advisor", "actor_type": "agent", "session_id": None},
    ]}
    hit = briefmod._actor_for("p#1", at, history, {}, field="completed_by")
    assert hit.name == "advisor"


def test_the_attribution_cutoff_comes_from_actor_not_a_copy():
    """actor.py owns the instant; two copies would drift into two stories.

    This used to scrape cli.py's source text, which coupled the compiler's
    suite to the exact formatting of a line in a CLI entry point somebody else
    owns. actor.py now exports the value, so compare values.
    """
    import actor
    assert briefmod.ATTRIBUTION_FIX_AT == actor.ATTRIBUTION_FIX_AT


def test_suspect_row_rule_is_actor_pys_not_a_reimplementation():
    """The brief and `work who` must agree on which rows are evidence."""
    import actor
    cases = [("operator", "operator", "2026-07-26T15:28:30", True),
             ("operator", "operator", "2026-07-27T09:00:00", False),
             ("agent:unidentified", "agent", "2026-07-26T15:28:30", False),
             ("agent:chief", "agent", "2026-07-26T15:49:51", False)]
    for actor_str, actor_type, ts, expected in cases:
        assert actor.is_suspect_operator_row(actor_str, actor_type, ts) is expected
        row = {"actor": actor_str, "actor_type": actor_type, "timestamp": ts}
        assert briefmod._is_defaulted_operator(row) is expected


def test_pre_cutoff_operator_rows_are_not_treated_as_evidence():
    """Before the fix, an unset actor silently defaulted to "operator".

    Those rows cannot be distinguished from real ones, so crediting the human
    would confidently attribute agent work to the operator — the exact failure
    the attribution layer exists to remove.
    """
    old = {"p#1": [{"timestamp": "2026-07-26T15:28:30", "field_name": "status",
                    "new_value": "done", "actor": "operator",
                    "actor_type": "operator", "session_id": None}]}
    stale = briefmod._actor_for("p#1", "2026-07-26T15:28:30", old, {},
                                field="completed_by")
    assert stale.kind == "unknown"
    assert briefmod._describe(stale, "completed", "x").startswith("Someone")

    new = {"p#1": [{"timestamp": "2026-07-27T09:00:00", "field_name": "status",
                    "new_value": "done", "actor": "operator",
                    "actor_type": "operator", "session_id": None}]}
    fresh = briefmod._actor_for("p#1", "2026-07-27T09:00:00", new, {},
                                field="completed_by")
    assert fresh.kind == "operator"


def test_a_pre_cutoff_agent_row_is_still_trusted():
    """Only the defaulted value is suspect — a named agent was written on purpose."""
    history = {"p#1": [{"timestamp": "2026-07-26T15:28:30", "field_name": "status",
                        "new_value": "done", "actor": "agent:unidentified",
                        "actor_type": "agent", "session_id": None}]}
    hit = briefmod._actor_for("p#1", "2026-07-26T15:28:30", history, {},
                              field="completed_by")
    assert (hit.kind, hit.name) == ("agent", "unidentified")


def test_history_follows_a_task_across_a_move(brief_env):
    """`move` re-IDs a task; entity_history keys on the old id.

    Without following the `moved_from` bridge, a moved task's entire past
    reads as unattributed.
    """
    conn = sqlite3.connect(str(brief_env["db_path"]))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entity_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id TEXT, "
        "field_name TEXT, old_value TEXT, new_value TEXT, actor TEXT, "
        "actor_type TEXT, timestamp TEXT, session_id TEXT)")
    conn.execute(
        "INSERT INTO entity_history (entity_type, entity_id, field_name, "
        "old_value, new_value, actor, actor_type, timestamp) VALUES "
        "('task','new#1','moved_from','old#9','new#1','operator','operator',"
        "'2026-07-27T10:00:00')")
    conn.execute(                                  # the past, under the old id
        "INSERT INTO entity_history (entity_type, entity_id, field_name, "
        "old_value, new_value, actor, actor_type, timestamp) VALUES "
        "('task','old#9','status','todo','done','agent:chief','agent',"
        "'2026-07-27T09:00:00')")
    conn.commit()
    conn.close()

    rows = briefmod._read_history(
        briefmod._connect_ro(brief_env["db_path"]), {"new#1"})
    completed = [r for r in rows if r["field_name"] == "status"]
    assert completed, "history under the old id was lost"
    assert completed[0]["entity_id"] == "new#1"    # re-keyed onto the current id
    assert completed[0]["actor"] == "agent:chief"


def test_task_activity_is_not_used_for_attribution():
    """Its `actor` column holds intake tokens like "subtask", and no kind."""
    import ast
    tree = ast.parse(Path(briefmod.__file__).read_text())

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            assert "task_activity" not in node.value, (
                f"line {node.lineno} still queries task_activity")


def test_unsigned_task_falls_back_to_history_then_to_unknown():
    task = {"id": "p#1", "fields": {}}
    history = {"p#1": [{"timestamp": "2026-07-26T15:31:46", "actor": "agent:chief",
                        "actor_type": "agent", "session_id": None}]}
    hit = briefmod._actor_for("p#1", "2026-07-26T15:31:46", history, {"p#1": task},
                              field="completed_by")
    assert (hit.kind, hit.name) == ("agent", "chief")   # prefix stripped

    miss = briefmod._actor_for("p#1", "2020-01-01T00:00:00", history, {"p#1": task},
                               field="completed_by")
    assert miss.kind == "unknown"


def test_a_human_git_author_is_named_but_not_classified():
    """A git author name is a name, not a role.

    Asserting that a human committer is "the operator" is a guess — they may
    not be this repo's committer at all.
    """
    commits = [{"sha": "a" * 40, "at": "2026-07-20T10:00:00",
                "author": "Some Person", "subject": "fix: a thing"},
               {"sha": "b" * 40, "at": "2026-07-21T10:00:00",
                "author": "Claude Fable 5", "subject": "feat: another"}]
    events = briefmod._build_timeline([], {}, [], [], commits, [])
    by_name = {e.actor.name: e.actor for e in events if e.kind == "commit"}
    assert by_name["Some Person"].kind == "unknown"
    assert by_name["Claude Fable 5"].kind == "agent"
    # The name still appears in the line either way.
    assert any("Some Person committed" in e.text for e in events)


def test_history_is_scoped_in_sql_not_by_truncating_a_global_list(brief_env):
    """An old row for this project must survive a flood of newer other-project rows."""
    conn = sqlite3.connect(str(brief_env["db_path"]))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entity_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id TEXT, "
        "field_name TEXT, old_value TEXT, new_value TEXT, actor TEXT, "
        "actor_type TEXT, timestamp TEXT, session_id TEXT)")
    conn.execute("INSERT INTO tasks (id, title, status, project_id, created_at) "
                 "VALUES ('mine#1', 'Ours', 'done', 'mine', '2020-01-01T00:00:00')")
    conn.execute(
        "INSERT INTO entity_history (entity_type, entity_id, field_name, "
        "old_value, new_value, actor, actor_type, timestamp) "
        "VALUES ('task','mine#1','status','todo','done','chief','agent',"
        "'2020-01-01T00:00:00')")
    for i in range(3000):                       # newer noise from other projects
        conn.execute(
            "INSERT INTO entity_history (entity_type, entity_id, field_name, "
            "old_value, new_value, actor, actor_type, timestamp) "
            "VALUES ('task',?,'status','todo','done','x','agent',?)",
            (f"other#{i}", f"2026-07-26T10:00:{i % 60:02d}"))
    conn.commit()
    conn.close()

    rows = briefmod._read_history(
        briefmod._connect_ro(brief_env["db_path"]), {"mine#1"})
    assert [r["entity_id"] for r in rows] == ["mine#1"]


def test_timeline_never_credits_the_operator_for_a_source_token(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.add_task("Filed by nobody in particular", project="demo")

    brief = briefmod.compile_brief("demo", store=False)
    created = [e for e in brief.recent_activity if e.kind == "task_created"]
    assert created
    for event in created:
        assert not event.text.startswith("You "), event.text
        assert event.actor.kind in ("unknown", "import", "agent", "cron")


# ── Tags ────────────────────────────────────────────────────────────────

def test_tags_are_derived_and_structural_tags_survive_the_cap():
    tasks = [_task("p#1", "One", tags=["quran", "education"]),
             _task("p#2", "Two", "waiting", tags=["quran"])]
    docs = [{"fm": {"tags": ["platform", "arabic", "tafsir", "education"]}}]
    conflicts = [Conflict(kind="stale_doc"), Conflict(kind="duplicate_spine")]
    tags = briefmod._derive_tags(tasks, docs, conflicts, repo_path=None)

    assert len(tags) <= briefmod.MAX_TAGS
    assert tags[:4] == ["not-started", "blocked", "stale-doc", "no-repo"]
    assert "quran" in tags            # the most frequent derived tag still lands
    assert all(t == t.lower() and " " not in t for t in tags)


def test_structural_tags_leave_room_for_topic_tags():
    """Five structural findings must not squeeze every real tag out of six."""
    tasks = [_task("p#1", "One", "waiting", tags=["quran", "education"])]
    conflicts = [Conflict(kind="stale_doc"), Conflict(kind="duplicate_spine"),
                 Conflict(kind="untracked_repo")]
    tags = briefmod._derive_tags(tasks, [], conflicts, repo_path=None)
    derived = [t for t in tags if t in ("quran", "education")]
    assert len(derived) == 2


# ── End to end ──────────────────────────────────────────────────────────

def test_compile_brief_on_a_seeded_project(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo Project", project_id="demo",
                    done_when="the demo runs end to end")
    eng.add_task("First thing", project="demo", priority=1)
    second = eng.add_task("Second thing", project="demo")
    eng.complete_task(second["id"])

    brief = briefmod.compile_brief("demo")

    assert isinstance(brief, ProjectBrief)
    assert brief.id == "demo"
    assert brief.title == "Demo Project"
    assert brief.done_when == "the demo runs end to end"
    assert (brief.task_count, brief.done_count, brief.pct) == (2, 1, 50)
    assert brief.state == "moving"
    assert brief.summary                     # deterministic prose is never blank
    assert brief.narrative is None
    assert brief.compile_ms >= 0
    assert brief.sources
    assert [n.task_id for n in brief.next_up][:1] == [brief.next_up[0].task_id]
    assert brief.recent_activity
    assert brief.recent_activity[0].actor is not None


def test_compile_brief_is_stored_and_loadable(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.add_task("Only thing", project="demo")

    compiled = briefmod.compile_brief("demo")
    stored = briefmod.load_brief("demo")

    assert stored is not None
    assert stored.id == compiled.id
    assert stored.task_count == compiled.task_count
    assert [p.label for p in stored.phases] == [p.label for p in compiled.phases]
    assert (briefmod.BRIEF_DIR / "demo.json").exists()


def test_load_brief_returns_none_before_any_compile(brief_env):
    assert briefmod.load_brief("never-compiled") is None


def test_narrative_survives_recompilation(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.add_task("Thing", project="demo")
    briefmod.compile_brief("demo")

    briefmod.set_narrative("demo", "This project is finding its feet.",
                           Actor(kind="agent", name="chief"))

    again = briefmod.compile_brief("demo")
    assert again.narrative == "This project is finding its feet."
    assert again.narrative_written_at
    assert again.narrative_aged is False


def test_narrative_ages_when_state_moves_on(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    task = eng.add_task("Thing", project="demo")
    briefmod.compile_brief("demo")
    briefmod.set_narrative("demo", "Nothing has happened yet.",
                           Actor(kind="operator", name="operator"))

    eng.complete_task(task["id"])
    after = briefmod.compile_brief("demo")

    assert after.narrative == "Nothing has happened yet."
    assert after.narrative_aged is True     # the facts moved; the paragraph did not


def test_narrative_written_during_a_compile_is_not_clobbered(brief_env):
    """The recompile-on-every-mutation path races `set_narrative`.

    A compile that carried the narrative forward from the copy it read at the
    *start* would silently destroy a paragraph written while it ran. The merge
    happens at write time instead, so simulate the race by writing the
    narrative after the compile has produced its payload.
    """
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.add_task("Thing", project="demo")
    briefmod.compile_brief("demo")

    in_flight = briefmod.compile_brief("demo", store=False)      # compile starts
    briefmod.set_narrative("demo", "Written mid-compile.",       # narrative lands
                           Actor(kind="agent", name="chief"))

    payload = briefmod.brief_to_dict(in_flight)                  # compile finishes
    payload["_state_hash"] = briefmod._state_hash(in_flight)
    briefmod._write_stored("demo", payload)

    assert briefmod.load_brief("demo").narrative == "Written mid-compile."


def test_narrative_actor_is_recorded(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    briefmod.compile_brief("demo")
    briefmod.set_narrative("demo", "Text.", Actor(kind="agent", name="advisor"))

    stored = json.loads((briefmod.BRIEF_DIR / "demo.json").read_text())
    assert stored["_narrative_actor"]["name"] == "advisor"
    assert stored["_narrative_actor"]["kind"] == "agent"


def test_brief_round_trips_through_json(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.add_task("Thing", project="demo")
    original = briefmod.compile_brief("demo", store=False)

    payload = briefmod.brief_to_dict(original)
    json.dumps(payload)                       # must be JSON-safe with no coaxing
    restored = briefmod.brief_from_dict(payload)

    assert restored.id == original.id
    assert restored.summary == original.summary
    assert len(restored.recent_activity) == len(original.recent_activity)
    if original.recent_activity:
        assert isinstance(restored.recent_activity[0].actor, Actor)


def test_render_markdown_covers_the_sections_that_have_signal(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo Project", project_id="demo",
                    done_when="it works end to end")
    task = eng.add_task("Build lane analytics and predictive ETAs", project="demo")
    eng.add_task("Build predictive ETAs from lane analytics", project="demo")
    eng.complete_task(task["id"])

    text = briefmod.render_markdown(briefmod.compile_brief("demo", store=False))

    assert text.startswith("# Demo Project")
    assert "Done when:" in text
    assert "## Problems in the plan" in text
    assert "duplicate_spine" in text
    assert "## Recent activity" in text
    assert "Compiled" in text


def test_render_markdown_marks_an_aged_narrative(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    task = eng.add_task("Thing", project="demo")
    briefmod.compile_brief("demo")
    briefmod.set_narrative("demo", "A paragraph.", Actor(kind="agent", name="chief"))
    eng.complete_task(task["id"])

    text = briefmod.render_markdown(briefmod.compile_brief("demo"))
    assert "A paragraph." in text
    assert "aged" in text.lower()


def test_compile_all_covers_every_project(brief_env):
    eng = brief_env["engine"]
    eng.add_project("One", project_id="one")
    eng.add_project("Two", project_id="two")
    eng.add_task("A", project="one")

    briefs = briefmod.compile_all()
    assert {b.id for b in briefs} == {"one", "two"}
    assert all(isinstance(b, ProjectBrief) for b in briefs)


def test_untracked_repo_surfaces_through_a_real_compile(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.add_task("Thing", project="demo")
    (brief_env["projects"] / "demo").mkdir()

    brief = briefmod.compile_brief("demo", store=False)
    kinds = {c.kind for c in brief.conflicts}
    assert "untracked_repo" in kinds
    assert "no-repo" in brief.tags


def test_initiative_doc_becomes_an_artifact(brief_env):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.add_task("Thing", project="demo")
    doc = brief_env["vault"] / "knowledge" / "initiatives" / "demo-plan.md"
    doc.write_text("---\ntitle: The Demo Plan\ntype: initiative\n"
                   "date: 2026-07-01\ntags: [demo, planning]\n---\n\n"
                   "# Heading\n\nThe first real sentence of the plan.\n")
    briefmod._FM_CACHE.clear()

    brief = briefmod.compile_brief("demo", store=False)
    paths = {a.path for a in brief.artifacts}
    assert "vault/knowledge/initiatives/demo-plan.md" in paths
    artifact = next(a for a in brief.artifacts if a.path.endswith("demo-plan.md"))
    assert artifact.kind == "initiative"
    assert artifact.title == "The Demo Plan"
    assert artifact.excerpt == "The first real sentence of the plan."
    assert "demo" in brief.tags or "planning" in brief.tags
    assert any(s.endswith("demo-plan.md") for s in brief.sources)


# ── Session scoping ─────────────────────────────────────────────────────
#
# `backend.get_task(id)["sessions"]` is known-corrupt: it returns ~3,945
# sessions for a single task — essentially every session on the machine. The
# compiler must never inherit that. These tests hold the line.

def test_brief_never_reads_the_corrupt_task_sessions_field():
    """Structural, not textual — comments about the bug are fine, uses are not."""
    import ast
    tree = ast.parse(Path(briefmod.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            assert node.slice.value != "sessions", (
                f"line {node.lineno} subscripts a task's ['sessions']")
        if isinstance(node, ast.Attribute):
            assert node.attr != "get_task", f"line {node.lineno} calls get_task"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("get_task", "get_all_tasks"), (
                f"line {node.lineno} calls {node.func.id} — its session list "
                "is unscoped")


def test_session_rows_only_ever_name_a_task_of_this_project():
    """A session's own `task_id` routinely belongs to another project."""
    tasks_by_id = {"aos#1": {"id": "aos#1", "title": "Ours"}}
    sessions = [
        # matched on project_id, but its primary task is another project's
        {"id": "s1", "started_at": "2026-07-20T10:00:00", "ended_at": None,
         "task_id": "quran-garden#78", "matched_task": None},
        {"id": "s2", "started_at": "2026-07-20T11:00:00", "ended_at": None,
         "task_id": "aos#1", "matched_task": "aos#1"},
    ]
    events = briefmod._build_timeline(
        list(tasks_by_id.values()), {}, [], sessions, [], [])
    text = " ".join(e.text for e in events if e.kind == "session")
    assert "quran-garden#78" not in text
    assert "this project" in text        # the honest phrasing for an unnamed match
    assert "Ours" in text


@pytest.mark.skipif(not (Path.home() / ".aos" / "data" / "qareen.db").exists(),
                    reason="no live session store on this machine")
def test_live_session_counts_are_plausible_not_thousands():
    """Single-digit-to-dozens per project, never the whole machine."""
    conn = briefmod._connect_ro(briefmod._work_db_path())
    if conn is None:
        pytest.skip("no work DB")
    try:
        ids = {r[0] for r in conn.execute(
            "SELECT id FROM tasks WHERE project_id = 'auto-tracker'")}
    finally:
        conn.close()
    if not ids:
        pytest.skip("auto-tracker not present")

    sessions = briefmod._read_sessions(ids, "auto-tracker")
    assert len(sessions) <= 40
    for session in sessions:
        matched = session.get("matched_task")
        assert matched is None or matched in ids, (
            f"session {session['id']} claims task {matched}, not ours")


# ── Nested repositories ─────────────────────────────────────────────────
#
# A project's real history often lives in a repo *inside* the linked one. hre
# is checked in at ~/project/hre, but the Next.js app actually being shipped is
# a separate repo at hre/app with its own remote — and missing it is how the
# work system reported HRE as "0/6, not started" while an app was being built.

def _git_repo(path: Path, *, subject="feat: a thing", remote=None, when=None):
    """A real one-commit git repo at `path`."""
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="Test Person",
               GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="Test Person",
               GIT_COMMITTER_EMAIL="t@example.invalid")
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when

    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, env=env,
                       capture_output=True, timeout=60)

    subprocess.run(["git", "init", "-q", str(path)], check=True,
                   capture_output=True, timeout=60)
    (path / "README.md").write_text("hi\n")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", subject)
    if remote:
        git("remote", "add", "origin", remote)
    return path


def test_nested_repos_finds_a_repo_inside_the_linked_one(tmp_path):
    root = _git_repo(tmp_path / "proj")
    _git_repo(root / "app", subject="feat: the real work")

    found = briefmod._nested_repos(str(root))
    assert [label for label, _ in found] == ["app"]
    assert found[0][1] == root / "app"


def test_nested_repo_scan_skips_the_expensive_directories(tmp_path):
    root = _git_repo(tmp_path / "proj")
    for junk in ("node_modules", ".venv", "_archive", "dist", ".mypy_cache"):
        _git_repo(root / junk / "pkg")
    _git_repo(root / "app")

    assert [label for label, _ in briefmod._nested_repos(str(root))] == ["app"]


def test_nested_repo_scan_respects_the_depth_bound(tmp_path):
    root = _git_repo(tmp_path / "proj")
    deep = root / "a" / "b" / "c" / "d" / "e"
    _git_repo(deep)
    (root / "a" / "b").mkdir(parents=True, exist_ok=True)

    found = [label for label, _ in briefmod._nested_repos(str(root))]
    assert "a/b/c/d/e" not in found


def test_nested_repos_degrades_on_missing_input():
    assert briefmod._nested_repos(None) == []
    assert briefmod._nested_repos("/no/such/directory") == []


def test_commits_carry_the_repo_that_holds_them(tmp_path):
    root = _git_repo(tmp_path / "proj", subject="chore: outer")
    nested = _git_repo(root / "app", subject="feat: inner")

    outer = briefmod._git_log(str(root))
    inner = briefmod._git_log(str(nested), label="app")

    assert outer[0]["repo"] == "" and outer[0]["repo_path"] == str(root)
    assert inner[0]["repo"] == "app" and inner[0]["repo_path"] == str(nested)


def test_git_log_cache_cannot_be_mutated_by_a_caller(tmp_path):
    """compile_brief concatenates onto this list.

    Handing out the cached object let that mutation accumulate into the cache,
    so every recompile duplicated the nested-repo rows.
    """
    root = _git_repo(tmp_path / "proj")
    first = briefmod._git_log(str(root))
    first.append({"sha": "deadbeef", "at": "2026-01-01T00:00:00",
                  "author": "x", "subject": "injected", "repo": "", "repo_path": ""})
    second = briefmod._git_log(str(root))
    assert len(second) == 1
    assert all(c["subject"] != "injected" for c in second)


def test_remote_url_is_read_and_normalised(tmp_path):
    ssh = _git_repo(tmp_path / "ssh", remote="git@example.com:owner/repo.git")
    assert briefmod._git_remote(ssh) == "https://example.com/owner/repo"

    https = _git_repo(tmp_path / "https",
                      remote="https://github.com/owner/sub-app.git")
    assert briefmod._git_remote(https) == "https://github.com/owner/sub-app"

    bare = _git_repo(tmp_path / "bare")
    assert briefmod._git_remote(bare) is None
    assert briefmod._git_remote(tmp_path / "not-a-repo") is None


def test_last_activity_counts_a_nested_repos_commits():
    """A project whose only recent movement is nested is moving, not cold."""
    old = "2026-01-01T00:00:00"
    recent = briefmod._now_iso()
    tasks = [_task("p#1", "Thing", created_at=old)]
    commits = [{"sha": "a" * 40, "at": recent, "author": "x",
                "subject": "feat: shipped", "repo": "app", "repo_path": "/x/app"}]

    at, source = briefmod._derive_last_activity(tasks, {}, [], commits)
    assert at == recent and source == "git"
    state, _ = briefmod._derive_state(
        {"status": "active"}, _counts(2, done=1), at, source, [], tasks)
    assert state == "moving"


def test_nested_repo_reaches_artifacts_timeline_and_remote(brief_env):
    eng = brief_env["engine"]
    root = _git_repo(brief_env["projects"] / "demo", subject="chore: scaffold")
    _git_repo(root / "app", subject="feat: the real application",
              remote="https://github.com/owner/the-app.git")
    eng.add_project("Demo", project_id="demo")
    eng.update_project("demo", path=str(root))
    eng.add_task("Thing", project="demo")

    brief = briefmod.compile_brief("demo", store=False)

    commits = [a for a in brief.artifacts if a.kind == "commit"]
    subjects = {a.title for a in commits}
    assert "feat: the real application" in subjects
    assert "chore: scaffold" in subjects

    # The nested commit's path must name the repo that holds it.
    nested_commit = next(a for a in commits
                         if a.title == "feat: the real application")
    assert str(root / "app") in nested_commit.path
    assert nested_commit.excerpt == "in app/"

    # And the timeline must say where it happened.
    lines = [e.text for e in brief.recent_activity if e.kind == "commit"]
    assert any("in app/" in line for line in lines)
    assert any(line.endswith('"chore: scaffold"') for line in lines)

    repos = [a for a in brief.artifacts if a.kind == "repo"]
    assert [a.excerpt for a in repos] == ["https://github.com/owner/the-app"]


def test_a_nested_repo_that_cannot_be_read_is_dropped_not_guessed(brief_env,
                                                                  monkeypatch):
    eng = brief_env["engine"]
    root = _git_repo(brief_env["projects"] / "demo")
    _git_repo(root / "app", subject="feat: unreachable")
    eng.add_project("Demo", project_id="demo")
    eng.update_project("demo", path=str(root))

    monkeypatch.setattr(briefmod, "_git_log",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
                        if k.get("label") else [])
    brief = briefmod.compile_brief("demo", store=False)   # must not raise
    assert brief.state
    assert not [a for a in brief.artifacts if a.kind == "commit"]


# ── Graceful degradation ────────────────────────────────────────────────

def test_unknown_project_compiles_instead_of_raising(brief_env):
    brief = briefmod.compile_brief("no-such-project", store=False)
    assert brief.id == "no-such-project"
    assert brief.state == "not_started"
    assert "no project record" in brief.state_reason
    assert brief.task_count == 0
    assert brief.summary
    assert briefmod.render_markdown(brief)


def test_project_with_no_tasks_compiles(brief_env):
    brief_env["engine"].add_project("Empty", project_id="empty")
    brief = briefmod.compile_brief("empty", store=False)
    assert brief.state == "not_started"
    assert "no tasks" in brief.state_reason
    assert brief.phases == [] and brief.next_up == []


def test_missing_vault_drops_the_artifact_sections(brief_env, tmp_path, monkeypatch):
    monkeypatch.setattr(briefmod, "VAULT", tmp_path / "not-a-vault")
    briefmod._FM_CACHE.clear()
    brief_env["engine"].add_project("Demo", project_id="demo")
    brief_env["engine"].add_task("Thing", project="demo")

    brief = briefmod.compile_brief("demo", store=False)
    assert all(not a.path.startswith("vault/") for a in brief.artifacts)
    assert brief.summary


def test_missing_project_root_does_not_break_conflict_detection(brief_env, tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(briefmod, "PROJECT_ROOT", tmp_path / "gone")
    brief_env["engine"].add_project("Demo", project_id="demo")
    brief = briefmod.compile_brief("demo", store=False)
    assert "untracked_repo" not in {c.kind for c in brief.conflicts}


def test_missing_repo_path_drops_commits(brief_env, monkeypatch):
    eng = brief_env["engine"]
    eng.add_project("Demo", project_id="demo")
    eng.update_project("demo", path="/definitely/not/here")
    brief = briefmod.compile_brief("demo", store=False)
    assert brief.repo_path is None
    assert not [a for a in brief.artifacts if a.kind == "commit"]
    assert "No repository is linked" in brief.summary


def test_git_log_on_a_non_repo_returns_nothing(tmp_path):
    assert briefmod._git_log(str(tmp_path)) == []
    assert briefmod._git_log(None) == []
    assert briefmod._git_log("/no/such/directory") == []


def test_unreadable_database_yields_an_empty_but_valid_brief(tmp_path, monkeypatch):
    monkeypatch.setenv("AOS_WORK_DB", str(tmp_path / "absent.db"))
    monkeypatch.setattr(briefmod, "BRIEF_DIR", tmp_path / "briefs")
    brief = briefmod.compile_brief("anything", store=False)
    assert brief.task_count == 0
    assert brief.state == "not_started"
    assert briefmod.render_markdown(brief)


def test_frontmatter_reader_never_raises_on_junk():
    for junk in ("", "no frontmatter here", "---\nunterminated: yes\n",
                 "---\n\ttabs: [1,2\n---\n", "---\n:::\n---\n"):
        assert isinstance(briefmod._parse_frontmatter(junk), dict)


def test_timestamp_parsing_tolerates_the_shapes_in_the_wild():
    assert briefmod._parse_dt("2026-07-26T15:31:46") is not None
    assert briefmod._parse_dt("2026-07-26") is not None
    assert briefmod._parse_dt("2026-07-26T15:31:46.123456") is not None
    assert briefmod._parse_dt("2026-07-26T15:31:46+00:00") is not None
    assert briefmod._parse_dt("not a date") is None
    assert briefmod._parse_dt(None) is None


# ── Budget ──────────────────────────────────────────────────────────────

def test_compile_stays_inside_the_budget(brief_env):
    """The compiler runs on every task mutation, so it has to stay cheap."""
    eng = brief_env["engine"]
    eng.add_project("Big", project_id="big")
    parent = eng.add_task("Spine", project="big")
    for i in range(60):
        eng.add_subtask(parent["id"], f"Part {i}: do the {i}th thing")

    briefmod.compile_brief("big", store=False)          # warm the caches
    brief = briefmod.compile_brief("big", store=False)
    assert brief.compile_ms < 300, f"compile took {brief.compile_ms}ms"
