"""
Migration 106: Wire the framework statusline into the Claude Code harness.

The statusline script has shipped in the framework (core/bin/cli/statusline)
since v0.7.x, but the instance-layer wiring — the ~/.claude/statusline.sh
symlink and the statusLine entry in ~/.claude/settings.json — was only ever
done by hand on the primary Mini. Fleet machines pulled the script on every
`aos update` and never used it: one machine was found running a stale March
copy while its settings pointed at a third, divergent June-era script.

This migration makes the wiring explicit and fleet-wide:

  1. ~/.claude/statusline.sh becomes a symlink to ~/aos/core/bin/cli/statusline.
     A pre-existing REAL file is backed up to statusline.sh.pre-106.bak first;
     a wrong symlink is repointed.
  2. settings.json gets statusLine = {type: command, command: ~/.claude/
     statusline.sh, padding: 2}, preserving every other key. If a different
     statusline was configured, the old value is printed for the record and
     the old script file is left on disk untouched.

POLICY (deliberate, operator-approved 2026-08-16): the framework statusline
is authoritative on every AOS machine. Overwrite-with-backup, never skip
because "something is already configured" — that is exactly how the fleet
drifted apart in the first place.

Graceful skip: no ~/.claude/settings.json means the harness was never set up
on this machine — onboarding owns that case, not this migration. A missing
framework binary is a hard failure (never symlink to nothing).

Idempotent: check() is True iff the symlink resolves to the framework binary
and settings.json points at the symlink; up() re-run is then a no-op.
"""

import json
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
LINK = CLAUDE_DIR / "statusline.sh"
TARGET = HOME / "aos" / "core" / "bin" / "cli" / "statusline"
SETTINGS = CLAUDE_DIR / "settings.json"
BACKUP = CLAUDE_DIR / "statusline.sh.pre-106.bak"

STATUSLINE_ENTRY = {
    "type": "command",
    "command": "~/.claude/statusline.sh",
    "padding": 2,
}


def _settings_ok() -> bool:
    try:
        cfg = json.loads(SETTINGS.read_text())
    except Exception:
        return False
    sl = cfg.get("statusLine")
    return (
        isinstance(sl, dict)
        and sl.get("type") == "command"
        and sl.get("command") == STATUSLINE_ENTRY["command"]
    )


def _link_ok() -> bool:
    return LINK.is_symlink() and LINK.resolve() == TARGET.resolve()


def check() -> bool:
    """Done when the symlink resolves to the framework binary and
    settings.json routes the statusline through it."""
    if not SETTINGS.exists():
        return True  # harness never set up here — onboarding's job
    return _link_ok() and _settings_ok()


def up() -> bool:
    if not SETTINGS.exists():
        print("  ~/.claude/settings.json not found — harness not set up on "
              "this machine; skipping (onboarding owns first-time setup)")
        return True

    if not TARGET.exists():
        print(f"  FAILED: framework statusline missing at {TARGET} — "
              "refusing to symlink to nothing (run `aos update` first)")
        return False

    # 1. Symlink, backing up whatever was there.
    if not _link_ok():
        if LINK.is_symlink():
            old = str(LINK.readlink())
            LINK.unlink()
            print(f"  repointed symlink (was → {old})")
        elif LINK.exists():
            LINK.replace(BACKUP)
            print(f"  backed up stale real file → {BACKUP.name}")
        LINK.symlink_to(TARGET)
        print(f"  statusline.sh → {TARGET}")

    # 2. settings.json — surgical: touch only the statusLine key.
    try:
        cfg = json.loads(SETTINGS.read_text())
    except Exception as e:
        print(f"  FAILED: could not parse {SETTINGS}: {e}")
        return False
    if not _settings_ok():
        old = cfg.get("statusLine")
        if old:
            print(f"  replacing previous statusLine config: {old!r} "
                  "(old script file, if any, left on disk)")
        cfg["statusLine"] = dict(STATUSLINE_ENTRY)
        SETTINGS.write_text(json.dumps(cfg, indent=2) + "\n")
        print("  settings.json: statusLine → ~/.claude/statusline.sh")

    return check()
