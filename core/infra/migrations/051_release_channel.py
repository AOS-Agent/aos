"""
Migration 051: Release channels (edge/stable).

The update path now reads ~/.aos/config/channel to decide which git ref this
machine tracks (edge → origin/main, stable → the `stable` tag). Absence of the
file resolves to `stable` in code, so no machine breaks without this migration —
but if a channel file already exists holding an unrecognised value, normalize it
to the safe default so the machine lands on stable rather than silently guessing.

This migration deliberately does NOT create the channel file when it's absent
(absence already means stable) and does NOT overwrite a valid edge/stable
choice — the operator's `edge` setting must survive updates.

Idempotent: safe to run multiple times.
"""

DESCRIPTION = "Normalize release channel config (edge/stable)"

from pathlib import Path

CHANNEL_FILE = Path.home() / ".aos" / "config" / "channel"
VALID = ("edge", "stable")


def _raw() -> str | None:
    try:
        return CHANNEL_FILE.read_text()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None


def check() -> bool:
    """True if already applied: file absent, or holds a valid channel."""
    raw = _raw()
    if raw is None:
        return True
    return raw.strip().lower() in VALID


def up() -> bool:
    """Rewrite an invalid channel file to the safe default; otherwise no-op."""
    raw = _raw()
    if raw is None:
        print("  No channel file — machine defaults to 'stable' (nothing to do)")
        return True

    value = raw.strip().lower()
    if value in VALID:
        print(f"  Channel already valid: '{value}'")
        return True

    CHANNEL_FILE.write_text("stable\n")
    print(f"  Channel value '{value}' unrecognised — reset to 'stable'")
    return True
