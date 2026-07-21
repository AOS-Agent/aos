#!/usr/bin/env python3
"""
AOS Reconcile Runner

Runs all invariant checks and attempts auto-repair.
Called by check-update on every cycle (not just when code changes).

Usage:
    python3 runner.py run          # Run all checks, fix what's broken
    python3 runner.py status       # Show last run results
    python3 runner.py check        # Dry run — report only, don't fix
"""

import json
import socket
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from base import CheckResult, ReconcileCheck, Status

LOG_FILE = Path.home() / ".aos" / "logs" / "reconcile.jsonl"
STATE_FILE = Path.home() / ".aos" / "data" / "reconcile-state.json"


def _load_checks() -> list[type[ReconcileCheck]]:
    """Import all checks from the checks/ package."""
    from checks import ALL_CHECKS
    return ALL_CHECKS


def _notify_telegram(message: str):
    """Best-effort Telegram notification."""
    import subprocess
    aos_dir = Path.home() / "aos"
    try:
        token = subprocess.run(
            [str(aos_dir / "core/bin/cli/agent-secret"), "get", "TELEGRAM_BOT_TOKEN"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        chat_id = subprocess.run(
            [str(aos_dir / "core/bin/cli/agent-secret"), "get", "TELEGRAM_CHAT_ID"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if not token or not chat_id:
            return
        import urllib.request
        data = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Best effort


def _log_results(results: list[CheckResult]):
    """Append results to JSONL log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    for r in results:
        entry = {
            "timestamp": ts,
            "check": r.name,
            "status": r.status.value,
            "message": r.message,
        }
        if r.detail:
            entry["detail"] = r.detail
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")


def _write_state(results: list[CheckResult]):
    """Write summary state for Qareen."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "total": len(results),
        "ok": sum(1 for r in results if r.status == Status.OK),
        "fixed": sum(1 for r in results if r.status == Status.FIXED),
        "notify": sum(1 for r in results if r.status == Status.NOTIFY),
        "error": sum(1 for r in results if r.status == Status.ERROR),
        "checks": {
            r.name: {"status": r.status.value, "message": r.message}
            for r in results
        },
    }
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def run_all(dry_run: bool = False, periodic: bool = False) -> list[CheckResult]:
    """Run all reconcile checks.

    Args:
        dry_run:  If True, only check — don't fix, don't notify.
        periodic: Lightweight between-deploy run (the 30-min cron). Every check
            reports check-only EXCEPT those that opt in with periodic_fix=True
            (ServiceLoadedCheck), which may repair — a dead service shouldn't
            wait for the next release. Only those periodic-fix results and
            genuine check crashes notify; the standing conditions the deploy-time
            reconcile owns (dead code, storage drift, vault debt) are logged but
            not re-pinged every 30 minutes.
    """
    check_classes = _load_checks()
    results = []
    needs_notify = []

    for cls in check_classes:
        c = cls()
        try:
            if c.check():
                results.append(CheckResult(c.name, Status.OK, "ok"))
            elif periodic and getattr(c, "periodic_fix", False):
                # Opt-in: allowed to actually repair on the periodic run.
                result = c.fix()
                results.append(result)
                # Surface what it did (a dead service restarted is worth a ping)
                # and anything it couldn't. Dedup below stops repeats.
                if result.status != Status.OK or result.notify:
                    needs_notify.append(result)
            elif dry_run or periodic:
                # Report-only. On the periodic run these are NOT added to
                # needs_notify — they don't nag about deploy-owned conditions.
                results.append(CheckResult(
                    c.name, Status.NOTIFY,
                    f"Would fix: {c.description}"
                ))
            else:
                result = c.fix()
                results.append(result)
                if result.notify or result.status == Status.NOTIFY:
                    needs_notify.append(result)
        except Exception as e:
            tb = traceback.format_exc()
            results.append(CheckResult(
                c.name, Status.ERROR,
                f"Check crashed: {e}",
                detail=tb,
                notify=True,
            ))
            needs_notify.append(results[-1])

    _log_results(results)
    _write_state(results)

    # Consolidated Telegram notification — but only for NEW or CHANGED
    # findings. Standing warnings (dead code awaiting review, storage
    # drift, vault debt) used to re-ping the operator every cycle with an
    # identical list; an alarm that repeats itself unchanged is noise and
    # trains the operator to ignore it (2026-07-15). We fingerprint each
    # finding (name + message) and persist the set; a finding notifies
    # once, then stays silent until its message changes or it clears.
    # Cleared findings are announced too — closure matters.
    if not dry_run:
        import hashlib
        # Periodic and deploy runs keep SEPARATE dedup sets. They notify on
        # different subsets (periodic: only periodic-fix results + crashes), so
        # sharing one set would make a condition present in one but absent in the
        # other look spuriously "cleared" on every cross-run.
        seen_name = "reconcile-notified-periodic.json" if periodic else "reconcile-notified.json"
        seen_path = Path.home() / ".aos" / "state" / seen_name
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            prev = json.loads(seen_path.read_text())
        except Exception:
            prev = {}
        current = {
            r.name: hashlib.sha256(f"{r.name}|{r.message}".encode()).hexdigest()[:16]
            for r in needs_notify
        }
        new_or_changed = [
            r for r in needs_notify
            if prev.get(r.name) != current[r.name]
        ]
        cleared = [name for name in prev if name not in current]

        # Translate findings into plain human copy before they hit Telegram.
        # The raw name/message/detail still went to the JSONL log above; only
        # the phone-facing message routes through alert_copy (aos#170). See
        # core/services/bridge/MESSAGE_STYLE.md → "System alerts".
        from alert_copy import render_report
        host = socket.gethostname()
        message = render_report(
            [(r.name, r.status.value, r.message, r.detail) for r in new_or_changed],
            cleared,
            host,
        )
        if message:
            _notify_telegram(message)
        try:
            seen_path.write_text(json.dumps(current, indent=1))
        except Exception:
            pass

    return results


def cmd_run():
    """Run all checks and fix."""
    print("=== AOS Reconcile ===\n")
    results = run_all(dry_run=False)

    for r in results:
        icon = {
            Status.OK: "✓",
            Status.FIXED: "⚡",
            Status.SKIP: "~",
            Status.NOTIFY: "⚠",
            Status.ERROR: "✗",
        }.get(r.status, "?")
        print(f"  {icon} {r.name}: {r.message}")
        if r.detail and r.status in (Status.NOTIFY, Status.ERROR):
            for line in r.detail.split("; "):
                print(f"      {line}")

    # Summary
    fixed = [r for r in results if r.status == Status.FIXED]
    issues = [r for r in results if r.status in (Status.NOTIFY, Status.ERROR)]
    print()
    if not fixed and not issues:
        print("  ✓ All checks passed")
    else:
        if fixed:
            print(f"  ⚡ Fixed {len(fixed)} issue(s)")
        if issues:
            print(f"  ⚠ {len(issues)} issue(s) need attention")


def cmd_status():
    """Show last run results."""
    if not STATE_FILE.exists():
        print("  No reconcile history — run 'aos reconcile' first")
        return
    state = json.loads(STATE_FILE.read_text())
    print(f"  Last run:  {state['last_run']}")
    print(f"  Machine:   {state['hostname']}")
    print(f"  OK: {state['ok']}  Fixed: {state['fixed']}  "
          f"Notify: {state['notify']}  Error: {state['error']}")
    print()
    for name, info in state.get("checks", {}).items():
        icon = {"ok": "✓", "fixed": "⚡", "notify": "⚠", "error": "✗"}.get(
            info["status"], "?"
        )
        print(f"  {icon} {name}: {info['message']}")


def cmd_check():
    """Dry run — report only."""
    print("=== AOS Reconcile (dry run) ===\n")
    results = run_all(dry_run=True)
    for r in results:
        icon = "✓" if r.status == Status.OK else "⚠"
        print(f"  {icon} {r.name}: {r.message}")


def cmd_periodic():
    """Lightweight between-deploy run: check-only for everything except
    periodic_fix checks (ServiceLoadedCheck), which may repair. Invoked by the
    30-min reconcile-periodic cron so dead services don't wait for a deploy."""
    print("=== AOS Reconcile (periodic) ===\n")
    results = run_all(periodic=True)
    for r in results:
        icon = {
            Status.OK: "✓", Status.FIXED: "⚡", Status.SKIP: "~",
            Status.NOTIFY: "⚠", Status.ERROR: "✗",
        }.get(r.status, "?")
        print(f"  {icon} {r.name}: {r.message}")
    fixed = [r for r in results if r.status == Status.FIXED]
    if fixed:
        print(f"\n  ⚡ Repaired {len(fixed)} service issue(s)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        cmd_run()
    elif cmd == "status":
        cmd_status()
    elif cmd == "check":
        cmd_check()
    elif cmd == "periodic":
        cmd_periodic()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: runner.py [run|status|check|periodic]")
        sys.exit(1)
