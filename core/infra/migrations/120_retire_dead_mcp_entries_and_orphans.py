"""
Migration 120: retire dead MCP entries and the slack-lite orphan (v0.7.7 cleanup,
operator-approved, aos#221).

Four actions, each independently idempotent.

a. Stalled LaunchAgents — NONE RETIRED.

   The brief named four LaunchAgents as "loaded but crash-looping with no live
   consumer": com.aos.scheduler, com.aos.domain-watch, com.aos.domain-catch,
   am.hish.superwhisper-launcher. Verified on the reference machine
   2026-09-13, before any code was written, per the brief's own instruction:

     - com.aos.scheduler: StartCalendarInterval every 5 min. Log
       (~/.aos-logs/crons/scheduler.log) shows a clean tick at the moment of
       verification and every 5 minutes back through the retention window —
       38 jobs defined, each run exiting 0 (watchdog, sync-sessions,
       auto-commit, qmd-reindex, check-update, inbox-collect, feed-ingest,
       reconcile-sessions). This is not a stalled agent; it is the scheduler.
     - com.aos.domain-watch: StartInterval 21600s (6h). Log shows a clean run
       every 6 hours, current as of the verification day, tracking
       quran.garden and alhuda.com expiry/registration state. Empty .err.
     - com.aos.domain-catch: StartInterval 300s (5m). Log shows a clean run
       every 5 minutes, current as of the verification day — an active
       drop-catch monitor on quran.garden (14→11 days to expiry over the
       observed window). Empty .err. Retiring this one specifically would be
       actively harmful: it is watching a domain the operator is trying to
       reclaim on drop.
     - am.hish.superwhisper-launcher: RunAtLoad, LimitLoadToSessionType Aqua,
       no KeepAlive — fires once per login, checks whether Superwhisper is
       already running, launches it if not. Log shows exactly that behaviour
       on its last login, no errors.

   All four show PID `-` / last exit `0` in `launchctl list` at any given
   instant — which is what an interval-or-login-triggered agent looks like
   *between* runs, not evidence of a crash loop. None qualify under the
   brief's own exit clause ("if any of the four is actually alive and doing
   work, leave it out and tell me"). `STALLED_LAUNCHER_LABELS` below is kept
   as an explicit empty tuple rather than omitted, so a future machine that
   finds one of these four genuinely dead has an obvious place to add it
   instead of a new migration — and so this migration's diff shows "looked,
   found nothing" rather than silently doing nothing.

b. Orphaned instance service dirs.

   The brief named ~/.aos/services/extractd and ~/.aos/services/slack-lite.
   Verified the same way — only after confirming no plist references them:

     - extractd: NOT orphaned, NOT touched. am.hish.yt-extractd.plist execs
       ~/.aos/launchers/Yt Extractd, which execs
       ~/.aos/services/extractd/.venv/bin/python .../app/main.py verbatim.
       At verification time the process was running (`ps` showed it resident
       since the prior boot). Excluded from ORPHAN_SERVICE_DIRS below.
     - slack-lite: orphaned, archived (not deleted). No plist anywhere
       references it — grepping every installed LaunchAgent plist for
       "services/slack-lite" (and the sibling name "slack-watch", a
       different, still-live service that migration 109 already covers)
       finds nothing. It was a hand-run prototype (aos#198 era): `slack.py`'s
       xoxc/xoxd cookie approach and `decrypt_cookie.py`'s Keychain recipe
       were generalized into core/engine/comms/channels/slack_session.py and
       core/engine/comms/converse/reauth.py months ago (both modules say so
       in their own docstrings) — the directory is a superseded reference
       copy, not a running service. Migration 109, from 2026-08-18, explicitly
       chose not to touch it ("sana-watch and slack-lite are NOT touched");
       this migration acts on a fresh, separate operator approval, and moves
       rather than deletes, so the one unresolved thread it contains (a
       stalled Slack conversation about a school timetable) is not lost.

   `_plist_references()` re-checks this at runtime rather than trusting the
   one-time manual verification above, so the same migration is safe to ship
   fleet-wide: any machine where a plist has grown a reference to one of
   these directories since is left alone, not archived out from under it.

c. Dead entries in ~/.claude.json's global `mcpServers`.

   memory, crawler, xcode all fail to connect at every session start.
   `memory` was already retired in code by migration 110, from 2026-08-18, which
   removed core/services/memory but — per that migration's own scope — only
   ever deregistered the entry on machines it actually ran on; a machine that
   updated past 110 without running it (or an entry restored some other way)
   still carries the stale key. `crawler` and `xcode` never worked as MCP
   servers on this machine (crawler's stdio command is a service venv MCP
   entrypoint that was never wired up correctly; xcode shells out to `xcrun
   mcpbridge`, which is not a subcommand `xcrun` has).

   Only the global `mcpServers` block is touched — per-project entries in
   `projects.*.mcpServers` are a separate scope this migration does not read
   or write (checked on the reference machine: 2/23 projects carry their own
   mcpServers block, neither lists any of these three names).

   `core/bin/cli/aos`'s `sync-mcp` subcommand independently re-registers
   "crawler" as a user-scope MCP server every time it runs, unconditionally
   on the venv existing — deleting the `~/.claude.json` entry here without
   also touching that would have this migration's own fix undone by the next
   `aos sync` or update. The `crawler` entry is dropped from `sync-mcp`'s
   `aos_servers` table in the *same commit* as this migration, mirroring how
   migration 110 shipped "code removed" and "sync-mcp stops registering it"
   together. Nothing else moves: the crawler service venv and
   core/services/crawler/crawl_cli.py stay, because
   core/engine/intelligence/content/backends/crawler.py still shells out to
   them directly as a content-extraction backend, entirely outside MCP — the
   dead thing is the MCP registration, not the service.

   Edits ~/.claude.json atomically (write-tmp, os.replace — the file is read
   and written by every running Claude Code session) and only after taking a
   timestamped backup of the whole file beside it.

d. Status drift in ~/.aos/config/integrations.yaml.

     - telegram (marked `failed`): com.aos.bridge was verified loaded and
       actually running at verification time — its own log shows the
       Telegram and Slack channels starting cleanly, heartbeat and daily
       briefing scheduled, no errors, most recent restart the morning of
       verification. `failed` was stale. Corrected to `active` with a note
       recording why, only when the bridge is independently confirmed
       running — this migration never simply overwrites the field.
     - obsidian (marked `failed`): core/infra/integrations/obsidian/setup.sh
       --check exists and is safe to run (its own header: "always
       non-destructive"). On the reference machine it FAILS — one real
       check failure, a missing ~/.aos/config/obsidian.yaml. Per the brief:
       "only flip it if the check passes, otherwise leave and report." Left
       as `failed` on this machine; reported, not fixed (writing
       obsidian.yaml is a separate, smaller fix this migration does not
       make). On a machine where the check passes, this flips it the same
       way telegram is flipped.

Reversible:
  a. n/a — nothing was retired.
  b. move the archived directory back from
     ~/.aos/backups/retired-services/<name>-<date>/ to
     ~/.aos/services/<name>.
  c. restore from the timestamped ~/.claude.json.bak-120-<timestamp> this
     migration writes before its first real edit.
  d. not reversible by this migration — edit integrations.yaml by hand.

Idempotent: check() passes once no orphan dir remains un-archived (excluding
any the runtime plist-reference check protects), ~/.claude.json carries none
of the three dead keys, and no known-good drift remains uncorrected.
"""

