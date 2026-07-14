"""People Intelligence CLI.

Invoke via ``python3 -m core.engine.people.intel.cli <command>``.

Commands:

    coverage                     Show which adapters are registered + available
    extract [options]            Run extraction and persist to signal_store
    stats                        Signal store row counts
    show <person_id>             Print stored signals for one person
    list-adapters                List registered adapters

The CLI uses stdlib only (argparse) so it runs from system Python without
venv activation — important for AOS 4am update cycle and troubleshooting
sessions.

Privacy note: output is intentionally aggregate-first. The ``show``
command prints per-person signal details on request, but ``extract`` and
``coverage`` never dump raw names or message content.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path

from .extractor import SignalExtractor

logger = logging.getLogger(__name__)

DEFAULT_PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"


# ── Formatting helpers (plain text, no deps) ────────────────────────

def _fmt_pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{round(100 * n / total)}%"


def _hr(char: str = "─", width: int = 60) -> str:
    return char * width


def _print_header(text: str) -> None:
    print(text)
    print(_hr())


# ── Commands ─────────────────────────────────────────────────────────

def cmd_coverage(args: argparse.Namespace) -> int:
    """Print adapter registration + availability."""
    ex = SignalExtractor(db_path=args.db)
    report = ex.coverage_report()

    _print_header("People Intelligence — adapter coverage")
    print(f"Registered: {report['total_count']}")
    print(f"Available:  {report['available_count']}")
    print(f"Coverage:   {_fmt_pct(report['available_count'], report['total_count'])}")
    print()

    print("Signal types covered:")
    for st in sorted(report["signal_types_covered"]):
        print(f"  ✓ {st}")
    if report["signal_types_missing"]:
        print("Signal types missing:")
        for st in sorted(report["signal_types_missing"]):
            print(f"  ✗ {st}")
    print()

    print(f"{'ADAPTER':<22}{'PLATFORM':<10}{'AVAILABLE':<12}SIGNALS")
    print(_hr())
    for detail in sorted(
        report["available"] + report["unavailable"], key=lambda d: d["name"]
    ):
        mark = "yes" if detail["available"] else "no"
        signals = ",".join(detail["signal_types"])
        print(f"{detail['name']:<22}{detail['platform']:<10}{mark:<12}{signals}")

    return 0


def cmd_list_adapters(args: argparse.Namespace) -> int:
    """Print registered adapters as JSON (machine-readable)."""
    ex = SignalExtractor(db_path=args.db)
    report = ex.coverage_report()
    items = []
    for d in report["available"] + report["unavailable"]:
        items.append(
            {
                "name": d["name"],
                "display_name": d.get("display_name", d["name"]),
                "available": d["available"],
                "platform": d["platform"],
                "signal_types": d["signal_types"],
            }
        )
    print(json.dumps(items, indent=2))
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Run signal extraction."""
    ex = SignalExtractor(db_path=args.db)

    adapter_names = None
    if args.adapters:
        adapter_names = [a.strip() for a in args.adapters.split(",") if a.strip()]

    person_ids = None
    if args.person:
        person_ids = [args.person]

    print(
        "Starting extraction "
        f"(limit={args.limit or 'none'}, "
        f"adapters={adapter_names or 'all'}, "
        f"dry_run={args.dry_run})"
    )
    print()

    report = ex.run(
        person_ids=person_ids,
        limit=args.limit,
        adapter_names=adapter_names,
        dry_run=args.dry_run,
    )

    _print_header("Extraction complete")
    print(f"Duration:          {report.duration_seconds}s")
    print(f"Persons indexed:   {report.persons_indexed}")
    print(f"Persons extracted: {report.persons_extracted} "
          f"({_fmt_pct(report.persons_extracted, report.persons_indexed)})")
    print(f"Sources used:      {len(report.sources_used)}")
    print(f"Sources skipped:   {len(report.sources_skipped)}")
    print(f"Errors:            {len(report.errors)}")
    print(f"Mode:              {'dry-run' if report.dry_run else 'persisted'}")
    print()

    if report.per_source_persons:
        print(f"{'SOURCE':<22}{'PERSONS':>10}")
        print(_hr())
        for src in sorted(
            report.per_source_persons.keys(),
            key=lambda k: -report.per_source_persons[k],
        ):
            count = report.per_source_persons[src]
            print(f"{src:<22}{count:>10}")
        print()

    if report.errors:
        print("Errors:")
        for e in report.errors:
            adapter = e.get("adapter", "?")
            err = e.get("error", "?")
            pid = e.get("person_id")
            if pid:
                print(f"  [{adapter}] {pid}: {err}")
            else:
                print(f"  [{adapter}] {err}")
        print()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))

    return 1 if report.errors and not report.sources_used else 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Print signal store row counts."""
    ex = SignalExtractor(db_path=args.db)
    s = ex.stats()

    _print_header("Signal store — row counts")
    print(f"Total rows:        {s.get('total_rows', 0)}")
    print(f"Distinct persons:  {s.get('distinct_persons', 0)}")
    print()

    by_source = s.get("by_source") or {}
    if by_source:
        print(f"{'SOURCE':<22}{'ROWS':>10}")
        print(_hr())
        for src in sorted(by_source.keys(), key=lambda k: -by_source[k]):
            print(f"{src:<22}{by_source[src]:>10}")
    else:
        print("(no signals stored yet — run `extract` first)")

    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print stored signals for one person."""
    ex = SignalExtractor(db_path=args.db)
    signals = ex.get_person_signals(args.person_id)
    if signals is None:
        print(f"No signals found for {args.person_id}", file=sys.stderr)
        return 1

    _print_header(f"Signals for {args.person_id}")
    print(f"Name:           {signals.person_name or '(unknown)'}")
    print(f"Sources:        {', '.join(sorted(signals.source_coverage)) or '(none)'}")
    print(f"Extracted at:   {signals.extracted_at or '(unknown)'}")
    print()

    print("Aggregates:")
    print(f"  Messages:       {signals.total_messages}")
    print(f"  Calls:          {signals.total_calls}")
    print(f"  Photos:         {signals.total_photos}")
    print(f"  Emails:         {signals.total_emails}")
    print(f"  Active channels: {signals.channel_count} "
          f"({', '.join(signals.channels_active) or 'none'})")
    print()

    print("Signal breakdown by type:")
    print(f"  communication:       {len(signals.communication)}")
    print(f"  voice:               {len(signals.voice)}")
    print(f"  physical_presence:   {len(signals.physical_presence)}")
    print(f"  professional:        {len(signals.professional)}")
    print(f"  group_membership:    {len(signals.group_membership)}")
    print(f"  mentions:            {len(signals.mentions)}")
    print(f"  metadata:            {len(signals.metadata)}")

    if args.json:
        from dataclasses import asdict
        print()
        print(json.dumps(asdict(signals), indent=2, default=str))

    return 0


