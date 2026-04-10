"""Maintenance report writer.

Writes the daily vault maintenance report to ~/vault/log/YYYY-MM-DD-maintenance.md
as a structured markdown document the operator can read in Obsidian or in
the Knowledge UI's Library tab.

Report structure (top to bottom):
    - Frontmatter (title, type=maintenance, date, tags)
    - Summary line (one sentence)
    - Orphans section
    - Stale docs section
    - Topic orientations refreshed (count, list)
    - Synthesis suggestions (full drafted proposals)
    - Errors encountered
    - Stats footer
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

VAULT_DIR = Path.home() / "vault"
LOG_DIR = VAULT_DIR / "log"


def write_report(report: dict[str, Any]) -> Path | None:
    """Write the maintenance report as a markdown file in vault/log/.

    Overwrites any existing report for the same date. Returns the path
    written, or None on failure.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = LOG_DIR / f"{today}-maintenance.md"

    frontmatter = {
        "title": f"Vault maintenance — {today}",
        "type": "maintenance",
        "date": today,
        "tags": ["maintenance", "automated", "lint"],
        "orphan_count": report.get("orphan_count", 0),
        "stale_count": report.get("stale_count", 0),
        "topics_refreshed": report.get("topics_refreshed", 0),
        "synthesis_drafted": report.get("synthesis_drafted", 0),
        "errors": report.get("error_count", 0),
    }

    lines: list[str] = [
        "---",
        yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip(),
        "---",
        "",
    ]

    # Summary line
    summary = _summary_line(report)
    lines.append(summary)
    lines.append("")

    # Orphans
    orphans = report.get("orphans") or []
    if orphans:
        lines.append(f"## Orphans ({len(orphans)})")
        lines.append("")
        lines.append(
            "Stage 3+ documents with no incoming backlinks. Add a link from "
            "another doc or archive if they're no longer relevant."
        )
        lines.append("")
        for o in orphans[:25]:
            lines.append(
                f"- `{o.get('path', '')}` — {o.get('title', '')} "
                f"(stage {o.get('stage', '?')}, {o.get('word_count', 0)} words)"
            )
        if len(orphans) > 25:
            lines.append(f"- _… and {len(orphans) - 25} more_")
        lines.append("")

    # Stale
    stale = report.get("stale") or []
    if stale:
        lines.append(f"## Stale ({len(stale)})")
        lines.append("")
        lines.append(
            "Documents at stage 3+ that haven't been touched in 6+ months. "
            "Either refresh them or promote to stage 5 (decision) / 6 (expertise)."
        )
        lines.append("")
        for s in stale[:25]:
            last = (s.get("last_modified") or "")[:10]
            lines.append(
                f"- `{s.get('path', '')}` — {s.get('title', '')} "
                f"(last modified {last})"
            )
        if len(stale) > 25:
            lines.append(f"- _… and {len(stale) - 25} more_")
        lines.append("")

    # Topics refreshed
    topic_stats = report.get("topic_refresh") or {}
    refreshed = topic_stats.get("topics_refreshed", 0)
    unchanged = topic_stats.get("topics_unchanged", 0)
    if topic_stats.get("topics_scanned"):
        lines.append("## Topic orientations")
        lines.append("")
        lines.append(
            f"Scanned {topic_stats.get('topics_scanned', 0)} topics — "
            f"{refreshed} rewritten, {unchanged} unchanged, "
            f"{topic_stats.get('topics_skipped', 0)} skipped (cooldown)."
        )
        lines.append("")

    # Synthesis suggestions
    synth_stats = report.get("synthesis") or {}
    suggestions = synth_stats.get("suggestions") or []
    if suggestions:
        lines.append(f"## Synthesis suggestions ({len(suggestions)})")
        lines.append("")
        lines.append(
            "Topics with enough captures to merit a stage-3 research doc. "
            "Review and promote the ones you want to investigate further."
        )
        lines.append("")
        for s in suggestions:
            lines.append(f"### {s.get('topic_title', '')} (`{s.get('topic_slug', '')}`)")
            lines.append(f"_{s.get('capture_count', 0)} captures_")
            lines.append("")
            if s.get("central_question"):
                lines.append(f"**Central question:** {s['central_question']}")
                lines.append("")
            sub_qs = s.get("sub_questions") or []
            if sub_qs:
                lines.append("**Sub-questions:**")
                for q in sub_qs:
                    lines.append(f"- {q}")
                lines.append("")
            contrs = s.get("contradictions") or []
            if contrs:
                lines.append("**Contradictions noted:**")
                for c in contrs:
                    lines.append(f"- {c}")
                lines.append("")
            if s.get("proposal"):
                lines.append(f"**Proposal:**")
                lines.append(s["proposal"])
                lines.append("")

    # Errors
    errors = report.get("errors") or []
    if errors:
        lines.append(f"## Errors ({len(errors)})")
        lines.append("")
        for e in errors[:15]:
            lines.append(f"- `{e}`")
        lines.append("")

    # Stats footer
    lines.append("---")
    lines.append("")
    lines.append("_Stats:_")
    stats_line_parts = []
    if report.get("duration_seconds") is not None:
        stats_line_parts.append(f"{report['duration_seconds']}s")
    tokens_in = report.get("tokens_in", 0)
    tokens_out = report.get("tokens_out", 0)
    if tokens_in or tokens_out:
        stats_line_parts.append(f"{tokens_in:,} tokens in")
        stats_line_parts.append(f"{tokens_out:,} tokens out")
    if stats_line_parts:
        lines.append(" · ".join(stats_line_parts))
    lines.append("")

    content = "\n".join(lines)
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.exception("Failed to write maintenance report to %s", path)
        return None

    return path


