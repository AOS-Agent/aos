"""
Invariant: The Auto Tracker (shipment intelligence) is healthy.

Four sub-checks, all REPORT-ONLY (notify, never fix — a broken tracker
needs an operator, not an auto-repair):

  a) Every carrier pack under core/qareen/tracking/carriers/ loads through
     the linter (a bad manifest would silently kill polling for that
     carrier at runtime).
  b) The Auto Tracker tables exist in ~/.aos/data/qareen.db (a machine
     where migration 093 / store self-init hasn't run tracks nothing).
  c) The scheduler singleton lock (tracking_state 'scheduler:lock') is
     not stale — a lock older than 24h means the poller died holding it
     (the TTL is 5 minutes, so 24h is unambiguous death, not contention).
  d) Carrier packs are never HALF-configured: for each pack, either all
     of its manifest's Keychain keys resolve or none do. A partial set
     means someone added one secret and forgot the rest — polls for that
     carrier will fail with auth errors.
  e) The tracking crons are actually SUCCEEDING. Sub-checks (a)-(d) are
     all structural — they inspect config and schema, never execution.
     v0.7.0 shipped with track-poll crashing on every single run (23/23,
     a format-string TypeError in the cron wrapper) while this check
     reported green, because nothing here had any notion of whether the
     system had ever done its job. A tracker whose poller has never
     succeeded is not healthy, however well-formed its manifests are.

Runs under the reconcile runner with plain sys.path; the qareen package is
imported from ~/aos/core explicitly.
"""

import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

HOME = Path.home()
AOS = HOME / "aos"
QAREEN_CORE = AOS / "core"
QAREEN_DB = HOME / ".aos" / "data" / "qareen.db"
AGENT_SECRET = AOS / "core" / "bin" / "agent-secret"

LOCK_KEY = "scheduler:lock"
LOCK_STALE_SECONDS = 24 * 3600  # TTL is 5 min; 24h = unambiguously dead

CRON_LOG_DIR = HOME / ".aos" / "logs" / "crons"
# The crons whose failure means the tracker is not doing its job. A cron
# with no log at all is NOT a finding — it may simply not be scheduled on
# this machine (framework code, no instance config = graceful skip).
TRACKING_CRONS = ("track-poll", "track-chitchats")
# Last N END records to consider when deciding "never succeeds".
CRON_LOG_WINDOW = 10
# AOS cron log format: "--- [<timestamp>] END <name> (exit: N, duration: Ns) ---"
_END_RE = re.compile(r"END\s+\S+\s+\(exit:\s*(\d+)")

AUTO_TRACKER_TABLES = (
    "shipments",
    "shipment_events",
    "shipment_numbers",
    "orders",
    "order_items",
    "order_shipments",
    "detection_priors",
    "domain_rules",
    "shipment_candidates",
    "detection_eval",
    "tracking_state",
)


def _secret_get(name):
    """Resolve one Keychain key via agent-secret; None when unset/failed."""
    try:
        result = subprocess.run(
            [str(AGENT_SECRET), "get", name],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _load_packs():
    """Import qareen.tracking.packs from the framework and load all packs.
    Returns (packs_dict, error_string).

    The sys.path insertion is scoped and reverted. A reconcile run loads
    every check into one process, so a check that permanently prepends a
    tree to sys.path changes how *later* checks resolve their imports —
    and `core/engine/work/engine.py` shadows the `engine` namespace
    package exactly this way. Import, then put the path back.
    """
    added = False
    try:
        if str(QAREEN_CORE) not in sys.path:
            sys.path.insert(0, str(QAREEN_CORE))
            added = True
        from qareen.tracking import packs as packs_mod
        return packs_mod.load_packs(), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    finally:
        if added:
            try:
                sys.path.remove(str(QAREEN_CORE))
            except ValueError:
                pass


def _db_has_tables():
    """(missing_tables, error). Read-only open; a missing db means the
    feature simply isn't installed here — ([], None) lets check() skip."""
    if not QAREEN_DB.exists():
        return [], None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % QAREEN_DB, uri=True)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        return [], "%s: %s" % (type(exc).__name__, exc)
    present = {r[0] for r in rows}
    return [t for t in AUTO_TRACKER_TABLES if t not in present], None