from __future__ import annotations

DESCRIPTION = "Archive orphaned slack-lite dir, drop dead MCP entries, fix integration status drift (0.7.7)"

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
LA_DIR = HOME / "Library" / "LaunchAgents"
SERVICES_DIR = HOME / ".aos" / "services"
BACKUP_DIR = HOME / ".aos" / "backups" / "retired-services"
CLAUDE_JSON = HOME / ".claude.json"
INTEGRATIONS_CONFIG = HOME / ".aos" / "config" / "integrations.yaml"
OBSIDIAN_CHECK = AOS_ROOT / "core" / "infra" / "integrations" / "obsidian" / "setup.sh"

BRIDGE_LABEL = "com.aos.bridge"

# ── (a) Stalled launchers — verified, none qualify. See docstring. ─────────
STALLED_LAUNCHER_LABELS: tuple[str, ...] = ()

# ── (b) Orphaned instance service dirs ──────────────────────────────────────
# extractd is deliberately absent — verified alive (am.hish.yt-extractd, a
# running process). See docstring.
ORPHAN_SERVICE_DIRS: tuple[str, ...] = ("slack-lite",)

# ── (c) Dead MCP entries ─────────────────────────────────────────────────────
DEAD_MCP_SERVERS: tuple[str, ...] = ("memory", "crawler", "xcode")


