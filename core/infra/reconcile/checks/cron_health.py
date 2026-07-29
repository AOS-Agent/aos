"""
Invariant: no scheduled job fails silently.

Every cron in config/crons.yaml runs unattended. When one breaks, the only
witness is a log file nobody opens — so a broken cron is indistinguishable from
a working one until someone goes looking, months later.

This is not hypothetical. When this check was written, the live machine showed:

    check-update       979 of 1432 runs failed   (68%)
    contact-sync        82 of  114 runs failed   (72%)
    comms-patterns      82 of  114 runs failed   (72%)
    comms-graduation    81 of  113 runs failed   (72%)
    weekly-digest       13 of   16 runs failed   (81%)

`check-update` is the mechanism that pulls updates to every machine at 4am. It
had been failing two runs in three and nothing had ever said so.

The lesson was already learned once, narrowly: `tracker_health` gained a
liveness sub-check after the Auto Tracker shipped green while its poll cron had
failed 23/23 runs, because that check "only checked packs, schema, lock, and
credentials, never whether a cron had ever succeeded." That fix was correct and
was applied to exactly one subsystem. This generalizes it to all of them.

What it reads, in order of preference:

  1. `cron_runs` in qareen.db — the structured telemetry `cron-wrap` writes
     (exit code, duration, output tails).
  2. `~/.aos/logs/crons/<name>.log` — the `END <name> (exit: N, ...)` markers
     the cron logger emits for every job. This is the fallback that makes the
     check useful *today*, before every cron is wrapped.

Reports, never repairs. A failing cron needs a human to read the error; silently
re-running it would just fail again on schedule. NOTIFY is the whole point.
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

HOME = Path.home()
AOS_ROOT = HOME / "aos"
CRONS_YAML = AOS_ROOT / "config" / "crons.yaml"
CRON_LOGS = HOME / ".aos" / "logs" / "crons"
QAREEN_DB = HOME / ".aos" / "data" / "qareen.db"

# Fail-rate above which a cron is considered broken rather than flaky. Some jobs
# legitimately exit non-zero occasionally (a network blip, a held lock); a job
# failing a third of the time is not doing that.
FAIL_RATE_THRESHOLD = 0.30  # retained for reporting context

# Below this many observed runs we don't have enough signal to judge.
MIN_RUNS = 5

# Only the most recent N runs count, and the LATEST run must be a failure for a
# job to be called broken.
#
# This is load-bearing, not a performance tweak. Two real examples from the
# machine this was written on, both of which a naive tally gets wrong:
#
#   check-update       979/1432 lifetime failures — every one before 2026-06-30,
#                      330 green runs since. The no-git-remote bug on
#                      release-shape installs was fixed; the script's own
#                      comment notes it "generated ~1000 false 'cron failed'
#                      alerts".
#   contact-sync,      ~82/114 each — all inside one 2026-04-13 → 07-12 window
#   comms-patterns,    (the `people` package moved and three sys.path
#   comms-graduation   bootstraps weren't updated), green every run since.
#
# Both were genuinely broken, and both are genuinely fixed. A check that keeps
# crying about them trains the operator to ignore it — which recreates the
# silence this check exists to break. So: require the most recent run to have
# failed, then confirm it's a pattern rather than a blip. A job self-heals the
# moment it starts passing again.
WINDOW = 10

# Of the last WINDOW runs, how many must have failed before we call it broken.
MIN_RECENT_FAILURES = 3

# `END <name> (exit: 0, duration: 3s)` — written by the cron logger for every
# job, wrapped or not.
END_MARKER = re.compile(r"END\s+(?P<name>[\w.-]+)\s+\(exit:\s*(?P<code>-?\d+)")


def _enabled_crons() -> list[str]:
    """Job names from config/crons.yaml — the scheduler's own source of truth."""
    try:
        import yaml
        data = yaml.safe_load(CRONS_YAML.read_text()) or {}
    except Exception:
        return []
    return [
        name
        for name, job in (data.get("jobs") or {}).items()
        if isinstance(job, dict) and job.get("enabled", True) is not False
    ]