# ── Phase 4 commands — profile / classify / tiers / correct ─────────


def _get_runner(db_path: str | None):
    """Lazy import to keep the extractor-only path free of Phase 4 deps."""
    from .runner import ClassifierRunner
    return ClassifierRunner(db_path=db_path)


def cmd_profile(args: argparse.Namespace) -> int:
    """Print a compiled PersonProfile for one person."""
    from .profiler import ProfileBuilder
    builder = ProfileBuilder(args.db)
    profile = builder.build(args.person_id)
    if profile is None:
        print(f"No profile for {args.person_id} (no stored signals)", file=sys.stderr)
        return 1

    _print_header(f"Profile for {args.person_id}")
    print(f"Name:             {profile.person_name or '(unknown)'}")
    print(f"Sources covered:  {', '.join(sorted(profile.source_coverage)) or '(none)'}")
    print(f"Extracted at:     {profile.extracted_at or '(unknown)'}")
    print()

    print("Aggregates:")
    print(f"  Messages:         {profile.total_messages}")
    print(f"  Calls:            {profile.total_calls}")
    print(f"  Photos:           {profile.total_photos}")
    print(f"  Emails:           {profile.total_emails}")
    print(f"  Mentions:         {profile.total_mentions}")
    print()

    print("Channel diversity:")
    print(f"  Active channels:  {', '.join(profile.channels_active) or 'none'}")
    print(f"  Channel count:    {profile.channel_count}")
    print(f"  Multi-channel:    {profile.is_multi_channel}")
    print()

    print("Temporal:")
    print(f"  First:            {profile.first_interaction_date or '(unknown)'}")
    print(f"  Last:             {profile.last_interaction_date or '(unknown)'}")
    print(f"  Days since last:  {profile.days_since_last if profile.days_since_last is not None else '(unknown)'}")
    print(f"  Span:             {profile.span_years} years")
    print(f"  Dominant pattern: {profile.dominant_pattern}")
    print()

    print("Density:")
    print(f"  Score:            {profile.density_score}")
    print(f"  Rank:             {profile.density_rank}")
    print()

    print("Metadata:")
    print(f"  Richness score:   {profile.metadata_richness}")
    print(f"  Has birthday:     {profile.has_birthday}")
    print(f"  Has address:      {profile.has_physical_address}")
    print(f"  Has related:      {profile.has_related_names}")
    print()

    if profile.circles:
        print("Circles:")
        for c in profile.circles[:10]:
            name = c.get("name", "?")
            ctype = c.get("type", "")
            conf = c.get("confidence", 0)
            print(f"  - {name} ({ctype}, conf={conf:.2f})")
    else:
        print("Circles: (none detected)")

    if args.json:
        from dataclasses import asdict
        print()
        print(json.dumps(asdict(profile), indent=2, default=str))

    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Run the classification pipeline."""
    runner = _get_runner(args.db)

    adapter_names = None  # classify uses all adapters via the extractor upstream

    person_ids = None
    if args.person:
        person_ids = [args.person]

    print(
        f"Starting classification "
        f"(limit={args.limit or 'none'}, "
        f"with_llm={args.with_llm}, "
        f"budget=${args.budget:.2f}, "
        f"dry_run={args.dry_run})"
    )
    print()

    try:
        report = asyncio.run(
            runner.run(
                person_ids=person_ids,
                limit=args.limit,
                with_llm=args.with_llm,
                max_budget_usd=args.budget,
                dry_run=args.dry_run,
                llm_model=args.model,
            )
        )
    except Exception as e:
        logger.exception("classify run failed")
        print(f"Error: {e}", file=sys.stderr)
        return 2

    _print_header("Classification complete")
    print(f"Duration:            {report.duration_seconds}s")
    print(f"Persons profiled:    {report.persons_profiled}")
    print(f"Rule classifications: {report.rule_classifications}")
    if report.with_llm:
        print(f"LLM classifications:  {report.llm_classifications}")
        print(f"LLM errors:           {report.llm_errors}")
        print(f"Estimated spend:      ${report.estimated_cost_usd:.4f}")
        print(f"Budget cap:           ${report.budget_usd:.2f}")
    print(f"Persisted:           {report.persisted}")
    print(f"Mode:                {'dry-run' if report.dry_run else 'persisted'}")
    if report.aborted_reason:
        print(f"ABORTED:             {report.aborted_reason}")
    print()

    if report.tier_distribution:
        print(f"{'TIER':<22}{'COUNT':>10}")
        print(_hr())
        for tier in sorted(
            report.tier_distribution.keys(),
            key=lambda k: -report.tier_distribution[k],
        ):
            print(f"{tier:<22}{report.tier_distribution[tier]:>10}")
        print()

    if report.errors:
        print("Errors (first 10):")
        for e in report.errors[:10]:
            pid = e.get("person_id", "?")
            err = e.get("error", "?")
            print(f"  {pid}: {err}")
        if len(report.errors) > 10:
            print(f"  ... and {len(report.errors) - 10} more")
        print()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))

    return 0 if not report.aborted_reason else 1


def cmd_tiers(args: argparse.Namespace) -> int:
    """Print the tier distribution — aggregate counts only."""
    runner = _get_runner(args.db)
    dist = runner.tier_distribution()

    _print_header("Tier distribution")
    if not dist:
        print("(no classifications stored yet — run `classify` first)")
        return 0

    total = sum(dist.values())
    print(f"Total classifications: {total}")
    print()
    print(f"{'TIER':<22}{'COUNT':>8}{'%':>8}")
    print(_hr())
    for tier in sorted(dist.keys(), key=lambda k: -dist[k]):
        count = dist[tier]
        pct = _fmt_pct(count, total)
        print(f"{tier:<22}{count:>8}{pct:>8}")
    return 0


def cmd_correct(args: argparse.Namespace) -> int:
    """Record an operator correction for one person's classification."""
    from .taxonomy import Tier

    runner = _get_runner(args.db)

    new_tier: Tier | None = None
    if args.tier:
        new_tier = Tier.from_str(args.tier)
        if new_tier == Tier.UNKNOWN and args.tier.lower() != "unknown":
            print(
                f"Unknown tier: {args.tier}. Valid: "
                + ", ".join(t.value for t in Tier),
                file=sys.stderr,
            )
            return 2

    new_tags: list[dict] | None = None
    if args.tags:
        # "tag1,tag2,tag3" → each with confidence 1.0
        new_tags = [
            {"tag": t.strip(), "confidence": 1.0}
            for t in args.tags.split(",")
            if t.strip()
        ]

    if new_tier is None and new_tags is None:
        print("At least one of --tier or --tags must be provided", file=sys.stderr)
        return 2

    try:
        result = runner.record_correction(
            args.person_id,
            new_tier=new_tier,
            new_tags=new_tags,
            notes=args.notes or "",
        )
    except Exception as e:
        logger.exception("correction failed")
        print(f"Error: {e}", file=sys.stderr)
        return 2

    _print_header(f"Correction recorded for {args.person_id}")
    print(f"Tier:    {result.tier.value}")
    if result.context_tags:
        tags = ", ".join(
            f"{t['tag']}({t['confidence']:.2f})" for t in result.context_tags
        )
        print(f"Tags:    {tags}")
    else:
        print("Tags:    (none)")
    if args.notes:
        print(f"Notes:   {args.notes}")
    return 0


