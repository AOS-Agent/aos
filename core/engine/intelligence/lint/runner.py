"""Maintenance pass runner — orchestrates the overnight lint pipeline.

One entry point: `run_maintenance_pass()`. Optional env knobs:
    AOS_MAINT_SKIP_LLM=1     — skip Sonnet calls (heuristics only)
    AOS_MAINT_MAX_TOPICS=N   — cap topic refresh count
    AOS_MAINT_MAX_SYNTH=N    — cap synthesis draft count
    AOS_MAINT_MODEL=sonnet   — override model (default: sonnet)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceReport:
    started_at: str = ""
    ended_at: str = ""
    duration_seconds: float = 0.0
    orphan_count: int = 0
    stale_count: int = 0
    topics_refreshed: int = 0
    topics_unchanged: int = 0
    synthesis_drafted: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error_count: int = 0
    llm_skipped: bool = False
    report_path: str | None = None

    orphans: list[dict[str, Any]] = field(default_factory=list)
    stale: list[dict[str, Any]] = field(default_factory=list)
    topic_refresh: dict[str, Any] = field(default_factory=dict)
    synthesis: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "orphan_count": self.orphan_count,
            "stale_count": self.stale_count,
            "topics_refreshed": self.topics_refreshed,
            "topics_unchanged": self.topics_unchanged,
            "synthesis_drafted": self.synthesis_drafted,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "error_count": self.error_count,
            "llm_skipped": self.llm_skipped,
            "report_path": self.report_path,
            "orphans": self.orphans,
            "stale": self.stale,
            "topic_refresh": self.topic_refresh,
            "synthesis": self.synthesis,
            "errors": self.errors,
        }


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


async def run_maintenance_pass(
    *,
    skip_llm: bool | None = None,
    model: str | None = None,
    max_topics: int | None = None,
    max_synthesis: int | None = None,
) -> MaintenanceReport:
    """Run the full overnight maintenance pipeline.

    Order:
        1. Refresh the vault_inventory table (cheap)
        2. Detect orphans + stale (SQL only)
        3. Refresh topic orientations (Sonnet, optional)
        4. Draft synthesis suggestions (Sonnet, optional)
        5. Write the daily report to vault/log/
    """
    from datetime import datetime, timezone

    started = time.monotonic()
    started_iso = datetime.now(timezone.utc).isoformat()

    report = MaintenanceReport(started_at=started_iso)

    # Resolve env knobs
    skip_llm_resolved = skip_llm if skip_llm is not None else _env_flag("AOS_MAINT_SKIP_LLM")
    model_resolved = model or os.environ.get("AOS_MAINT_MODEL") or "sonnet"
    max_topics_resolved = max_topics if max_topics is not None else _env_int("AOS_MAINT_MAX_TOPICS", 30)
    max_synthesis_resolved = max_synthesis if max_synthesis is not None else _env_int("AOS_MAINT_MAX_SYNTH", 10)
    report.llm_skipped = skip_llm_resolved

    # ─── 1. Refresh vault inventory ───
    try:
        from ..inventory import scan_vault
        scan_vault()
    except Exception as e:
        logger.exception("vault scan failed")
        report.errors.append(f"scan: {e}")

    # ─── 2. Orphans + stale (cheap, deterministic) ───
    try:
        from .orphans import find_orphans
        report.orphans = find_orphans()
        report.orphan_count = len(report.orphans)
    except Exception as e:
        logger.exception("orphan detection failed")
        report.errors.append(f"orphans: {e}")

    try:
        from .stale import find_stale
        report.stale = find_stale()
        report.stale_count = len(report.stale)
    except Exception as e:
        logger.exception("stale detection failed")
        report.errors.append(f"stale: {e}")

    # ─── 3. Topic orientation refresh (Sonnet) ───
    if not skip_llm_resolved:
        try:
            from .topics_refresh import refresh_topic_orientations
            stats = await refresh_topic_orientations(
                model=model_resolved,
                max_topics=max_topics_resolved,
            )
            report.topic_refresh = stats
            report.topics_refreshed = stats.get("topics_refreshed", 0)
            report.topics_unchanged = stats.get("topics_unchanged", 0)
            report.tokens_in += stats.get("total_tokens_in", 0)
            report.tokens_out += stats.get("total_tokens_out", 0)
            report.errors.extend(stats.get("errors", []))
        except Exception as e:
            logger.exception("topic refresh pass failed")
            report.errors.append(f"topic_refresh: {e}")

    # ─── 4. Synthesis suggestions (Sonnet) ───
    if not skip_llm_resolved:
        try:
            from .synthesis_suggestions import draft_synthesis_suggestions
            stats = await draft_synthesis_suggestions(
                model=model_resolved,
                max_topics=max_synthesis_resolved,
            )
            report.synthesis = stats
            report.synthesis_drafted = stats.get("suggestions_drafted", 0)
            report.tokens_in += stats.get("total_tokens_in", 0)
            report.tokens_out += stats.get("total_tokens_out", 0)
            report.errors.extend(stats.get("errors", []))
        except Exception as e:
            logger.exception("synthesis draft pass failed")
            report.errors.append(f"synthesis: {e}")

    # ─── 5. Write report ───
    report.error_count = len(report.errors)
    try:
        from .report import write_report
        path = write_report(report.to_dict())
        if path:
            report.report_path = str(path)
    except Exception as e:
        logger.exception("report write failed")
        report.errors.append(f"report: {e}")
        report.error_count = len(report.errors)

    ended = time.monotonic()
    report.duration_seconds = round(ended - started, 2)
    report.ended_at = datetime.now(timezone.utc).isoformat()

    logger.info(
        "maintenance pass complete: orphans=%d stale=%d refreshed=%d synth=%d errors=%d duration=%.1fs",
        report.orphan_count, report.stale_count, report.topics_refreshed,
        report.synthesis_drafted, report.error_count, report.duration_seconds,
    )

    return report


def run_sync(**kwargs: Any) -> MaintenanceReport:
    """Synchronous wrapper for the cron entrypoint."""
    return asyncio.run(run_maintenance_pass(**kwargs))


# CLI entrypoint — invoked by the vault-maintenance cron
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="AOS vault maintenance pass")
    p.add_argument("--skip-llm", action="store_true", help="Skip Sonnet calls (heuristics only)")
    p.add_argument("--model", default=None, help="Model override (default: sonnet)")
    p.add_argument("--max-topics", type=int, default=None, help="Cap topic refreshes")
    p.add_argument("--max-synthesis", type=int, default=None, help="Cap synthesis drafts")
    args = p.parse_args()

    report = run_sync(
        skip_llm=args.skip_llm,
        model=args.model,
        max_topics=args.max_topics,
        max_synthesis=args.max_synthesis,
    )

    print(f"\n[vault-maintenance] done in {report.duration_seconds:.1f}s")
    print(f"  orphans:        {report.orphan_count}")
    print(f"  stale:          {report.stale_count}")
    print(f"  topics refreshed: {report.topics_refreshed}")
    print(f"  synthesis drafts: {report.synthesis_drafted}")
    print(f"  errors:         {report.error_count}")
    print(f"  tokens:         {report.tokens_in:,} in / {report.tokens_out:,} out")
    if report.report_path:
        print(f"  report:         {report.report_path}")

    exit(1 if report.error_count > 0 and report.orphan_count == 0 and report.stale_count == 0 else 0)