def _codes_from_db() -> dict[str, list[int]]:
    """{cron_name: [exit codes, oldest→newest]} over the last WINDOW runs."""
    if not QAREEN_DB.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{QAREEN_DB}?mode=ro", uri=True)
    except Exception:
        return {}
    try:
        rows = con.execute(
            """
            WITH recent AS (
                SELECT cron_name,
                       COALESCE(exit_code, 0) AS exit_code,
                       ROW_NUMBER() OVER (
                           PARTITION BY cron_name ORDER BY started_at DESC
                       ) AS rn
                  FROM cron_runs
                 WHERE status != 'running'
            )
            SELECT cron_name, exit_code, rn
              FROM recent
             WHERE rn <= ?
             ORDER BY cron_name, rn DESC
            """,
            (WINDOW,),
        ).fetchall()
    except Exception:
        return {}
    finally:
        con.close()

    out: dict[str, list[int]] = {}
    for name, code, _rn in rows:
        out.setdefault(name, []).append(int(code))
    return out


def _codes_from_log(name: str) -> list[int]:
    """Exit codes, oldest→newest, over the last WINDOW runs in this cron's log."""
    log = CRON_LOGS / f"{name}.log"
    if not log.exists():
        return []
    try:
        text = log.read_text(errors="replace")
    except Exception:
        return []
    codes = [
        int(m.group("code"))
        for m in END_MARKER.finditer(text)
        if m.group("name") == name
    ]
    return codes[-WINDOW:]


def _collect() -> list[dict]:
    """Per-cron run stats, preferring structured telemetry over log scraping."""
    db_codes = _codes_from_db()
    out = []
    for name in _enabled_crons():
        codes = db_codes.get(name) or []
        source = "cron_runs"
        if not codes:
            codes = _codes_from_log(name)
            source = "log"
        fails = sum(1 for c in codes if c != 0)
        out.append({
            "name": name,
            "runs": len(codes),
            "failures": fails,
            "rate": (fails / len(codes)) if codes else 0.0,
            # The signals that separate "broken now" from "was broken once".
            "latest_failed": bool(codes) and codes[-1] != 0,
            "recovering": _is_recovering(codes),
            "source": source,
        })
    return out


def _is_recovering(codes: list[int]) -> bool:
    """
    True when every failure in the window precedes every success.

    This is what separates a job that *was* broken and has since been fixed from
    one that is unreliable right now. A fixed job's window looks like
    `F F F F P P` — one contiguous block of failures, then successes. A flaky
    job interleaves: `P F P F P`.

    The distinction matters most for low-frequency jobs. `weekly-digest` runs
    once a week, so after the mid-July fix it had only two green runs to show
    against eight old failures. Counting alone would keep calling it broken for
    two months; shape gets it right immediately.
    """
    first_success = next((i for i, c in enumerate(codes) if c == 0), None)
    if first_success is None:
        return False  # nothing but failures — not recovering
    return all(c == 0 for c in codes[first_success:])


def _broken(stats: list[dict]) -> list[dict]:
    """
    Jobs that are unhealthy *now*.

    A job is broken when it has enough recent failures to rule out a blip, and
    it is not simply recovering from an already-fixed problem.
    """
    return sorted(
        (
            s for s in stats
            if s["runs"] >= MIN_RUNS
            and s["failures"] >= MIN_RECENT_FAILURES
            and not s["recovering"]
        ),
        key=lambda s: (s["latest_failed"], s["rate"]),
        reverse=True,
    )


class CronHealthCheck(ReconcileCheck):
    name = "cron_health"
    description = "Scheduled jobs are actually succeeding, not failing silently"

    def check(self) -> bool:
        # No crons configured, or no history yet — nothing to judge.
        stats = _collect()
        if not stats:
            return True
        return not _broken(stats)

    def fix(self) -> CheckResult:
        """
        Report only.

        A cron failing 70% of runs has a cause — a missing path, a changed
        API, a stale lock. Re-running it on a shorter interval just fails
        faster. This surfaces it and hands it to a human.
        """
        stats = _collect()
        broken = _broken(stats)

        if not broken:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                message="All scheduled jobs are succeeding",
            )

        lines = []
        for s in broken:
            lines.append(
                f"{s['name']}: {s['failures']}/{s['runs']} runs failed "
                f"({s['rate']:.0%})"
            )

        # Never-observed crons are a weaker signal (a weekly job on a fresh
        # machine is legitimately unobserved), so they are informational.
        unobserved = [s["name"] for s in stats if s["runs"] == 0]
        detail = "\n".join(lines)
        if unobserved:
            detail += "\n\nNo run history: " + ", ".join(sorted(unobserved))

        worst = broken[0]
        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message=(
                f"{len(broken)} scheduled job(s) failing repeatedly — "
                f"worst: {worst['name']} at {worst['rate']:.0%}"
            ),
            detail=detail,
            notify=True,
        )