def cmd_show_classification(args: argparse.Namespace) -> int:
    """Print the current classification for one person."""
    runner = _get_runner(args.db)
    result = runner.get_classification(args.person_id)
    if result is None:
        print(
            f"No classification for {args.person_id} (run `classify` first)",
            file=sys.stderr,
        )
        return 1

    _print_header(f"Classification for {args.person_id}")
    print(f"Tier:     {result.tier.value}")
    print(f"Model:    {result.model or '(rule-only)'}")
    print(f"Run ID:   {result.run_id}")
    print(f"Created:  {result.created_at}")

    if result.context_tags:
        print("Tags:")
        for t in result.context_tags:
            print(f"  - {t['tag']} ({t['confidence']:.2f})")
    else:
        print("Tags: (none)")

    if result.reasoning:
        print(f"Reasoning: {result.reasoning}")

    if args.json:
        print()
        print(json.dumps(result.to_dict(), indent=2))
    return 0


# ── Nudges ───────────────────────────────────────────────────────────


def _nudge_db(args: argparse.Namespace) -> "object":  # noqa: F821
    """Open a sqlite connection to the people DB; raise SystemExit on missing."""
    import sqlite3
    db_path = Path(args.db) if args.db else DEFAULT_PEOPLE_DB
    if not db_path.exists():
        print(f"error: people.db not found at {db_path}", file=sys.stderr)
        raise SystemExit(2)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def cmd_nudges_generate(args: argparse.Namespace) -> int:
    """Run all nudge generators against people.db (idempotent)."""
    from . import nudges as nudges_mod

    conn = _nudge_db(args)
    try:
        counts = nudges_mod.generate_all(conn)
    finally:
        conn.close()

    _print_header("People Intelligence — nudges generated")
    total = sum(counts.values())
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print(f"  total inserted: {total}")
    return 0


