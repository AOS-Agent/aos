"""
Migration 131: comms-intelligence nightly off by default; retire two
dead crons (v0.7.7 cron audit, 2026-09-13, aos#240).

Cron-by-cron necessity audit:
~/vault/knowledge/references/crons-audit-2026-09-13.md

PART A — park the comms-intelligence nightly (operator decision, aos#240.1):

  enrich-comms       ~46 min/night of Haiku entity extraction — the single
                     heaviest job on the schedule, and the scheduling-
                     contention culprit that starves loop-sensors and
                     compile-patterns of their catch_up windows most nights
                     it runs long. The audit's own per-job verdict was KEEP
                     (comms.db entities feed real ambient nudges), but the
                     operator chose to pause the whole comms-intelligence
                     nightly stack rather than run four back-to-back comms-DB
                     jobs every night for marginal refresh value.
  comms-patterns     computes per-person response patterns/importance,
                     downstream of enrich-comms's entities — parked alongside
                     so one thing is paused, not two half-paused things.
  comms-graduation   graduates contacts through trust levels, downstream of
                     comms-patterns — same reasoning.

  backup-comms (03:15) is EXPLICITLY NOT touched by this migration or by
  config/crons.yaml — comms.db's nightly snapshot keeps running so the
  database stays safe regardless of whether anything enriches it that night.

  Readers verified to tolerate these tables going stale (no code change
  needed, confirmed by reading each): core/hooks/mention_context.py (reads
  frozen JSON snapshots under ~/.aos/cache/ambient/, already wrapped in
  broad try/except, already has tests for a missing/empty cache); core/bin/
  crons/morning-context (people-nudges refresh wrapped in try/except Exception,
  returns [] on any failure — does not touch communication_patterns/trust.yaml
  at all, reads person_classification/relationship_state instead, which
  neither parked job writes); feed-digest and weekly-digest (grepped: neither
  references communication_patterns, message_entities, or trust.yaml — they
  read qareen.db intelligence_briefs and work.yaml/patterns.yaml respectively,
  unrelated tables).

PART B — retire two dead crons (deleted outright in this same release; this
migration only mops up their pre-existing instance-side leftovers):

  inbox-collect      all three declared sources (calendar/voicememo/notes)
                     report "not available" every run — the reader modules
                     it imports (calendar_reader/voicememo_reader/
                     notes_reader) don't exist anywhere in this tree. 159 of
                     160 daily notes are the bare unfilled template. Its
                     `_ensure_daily_note()` duplicates what
                     core/services/bridge/daily_briefing.py already creates
                     independently, so nothing is lost by deleting it.
  stale-detector     writes ~/.aos/work/stale-report.yaml and never
                     notifies; grep confirms zero readers anywhere in core,
                     config, or docs. stale-initiatives (09:00) already
                     covers the notifying half (initiatives untouched 3+
                     days, verified Telegram send) — the two were never the
                     same feature, but stale-detector's half had no consumer
                     at all.

  ~/vault/daily/ (inbox-collect's nominal output directory) is deliberately
  NOT touched here — it is actively written by core/services/bridge/
  daily_briefing.py and evening_checkin.py, and read by core/engine/people/
  intel/sources/vault.py, so it is shared infrastructure inbox-collect merely
  also wrote into, not something it owned alone. stale-detector's report
  file is the one true orphan output, and is the thing this migration
  archives — never deletes outright, per "destructive operations require
  operator approval": an operator who wants the old stale list can still
  find it under ~/.aos/backups/.

Shape: same as migration 127 — `enabled: false` set directly on the target
jobs in config/crons.yaml (the scheduler's own source of truth; see
core/bin/internal/scheduler and cron_health.py's `_enabled_crons()`), a
line-based patch (not a YAML round-trip, for the same hand-commented-file
reason 127 gives) for a machine whose crons.yaml predates this release. An
explicit operator `enabled: true` already on a job is always respected and
never re-disabled, on this run or any later one.

inbox-collect and stale-detector are deleted from crons.yaml directly in
this release (same treatment ascbuild-sync's track-poll/track-chitchats
siblings got before them) — there is nothing for a migration to patch
there: an install that already has the file just inherits the shipped
absence, and one that doesn't will get it the moment the release lands.

Idempotent: check() passes once the three target jobs are each explicit
(`enabled: true` or `enabled: false`) and stale-report.yaml no longer sits
at its old instance path (either archived already, or never existed).
Reversible: flip the three jobs' `enabled:` back to `true` to resume them;
the archived stale-report.yaml is left under ~/.aos/backups/ for the
operator to restore by hand if ever wanted — down() does not automate
either reversal.
"""

