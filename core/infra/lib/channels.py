#!/usr/bin/env python3
"""Release-channel logic for AOS two-lane updates.

A channel decides which git ref a machine tracks when it updates:

    edge    → origin/main HEAD        (same-day; the operator's machine)
    stable  → the `stable` git tag     (promoted releases only; friend machines)

The channel is a single line in ``~/.aos/config/channel``. When that file is
absent or holds anything unrecognised, the channel resolves to ``stable`` — the
safe lane — so a machine that merely *receives* this code lands on stable with
zero operator action. If the ``stable`` tag does not exist yet (before the first
promotion), the stable channel falls back to ``main`` so the machine keeps
updating instead of stranding itself.

Everything here is pure logic — no git calls, and the only I/O is reading the
channel file — so resolution and the promotion guard are unit-testable. The
update scripts (``check-update``, ``release-manager``) and ``aos promote`` shell
out to the subcommands at the bottom for the derived values.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

CHANNELS = ("edge", "stable")
DEFAULT_CHANNEL = "stable"

# git ref each channel tracks (stable may fall back to MAIN_REF — see resolve_target)
MAIN_REF = "origin/main"
STABLE_REF = "refs/tags/stable"

# Default promotion soak: a candidate must have run on the edge machine this
# many days before it can be promoted to stable.
DEFAULT_MIN_SOAK_DAYS = 2


def normalize_channel(raw: str | None) -> str:
    """Coerce a raw channel string to a known channel.

    Unknown / empty / None all collapse to the safe default (stable). Matching
    is case-insensitive and whitespace-insensitive.
    """
    if not raw:
        return DEFAULT_CHANNEL
    value = raw.strip().lower()
    return value if value in CHANNELS else DEFAULT_CHANNEL


def channel_file_path(config_dir: Path | str | None = None) -> Path:
    """Location of the channel config file (``<config_dir>/channel``)."""
    base = Path(config_dir) if config_dir else Path.home() / ".aos" / "config"
    return base / "channel"


def read_channel(config_dir: Path | str | None = None) -> str:
    """Read and normalize the machine's channel. Absent file → stable."""
    path = channel_file_path(config_dir)
    try:
        return normalize_channel(path.read_text())
    except (FileNotFoundError, NotADirectoryError, OSError):
        return DEFAULT_CHANNEL


def resolve_target(channel: str, main_hash: str, stable_tag_hash: str | None) -> dict:
    """Resolve which ref+commit a channel should deploy.

    Args:
        channel: raw or normalized channel name.
        main_hash: commit hash of origin/main (may be "").
        stable_tag_hash: commit the ``stable`` tag points at, or "" / None if
            the tag does not exist on this machine.

    Returns a dict with:
        channel  — normalized channel
        ref      — git ref to track ("origin/main" or "refs/tags/stable")
        hash     — target commit hash ("" if unresolved)
        fellback — True if a stable machine fell back to main (tag missing)
        reason   — human-readable explanation
    """
    channel = normalize_channel(channel)
    main_hash = (main_hash or "").strip()
    stable_tag_hash = (stable_tag_hash or "").strip()

    if channel == "edge":
        return {
            "channel": "edge",
            "ref": MAIN_REF,
            "hash": main_hash,
            "fellback": False,
            "reason": "edge tracks origin/main",
        }

    # stable
    if stable_tag_hash:
        return {
            "channel": "stable",
            "ref": STABLE_REF,
            "hash": stable_tag_hash,
            "fellback": False,
            "reason": "stable tracks the stable tag",
        }

    # stable, but the tag does not exist yet → keep updating from main
    return {
        "channel": "stable",
        "ref": MAIN_REF,
        "hash": main_hash,
        "fellback": True,
        "reason": "stable tag not found — falling back to origin/main until first promotion",
    }


def promotion_guard(
    deployed_at: float | None,
    now: float | None = None,
    min_days: float = DEFAULT_MIN_SOAK_DAYS,
    force: bool = False,
) -> dict:
    """Decide whether the currently-running release may be promoted to stable.

    The rule: the candidate must have been *running* on this machine for at
    least ``min_days`` days (soak time), unless ``force`` overrides it.

    Args:
        deployed_at: epoch seconds when the running release was deployed
            (None/unknown → cannot verify soak).
        now: epoch seconds "now" (defaults to time.time()).
        min_days: required soak in days.
        force: operator override.

    Returns a dict: allowed(bool), soak_days(float|None), min_days, forced(bool),
    reason(str).
    """
    if now is None:
        now = time.time()

    if force:
        soak = None if deployed_at is None else max(0.0, (now - deployed_at) / 86400.0)
        return {
            "allowed": True,
            "soak_days": soak,
            "min_days": min_days,
            "forced": True,
            "reason": "forced (soak check overridden)",
        }

    if deployed_at is None:
        return {
            "allowed": False,
            "soak_days": None,
            "min_days": min_days,
            "forced": False,
            "reason": "cannot determine when the running release was deployed — re-run with --force to override",
        }

    soak_days = max(0.0, (now - deployed_at) / 86400.0)
    if soak_days >= min_days:
        return {
            "allowed": True,
            "soak_days": soak_days,
            "min_days": min_days,
            "forced": False,
            "reason": f"running release has soaked {soak_days:.1f}d (≥ {min_days}d)",
        }
    return {
        "allowed": False,
        "soak_days": soak_days,
        "min_days": min_days,
        "forced": False,
        "reason": f"running release has only soaked {soak_days:.1f}d (< {min_days}d) — wait or use --force",
    }


# ── CLI shim (consumed by the bash update scripts) ───────────────────────────
#
# Kept deliberately terse and tab-separated so bash can `read` the fields.


def _cmd_channel(args: list[str]) -> int:
    config_dir = args[0] if args else None
    print(read_channel(config_dir))
    return 0


def _cmd_resolve(args: list[str]) -> int:
    # resolve <main_hash> <stable_tag_hash> [config_dir]
    main_hash = args[0] if len(args) > 0 else ""
    stable_tag_hash = args[1] if len(args) > 1 else ""
    config_dir = args[2] if len(args) > 2 else None
    channel = read_channel(config_dir)
    r = resolve_target(channel, main_hash, stable_tag_hash)
    print(f"{r['channel']}\t{r['ref']}\t{r['hash']}\t{1 if r['fellback'] else 0}\t{r['reason']}")
    return 0


def _cmd_guard(args: list[str]) -> int:
    # guard <deployed_at_epoch|-> <now_epoch|-> <min_days> <force 0|1>
    def _num(v):
        v = (v or "").strip()
        if v in ("", "-"):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    deployed_at = _num(args[0]) if len(args) > 0 else None
    now = _num(args[1]) if len(args) > 1 else None
    min_days = _num(args[2]) if len(args) > 2 else DEFAULT_MIN_SOAK_DAYS
    if min_days is None:
        min_days = DEFAULT_MIN_SOAK_DAYS
    force = len(args) > 3 and str(args[3]).strip() in ("1", "true", "yes", "--force")

    g = promotion_guard(deployed_at, now, min_days, force)
    soak = "-" if g["soak_days"] is None else f"{g['soak_days']:.3f}"
    print(f"{1 if g['allowed'] else 0}\t{soak}\t{g['reason']}")
    return 0 if g["allowed"] else 1


_COMMANDS = {
    "channel": _cmd_channel,
    "resolve": _cmd_resolve,
    "guard": _cmd_guard,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("usage: channels.py {channel|resolve|guard} ...", file=sys.stderr)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f"channels.py: unknown command {cmd!r}", file=sys.stderr)
        return 2
    return fn(rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
