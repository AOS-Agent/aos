"""Task enricher — link-and-pull regression tests.

The enricher's whole value is that it *doesn't* write prose. These tests pin
the three rules from BRIEF-CONTRACT.md:

  * a body is pulled verbatim from a real doc section into `tasks.description`
    and carries an anchor back to the source;
  * a task with no source section gets an empty body and a `no_body` conflict,
    never generated filler — and a stub ("Full detail: x.md. The decisions:")
    counts as no body;
  * a hand-written description is never overwritten;
  * disagreements between the docs and the tracker (and inside a single doc)
    are reported and never auto-resolved.

Isolated: the work_env fixture points the backend at a throwaway SQLite DB,
and AOS_VAULT_DIR points doc discovery at a tmp vault. One test reads the real
`hre-mvp-scope.md` (read-only, no DB) to keep the fixture honest against the
document that motivated this module — it skips when the vault is absent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the `qareen` package importable (package root is core/).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core"))

import enrich  # noqa: E402  (work dir is on sys.path via tests/conftest.py)
from enrich import (  # noqa: E402
    enrich_project,
    find_project_docs,
    match_section,
    parse_doc,
    task_body,
)


# ── Fixtures ────────────────────────────────────────────────────────────

# Mirrors the real hre-mvp-scope.md: a fenced progress index, then sections
# whose number prefixes are irregular (4, 4A, 4C) so nothing can be inferred
# from prefix ordering — only the "Part N" token is reliable. The index and the
# headings deliberately disagree for Part 2, exactly like the live doc.
SCOPE_DOC = """---
title: Demo MVP — Scope
type: initiative
date: 2026-07-25
project: demo
supersedes_partially: [demo-old.md]
---

# DEMO MVP — SCOPE

## 3 · Where we are

```
🔶  0. Content velocity      — how fast can we produce lessons?
⬜  1. Foundations & stack   — what we build it with
⬜  2. The claim spine       — how content is structured
```

## 4 · Part 0 — Content velocity  🔶

**The question:** how fast can we turn sources into finished lessons? The
answer decides whether this project is viable at all, and nothing else matters
until it is settled.

### 4.3 Still to do

- minutes per lesson
- how much was human vs machine

## 4A · Part 1 — Foundations & stack  ✅

Full detail: `research/demo-stack.md`. The decisions:

### 4A.1 What we build it with

| Layer | Decision | Note |
|---|---|---|
| **App** | Next.js as a PWA | Pin the versions |
| Database | Postgres with row-level security | Rules live in the database |

### 4A.3 Logins

Identify students by their permanent directory id rather than their email
address, because emails change every single year and the review history is
keyed to whatever we pick here.

## 4C · Part 2 — The claim spine  ✅

Everything in this product is a view over claims, and there is no second
content system anywhere in the design.

| Layer | What's in it |
|---|---|
| Content pool | Passages and claims |
| Curriculum | Courses and lessons |

### 4C.2 Layer 1 — the content pool

```sql
passages          id · surah · ayah_start
```

## 4B · What this costs

Roughly two to five dollars of compute per finished passage, and far more than
that again in the human review time it takes to check one.

## 4D · Part 6 — Design system

See `research/demo-design.md`.
"""

BUILD_DOC = """---
title: Demo Build Spec
type: initiative
date: 2026-07-20
project: demo
---

# DEMO — BUILD SPEC

### ② Vertical slice — one week, everything, tiny
Four briefs run through the whole loop end to end, which proves the machine
coheres and gives us something real to show the school.

## 9 · Part 8 — Rollout

Ship it to one class first, then the rest of the grade.

### Acceptance criteria

- the loop runs end to end
- a teacher can see today's class
"""


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """A tmp vault holding the demo project's source docs."""
    root = tmp_path / "vault" / "knowledge" / "initiatives"
    root.mkdir(parents=True)
    (root / "demo-mvp-scope.md").write_text(SCOPE_DOC, encoding="utf-8")
    (root / "demo-build.md").write_text(BUILD_DOC, encoding="utf-8")
    monkeypatch.setenv("AOS_VAULT_DIR", str(tmp_path / "vault"))
    return root


@pytest.fixture()
def demo(work_env, vault):
    """work_env + vault, seeded with a project whose tasks mirror hre's."""
    eng = work_env["engine"]
    eng.add_project("Demo", short_id="demo", project_id="demo")

    tasks = {}
    for title in (
        "Part 0: Content velocity probe",
        "Part 1: Foundations & stack",
        "Part 2: The claim spine",
        "Part 3: Content to drill pipeline",
        "Part 6: Design system",
        "Demo: vertical slice",
    ):
        task = eng.add_task(title, project="demo")
        tasks[title] = task["id"]

    work_env["tasks"] = tasks
    work_env["vault"] = vault
    return work_env