def _plist_references(name: str) -> bool:
    """True if any installed LaunchAgent plist mentions services/<name>.

    Re-checked at runtime, not trusted from a one-time manual pass — see
    docstring §b. A literal substring match on the plist bytes is enough:
    every launcher wrapper this codebase generates execs an absolute path
    that contains "services/<name>" verbatim (see core/infra/lib/launchers.py).
    """
    if not LA_DIR.exists():
        return False
    needle = f"services/{name}".encode()
    for plist in LA_DIR.glob("*.plist"):
        try:
            if needle in plist.read_bytes():
                return True
        except OSError:
            continue
    return False


def _orphans_present() -> list[str]:
    """Named orphan dirs that exist and are not (yet) claimed by a plist."""
    return [
        name for name in ORPHAN_SERVICE_DIRS
        if (SERVICES_DIR / name).exists() and not _plist_references(name)
    ]


def _archive_orphan(name: str) -> Path:
    """Move services/<name> to backups/retired-services/<name>-<date>/.

    Never overwrites a previous archive of the same name taken the same day —
    a second run that somehow still found the source present (it should not,
    since the move already removed it) gets a numbered sibling instead of a
    silent clobber.
    """
    src = SERVICES_DIR / name
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    dest = BACKUP_DIR / f"{name}-{stamp}"
    n = 2
    while dest.exists():
        dest = BACKUP_DIR / f"{name}-{stamp}-{n}"
        n += 1
    shutil.move(str(src), str(dest))
    return dest


