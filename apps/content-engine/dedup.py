"""URL deduplication — track processed URLs to avoid reprocessing."""

import json
from datetime import datetime
from pathlib import Path

DEDUP_FILE = Path(__file__).parent / "processed_urls.jsonl"


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
    """Record a URL as processed."""
    entry = {
        "url": url,
        "content_id": content_id,
        "platform": platform,
        "tier": tier,
        "vault_path": vault_path,
        "processed_at": datetime.now().isoformat(),
    }

    with open(DEDUP_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_processed_count() -> int:
    """Get total number of processed URLs."""
    if not DEDUP_FILE.exists():
        return 0
    return sum(1 for line in DEDUP_FILE.read_text().splitlines() if line.strip())
