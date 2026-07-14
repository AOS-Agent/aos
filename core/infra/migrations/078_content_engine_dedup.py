"""
Migration 078: Move the content-engine dedup ledger out of the framework dir.

The content engine recorded every processed URL to
`apps/content-engine/processed_urls.jsonl` — i.e. INSIDE the framework tree.
On a release install (~/aos -> ~/aos-releases/<version>/, read-only at runtime)
that path is not writable, so `mark_processed()` raised PermissionError at the
very end of every extraction. The vault note was written first, so nothing was
lost, but the CLI always exited non-zero and dedup never actually worked.

dedup.py now writes to ~/.aos/data/content-engine/processed_urls.jsonl. This
migration creates that directory and folds in any pre-existing ledgers found in
framework trees (the live release, any older releases, and the dev workspace),
deduplicating by platform:content_id so re-runs are safe.

Idempotent: merge is keyed on platform:content_id; re-running adds nothing new.
"""

DESCRIPTION = "Relocate content-engine dedup ledger to ~/.aos/data/"

import json
from pathlib import Path

HOME = Path.home()

DEST_DIR = HOME / ".aos" / "data" / "content-engine"
DEST = DEST_DIR / "processed_urls.jsonl"

LEDGER_RELPATH = Path("apps") / "content-engine" / "processed_urls.jsonl"


def _legacy_ledgers() -> list[Path]:
    """Every framework-tree location a stale ledger could be hiding in."""
    roots = [HOME / "aos", HOME / "project" / "aos"]

    releases = HOME / "aos-releases"
    if releases.is_dir():
        roots.extend(p for p in releases.iterdir() if p.is_dir())

    found, seen = [], set()
    for root in roots:
        candidate = root / LEDGER_RELPATH
        try:
            key = candidate.resolve()
        except OSError:
            continue
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        found.append(candidate)
    return found


def _read_entries(path: Path) -> list[dict]:
    entries = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return entries
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "content_id" in entry and "platform" in entry:
            entries.append(entry)
    return entries


def _key(entry: dict) -> str:
    return f"{entry['platform']}:{entry['content_id']}"


def up() -> None:
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    merged: dict[str, dict] = {}
    for entry in _read_entries(DEST):
        merged[_key(entry)] = entry

    imported = 0
    for legacy in _legacy_ledgers():
        for entry in _read_entries(legacy):
            if _key(entry) not in merged:
                merged[_key(entry)] = entry
                imported += 1

    with open(DEST, "w") as f:
        for entry in merged.values():
            f.write(json.dumps(entry) + "\n")

    print(f"  Dedup ledger at {DEST} ({len(merged)} entries, {imported} imported)")

    # Retire the legacy ledgers we could write to. The live release tree is
    # read-only; leaving a stale file there is harmless now that nothing reads it.
    for legacy in _legacy_ledgers():
        try:
            legacy.rename(legacy.with_suffix(".jsonl.migrated"))
            print(f"  Retired legacy ledger: {legacy}")
        except OSError:
            pass  # read-only release tree — expected, and fine


def check() -> bool:
    """Ledger directory exists and is writable, and dedup.py points at it."""
    if not DEST_DIR.is_dir():
        return False

    probe = DEST_DIR / ".write-probe"
    try:
        probe.touch()
        probe.unlink()
    except OSError:
        return False

    dedup_py = HOME / "aos" / "apps" / "content-engine" / "dedup.py"
    if dedup_py.is_file():
        source = dedup_py.read_text()
        if 'Path(__file__).parent / "processed_urls.jsonl"' in source:
            return False  # framework still shipping the old path

    return True