def _conflicts(report, kind):
    return [c for c in report.conflicts if c.kind == kind]


# ── Pulling bodies ──────────────────────────────────────────────────────

def test_pulls_body_from_matching_section(demo):
    report = enrich_project("demo", dry_run=True)
    tid = demo["tasks"]["Part 2: The claim spine"]

    payload = report.pulled[tid]
    assert payload["body"].startswith("Everything in this product is a view over claims")
    assert payload["body_source"].endswith("demo-mvp-scope.md#4C")
    assert (tid, payload["body_source"]) in report.matched


def test_matches_on_part_token_not_prefix_order(demo):
    """Prefixes run 4, 4A, 4C — only the "Part N" token identifies a section."""
    report = enrich_project("demo", dry_run=True)

    assert report.pulled[demo["tasks"]["Part 0: Content velocity probe"]][
        "body_source"].endswith("#4")
    assert report.pulled[demo["tasks"]["Part 1: Foundations & stack"]][
        "body_source"].endswith("#4A")
    assert report.pulled[demo["tasks"]["Part 2: The claim spine"]][
        "body_source"].endswith("#4C")


def test_stub_paragraph_is_skipped_for_the_decisions_it_introduces(demo):
    """Part 1's only prose is "Full detail: … The decisions:" — a stub.

    That tells the operator nothing, so the body falls through to the table
    the stub was introducing. Cells are moved, never reworded.
    """
    report = enrich_project("demo", dry_run=True)
    body = report.pulled[demo["tasks"]["Part 1: Foundations & stack"]]["body"]

    assert "Full detail" not in body
    assert body == (
        "- **App** — Next.js as a PWA · Pin the versions\n"
        "- **Database** — Postgres with row-level security · Rules live in the database"
    )


def test_prose_wins_over_a_later_table(demo):
    """Document order decides: real prose first, so the table stays out."""
    report = enrich_project("demo", dry_run=True)
    body = report.pulled[demo["tasks"]["Part 2: The claim spine"]]["body"]

    assert body == (
        "Everything in this product is a view over claims, and there is no "
        "second content system anywhere in the design."
    )
    assert "Content pool" not in body
    assert "passages" not in body      # the SQL block is never content


def test_section_that_is_only_a_pointer_gets_no_body(demo):
    """A section containing just "See other-doc.md" is a gap, not a body."""
    report = enrich_project("demo", dry_run=True)
    tid = demo["tasks"]["Part 6: Design system"]

    assert tid in report.unmatched
    assert tid not in report.pulled
    conflict = [c for c in _conflicts(report, "no_body") if tid in c.refs][0]
    assert "nothing in it that describes the work" in conflict.message
    assert any(r.endswith("demo-mvp-scope.md#4D") for r in conflict.refs)


def test_missing_section_gets_no_body_conflict_not_filler(demo):
    """Part 3 exists as a task but nowhere in the docs. Leave it empty."""
    report = enrich_project("demo", dry_run=True)
    tid = demo["tasks"]["Part 3: Content to drill pipeline"]

    assert tid in report.unmatched
    assert tid not in report.pulled
    conflicts = [c for c in _conflicts(report, "no_body") if tid in c.refs]
    assert len(conflicts) == 1
    assert "generated text" in conflicts[0].message


def test_title_fallback_matches_when_no_part_token(demo):
    report = enrich_project("demo", dry_run=True)
    tid = demo["tasks"]["Demo: vertical slice"]

    assert report.pulled[tid]["body"].startswith("Four briefs run through the whole loop")


def test_title_fallback_is_strict(demo, vault):
    """A loosely-related title must not attach the wrong section's prose."""
    eng = demo["engine"]
    task = eng.add_task("Write the weekly investor update", project="demo")

    report = enrich_project("demo", dry_run=True)
    assert task["id"] in report.unmatched


def test_acceptance_pulled_from_criteria_subheading(demo):
    docs = find_project_docs("demo")
    section = match_section("Part 8: Rollout", docs)
    assert section is not None
    assert enrich._acceptance(section) == [
        "the loop runs end to end",
        "a teacher can see today's class",
    ]


def test_acceptance_pulled_from_checkboxes(vault):
    (vault / "demo-extra.md").write_text(
        "---\nproject: demo\ndate: 2026-07-01\n---\n\n"
        "## 7 · Part 4 — Learning engine\n\n"
        "The engine schedules review so nothing is forgotten silently.\n\n"
        "- [x] spaced repetition table\n"
        "- [ ] quiz integrity checks\n",
        encoding="utf-8",
    )
    docs = find_project_docs("demo")
    section = match_section("Part 4: Learning engine", docs)

    assert enrich._acceptance(section) == [
        "spaced repetition table",
        "quiz integrity checks",
    ]


