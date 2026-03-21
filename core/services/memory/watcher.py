"""File watcher — auto-reindexes workspace files on change."""

import threading
import time
from pathlib import Path

from watchfiles import watch, Change

from indexer import MemoryIndexer, WORKSPACE, WATCH_GLOBS

# Extensions we care about
WATCH_EXTENSIONS = {".md", ".yaml", ".yml", ".toml"}

# Debounce window in seconds
DEBOUNCE_SECS = 0.5


def _is_watched(path: Path) -> bool:
    """Check if a path matches our watch globs."""
    if path.suffix not in WATCH_EXTENSIONS:
        return False
    try:
        rel = str(path.relative_to(WORKSPACE))
    except ValueError:
        return False
    # Check against each glob pattern
    for glob_pattern in WATCH_GLOBS:
        if list(WORKSPACE.glob(glob_pattern)):
            # Check if this specific file matches
            from fnmatch import fnmatch
            if fnmatch(rel, glob_pattern):
                return True
    return False


def start_watcher(indexer: MemoryIndexer) -> threading.Thread:
    """Start the file watcher as a daemon thread."""

    def _watch_loop():
        pending: dict[str, float] = {}

        for changes in watch(str(WORKSPACE), recursive=True, step=200):
            for change_type, path_str in changes:
                path = Path(path_str)
                if not _is_watched(path):
                    continue
                try:
                    rel = str(path.relative_to(WORKSPACE))
                except ValueError:
                    continue
                pending[rel] = time.monotonic()

            # Process debounced changes
            now = time.monotonic()
            ready = [p for p, t in pending.items() if now - t >= DEBOUNCE_SECS]
            for rel_path in ready:
                try:
                    indexer.reindex_file(rel_path)
                except Exception as e:
                    print(f"[watcher] Error reindexing {rel_path}: {e}")
                del pending[rel_path]

    thread = threading.Thread(target=_watch_loop, daemon=True, name="file-watcher")
    thread.start()
    return thread
