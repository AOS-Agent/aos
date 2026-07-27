"""Worktree audit / normalisation-plan tests.

These guard the distinctions that make the difference between a safe plan and a
destructive one — the live data had all three cases at once:

  * a registered worktree whose DIRECTORY IS GONE
  * a registered worktree whose directory exists but holds ZERO FILES (the OS
    tmp cleaner deletes files and leaves the tree) — this is the one a naive
    "does the directory exist?" check gets wrong, and 17 of them were live
  * a genuinely live worktree that must never be treated as disposable

And the safety rule: `git worktree move` is blocked by DIRTY state only.
Committed-but-unmerged work moves along fine, so `ahead` must not make a move
look unsafe — while still meaning "do not delete this one".

Nothing here runs git mutations; the module only ever emits commands.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "engine" / "work"))

import project_worktrees as pw  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(cwd), *args), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "README.md").write_text("x")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _rows(rep, branch):
    return next(r for r in rep.rows if r.branch == branch)


def _actions(rep, kind):
    return [a for a in rep.actions if a.kind == kind]


# ── the three states a "registered worktree" can be in ──────────────

def test_live_worktree_is_recognised_and_located(repo, tmp_path):
    wt = repo / ".claude" / "worktrees" / "feature"
    _git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")

    rep = pw.audit({"proj": repo})
    row = _rows(rep, "feature")
    assert row.location_kind == "canonical"
    assert row.exists and row.file_count and row.file_count > 0
    assert "live worktree" in row.verdict
    assert not _actions(rep, "move"), "a canonical worktree needs no move"


def test_missing_directory_is_distinguished_from_empty_directory(repo, tmp_path):
    """The distinction that matters: 'gone' and 'empty skeleton' need different
    handling, and 'exists' alone cannot tell them apart."""
    gone = tmp_path / "gone-wt"
    husk = tmp_path / "husk-wt"
    _git(repo, "worktree", "add", "-q", str(gone), "-b", "gone-branch")
    _git(repo, "worktree", "add", "-q", str(husk), "-b", "husk-branch")

    # Directory deleted outright.
    subprocess.run(["rm", "-rf", str(gone)], check=True)
    # Files deleted, directory tree left standing — what the tmp cleaner does.
    for f in sorted(husk.rglob("*"), key=lambda p: -len(p.parts)):
        if f.is_file() or f.is_symlink():
            f.unlink()

    rep = pw.audit({"proj": repo})
    g, h = _rows(rep, "gone-branch"), _rows(rep, "husk-branch")

    assert g.exists is False
    assert "directory is gone" in g.verdict

    assert h.exists is True, "the husk directory still exists"
    assert h.file_count == 0
    assert "EMPTY SKELETON" in h.verdict
    assert "nothing" in h.verdict.lower() or "no work" in h.verdict.lower()


def test_husk_is_offered_for_removal_and_gone_is_only_pruned(repo, tmp_path):
    husk = tmp_path / "husk-wt"
    _git(repo, "worktree", "add", "-q", str(husk), "-b", "husk-branch")
    for f in sorted(husk.rglob("*"), key=lambda p: -len(p.parts)):
        if f.is_file() or f.is_symlink():
            f.unlink()

    rep = pw.audit({"proj": repo})
    rm = _actions(rep, "rm_husk")
    assert len(rm) == 1
    assert str(husk) in rm[0].command
    assert rm[0].safe is True
    assert "ZERO regular files" in rm[0].why
    assert "prune" in rm[0].caveat, "ordering matters — prune before rm"


def test_husks_are_collapsed_into_one_reviewable_command(repo, tmp_path):
    for i in range(3):
        h = tmp_path / f"husk{i}"
        _git(repo, "worktree", "add", "-q", str(h), "-b", f"b{i}")
        for f in sorted(h.rglob("*"), key=lambda p: -len(p.parts)):
            if f.is_file() or f.is_symlink():
                f.unlink()

    rep = pw.audit({"proj": repo})
    rm = _actions(rep, "rm_husk")
    assert len(rm) == 1, "one command listing every path, not one per husk"
    for i in range(3):
        assert f"husk{i}" in rm[0].command


def test_prune_is_described_as_non_destructive_to_directories(repo, tmp_path):
    gone = tmp_path / "gone-wt"
    _git(repo, "worktree", "add", "-q", str(gone), "-b", "gone-branch")
    subprocess.run(["rm", "-rf", str(gone)], check=True)

    rep = pw.audit({"proj": repo})
    prune = _actions(rep, "prune")
    assert len(prune) == 1
    assert prune[0].safe is True
    assert "worktree prune" in prune[0].command
    assert "does NOT delete" in prune[0].why


# ── locations ───────────────────────────────────────────────────────

@pytest.mark.parametrize("prefix", pw.EPHEMERAL_PREFIXES)
def test_every_tmp_prefix_is_classified_ephemeral(repo, prefix):
    """The rule against /private/tmp, enforced by classification not by memory."""
    assert pw._classify_location(Path(prefix + "some-wt"), repo) == "ephemeral"


def test_pytest_tmp_dirs_are_not_mistaken_for_ephemeral(repo, tmp_path):
    """Guards the tests themselves: macOS puts tmp_path under /private/var/folders,
    which must NOT match the /private/var/tmp/ prefix."""
    assert pw._classify_location(tmp_path / "wt", repo) != "ephemeral"


def test_live_worktree_in_a_wiped_location_is_planned_into_the_project(
        repo, tmp_path, monkeypatch):
    """A live worktree sitting where the OS deletes files must be moved inside
    the project — this is the /private/tmp/aos-* case."""
    doomed = tmp_path / "doomed-wt"
    _git(repo, "worktree", "add", "-q", str(doomed), "-b", "urgent/fix")
    monkeypatch.setattr(pw, "EPHEMERAL_PREFIXES", (str(tmp_path) + "/",))

    rep = pw.audit({"proj": repo})
    row = _rows(rep, "urgent/fix")
    assert row.location_kind == "ephemeral"

    mv = _actions(rep, "move")
    assert len(mv) == 1
    assert str(doomed) in mv[0].command
    assert str(repo / ".claude" / "worktrees" / "urgent-fix") in mv[0].command
    assert "the OS wipes" in mv[0].why


def test_sibling_worktree_is_flagged_and_planned_into_the_project(repo, tmp_path):
    sib = repo.parent / "proj-featurebranch"
    _git(repo, "worktree", "add", "-q", str(sib), "-b", "feature/thing")

    rep = pw.audit({"proj": repo})
    row = _rows(rep, "feature/thing")
    assert row.location_kind == "sibling"

    mv = _actions(rep, "move")
    assert len(mv) == 1
    assert str(sib) in mv[0].command
    # Destination is inside the project, named for the branch, not the old dir.
    assert str(repo / ".claude" / "worktrees" / "feature-thing") in mv[0].command
    assert "one-directory-one-project" in mv[0].why


def test_main_checkout_is_never_proposed_for_moving(repo):
    rep = pw.audit({"proj": repo})
    main = [r for r in rep.rows if r.is_main]
    assert len(main) == 1
    assert "main checkout" in main[0].verdict
    assert not _actions(rep, "move")


# ── move safety: dirty blocks, unmerged does not ─────────────────────

def test_unmerged_commits_do_not_make_a_move_unsafe(repo):
    """`git worktree move` carries commits along. Calling this unsafe would send
    the operator off to merge work for no reason."""
    sib = repo.parent / "proj-ahead"
    _git(repo, "worktree", "add", "-q", str(sib), "-b", "ahead-branch")
    (sib / "new.txt").write_text("work")
    _git(sib, "add", "-A")
    _git(sib, "commit", "-qm", "unmerged work")

    rep = pw.audit({"proj": repo})
    row = _rows(rep, "ahead-branch")
    assert row.ahead == 1
    assert row.dirty == 0
    assert row.safe is True

    mv = _actions(rep, "move")[0]
    assert mv.safe is True
    assert "do not delete" in mv.caveat
    assert "commit or stash" not in mv.caveat


def test_dirty_state_makes_a_move_need_care(repo):
    sib = repo.parent / "proj-dirty"
    _git(repo, "worktree", "add", "-q", str(sib), "-b", "dirty-branch")
    (sib / "scratch.txt").write_text("uncommitted")

    rep = pw.audit({"proj": repo})
    row = _rows(rep, "dirty-branch")
    assert row.dirty == 1
    assert row.safe is False

    mv = _actions(rep, "move")[0]
    assert mv.safe is False
    assert "commit or stash" in mv.caveat
    assert "--force" in mv.caveat, "the operator should know why not to force it"


def test_dirty_and_unmerged_reports_both_facts(repo):
    sib = repo.parent / "proj-both"
    _git(repo, "worktree", "add", "-q", str(sib), "-b", "both-branch")
    (sib / "a.txt").write_text("committed")
    _git(sib, "add", "-A")
    _git(sib, "commit", "-qm", "work")
    (sib / "b.txt").write_text("dirty")

    rep = pw.audit({"proj": repo})
    mv = _actions(rep, "move")[0]
    assert "commit or stash" in mv.caveat
    assert "do not delete" in mv.caveat


# ── hash-named worktrees: index, not symlinks, not renaming ─────────

def test_hash_named_worktrees_get_an_index_not_a_rename(repo):
    wt = repo / ".claude" / "worktrees" / "agent-deadbeef1234"
    _git(repo, "worktree", "add", "-q", str(wt), "-b", "feat/readable-name")

    rep = pw.audit({"proj": repo})
    notes = _actions(rep, "rename_note")
    assert len(notes) == 1
    assert notes[0].safe is True
    assert "INDEX.md" in notes[0].why
    assert not _actions(rep, "move"), "these are already in the right place"


def test_index_maps_hashes_back_to_branches(repo):
    wt = repo / ".claude" / "worktrees" / "agent-deadbeef1234"
    _git(repo, "worktree", "add", "-q", str(wt), "-b", "feat/readable-name")
    (wt / "x.txt").write_text("w")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "w")

    text = pw.render_index(repo)
    assert "feat/readable-name" in text
    assert "agent-deadbeef1234" in text
    assert "| 1 |" in text, "ahead count should be shown"
    assert "Do not hand-edit" in text


def test_index_marks_missing_directories(repo, tmp_path):
    gone = tmp_path / "gone-wt"
    _git(repo, "worktree", "add", "-q", str(gone), "-b", "gone-branch")
    subprocess.run(["rm", "-rf", str(gone)], check=True)
    assert "(missing)" in pw.render_index(repo)


def test_layout_proposal_states_the_tmp_rule_and_rejects_symlinks():
    text = pw.LAYOUT_PROPOSAL
    assert ".claude/worktrees" in text
    assert "INDEX.md" in text
    assert "Symlinks were considered and rejected" in text
    assert "/private/tmp" in text


# ── plumbing / degradation ──────────────────────────────────────────

def test_prunable_reason_is_captured_not_just_the_flag(repo, tmp_path):
    gone = tmp_path / "gone-wt"
    _git(repo, "worktree", "add", "-q", str(gone), "-b", "gone-branch")
    subprocess.run(["rm", "-rf", str(gone)], check=True)

    rows = pw.list_worktrees(repo)
    prunable = [r for r in rows if "prunable" in r]
    assert prunable, "git should report this as prunable"
    assert prunable[0]["prunable"], "the reason string must be kept, not dropped"


def test_branch_slug_is_filesystem_safe():
    assert pw._slug("feat/thing_two") == "feat-thing-two"
    assert pw._slug("") == "detached"


def test_default_branch_falls_back_to_master(tmp_path):
    r = tmp_path / "m"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "f").write_text("x")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "i")
    assert pw._default_branch(r) == "master"


def test_audit_of_a_non_repo_is_empty_not_a_crash(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    rep = pw.audit({"x": d})
    assert rep.rows == [] and rep.actions == []


def test_audit_never_mutates_git_state(repo, tmp_path):
    sib = repo.parent / "proj-sib"
    _git(repo, "worktree", "add", "-q", str(sib), "-b", "sib")
    before = subprocess.run(["git", "-C", str(repo), "worktree", "list"],
                            capture_output=True, text=True).stdout

    rep = pw.audit({"proj": repo})
    pw.render_report(rep)
    pw.report_to_dict(rep)

    after = subprocess.run(["git", "-C", str(repo), "worktree", "list"],
                           capture_output=True, text=True).stdout
    assert before == after, "auditing must not change any git state"
    assert sib.is_dir(), "auditing must not move or remove anything"
