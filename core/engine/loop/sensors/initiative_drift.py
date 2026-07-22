"""Initiative-drift sensor — flags portfolio hygiene problems.

Scans vault initiative frontmatter (status, updated/date) and writes
signals for: executing-but-stale initiatives, and a portfolio-shape
summary when sprawl crosses the floor. First-party system state —
NOT tainted. Deterministic; no LLM.

The 2026-07-21 audit found 10 initiatives claiming "executing" and 12
parked in stale-review — precisely the pattern this sensor exists to
keep visible.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .. import signals

INITIATIVES_DIR = Path.home() / "vault" / "knowledge" / "initiatives"

STALE_EXECUTING_DAYS = 7
SPRAWL_FLOOR = 8  # signal portfolio shape when active statuses exceed this

_ACTIVE = {"executing", "shaping", "planning", "research"}


def _frontmatter(text: str) -> dict:
    m = re.match(r"\A---\n(.*?)\n---", text, re.DOTALL)
    fields: dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^(\w[\w-]*):\s*[\"']?([^\"'\n]*)[\"']?\s*$", line)
            if kv:
                fields[kv.group(1)] = kv.group(2).strip()
    return fields


def _age_days(path: Path, fm: dict) -> float:
    for key in ("updated", "date"):
        raw = fm.get(key, "")
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        if m:
            try:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - dt).days
            except ValueError:
                pass
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).days


def run(initiatives_dir: Path | None = None) -> list[str]:
    """Scan initiatives, write drift signals. Returns new signal ids."""
    root = initiatives_dir or INITIATIVES_DIR
    if not root.exists():
        return []

    statuses: dict[str, list[str]] = {}
    stale: list[dict] = []
    for path in sorted(root.glob("*.md")):
        fm = _frontmatter(path.read_text(errors="replace"))
        status = (fm.get("status") or "unknown").strip()
        statuses.setdefault(status, []).append(path.name)
        if status == "executing":
            age = _age_days(path, fm)
            if age >= STALE_EXECUTING_DAYS:
                stale.append({"file": path.name, "days_stale": int(age)})

    written: list[str] = []
    for item in stale:
        written.append(
            signals.append_signal(
                sensor="initiative_drift",
                signal_type="stale_executing",
                payload=item,
                source_refs=[f"vault:knowledge/initiatives/{item['file']}"],
                tainted=False,
            )
        )

    active_count = sum(len(v) for s, v in statuses.items() if s in _ACTIVE)
    if active_count > SPRAWL_FLOOR:
        written.append(
            signals.append_signal(
                sensor="initiative_drift",
                signal_type="portfolio_sprawl",
                payload={
                    "active_count": active_count,
                    "by_status": {s: len(v) for s, v in sorted(statuses.items())},
                },
                source_refs=["vault:knowledge/initiatives/"],
                tainted=False,
            )
        )
    return written