# ── Status disagreement ─────────────────────────────────────────────────

def test_reports_doc_vs_tracker_disagreement(demo):
    """Heading says ✅, tracker says todo — surface it, don't reconcile it."""
    report = enrich_project("demo", dry_run=True)
    tid = demo["tasks"]["Part 2: The claim spine"]

    hits = [c for c in report.disagreements if tid in c.refs and "done" in c.message]
    assert hits, [c.message for c in report.disagreements]
    c = hits[0]
    assert c.kind == "status_disagreement"
    assert c.severity == "warn"
    assert "is marked done in demo-mvp-scope.md" in c.message
    assert "not started in the tracker" in c.message
    assert any(r.endswith("demo-mvp-scope.md#4C") for r in c.refs)


def test_reports_internal_doc_contradiction(demo):
    """The doc's own index (⬜) contradicts its heading (✅)."""
    report = enrich_project("demo", dry_run=True)

    hits = [c for c in report.disagreements
            if "contradicts itself" in c.message and "Part 2" in c.message]
    assert len(hits) == 1
    c = hits[0]
    assert any(r.endswith("demo-mvp-scope.md#3") for r in c.refs)     # the index
    assert any(r.endswith("demo-mvp-scope.md#4C") for r in c.refs)    # the heading


def test_marker_agreeing_with_tracker_is_silent(demo):
    """Index and heading both say 🔶 for Part 0; mark it active and it's quiet."""
    eng = demo["engine"]
    eng.start_task(demo["tasks"]["Part 0: Content velocity probe"])

    report = enrich_project("demo", dry_run=True)
    tid = demo["tasks"]["Part 0: Content velocity probe"]
    assert [c for c in report.disagreements if tid in c.refs] == []


def test_disagreements_are_never_auto_resolved(demo):
    """A real enrich pass changes bodies only — never a task's status."""
    eng = demo["engine"]
    tid = demo["tasks"]["Part 2: The claim spine"]

    enrich_project("demo")
    assert eng.get_task(tid)["status"] == "todo"


def test_status_disagreements_appear_in_conflicts(demo):
    report = enrich_project("demo", dry_run=True)
    assert _conflicts(report, "status_disagreement") == report.disagreements


# ── Writing ─────────────────────────────────────────────────────────────

def test_dry_run_writes_nothing(demo):
    eng = demo["engine"]
    tid = demo["tasks"]["Part 2: The claim spine"]

    report = enrich_project("demo", dry_run=True)

    assert report.changed == 0
    assert report.pulled[tid]["body"]          # it knows what it would write
    assert tid in report.pending               # and that it would write it
    assert task_body(eng.get_task(tid)) == {}  # and wrote none of it
    assert not eng.get_task(tid).get("notes")


def test_write_lands_in_the_description_column(demo):
    eng = demo["engine"]
    report = enrich_project("demo")
    tid = demo["tasks"]["Part 0: Content velocity probe"]

    task = eng.get_task(tid)
    assert task["notes"].startswith("**The question:** how fast")   # the column

    stored = task_body(task)
    assert stored["body_source"].endswith("demo-mvp-scope.md#4")
    assert stored["acceptance"] == ["minutes per lesson", "how much was human vs machine"]
    assert stored["body_synced_at"]
    assert report.changed == 4       # Parts 0-2 and the vertical slice


def test_second_pass_is_idempotent(demo):
    enrich_project("demo")
    again = enrich_project("demo")

    assert again.changed == 0
    assert again.pending == []
    assert len(again.matched) == 4


def test_hand_written_description_is_never_overwritten(demo):
    """The doc does not get to overwrite a person."""
    eng = demo["engine"]
    tid = demo["tasks"]["Part 2: The claim spine"]
    eng.update_task(tid, description="Mine. Do not touch — decided over lunch.")

    report = enrich_project("demo")

    assert eng.get_task(tid)["notes"] == "Mine. Do not touch — decided over lunch."
    assert [t for t, _ in report.skipped] == [tid]
    assert (tid, "vault/knowledge/initiatives/demo-mvp-scope.md#4C") in report.matched


def test_our_own_body_is_re_synced_when_the_doc_changes(demo, vault):
    """An enricher-written body is ours to update; provenance proves it."""
    eng = demo["engine"]
    tid = demo["tasks"]["Part 2: The claim spine"]
    enrich_project("demo")

    doc = vault / "demo-mvp-scope.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "there is no second\ncontent system anywhere in the design",
            "there is exactly one content system and this is it",
        ),
        encoding="utf-8",
    )
    report = enrich_project("demo")

    assert report.skipped == []
    assert report.changed == 1
    assert "exactly one content system" in eng.get_task(tid)["notes"]


