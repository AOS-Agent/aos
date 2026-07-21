#!/usr/bin/env python3
"""envoy — commission and manage autonomous outbound conversations.

Usage:
  envoy start --to <E.164-number> --name "Firstname" \\
      --mission "Help him get the TestFlight build installed" \\
      --success "He confirms the app is on his screen" \\
      [--constraints "..."] [--max-messages 12] [--expires-days 5] [--dry-run]
  envoy list
  envoy show <id>
  envoy stop <id>
  envoy resume <id>          # un-pause an escalated/capped conversation
  envoy run-once             # execute one poll cycle now
  envoy install-daemon       # install/refresh the LaunchAgent (5-min poll)
  envoy uninstall-daemon
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()


def _ensure_path():
    for p in (HOME / "aos", HOME / "project" / "aos"):
        if (p / "core" / "engine" / "comms" / "envoy").is_dir():
            sys.path.insert(0, str(p))
            return str(p)
    return None


AOS_ROOT = _ensure_path()
from core.engine.comms.envoy import store  # noqa: E402

PLIST = HOME / "Library" / "LaunchAgents" / "com.aos.envoy.plist"
LABEL = "com.aos.envoy"

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{runner}</string>
    </array>
    <key>StartInterval</key><integer>300</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>{home}/.aos/logs/envoy/launchd.out</string>
    <key>StandardErrorPath</key><string>{home}/.aos/logs/envoy/launchd.err</string>
</dict>
</plist>
"""


def cmd_start(args) -> int:
    conv = store.Conversation.create(
        contact=args.to, name=args.name, mission=args.mission,
        success=args.success, constraints=args.constraints or "",
        max_messages=args.max_messages, expires_days=args.expires_days,
        dry_run=args.dry_run)
    print(f"created {conv.id} (phase=kickoff)")
    cmd_install_daemon(args, quiet=True)
    # Run the kickoff turn inline so the intro goes out immediately.
    from core.engine.comms.envoy import runner
    runner.LOG_DIR.mkdir(parents=True, exist_ok=True)
    import logging
    logging.basicConfig(level=logging.WARNING)
    runner.process(conv)
    st = conv.state
    if st["phase"] in ("active",) or (args.dry_run and st["turns"] > 0):
        last = conv.transcript(1)
        intro = last[-1]["text"] if last else "(?)"
        tag = "[DRY RUN — not sent] " if args.dry_run else ""
        print(f"kickoff sent {tag}→ {args.to}:\n  {intro}")
    else:
        print(f"kickoff pending (phase={st['phase']}, errors={st.get('errors', 0)}) — "
              "daemon will retry within 5 min; check ~/.aos/logs/envoy/runner.log")
    return 0


def cmd_list(_args) -> int:
    convs = store.all_conversations()
    if not convs:
        print("no conversations")
        return 0
    for c in convs:
        st, m = c.state, c.mission
        print(f"{c.id:<28} {st['phase']:<10} sent={st['sent_count']:<3} "
              f"{m['name']} {m['contact']}  {m['mission'][:50]}")
    return 0


def cmd_show(args) -> int:
    c = store.get(args.id)
    if not c:
        print(f"not found: {args.id}", file=sys.stderr)
        return 1
    print(json.dumps({"mission": c.mission, "state": c.state}, indent=2, default=str))
    print("--- transcript ---")
    for m in c.transcript():
        print(f"[{m['ts']}] {m['role']}: {m['text']}")
    return 0


def _set_phase(conv_id: str, phase: str) -> int:
    c = store.get(conv_id)
    if not c:
        print(f"not found: {conv_id}", file=sys.stderr)
        return 1
    st = c.state
    st["phase"] = phase
    c.save_state(st)
    c.log_message("system", f"phase -> {phase} (cli)")
    print(f"{c.id}: phase -> {phase}")
    return 0


def cmd_stop(args) -> int:
    return _set_phase(args.id, "stopped")


def cmd_resume(args) -> int:
    return _set_phase(args.id, "active")


def cmd_run_once(_args) -> int:
    from core.engine.comms.envoy import runner
    runner.main()
    print("cycle complete")
    return 0


def cmd_install_daemon(_args, quiet: bool = False) -> int:
    runner_path = Path(AOS_ROOT) / "core" / "engine" / "comms" / "envoy" / "runner.py"
    (HOME / ".aos" / "logs" / "envoy").mkdir(parents=True, exist_ok=True)
    content = PLIST_TEMPLATE.format(
        label=LABEL, python=sys.executable, runner=str(runner_path), home=str(HOME))
    if PLIST.exists() and PLIST.read_text() == content:
        if not quiet:
            print("daemon already installed")
        return 0
    PLIST.write_text(content)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                   capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"launchctl bootstrap failed: {r.stderr.strip()}", file=sys.stderr)
        return 1
    if not quiet:
        print(f"daemon installed ({PLIST})")
    return 0


def cmd_uninstall_daemon(_args) -> int:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                   capture_output=True)
    PLIST.unlink(missing_ok=True)
    print("daemon removed")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="envoy", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start")
    s.add_argument("--to", required=True, help="phone number or iMessage email")
    s.add_argument("--name", required=True, help="contact's first name")
    s.add_argument("--mission", required=True)
    s.add_argument("--success", required=True)
    s.add_argument("--constraints", default="")
    s.add_argument("--max-messages", type=int, default=12)
    s.add_argument("--expires-days", type=int, default=5)
    s.add_argument("--dry-run", action="store_true",
                   help="compose turns but never send")
    s.set_defaults(fn=cmd_start)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("stop")
    s.add_argument("id")
    s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("resume")
    s.add_argument("id")
    s.set_defaults(fn=cmd_resume)
    sub.add_parser("run-once").set_defaults(fn=cmd_run_once)
    sub.add_parser("install-daemon").set_defaults(fn=cmd_install_daemon)
    sub.add_parser("uninstall-daemon").set_defaults(fn=cmd_uninstall_daemon)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
