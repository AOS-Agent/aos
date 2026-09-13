"""
Migration 117: freeze the machine, and tell the operator once.

v0.8.0 is the last AOS release. This migration is where that becomes true on
each machine:

  1. Writes `frozen: true` to ~/.aos/config/update-policy.yaml. From then on
     `check-update` offers patches and nothing else (core/lib/channels.py
     freeze_gate, tested in tests/test_freeze.py).
  2. Sends one Telegram notice through the existing aos-notify path.

**The notice is sent exactly once, ever.** A migration that re-runs is normal —
that is what idempotency means — and a notice tied to "did the migration run"
would re-announce the end of AOS every time the runner replayed it. So a
separate marker file (~/.aos/state/.freeze-notice-sent) records delivery, and
it is written whether or not the send succeeded: a Telegram outage must not
turn into the same message arriving three days later out of context. The
CHANGELOG is the durable record; the Telegram message is a courtesy.

Ordering matters: the flag is written first. If the notify call hangs or the
process is killed mid-migration, a machine that is frozen but silent is a much
better outcome than one that announced a freeze it never applied.

The text follows the operator's own Telegram rule — clean English, short
sentences, no jargon, no paths, no version numbers. It says three things: AOS
is done, Qren is coming, it will be invite only.

**Its own file, deliberately.** The obvious name was channel-update.yaml, and
that name is already taken: since March it holds the hourly Telegram
status-update settings (forum_topic_id, include: {...}). Writing a fresh
`frozen: true` document there would silently delete a working config for an
unrelated feature, and nobody would notice until they asked why the hourly
updates stopped. channels.py still READS a `frozen:` key there for an operator
who set it by hand; nothing ever writes it.

Idempotent: check() passes once the flag is set and the notice marker exists.
Reversible: set `frozen: false` (or delete the file) to resume normal updates.
"""

from __future__ import annotations

DESCRIPTION = "Freeze updates to patches only; send the one-time freeze notice"

import subprocess
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
CONFIG = HOME / ".aos" / "config" / "update-policy.yaml"
NOTICE_MARKER = HOME / ".aos" / "state" / ".freeze-notice-sent"
NOTIFY_CLI = AOS_ROOT / "core" / "bin" / "cli" / "aos-notify"

_CONFIG_BODY = """\
# Update policy for THIS machine.
#
# frozen: true  — this machine takes patches only. Feature releases are not
#                 offered. AOS v0.8.0 is the final feature release; what comes
#                 next is a different system, installed deliberately, not an
#                 update that arrives at 4am.
#
# Set frozen: false to resume normal updates on the release channel.
#
# Instance data — never committed, never shared between machines.

frozen: true
"""

# Clean English, short sentences, no jargon, no paths, no version numbers.
NOTICE = (
    "🕊️ <b>AOS is finished</b>\n\n"
    "This is the last update. AOS will keep running exactly as it is, and "
    "it will still get important fixes — but no new features are coming.\n\n"
    "What's next is Qren. It's being built now, and it will be invite only.\n\n"
    "Nothing you use stops working. Nothing needs doing today."
)


def _flag_set() -> bool:
    if not CONFIG.exists():
        return False
    try:
        import yaml
        raw = yaml.safe_load(CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return False
    return isinstance(raw, dict) and raw.get("frozen") is True


def _send_notice() -> bool:
    if not NOTIFY_CLI.exists():
        return False
    try:
        r = subprocess.run(
            [str(NOTIFY_CLI), NOTICE, "--topic", "system"],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def check() -> bool:
    return _flag_set() and NOTICE_MARKER.exists()


def up() -> bool:
    # 1. Flag FIRST. A frozen-but-silent machine beats one that announced a
    #    freeze it never applied.
    if not _flag_set():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(_CONFIG_BODY)
        print(f"  ✓ {CONFIG}: frozen: true (patches only)")
    else:
        print(f"  · {CONFIG} already frozen")

    # 2. The notice, at most once ever.
    if NOTICE_MARKER.exists():
        print("  · Freeze notice already sent — not repeating")
        return check()

    sent = _send_notice()
    NOTICE_MARKER.parent.mkdir(parents=True, exist_ok=True)
    # Written either way: a Telegram outage must not become the same message
    # arriving days later, out of context.
    NOTICE_MARKER.write_text("sent\n" if sent else "attempted\n")
    print("  ✓ Freeze notice sent" if sent
          else "  ~ Freeze notice could not be delivered — marked, will not retry")

    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 117 already applied" if check() else ("Done" if up() else "Failed"))
