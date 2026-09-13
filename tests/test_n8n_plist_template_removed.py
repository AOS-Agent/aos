"""
config/launchagents/com.aos.n8n.plist.template — confirmed already gone
(aos#236.10).

Dangling-wires audit (2026-09-13): "com.aos.n8n.plist.template — nothing
references it; n8n retired by 109." Investigation found this was already
resolved before this task started: commit 6c59294/448d147 ("Retire
half-baked services: companion, listen, n8n, slack-watch", aos#208 sweep)
deleted the template from the framework tree in the same change that
shipped migration 109 (which retires the instance-side venv, state.yaml
entry, and any deployed LaunchAgent). No action was needed here — this test
just locks the phantom from silently coming back.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_n8n_plist_template_does_not_exist():
    assert not (REPO_ROOT / "config" / "launchagents" / "com.aos.n8n.plist.template").exists()


def test_nothing_references_com_aos_n8n_outside_historical_migrations():
    """com.aos.n8n may still be named in migration 109's own docstring/code
    (it is the migration that retires it), in tests pinning that migration's
    behavior, and in CHANGELOG.md (a narrative history, same spirit as a
    migration docstring) — those are history, not live wiring. Nothing else
    should reference it."""
    exempt_files = {REPO_ROOT / "CHANGELOG.md"}
    hits = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__", "node_modules"} for part in path.parts):
            continue
        if path in exempt_files:
            continue
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[0] in ("core",) and rel.parts[1:2] == ("infra",) and "migrations" in rel.parts:
            continue
        if rel.parts[0] == "tests":
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "com.aos.n8n" in text:
            hits.append(str(rel))
    assert hits == [], f"com.aos.n8n referenced outside migrations/tests/CHANGELOG: {hits}"