def _summary_line(report: dict[str, Any]) -> str:
    parts: list[str] = []
    orphans = report.get("orphan_count", 0)
    stale = report.get("stale_count", 0)
    refreshed = report.get("topics_refreshed", 0)
    synth = report.get("synthesis_drafted", 0)

    if orphans:
        parts.append(f"{orphans} orphan{'s' if orphans != 1 else ''}")
    if stale:
        parts.append(f"{stale} stale doc{'s' if stale != 1 else ''}")
    if refreshed:
        parts.append(f"{refreshed} topic orientation{'s' if refreshed != 1 else ''} refreshed")
    if synth:
        parts.append(f"{synth} synthesis suggestion{'s' if synth != 1 else ''} drafted")

    if not parts:
        return "Vault is clean — no maintenance needed today."
    return "Found " + ", ".join(parts) + "."


def find_latest_report() -> Path | None:
    """Return the path to the most recent maintenance report, or None."""
    if not LOG_DIR.is_dir():
        return None
    reports = sorted(LOG_DIR.glob("*-maintenance.md"), reverse=True)
    return reports[0] if reports else None


def list_reports(limit: int = 30) -> list[dict[str, Any]]:
    """List recent maintenance report files with their frontmatter."""
    if not LOG_DIR.is_dir():
        return []
    reports: list[dict[str, Any]] = []
    for p in sorted(LOG_DIR.glob("*-maintenance.md"), reverse=True)[:limit]:
        fm = _read_frontmatter(p)
        reports.append({
            "path": str(p.relative_to(VAULT_DIR)),
            "date": fm.get("date", p.stem.split("-maintenance")[0]),
            "orphan_count": fm.get("orphan_count", 0),
            "stale_count": fm.get("stale_count", 0),
            "topics_refreshed": fm.get("topics_refreshed", 0),
            "synthesis_drafted": fm.get("synthesis_drafted", 0),
        })
    return reports


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    raw = content[3:end].strip()
    try:
        fm = yaml.safe_load(raw)
        return fm if isinstance(fm, dict) else {}
    except yaml.YAMLError:
        return {}
