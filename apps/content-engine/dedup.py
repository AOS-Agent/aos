"""URL deduplication — track processed URLs to avoid reprocessing.

The ledger is INSTANCE state, not framework state. It lives in ~/.aos/data/
so that the framework directory (~/aos/, read-only at runtime) stays clean.

Historically this wrote to `Path(__file__).parent / "processed_urls.jsonl"`,
which crashed with PermissionError on any release install and meant dedup
silently never worked. Migration 051 relocates the old ledger if present.
"""

import json
import os
from datetime import datetime
from pathlib import Path

# Instance data dir — overridable for tests.
_DEFAULT_DIR = Path.home() / ".aos" / "data" / "content-engine"
DEDUP_DIR = Path(os.environ.get("AOS_CONTENT_ENGINE_DIR", _DEFAULT_DIR))
DEDUP_FILE = DEDUP_DIR / "processed_urls.jsonl"


def is_processed(content_id: str, platform: str) -> bool:
    """Check if a content ID has already been processed."""
    if not DEDUP_FILE.exists():
        return False

    key = f"{platform}:{content_id}"
    for line in DEDUP_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if f"{entry['platform']}:{entry['content_id']}" == key:
                return True
        except (json.JSONDecodeError, KeyError):
            continue
    return False


def mark_processed(url: str, content_id: str, platform: str,
                   tier: str = "deep", vault_path: str = "") -> None:
    """Record a URL as processed.

    Never fatal: a dedup-ledger failure must not lose an extraction that has
    already been written to the vault.
    """
    entry = {
        "url": url,
        "content_id": content_id,
        "platform": platform,
        "tier": tier,
        "vault_path": vault_path,
        "processed_at": datetime.now().isoformat(),
    }

    try:
        DEDUP_DIR.mkdir(parents=True, exist_ok=True)
        with open(DEDUP_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"  Warning: could not record dedup entry ({e}). "
              f"Extraction itself succeeded.")


def get_processed_count() -> int:
    """Get total number of processed URLs."""
    if not DEDUP_FILE.exists():
        return 0
    return sum(1 for line in DEDUP_FILE.read_text().splitlines() if line.strip())