def cmd_nudges_list(args: argparse.Namespace) -> int:
    """List currently-live pending nudges."""
    from . import nudges as nudges_mod

    conn = _nudge_db(args)
    try:
        live = nudges_mod.list_live_nudges(conn, limit=args.limit)
    finally:
        conn.close()

    if not live:
        print("No live nudges.")
        return 0

    _print_header(f"People Intelligence — live nudges ({len(live)})")
    import time as _time
    now = int(_time.time())
    for n in live:
        age = _fmt_age(max(0, now - int(n["created_at"] or now)))
        print(
            f"  {n['id']}  [{n['surface_type']:9}] p{n['priority']} "
            f"({age} old)  {n['content']}"
        )
    return 0


def cmd_compile_profile(args: argparse.Namespace) -> int:
    """Compile a single person profile and write to vault + profile_versions."""
    from core.engine.people import profile as profile_mod

    conn = profile_mod.open_combined()
    try:
        prof = profile_mod.compile_profile(args.person_id, conn=conn)
        if prof is None:
            print(f"error: person {args.person_id} not found", file=sys.stderr)
            return 1

        if args.json:
            import json as _json
            print(_json.dumps(prof.to_dict(), indent=2, default=str))
            return 0

        if args.print_only:
            print(profile_mod.render_markdown(prof))
            return 0

        vault_path = profile_mod.persist(
            prof, conn,
            write_vault=not args.no_vault,
            trigger="cli",
        )
        print(f"✓ Compiled profile for {prof.basics.get('canonical_name')} ({args.person_id})")
        if vault_path:
            print(f"  vault: {vault_path}")
        return 0
    finally:
        conn.close()