def test_meta_comment_survives_the_write(demo):
    """`<!-- meta: source_ref:… -->` is smuggled through description. Keep it."""
    eng = demo["engine"]
    tid = demo["tasks"]["Part 0: Content velocity probe"]
    meta = "<!-- meta: source_ref:vault/knowledge/initiatives/demo-mvp-scope.md -->"
    eng.update_task(tid, description=meta)

    report = enrich_project("demo")

    notes = eng.get_task(tid)["notes"]
    assert notes.startswith("**The question:** how fast")
    assert meta in notes
    assert report.skipped == []      # a meta comment is not a hand-written body


def test_source_ref_prioritises_its_own_doc(demo, vault):
    """A task's own source_ref outranks the project-wide doc ordering."""
    (vault / "demo-side.md").write_text(
        "---\nproject: demo\ndate: 2026-07-30\n---\n\n"
        "## 2 · Part 2 — The claim spine\n\n"
        "The side document's account of the claim spine, which is the one this "
        "task was actually created from.\n",
        encoding="utf-8",
    )
    eng = demo["engine"]
    tid = demo["tasks"]["Part 2: The claim spine"]

    # Newest date, so it wins by default...
    assert enrich_project("demo", dry_run=True).pulled[tid]["body_source"].endswith(
        "demo-side.md#2")

    # ...until the task names the doc it came from.
    eng.update_task(
        tid,
        description="<!-- meta: source_ref:vault/knowledge/initiatives/demo-mvp-scope.md -->",
    )
    report = enrich_project("demo", dry_run=True)
    assert report.pulled[tid]["body_source"].endswith("demo-mvp-scope.md#4C")


def test_write_preserves_unrelated_fields(demo):
    eng = demo["engine"]
    tid = demo["tasks"]["Part 2: The claim spine"]
    eng.update_task(tid, fields={"custom": "keep me"})

    enrich_project("demo")

    task = eng.get_task(tid)
    assert task["fields"]["custom"] == "keep me"
    assert task["notes"].startswith("Everything in this product")


def test_unknown_project_raises(work_env, vault):
    with pytest.raises(ValueError):
        enrich_project("nope", dry_run=True)


def test_project_with_no_docs_degrades(work_env, tmp_path, monkeypatch):
    eng = work_env["engine"]
    monkeypatch.setenv("AOS_VAULT_DIR", str(tmp_path / "empty-vault"))
    eng.add_project("Lonely", short_id="lonely", project_id="lonely")
    task = eng.add_task("Part 2: The claim spine", project="lonely")

    report = enrich_project("lonely", dry_run=True)

    assert report.matched == []
    assert report.unmatched == [task["id"]]
    assert _conflicts(report, "no_body")


# ── Doc discovery ───────────────────────────────────────────────────────

def test_superseded_docs_rank_below_the_doc_that_supersedes_them(vault):
    (vault / "demo-old.md").write_text(
        "---\nproject: demo\ndate: 2026-07-26\n---\n\n"
        "## 1 · Part 2 — The old claim spine\n\nStale prose nobody should pull.\n",
        encoding="utf-8",
    )
    docs = find_project_docs("demo")
    names = [Path(d.rel).name for d in docs]

    assert names.index("demo-mvp-scope.md") < names.index("demo-old.md")
    section = match_section("Part 2: The claim spine", docs)
    assert section.anchor == "4C"


def test_frontmatter_project_match_finds_docs_without_the_slug_prefix(vault, tmp_path):
    other = tmp_path / "vault" / "knowledge" / "research"
    other.mkdir(parents=True)
    (other / "stack-notes.md").write_text(
        "---\nproject: demo\ndate: 2026-07-01\n---\n\n# Notes\n\nSome prose.\n",
        encoding="utf-8",
    )
    rels = [d.rel for d in find_project_docs("demo")]
    assert any(r.endswith("stack-notes.md") for r in rels)


# ── The document that motivated all of this ─────────────────────────────

REAL_SCOPE = Path.home() / "vault" / "knowledge" / "initiatives" / "hre-mvp-scope.md"


@pytest.mark.skipif(not REAL_SCOPE.exists(), reason="operator vault not present")
def test_real_hre_scope_doc_parses_as_expected():
    """Read-only check against the live doc. No DB, no writes."""
    doc = parse_doc(REAL_SCOPE)

    part2 = [s for s in doc.sections if s.part_no == 2 and s.level == 2]
    assert len(part2) == 1
    section = part2[0]
    assert section.anchor == "4C"
    assert section.marker == "✅"
    assert enrich._first_body(section).startswith("Everything in this product")

    index = {e.part_no: e.marker for e in doc.index_entries}
    assert index[1] == "⬜" and index[2] == "⬜"    # the live contradiction
    assert index[0] == "🔶"
