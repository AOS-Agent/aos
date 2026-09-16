"""
Migration 136: seed ~/.aos/config/comms.yaml for the iMessage scope gate.

Background
----------
This release adds `core/engine/comms/scope.py`: an allowlist gate applied on
every `chat.db` connection the comms engine opens (adapter, desktop ingest,
Converse, Sentinel, `core/bin/internal/operator-link`) and on every outbound
iMessage send. macOS Full Disk Access is all-or-nothing — once a process can
open `~/Library/Messages/chat.db` it can read every conversation on the Mac —
and the gate is the only thing that narrows that.

The gate reads `~/.aos/config/comms.yaml`. A MISSING file means `access: all`,
which is exactly today's behaviour: nothing changes for a machine that never
asked for the allowlist. So strictly no migration is required for correctness.

It is required for honesty. The operator deserves the file, with its comments,
sitting in `~/.aos/config/` — so "how do I restrict AOS to one thread?" is
answered by opening the directory, not by reading framework source. This is
the same shape `install.sh` uses for `trust.yaml`: copy the default template
if the instance file is absent; a machine that installed before this shipped
gets the file on its next `aos update` without re-running the installer.

What this migration does
------------------------
  1. If `~/.aos/config/comms.yaml` does not exist, copy
     `~/aos/config/defaults/comms.yaml` there byte-for-byte. If the template
     is missing (a stripped framework tree), write the inline copy below.
  2. Nothing else. An EXISTING file — whatever it says — is never read,
     rewritten, reformatted, or touched. The reference machine already
     carries an operator-written allowlist and it must survive this
     migration byte-for-byte; the test suite asserts exactly that.

Idempotent: `check()` is true once the file exists, so the runner never
calls `up()` again; and `up()` itself refuses to write over an existing
file even if called directly. Reversible: `down()` returns False — deleting
a config file the operator may since have edited is not something a
migration gets to do (see 131/133/134 for the same stance).
"""
from __future__ import annotations

import shutil
from pathlib import Path

DESCRIPTION = (
    "Seed ~/.aos/config/comms.yaml (iMessage scope gate config) from the "
    "default template when absent; never overwrites an existing file"
)

# Inline copy of config/defaults/comms.yaml, used only when the framework
# template itself is missing. Keep the two in sync when the template changes.
_FALLBACK_CONFIG = """# Comms configuration — Default Template
# Instance copy lives at ~/.aos/config/comms.yaml (seeded by install.sh or
# migration 136 when absent; never overwritten). A missing file means every
# setting below is at its default.
#
# ── iMessage scope ───────────────────────────────────────────────────────
# macOS Full Disk Access is all-or-nothing: once AOS can open
# ~/Library/Messages/chat.db it can read every conversation on the Mac. This
# section is the only thing that narrows that. The gate lives in
# core/engine/comms/scope.py and is applied on every chat.db connection the
# comms engine opens (adapter, desktop ingest, Converse, Sentinel) and on
# every outbound send.
#
#   access: all        — every thread visible, every recipient sendable.
#                        This is the default and today's behaviour.
#   access: allowlist  — only the contacts under `allowed:` are visible or
#                        sendable. Everything else is invisible to AOS.
#
# Handles are matched on digits for phones (+1 416 555 0123 == 4165550123)
# and case-insensitively for emails. A 1:1 chat is visible when its one
# participant is allowlisted. Group chats are hidden unless
# `include_groups: true`, in which case any group containing an allowlisted
# participant becomes visible (including the other members' messages).
#
# Every gated open writes one line to ~/.aos/logs/comms.log:
#   2026-09-15T19:20:01 scope=allowlist source=adapter chats=1 handles=2

imessage:
  access: all
  include_groups: false
  # allowed:
  #   - name: Hisham
  #     handles:
  #       - "+14165550123"
  #       - "hisham@example.com"
"""


# Resolved on every call, never captured at import — a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process (see
# migrations 133–135 for the same pattern).
def _home() -> Path:
    return Path.home()


def _instance_config() -> Path:
    return _home() / ".aos" / "config" / "comms.yaml"


def _template() -> Path:
    return _home() / "aos" / "config" / "defaults" / "comms.yaml"


def check() -> bool:
    """Applied once the instance file exists — whoever wrote it."""
    return _instance_config().is_file()


def up() -> bool:
    dest = _instance_config()
    if dest.exists():
        # Never touch an existing file: it may be the operator's allowlist.
        print(f"       - {dest} already present — left untouched")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    template = _template()
    if template.is_file():
        shutil.copyfile(template, dest)
        print(f"       - seeded {dest} from config/defaults/comms.yaml")
    else:
        dest.write_text(_FALLBACK_CONFIG)
        print(f"       - seeded {dest} from inline fallback "
              "(config/defaults/comms.yaml not found in framework tree)")
    print("       - access: all — nothing changes until the operator sets "
          "access: allowlist")
    return True


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 136 already applied" if check() else ("Done" if up() else "Failed"))