from __future__ import annotations

DESCRIPTION = (
    "enrich-comms/comms-patterns/comms-graduation off by default (comms "
    "nightly parked; backup-comms untouched); archive stale-detector's "
    "orphaned stale-report.yaml to ~/.aos/backups/ (inbox-collect and "
    "stale-detector retired outright in this release)"
)

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _crons_yaml() -> Path:
    return Path.home() / "aos" / "config" / "crons.yaml"


def _stale_report() -> Path:
    return Path.home() / ".aos" / "work" / "stale-report.yaml"


def _backups_dir() -> Path:
    return Path.home() / ".aos" / "backups"


TARGET_JOBS = ("enrich-comms", "comms-patterns", "comms-graduation")

# A top-level job header, e.g. "  enrich-comms:" — two-space indent, no
# further indentation, so it never matches a field line inside a block.
_JOB_HEADER = re.compile(r"^  (\S+):\s*$")
_ENABLED_LINE = re.compile(r"^\s*enabled:\s*(true|false)\s*$", re.IGNORECASE)


def _job_blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """{job_name: (header_line_index, block_end_index)} — end is exclusive,
    the index of the next top-level header (or len(lines))."""
    headers = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := _JOB_HEADER.match(line))
    ]
    blocks = {}
    for idx, (line_no, name) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        blocks[name] = (line_no, end)
    return blocks


def _job_enabled_state(lines: list[str], start: int, end: int) -> bool | None:
    """True/False if an explicit `enabled:` line is in this job's block, else None."""
    for line in lines[start + 1:end]:
        m = _ENABLED_LINE.match(line)
        if m:
            return m.group(1).lower() == "true"
    return None


def _read_lines(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    try:
        return path.read_text().splitlines(keepends=True)
    except Exception:  # noqa: BLE001
        return None


def check() -> bool:
    """Applied once crons.yaml's three target jobs are all explicit, and
    stale-report.yaml no longer sits at its pre-migration instance path."""
    lines = _read_lines(_crons_yaml())
    if lines is not None:
        blocks = _job_blocks(lines)
        for job in TARGET_JOBS:
            if job not in blocks:
                continue  # job renamed/removed — not this migration's concern
            start, end = blocks[job]
            if _job_enabled_state(lines, start, end) is None:
                return False
    # else: nothing to patch — a fresh/release install ships the file already correct

    return not _stale_report().exists()


def up() -> bool:
    lines = _read_lines(_crons_yaml())
    if lines is None:
        print(f"  · {_crons_yaml()} not found — nothing to patch (release ships it pre-set)")
    else:
        blocks = _job_blocks(lines)
        changed = False
        for job in TARGET_JOBS:
            if job not in blocks:
                print(f"  · {job}: not in crons.yaml — skipped")
                continue
            start, end = blocks[job]
            state = _job_enabled_state(lines, start, end)
            if state is True:
                print(f"  ✓ {job}: explicitly opted in (`enabled: true`) — left as-is")
                continue
            if state is False:
                print(f"  ✓ {job}: already `enabled: false`")
                continue
            # No explicit key — insert right after the job header, matching
            # the indentation the rest of the file's `enabled: false` jobs use.
            indent = "    "
            lines.insert(start + 1, f"{indent}enabled: false\n")
            # Every later block's start index shifted by one insertion.
            blocks = _job_blocks(lines)
            changed = True
            print(f"  ✓ {job}: inserted `enabled: false`")

        if changed:
            _crons_yaml().write_text("".join(lines))
            print(f"     Wrote {_crons_yaml()}")
            print("     Opt back in: set `enabled: true` under the job in crons.yaml")

    report = _stale_report()
    if report.exists():
        backups = _backups_dir()
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = backups / f"stale-report-retired-{stamp}.yaml"
        shutil.move(str(report), str(dest))
        print(f"  ✓ archived {report} → {dest} (stale-detector retired)")
    else:
        print(f"  · {report} not found — nothing to archive")

    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 131 already applied" if check() else ("Done" if up() else "Failed"))
