"""Shared context — cross-session decision store for Bridge v2.

Records decisions and facts that persist across sessions. Any session can
read decisions made in other sessions, enabling continuity without requiring
the operator to repeat themselves.

Store location: ~/.aos/data/bridge/shared-context.json
TTL: 30 days (entries older than that are pruned on every load).
Concurrency: last-write-wins (no file locking). Atomic writes via os.replace().
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STORE_DIR = Path.home() / ".aos" / "data" / "bridge"
STORE_FILE = STORE_DIR / "shared-context.json"

TTL_DAYS = 30


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutoff() -> datetime:
    """Datetime threshold — entries older than this are expired."""
    return datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string to a datetime."""
    # Handle both "Z" suffix and "+00:00" offset
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def _is_expired(entry: dict) -> bool:
    """True if the entry's timestamp is older than TTL_DAYS."""
    try:
        return _parse_ts(entry["timestamp"]) < _cutoff()
    except (KeyError, ValueError):
        # Malformed entry — treat as expired so it gets pruned
        return True


def _empty_store() -> dict:
    """Return a fresh, empty store structure."""
    return {"decisions": []}


def _save(data: dict) -> None:
    """Write the store atomically: write to temp file, then os.replace()."""
    try:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(STORE_DIR), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, str(STORE_FILE))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning("Failed to save shared context: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load() -> dict:
    """Read the store from disk and prune expired entries.

    Returns the store dict (always valid, even if the file is missing or
    corrupt). Writes back the pruned version if any entries were removed.
    """
    try:
        if not STORE_FILE.exists():
            return _empty_store()

        raw = STORE_FILE.read_text()
        data = json.loads(raw)

        if not isinstance(data, dict) or "decisions" not in data:
            logger.warning("Shared context file has unexpected structure, resetting")
            return _empty_store()

    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read shared context, starting fresh: %s", e)
        return _empty_store()

    # Prune expired entries
    before = len(data["decisions"])
    data["decisions"] = [d for d in data["decisions"] if not _is_expired(d)]
    after = len(data["decisions"])

    if after < before:
        logger.info("Pruned %d expired decision(s) from shared context", before - after)
        _save(data)

    return data


def add_decision(
    project: str,
    decision: str,
    session: str,
    source: str | None = None,
) -> None:
    """Record a decision in the shared context store.

    Args:
        project:  Project name (e.g. "nuchay", "aos").
        decision: The decision text.
        session:  Session identifier (e.g. "telegram:6679471412").
        source:   Optional provenance (e.g. "initiative:initiative-pipeline").
    """
    try:
        data = load()

        entry: dict = {
            "project": project,
            "decision": decision,
            "session": session,
            "timestamp": _now_iso(),
        }
        if source:
            entry["source"] = source

        data["decisions"].append(entry)
        _save(data)
    except Exception as e:
        logger.warning("Failed to add decision to shared context: %s", e)


def get_decisions(project: str | None = None) -> list[dict]:
    """Return decisions, optionally filtered by project.

    Args:
        project: If provided, only return decisions for this project.

    Returns:
        List of decision dicts, newest first.
    """
    try:
        data = load()
        decisions = data["decisions"]

        if project:
            decisions = [d for d in decisions if d.get("project") == project]

        # Newest first
        decisions.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
        return decisions
    except Exception as e:
        logger.warning("Failed to get decisions from shared context: %s", e)
        return []


def get_context_for_session() -> str:
    """Format all current decisions as a text block for session injection.

    Returns an empty string if there are no decisions, otherwise a
    human-readable summary grouped by project.
    """
    try:
        data = load()
        decisions = data["decisions"]

        if not decisions:
            return ""

        # Group by project
        by_project: dict[str, list[str]] = {}
        for d in decisions:
            proj = d.get("project", "general")
            text = d.get("decision", "")
            if text:
                by_project.setdefault(proj, []).append(text)

        lines = ["Cross-session decisions:"]
        for proj, items in sorted(by_project.items()):
            lines.append(f"  [{proj}]")
            for item in items:
                lines.append(f"    - {item}")

        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to format shared context: %s", e)
        return ""


def prune() -> int:
    """Remove entries older than 30 days. Returns the number of entries removed."""
    try:
        if not STORE_FILE.exists():
            return 0

        raw = STORE_FILE.read_text()
        data = json.loads(raw)

        if not isinstance(data, dict) or "decisions" not in data:
            return 0

        before = len(data["decisions"])
        data["decisions"] = [d for d in data["decisions"] if not _is_expired(d)]
        removed = before - len(data["decisions"])

        if removed > 0:
            _save(data)
            logger.info("Pruned %d expired decision(s)", removed)

        return removed
    except Exception as e:
        logger.warning("Failed to prune shared context: %s", e)
        return 0