def cmd_compile_profiles(args: argparse.Namespace) -> int:
    """Bulk-compile profiles by tier or importance filter."""
    from core.engine.people import profile as profile_mod

    only_tiers = args.tier.split(",") if args.tier else None
    counts = profile_mod.compile_all(
        only_tiers=only_tiers,
        only_importance_at_most=args.importance_at_most,
        write_vault=not args.no_vault,
        trigger="cli-bulk",
    )

    _print_header("Profile compiler — bulk run")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return 0


def cmd_nudge_done(args: argparse.Namespace) -> int:
    """Mark a nudge as actioned by id."""
    from . import nudges as nudges_mod

    conn = _nudge_db(args)
    try:
        ok = nudges_mod.mark_actioned(conn, args.nudge_id)
    finally:
        conn.close()
    if ok:
        print(f"✓ {args.nudge_id} marked as acted")
        return 0
    print(f"✗ {args.nudge_id} not found or not pending", file=sys.stderr)
    return 1


# ── Dashboard + Hygiene commands ──────────────────────────────────────


def _resolve_db(args: argparse.Namespace) -> Path:
    """Resolve the people.db path from args or default."""
    if args.db:
        return Path(args.db)
    return DEFAULT_PEOPLE_DB


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Print a People Intelligence dashboard summary."""
    db_path = _resolve_db(args)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Tier distribution
    tiers = conn.execute("""
        SELECT pc.tier, COUNT(*) as count
        FROM person_classification pc
        JOIN people p ON pc.person_id = p.id
        WHERE p.is_archived = 0
        GROUP BY pc.tier ORDER BY count DESC
    """).fetchall()

    # Inner circle
    inner = conn.execute("""
        SELECT p.canonical_name, COALESCE(pc.tier, 'n/a') as tier,
               CASE WHEN p.pinned_importance IS NOT NULL THEN 'PIN' ELSE '' END as pin
        FROM people p
        LEFT JOIN person_classification pc ON p.id = pc.person_id
        WHERE p.importance = 1 AND p.is_self = 0 AND p.is_archived = 0
        ORDER BY p.canonical_name
    """).fetchall()

    # Coverage
    coverage = conn.execute("""
        SELECT source_name, COUNT(DISTINCT person_id) as people
        FROM signal_store GROUP BY source_name ORDER BY people DESC
    """).fetchall()

    # Hygiene summary
    hygiene = conn.execute("""
        SELECT action_type, COUNT(*) as count
        FROM hygiene_queue WHERE status = 'pending'
        GROUP BY action_type
    """).fetchall()

    # Stats
    total = conn.execute("SELECT COUNT(*) FROM people WHERE is_archived = 0").fetchone()[0]
    with_signals = conn.execute(
        "SELECT COUNT(DISTINCT person_id) FROM signal_store"
    ).fetchone()[0]
    pinned = conn.execute(
        "SELECT COUNT(*) FROM people WHERE pinned_importance IS NOT NULL AND is_archived = 0"
    ).fetchone()[0]

    conn.close()

    print("=" * 60)
    print("  PEOPLE INTELLIGENCE DASHBOARD")
    print("=" * 60)
    print(f"\n  People: {total} active | {with_signals} with signals | {pinned} pinned")

    print(f"\n  Tier Distribution:")
    for row in tiers:
        bar = "#" * min(row["count"] // 5, 40)
        print(f"    {row['tier']:15s} {row['count']:4d}  {bar}")

    print(f"\n  Inner Circle ({len(inner)}):")
    for row in inner:
        pin = f" [{row['pin']}]" if row["pin"] else ""
        print(f"    {row['canonical_name']}{pin}")

    print(f"\n  Signal Coverage:")
    for row in coverage:
        print(f"    {row['source_name']:20s} {row['people']:4d} people")

    if hygiene:
        print(f"\n  Hygiene Queue (pending):")
        for row in hygiene:
            print(f"    {row['action_type']:15s} {row['count']:4d}")

    print()
    return 0


def cmd_hygiene_stats(args: argparse.Namespace) -> int:
    """Print hygiene queue statistics."""
    db_path = _resolve_db(args)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    from ..hygiene import HygieneEngine
    h = HygieneEngine(conn)
    stats = h.queue_stats()
    conn.close()

    if not stats:
        print("Hygiene queue is empty.")
        return 0

    print(f"{'Status':12s} {'Type':15s} {'Count':>6s} {'Avg Conf':>9s}")
    print("-" * 45)
    for row in stats:
        print(f"{row['status']:12s} {row['action_type']:15s} {row['count']:6d} {row['avg_conf'] or 0:9.2f}")
    return 0


def cmd_hygiene_approve(args: argparse.Namespace) -> int:
    """Bulk approve hygiene items above confidence threshold."""
    db_path = _resolve_db(args)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    from ..hygiene import HygieneEngine
    h = HygieneEngine(conn)
    result = h.bulk_process(min_confidence=args.min_confidence)
    conn.close()

    print(f"Processed: {result['processed']}, Skipped: {result['skipped']}, Errors: {result['errors']}")
    return 0


def cmd_hygiene_scan(args: argparse.Namespace) -> int:
    """Run hygiene scans (duplicates + lifecycle candidates)."""
    db_path = _resolve_db(args)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    from ..hygiene import HygieneEngine
    h = HygieneEngine(conn)

    print("Running batch dedup...")
    dedup = h.batch_dedup()
    print(f"  Candidates: {dedup['candidates']}, Auto-merged: {dedup['auto_merged']}, Queued: {dedup['queued_for_review']}")

    print("Scanning lifecycle candidates...")
    candidates = h.scan_lifecycle_candidates()
    print(f"  Found: {len(candidates)} candidates for archival")

    conn.close()
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    """Pin a person's importance level."""
    db_path = _resolve_db(args)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Resolve person by ID or name
    person = conn.execute(
        "SELECT id, canonical_name FROM people WHERE id = ? OR canonical_name LIKE ?",
        (args.person, f"%{args.person}%"),
    ).fetchone()

    if not person:
        print(f"Person not found: {args.person}", file=sys.stderr)
        conn.close()
        return 1

    import time
    conn.execute(
        "UPDATE people SET pinned_importance = ?, importance = ?, updated_at = ? WHERE id = ?",
        (args.importance, args.importance, int(time.time()), person["id"]),
    )
    conn.commit()
    conn.close()
    print(f"Pinned {person['canonical_name']} at importance={args.importance}")
    return 0