def _stale_lock():
    """Lock age in seconds if the scheduler lock is stale, else None."""
    if not QAREEN_DB.exists():
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % QAREEN_DB, uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM tracking_state WHERE key = ?", (LOCK_KEY,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None  # unreadable db is sub-check (b)'s problem to report
    if not row or not row[0]:
        return None  # no lock held / released cleanly (empty string)
    try:
        data = json.loads(row[0])
        at = float(data.get("at", 0))
    except (ValueError, TypeError, AttributeError):
        return 0.0  # corrupt lock payload — treat as broken
    age = time.time() - at
    return age if age > LOCK_STALE_SECONDS else None


def _half_configured(packs):
    """Packs where SOME but not all Keychain keys resolve."""
    problems = []
    for slug, pack in sorted(packs.items()):
        names = list((pack.auth or {}).get("keychain_keys") or [])
        if not names:
            continue
        resolved = [n for n in names if _secret_get(n)]
        if 0 < len(resolved) < len(names):
            missing = [n for n in names if n not in resolved]
            problems.append((slug, missing))
    return problems


def _cron_failures():
    """Tracking crons whose recent runs are failing.

    Returns [(cron_name, last_exit, fails, total)] for crons whose MOST
    RECENT run failed. A cron with no log is skipped entirely — absence of
    a log means "not scheduled here", which is a legitimate state, not a
    fault. Only completed runs (END records) are considered, so a run in
    flight is never mistaken for a failure.
    """
    problems = []
    for name in TRACKING_CRONS:
        log = CRON_LOG_DIR / ("%s.log" % name)
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue  # no log → not scheduled on this machine
        exits = [int(m) for m in _END_RE.findall(text)]
        if not exits:
            continue  # started but never finished a run yet
        window = exits[-CRON_LOG_WINDOW:]
        if window[-1] != 0:
            problems.append(
                (name, window[-1], sum(1 for e in window if e != 0), len(window))
            )
    return problems


def _findings():
    """All current violations as human-readable strings."""
    findings = []

    packs, err = _load_packs()
    if err is not None:
        findings.append("carrier packs failed to load: %s" % err)
    elif not packs:
        findings.append("no carrier packs discovered under tracking/carriers/")

    missing_tables, db_err = _db_has_tables()
    if db_err is not None:
        findings.append("qareen.db unreadable: %s" % db_err)
    elif missing_tables:
        findings.append(
            "qareen.db missing Auto Tracker tables: %s" % ", ".join(missing_tables)
        )

    lock_age = _stale_lock()
    if lock_age is not None:
        if lock_age == 0.0:
            findings.append("scheduler lock payload is corrupt (unparseable)")
        else:
            findings.append(
                "scheduler lock stale %.0fh — poller died holding it"
                % (lock_age / 3600.0)
            )

    if packs:
        for slug, missing in _half_configured(packs):
            findings.append(
                "carrier %s half-configured: missing Keychain keys %s"
                % (slug, ", ".join(missing))
            )

    for name, last_exit, fails, total in _cron_failures():
        if fails == total:
            findings.append(
                "cron %s has failed every one of its last %d runs "
                "(exit %d) — the tracker is not running"
                % (name, total, last_exit)
            )
        else:
            findings.append(
                "cron %s last run failed (exit %d; %d/%d recent runs failed)"
                % (name, last_exit, fails, total)
            )

    return findings


class TrackerHealthCheck(ReconcileCheck):
    name = "tracker_health"
    description = (
        "Auto Tracker healthy: packs lint-load, qareen.db tables exist, "
        "scheduler lock not stale, carrier credentials not half-configured, "
        "tracking crons succeeding"
    )
    # Report-only: check() detects, fix() notifies. No destructive repair.

    def check(self) -> bool:
        return len(_findings()) == 0

    def fix(self) -> CheckResult:
        findings = _findings()
        if not findings:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                message="Auto Tracker healthy",
            )
        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message="%d Auto Tracker problem(s)" % len(findings),
            detail="\n".join("  - %s" % f for f in findings),
            notify=True,
        )