def _claude_json_servers() -> dict:
    if not CLAUDE_JSON.exists():
        return {}
    try:
        config = json.loads(CLAUDE_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    servers = config.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def _claude_json_has_dead_server() -> bool:
    servers = _claude_json_servers()
    return any(name in servers for name in DEAD_MCP_SERVERS)


def _clean_claude_json() -> list[str]:
    """Remove DEAD_MCP_SERVERS from the global mcpServers block.

    Backs up the whole file (timestamped, beside the original) before the
    first real edit, then writes tmp + os.replace — the file is live-read and
    live-written by every running Claude Code session. Per-project
    `projects.*.mcpServers` blocks are never touched.
    """
    if not CLAUDE_JSON.exists():
        return []
    try:
        config = json.loads(CLAUDE_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return []

    removed = [name for name in DEAD_MCP_SERVERS if name in servers]
    if not removed:
        return []

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = CLAUDE_JSON.with_name(f".claude.json.bak-120-{stamp}")
    backup.write_text(CLAUDE_JSON.read_text())

    for name in removed:
        del servers[name]

    tmp = CLAUDE_JSON.with_suffix(CLAUDE_JSON.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    os.replace(tmp, CLAUDE_JSON)
    return removed


def _bridge_running() -> bool:
    """True if com.aos.bridge is loaded AND currently has a running pid."""
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{BRIDGE_LABEL}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        return False
    if r.returncode != 0:
        return False
    return "pid = " in r.stdout


def _obsidian_check_passes() -> bool | None:
    """True/False if the integration's own --check ran; None if unavailable.

    None (script missing, or could not run it) must never be treated as
    "passed" — see docstring §d, "only flip it if the check passes".
    """
    if not OBSIDIAN_CHECK.exists():
        return None
    try:
        r = subprocess.run(
            ["bash", str(OBSIDIAN_CHECK), "--check"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:  # noqa: BLE001
        return None
    return r.returncode == 0


def _read_integrations() -> dict:
    if not INTEGRATIONS_CONFIG.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(INTEGRATIONS_CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write_integrations(data: dict) -> bool:
    try:
        import yaml
    except Exception:  # noqa: BLE001
        return False
    INTEGRATIONS_CONFIG.write_text(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    )
    return True


def _drift_to_fix() -> dict:
    """{'telegram': True, 'obsidian': True} for each entry a fix would touch."""
    data = _read_integrations()
    integrations = data.get("integrations")
    if not isinstance(integrations, dict):
        return {}

    todo = {}
    telegram = integrations.get("telegram")
    if isinstance(telegram, dict) and telegram.get("status") == "failed" and _bridge_running():
        todo["telegram"] = True

    obsidian = integrations.get("obsidian")
    if isinstance(obsidian, dict) and obsidian.get("status") == "failed" and _obsidian_check_passes() is True:
        todo["obsidian"] = True

    return todo


def _fix_status_drift() -> list[str]:
    todo = _drift_to_fix()
    if not todo:
        return []

    data = _read_integrations()
    integrations = data["integrations"]  # present — _drift_to_fix already checked

    fixed = []
    if todo.get("telegram"):
        integrations["telegram"]["status"] = "active"
        integrations["telegram"]["note"] = (
            "corrected by migration 120: com.aos.bridge verified loaded and "
            "running (Telegram + Slack channels started, heartbeat active, "
            "no errors) — prior `failed` was stale, not a real integration "
            "failure"
        )
        fixed.append("telegram")

    if todo.get("obsidian"):
        integrations["obsidian"]["status"] = "active"
        integrations["obsidian"]["note"] = (
            "corrected by migration 120: core/infra/integrations/obsidian/"
            "setup.sh --check passed"
        )
        fixed.append("obsidian")

    if fixed:
        _write_integrations(data)
    return fixed


def check() -> bool:
    """Applied when there is nothing left for any of (b), (c), (d) to do.

    (a) never contributes a pending item — it retires nothing.
    """
    if _orphans_present():
        return False
    if _claude_json_has_dead_server():
        return False
    if _drift_to_fix():
        return False
    return True


def up() -> bool:
    print(
        "  (a) stalled launchers: none retired — com.aos.scheduler, "
        "com.aos.domain-watch, com.aos.domain-catch and "
        "am.hish.superwhisper-launcher were all verified alive and doing "
        "real work on the reference machine (see module docstring); left "
        "running, untouched"
    )

    orphans = _orphans_present()
    if orphans:
        for name in orphans:
            dest = _archive_orphan(name)
            print(f"  (b) archived {name} -> {dest}")
    else:
        already_done = [n for n in ORPHAN_SERVICE_DIRS if not (SERVICES_DIR / n).exists()]
        still_referenced = [
            n for n in ORPHAN_SERVICE_DIRS
            if (SERVICES_DIR / n).exists() and _plist_references(n)
        ]
        if still_referenced:
            print(f"  (b) left alone (plist now references it): {', '.join(still_referenced)}")
        if already_done:
            print(f"  (b) already archived: {', '.join(already_done)}")
        if not still_referenced and not already_done:
            print("  (b) nothing present to archive")
        print("      extractd was excluded on purpose — verified alive (am.hish.yt-extractd)")

    removed = _clean_claude_json()
    if removed:
        print(f"  (c) removed from ~/.claude.json mcpServers: {', '.join(removed)}")
    else:
        print("  (c) ~/.claude.json already clean of memory/crawler/xcode")

    fixed = _fix_status_drift()
    if fixed:
        print(f"  (d) corrected status drift: {', '.join(fixed)}")
    else:
        print("  (d) no status drift corrected this run")

    return check()


def down() -> bool:
    """Not reversible as a single operation — see docstring's per-item notes."""
    return False


if __name__ == "__main__":
    print("Migration 120 already applied" if check() else ("Done" if up() else "Failed"))
