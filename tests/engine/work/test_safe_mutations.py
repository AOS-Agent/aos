"""A mutating command never guesses which task it is about to change.

`TaskResolver.resolve` ends in a scored fuzzy tier (word hits 60% +
SequenceMatcher 40%, threshold 0.3) and returns the single best hit. When two
titles score the same, which one wins is whatever order the adapter listed them
in — and the live database proves this is not hypothetical: it held 64 rows
titled "Fix the login bug", 60 titled "Real task", 60 titled "First task"
(migration 114 purged them, the shape recurs). `work done "fix the login bug"`
silently flipped one of sixty-four, and the operator had no way to know which.

The fix keeps titles working and refuses to pick between ties:

  * an exact ID (or legacy ID, or project-scoped shorthand) always wins — those
    are identities, not guesses;
  * otherwise the best-scoring candidates are collected, and a candidate is
    anything within 0.1 of the top score: that is the definition of "these are
    indistinguishable";
  * one candidate mutates; more than one refuses, prints every candidate with
    its id, exits 2, and changes nothing.

Read commands (`show`, `search`, `dispatch`) are untouched. Showing the wrong
task costs a second glance; completing the wrong task costs the record of what
happened.

Exit 2 specifically, not 1: "I could not find that" and "I found several and
will not choose" are different answers, and a caller — the Telegram handler, a
script, an agent — should be able to tell them apart without parsing prose.

Isolated: uses the work_env fixture (throwaway AOS_WORK_DB), never the real DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

import cli  # noqa: E402

AMBIGUOUS = "Fix the login bug"


def _run(monkeypatch, capsys, *argv):
    """Run cli.main() with argv = ["cli.py", *argv]; return (exit_code, stdout)."""
    monkeypatch.setattr(sys, "argv", ["cli.py", *argv])
    exit_code = 0
    try:
        cli.main()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    out = capsys.readouterr().out
    return exit_code, out


@pytest.fixture()
def twins(work_env):
    """Two tasks with the same title — the live shape, at the smallest size."""
    eng = work_env["engine"]
    a = eng.add_task(AMBIGUOUS)
    b = eng.add_task(AMBIGUOUS)
    work_env["a"], work_env["b"] = a, b
    work_env["statuses"] = {
        t["id"]: t["status"] for t in eng.get_all_tasks()
    }
    return work_env


def _statuses(eng):
    return {t["id"]: t["status"] for t in eng.get_all_tasks()}


# ---------------------------------------------------------------------------
# The done-when
# ---------------------------------------------------------------------------

def test_done_on_an_ambiguous_title_exits_2_and_changes_nothing(twins, monkeypatch, capsys):
    eng = twins["engine"]
    before = _statuses(eng)

    code, out = _run(monkeypatch, capsys, "done", AMBIGUOUS)

    assert code == 2, f"expected exit 2 (ambiguous), got {code}: {out}"
    assert twins["a"]["id"] in out and twins["b"]["id"] in out, (
        f"both candidate ids must be printed so the operator can pick: {out}"
    )
    assert _statuses(eng) == before, "a refused command must mutate nothing"


def test_done_on_an_exact_id_still_works(twins, monkeypatch, capsys):
    eng = twins["engine"]
    target = twins["b"]["id"]

    code, out = _run(monkeypatch, capsys, "done", target)

    assert code == 0, out
    assert eng.get_task(target)["status"] == "done"
    assert eng.get_task(twins["a"]["id"])["status"] != "done", (
        "the other twin must be untouched — that is the whole point"
    )


# ---------------------------------------------------------------------------
# Every mutating command, same contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ("done", AMBIGUOUS),
    ("start", AMBIGUOUS),
    ("cancel", AMBIGUOUS),
    ("subtask", AMBIGUOUS, "a new part"),
    ("handoff", AMBIGUOUS, "--state", "halfway"),
])
def test_mutating_commands_refuse_an_ambiguous_title(twins, monkeypatch, capsys, argv):
    eng = twins["engine"]
    before = _statuses(eng)
    tasks_before = len(eng.get_all_tasks())

    code, out = _run(monkeypatch, capsys, *argv)

    assert code == 2, f"`work {argv[0]}` should refuse, got {code}: {out}"
    assert "Ambiguous" in out or "ambiguous" in out
    assert _statuses(eng) == before
    assert len(eng.get_all_tasks()) == tasks_before, (
        "subtask must not create a child under a guessed parent"
    )


def test_move_refuses_an_ambiguous_title(twins, monkeypatch, capsys):
    eng = twins["engine"]
    eng.add_project("Target", project_id="target")
    before = {t["id"] for t in eng.get_all_tasks()}

    code, out = _run(monkeypatch, capsys, "move", AMBIGUOUS, "--to", "target")

    assert code == 2, out
    assert {t["id"] for t in eng.get_all_tasks()} == before, (
        "move re-IDs tasks; a guessed move is unusually hard to undo"
    )


def test_move_accepts_an_unambiguous_title(work_env, monkeypatch, capsys):
    """`move` only ever matched literal ids before — a title silently moved
    nothing and said "No tasks found to move". It resolves now, with the same
    refusal on a tie."""
    eng = work_env["engine"]
    eng.add_project("Target", project_id="target")
    task = eng.add_task("Wire the evening check-in")

    code, out = _run(monkeypatch, capsys, "move", "evening check-in", "--to", "target")

    assert code == 0, out
    moved = [t for t in eng.get_all_tasks() if t["title"] == "Wire the evening check-in"]
    assert moved and moved[0]["project"] == "target"
    assert moved[0]["id"] != task["id"], "a move re-IDs into the target project"


# ---------------------------------------------------------------------------
# Still fuzzy-friendly: a clear winner wins
# ---------------------------------------------------------------------------

def test_a_clear_best_match_is_not_treated_as_ambiguous(work_env, monkeypatch, capsys):
    eng = work_env["engine"]
    target = eng.add_task("SSE push from the work engine")
    eng.add_task("Write the onboarding docs")
    eng.add_task("Rebuild the transcriber venv")

    code, out = _run(monkeypatch, capsys, "done", "sse push")

    assert code == 0, f"a clear winner must still resolve from a title: {out}"
    assert eng.get_task(target["id"])["status"] == "done"


def test_a_unique_substring_resolves(work_env, monkeypatch, capsys):
    eng = work_env["engine"]
    target = eng.add_task("Wire the Telegram bridge reconnect")
    eng.add_task("Rebuild the transcriber venv")

    code, out = _run(monkeypatch, capsys, "start", "bridge reconnect")
    assert code == 0, out
    assert eng.get_task(target["id"])["status"] == "active"


def test_no_match_at_all_still_exits_1(work_env, monkeypatch, capsys):
    """"Not found" and "found several" are different answers and must keep
    different exit codes."""
    eng = work_env["engine"]
    eng.add_task("Something entirely unrelated")

    code, out = _run(monkeypatch, capsys, "done", "zzzz no such thing zzzz")
    assert code == 1, out


# ---------------------------------------------------------------------------
# Read commands keep today's behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["show", "dispatch", "who"])
def test_read_commands_still_resolve_an_ambiguous_title(twins, monkeypatch, capsys, command):
    code, out = _run(monkeypatch, capsys, command, AMBIGUOUS)
    assert code == 0, f"`work {command}` must stay fuzzy: {out}"
    assert AMBIGUOUS in out


def test_search_still_lists_every_match(twins, monkeypatch, capsys):
    code, out = _run(monkeypatch, capsys, "search", AMBIGUOUS)
    assert code == 0, out
    assert twins["a"]["id"] in out and twins["b"]["id"] in out


# ---------------------------------------------------------------------------
# The resolver contract, directly
# ---------------------------------------------------------------------------

def test_resolver_returns_every_tied_candidate(twins):
    eng = twins["engine"]
    resolver = eng._get_resolver()

    task, candidates = resolver.resolve_for_mutation(AMBIGUOUS)
    assert task is None
    assert {c["id"] for c in candidates} == {twins["a"]["id"], twins["b"]["id"]}


def test_resolver_prefers_an_exact_id_over_any_score(twins):
    eng = twins["engine"]
    resolver = eng._get_resolver()

    task, candidates = resolver.resolve_for_mutation(twins["a"]["id"])
    assert task is not None and task["id"] == twins["a"]["id"]
    assert candidates == []