def cmd_lifecycle(args: argparse.Namespace) -> int:
    """Set a person's lifecycle state."""
    db_path = _resolve_db(args)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    person = conn.execute(
        "SELECT id, canonical_name FROM people WHERE id = ? OR canonical_name LIKE ?",
        (args.person, f"%{args.person}%"),
    ).fetchone()

    if not person:
        print(f"Person not found: {args.person}", file=sys.stderr)
        conn.close()
        return 1

    import time
    now = int(time.time())
    updates = {"lifecycle_state": args.state, "updated_at": now}
    if args.state == "archived":
        updates["is_archived"] = 1
    elif args.state == "active":
        updates["is_archived"] = 0

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE people SET {set_clause} WHERE id = ?",
        list(updates.values()) + [person["id"]],
    )
    conn.commit()
    conn.close()
    print(f"Set {person['canonical_name']} lifecycle → {args.state}")
    return 0


# ── Parser ───────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m core.engine.people.intel.cli",
        description="People Intelligence — extract typed signals from local sources",
    )
    parser.add_argument(
        "--db",
        help="Override people.db path (defaults to ~/.aos/data/people.db)",
        default=None,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    sub = parser.add_subparsers(dest="command", required=True)

    # coverage
    sp = sub.add_parser("coverage", help="Show adapter registration + availability")
    sp.set_defaults(func=cmd_coverage)

    # list-adapters (machine-readable)
    sp = sub.add_parser("list-adapters", help="List registered adapters as JSON")
    sp.set_defaults(func=cmd_list_adapters)

    # extract
    sp = sub.add_parser("extract", help="Run signal extraction")
    sp.add_argument("--limit", type=int, default=None, help="Cap persons extracted")
    sp.add_argument("--person", default=None, help="Extract only for a single person_id")
    sp.add_argument(
        "--adapters",
        default=None,
        help="Comma-separated list of adapter names to run (default: all available)",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="Run extraction but do not persist to signal_store",
    )
    sp.add_argument(
        "--json",
        action="store_true",
        help="Also print the full run report as JSON",
    )
    sp.set_defaults(func=cmd_extract)

    # stats
    sp = sub.add_parser("stats", help="Signal store row counts")
    sp.set_defaults(func=cmd_stats)

    # show <person_id>
    sp = sub.add_parser("show", help="Print stored signals for one person")
    sp.add_argument("person_id", help="Person ID (e.g. p_xyz123)")
    sp.add_argument("--json", action="store_true", help="Print full signals as JSON")
    sp.set_defaults(func=cmd_show)

    # ── Phase 4 commands ──

    # profile <person_id>
    sp = sub.add_parser(
        "profile", help="Print compiled profile for one person (Phase 4)"
    )
    sp.add_argument("person_id", help="Person ID")
    sp.add_argument("--json", action="store_true", help="Print full profile as JSON")
    sp.set_defaults(func=cmd_profile)

    # classify
    sp = sub.add_parser(
        "classify",
        help="Run the classification pipeline (rule-based + optional LLM)",
    )
    sp.add_argument("--limit", type=int, default=None, help="Cap persons classified")
    sp.add_argument("--person", default=None, help="Classify only one person_id")
    sp.add_argument(
        "--with-llm",
        action="store_true",
        help="Enable LLM classifier for context tags (default: rule-only)",
    )
    sp.add_argument(
        "--budget",
        type=float,
        default=1.00,
        help="Soft USD budget cap for LLM runs (default: 1.00)",
    )
    sp.add_argument(
        "--model",
        default=None,
        help="Override LLM model (default: operator preferred execution model)",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="Compile profiles + prompts without LLM calls or persistence",
    )
    sp.add_argument("--json", action="store_true", help="Also print run report as JSON")
    sp.set_defaults(func=cmd_classify)

    # tiers
    sp = sub.add_parser("tiers", help="Aggregate tier distribution (no names)")
    sp.set_defaults(func=cmd_tiers)

    # correct <person_id>
    sp = sub.add_parser(
        "correct",
        help="Record an operator correction to a person's classification",
    )
    sp.add_argument("person_id", help="Person ID")
    sp.add_argument("--tier", default=None, help="Set the tier (core, active, ...)")
    sp.add_argument(
        "--tags",
        default=None,
        help="Comma-separated list of context tags (e.g. family_nuclear,close_friend)",
    )
    sp.add_argument("--notes", default="", help="Free-text notes")
    sp.set_defaults(func=cmd_correct)

    # classification <person_id> — show current classification
    sp = sub.add_parser(
        "classification",
        help="Print the current classification for one person",
    )
    sp.add_argument("person_id", help="Person ID")
    sp.add_argument("--json", action="store_true", help="Print full result as JSON")
    sp.set_defaults(func=cmd_show_classification)

    # nudges-generate
    sp = sub.add_parser(
        "nudges-generate",
        help="Run all nudge generators (birthday, drift, reconnect)",
    )
    sp.set_defaults(func=cmd_nudges_generate)

    # nudges
    sp = sub.add_parser("nudges", help="List currently-live pending nudges")
    sp.add_argument("--limit", type=int, default=20, help="Max rows (default 20)")
    sp.set_defaults(func=cmd_nudges_list)

    # nudge-done <id>
    sp = sub.add_parser("nudge-done", help="Mark a nudge as actioned")
    sp.add_argument("nudge_id", help="Nudge ID (e.g. iq_abc12345)")
    sp.set_defaults(func=cmd_nudge_done)

    # compile-profile <person_id>
    sp = sub.add_parser(
        "compile-profile",
        help="Compile a deterministic profile for one person + write to vault",
    )
    sp.add_argument("person_id")
    sp.add_argument("--print-only", action="store_true",
                    help="Print markdown to stdout, don't persist")
    sp.add_argument("--json", action="store_true",
                    help="Print full profile dict as JSON")
    sp.add_argument("--no-vault", action="store_true",
                    help="Skip vault file write (still writes profile_versions)")
    sp.set_defaults(func=cmd_compile_profile)

    # compile-profiles  (bulk)
    sp = sub.add_parser(
        "compile-profiles",
        help="Bulk-compile profiles by tier / importance filter",
    )
    sp.add_argument("--tier", default=None,
                    help="Comma-sep tier filter (e.g. 'core,active,emerging')")
    sp.add_argument("--importance-at-most", type=int, default=None,
                    help="Filter to people.importance <= N")
    sp.add_argument("--no-vault", action="store_true")
    sp.set_defaults(func=cmd_compile_profiles)

    # ── Dashboard + Hygiene commands ──

    # dashboard
    sp = sub.add_parser("dashboard", help="People Intelligence dashboard summary")
    sp.set_defaults(func=cmd_dashboard)

    # hygiene stats
    sp = sub.add_parser("hygiene-stats", help="Hygiene queue summary")
    sp.set_defaults(func=cmd_hygiene_stats)

    # hygiene auto-approve
    sp = sub.add_parser("hygiene-approve", help="Bulk approve high-confidence items")
    sp.add_argument("--min-confidence", type=float, default=0.9)
    sp.set_defaults(func=cmd_hygiene_approve)

    # hygiene scan
    sp = sub.add_parser("hygiene-scan", help="Run duplicate + lifecycle scans")
    sp.set_defaults(func=cmd_hygiene_scan)

    # pin
    sp = sub.add_parser("pin", help="Pin a person's importance level")
    sp.add_argument("person", help="Person ID or name")
    sp.add_argument("importance", type=int, choices=[1, 2, 3, 4])
    sp.set_defaults(func=cmd_pin)

    # lifecycle
    sp = sub.add_parser("lifecycle", help="Set a person's lifecycle state")
    sp.add_argument("person", help="Person ID or name")
    sp.add_argument("state", choices=["active", "deceased", "archived", "blocked"])
    sp.set_defaults(func=cmd_lifecycle)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as e:
        logger.exception("Unhandled error")
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
