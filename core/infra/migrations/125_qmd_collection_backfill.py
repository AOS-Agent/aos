"""
Migration 125: QMD collection backfill — curated collection set.

install.sh's `prereq_qmd` (see `prereq_qmd()` in `install.sh`) installs the
`qmd` binary and stops there — nothing in the framework has ever registered a
collection. On the primary reference machine that gap was papered over by
hand, one `qmd collection add` at a time, years apart. A second-operator
machine that never got that manual attention (see aos#237, the faisal-mini
parity audit) ends up with `qmd status` reporting zero collections, or — if
the `deployment_health` reconcile check's `_fix_qmd_collection()` got there
first — a single ad-hoc flat `vault` collection covering the whole vault with
no per-area boundaries. Neither matches what a reference machine actually
runs: six curated, purpose-described collections (`qmd collection show
<name>` on this machine, 2026-09-13):

    log          -> ~/vault/log
    knowledge    -> ~/vault/knowledge
    skills       -> ~/.claude/skills
    skills-core  -> ~/aos/core/skills
    agents       -> ~/aos/core/agents
    aos-docs     -> ~/aos/docs

This migration backfills only what is MISSING from that set, one `qmd
collection add <name> <path> --pattern "**/*.md"` per gap. It never adds,
removes, or renames a collection outside _aos_collections() below — an
operator's own collections (a hand-added flat `vault`, a business knowledge
base, anything else) are not this migration's to touch, on the same principle
122 applied to fleet.yaml and 021 applied when it pruned only its own named
stale entries.

A collection whose declared source directory does not exist on this machine
(e.g. no ~/aos/docs on a partial install) is skipped rather than blocking the
migration forever — it becomes addable once that directory shows up, on a
later run.

Triggers one `qmd update && qmd embed` reindex at the end, but only if
something was actually added — a replay that changes nothing must not pay for
a reindex either. This is the same pair the `qmd-reindex` cron
(`core/bin/crons/qmd-reindex`) already runs on a schedule; config/crons.yaml
already declares that job framework-wide, so there is no separate scheduler
gate here to fix (verified against a live second-operator log, aos#237).

Idempotent: check() passes once every collection whose source path exists is
registered. A second run (or the periodic reconcile) finds nothing to add.
"""

from __future__ import annotations

DESCRIPTION = "QMD collection backfill — ensure the curated AOS collection set exists"

import subprocess
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _qmd() -> Path:
    return Path.home() / ".bun" / "bin" / "qmd"


def _aos_collections() -> dict[str, Path]:
    """name -> framework-declared source path. Read off a reference machine via
    `qmd collection show <name>` — see this module's docstring. Adding a new
    framework collection means adding it here, in the same commit."""
    home = Path.home()
    return {
        "log": home / "vault" / "log",
        "knowledge": home / "vault" / "knowledge",
        "skills": home / ".claude" / "skills",
        "skills-core": home / "aos" / "core" / "skills",
        "agents": home / "aos" / "core" / "agents",
        "aos-docs": home / "aos" / "docs",
    }


def _addable() -> dict[str, Path]:
    """`_aos_collections()` entries whose source directory exists here."""
    return {name: path for name, path in _aos_collections().items() if path.exists()}


def _collection_exists(name: str) -> bool:
    try:
        result = subprocess.run(
            [str(_qmd()), "collection", "show", name],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return False
    return result.returncode == 0


def check() -> bool:
    """Applied once every addable collection is registered."""
    if not _qmd().exists():
        return True  # qmd not installed — nothing for this migration to do
    return all(_collection_exists(name) for name in _addable())


def up() -> bool:
    if not _qmd().exists():
        print("       qmd not installed — skipping collection backfill")
        return True

    for name, path in _aos_collections().items():
        if name not in _addable():
            print(f"       Skipping '{name}': {path} does not exist yet")

    added = []
    for name, path in _addable().items():
        if _collection_exists(name):
            continue
        try:
            result = subprocess.run(
                [str(_qmd()), "collection", "add", name, str(path), "--pattern", "**/*.md"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            print(f"       Failed to add collection '{name}': {e}")
            continue
        if result.returncode == 0:
            added.append(name)
            print(f"       Added QMD collection: {name} -> {path}")
        else:
            print(f"       Failed to add collection '{name}': {result.stderr.strip()}")

    if added:
        try:
            subprocess.run([str(_qmd()), "update"], capture_output=True, timeout=180)
            subprocess.run([str(_qmd()), "embed"], capture_output=True, timeout=300)
            print(f"       Reindexed after adding: {', '.join(added)}")
        except Exception as e:
            print(f"       Reindex after backfill failed (non-fatal): {e}")

    return True


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 125 already applied" if check() else ("Done" if up() else "Failed"))
