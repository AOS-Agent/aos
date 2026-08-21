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

Two more gates live here as of v0.8.0, for the same reason: they decide whether
an update happens at all, and a decision that important must be testable
without a git remote or a second Mac.

    freeze     ~/.aos/config/channel-update.yaml `frozen: true` stops the
               system offering feature updates. Patches still flow.
    host scope some machines must never be updated by this system at all.
               `excluded_host()` names them.

Everything here is pure logic — no git calls, and the only I/O is reading two
small config files — so resolution, the promotion guard, the freeze gate and
the host guard are all unit-testable. The update scripts (``check-update``,
``release-manager``) and ``aos promote`` shell out to the subcommands at the
bottom for the derived values.
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


# ── Freeze (v0.8.0) ──────────────────────────────────────────────────────────
#
# AOS v0.8.0 is the last feature release. Freezing is a declaration the machine
# holds, not a property of the server: `frozen: true` in
# ~/.aos/config/channel-update.yaml. A frozen machine stops being offered
# feature updates and keeps taking patches, so a security fix still lands on a
# system nobody is developing any more.
#
# "Patch" is decided by the VERSION numbers, not by trust in the sender: same
# MAJOR.MINOR, greater PATCH. 0.8.0 → 0.8.1 flows; 0.8.0 → 0.9.0 does not. A
# machine whose remote version cannot be read fails CLOSED (no update offered)
# — the opposite of the services opt-out, deliberately: there, failing open
# keeps the operator's bridge running; here, failing open would push an
# unknown release onto a machine that asked to stop receiving them.

# The flag lives in its OWN file. `channel-update.yaml` was the obvious name
# and is already taken: on machines going back to March it holds the hourly
# Telegram status-update settings (forum_topic_id, include: {...}). A freeze
# migration that wrote a fresh `frozen: true` document there would silently
# delete a working config for an unrelated feature — the kind of collision that
# is invisible until someone asks why the hourly updates stopped. So the policy
# gets update-policy.yaml, and channel-update.yaml is only ever READ, never
# written, in case an operator set the flag there by hand.
FREEZE_FILE = "update-policy.yaml"
LEGACY_FREEZE_FILES = ("channel-update.yaml",)


def freeze_config_path(config_dir=None) -> Path:
    """The file the freeze flag is WRITTEN to."""
    base = Path(config_dir) if config_dir else Path.home() / ".aos" / "config"
    return base / FREEZE_FILE


def _frozen_in(path: Path) -> bool:
    try:
        text = path.read_text()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    try:
        import yaml
        raw = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        return False
    return isinstance(raw, dict) and raw.get("frozen") is True


def is_frozen(config_dir=None) -> bool:
    """True when this machine has declared itself frozen.

    Total by construction: a missing file, unreadable file, malformed YAML, or
    a non-boolean `frozen` all mean "not frozen". A machine must never be
    accidentally frozen by a typo — that failure mode is silent and lasts until
    someone notices months of missed patches.
    """
    base = Path(config_dir) if config_dir else Path.home() / ".aos" / "config"
    if _frozen_in(base / FREEZE_FILE):
        return True
    return any(_frozen_in(base / name) for name in LEGACY_FREEZE_FILES)


def parse_version(raw: str | None) -> tuple[int, int, int] | None:
    """('v0.8.1' | '0.8.1' | '0.8.1-dff5c0d') → (0, 8, 1). Unparseable → None."""
    if not raw:
        return None
    text = raw.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = text.split(".")
    if len(parts) < 3:
        return None
    try:
        return tuple(int(p) for p in parts[:3])  # type: ignore[return-value]
    except ValueError:
        return None


def is_patch_upgrade(current: str | None, candidate: str | None) -> bool:
    """True when candidate is a PATCH bump of current (same major.minor, higher patch)."""
    cur = parse_version(current)
    cand = parse_version(candidate)
    if cur is None or cand is None:
        return False
    return cur[0] == cand[0] and cur[1] == cand[1] and cand[2] > cur[2]


def freeze_gate(current: str | None, candidate: str | None, frozen: bool) -> dict:
    """Decide whether an available update may be offered on this machine.

    Returns: allowed(bool), frozen(bool), kind(str), reason(str).
    `kind` is one of: not-frozen, patch, feature, unknown.
    """
    if not frozen:
        return {"allowed": True, "frozen": False, "kind": "not-frozen",
                "reason": "machine is not frozen"}

    cur = parse_version(current)
    cand = parse_version(candidate)
    if cur is None or cand is None:
        return {"allowed": False, "frozen": True, "kind": "unknown",
                "reason": f"frozen and cannot compare versions "
                          f"({current!r} → {candidate!r}) — not offering"}

    if is_patch_upgrade(current, candidate):
        return {"allowed": True, "frozen": True, "kind": "patch",
                "reason": f"frozen, but {current} → {candidate} is a patch"}

    return {"allowed": False, "frozen": True, "kind": "feature",
            "reason": f"frozen — {current} → {candidate} is not a patch, not offering"}


# ── Host scope guard ─────────────────────────────────────────────────────────
#
# One machine on this tailnet belongs to someone else (a Mac mini shared in
# from another account). AOS runs there and must stay exactly as it is: the
# v0.8.0 rollout does not touch it. That is a decision about a person's
# computer, so it is enforced in code on the machine itself rather than by
# remembering not to run a command.
#
# Matching is on identity strings, never on a normalized ComputerName. The two
# minis are literally "Agent's Mac mini" and "Agent's Mac mini (2)"; strip the
# punctuation and they collide, and the guard would refuse to update the very
# machine the release rolls out to first. The excluded machine's LocalHostName
# is "Agents-Mac-mini" and its tailscale name "agents-mac-mini-2"; this one's
# are "agentalhadi" / "agents-mac-mini". Those are distinguishable, so match
# them exactly and leave ComputerName out of it.

EXCLUDED_HOST_PATTERNS = (
    "agents-mac-mini-2",   # tailscale hostname (prefix match: .local, .ts.net)
    "agents-mac-mini.local",  # LocalHostName-derived hostname of the excluded mini
)

# Never excluded, whatever else matches. The release machine.
HOST_ALLOWLIST = ("agentalhadi",)

OVERRIDE_FILE = "allow-updates"


def _host_candidates(hostname: str | None, local_hostname: str | None) -> list[str]:
    return [h.strip().lower() for h in (hostname, local_hostname) if h and h.strip()]


def excluded_host(hostname=None, local_hostname=None, config_dir=None) -> dict:
    """Is this machine excluded from AOS updates?

    Returns: excluded(bool), matched(str|None), reason(str).

    An operator on an excluded machine can override with a file
    (~/.aos/config/allow-updates) — the guard is a safety default about someone
    else's computer, not a lock on their own.
    """
    import socket

    if hostname is None:
        hostname = socket.gethostname()

    names = _host_candidates(hostname, local_hostname)
    if not names:
        return {"excluded": False, "matched": None, "reason": "no hostname to test"}

    for allowed in HOST_ALLOWLIST:
        for n in names:
            if n == allowed or n == f"{allowed}.local":
                return {"excluded": False, "matched": n,
                        "reason": f"{n} is on the host allowlist"}

    override = freeze_config_path(config_dir).parent / OVERRIDE_FILE
    for pattern in EXCLUDED_HOST_PATTERNS:
        for n in names:
            if n == pattern or n.startswith(pattern + ".") or n.startswith(pattern + "-"):
                if override.exists():
                    return {"excluded": False, "matched": n,
                            "reason": f"{n} is excluded, but {override.name} overrides it"}
                return {"excluded": True, "matched": n,
                        "reason": f"{n} is excluded from AOS updates "
                                  f"(create {override} to override)"}

    return {"excluded": False, "matched": None, "reason": "host not excluded"}


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


def _cmd_frozen(args: list[str]) -> int:
    # frozen [config_dir] — exit 0 if frozen, 1 if not. Prints true/false.
    config_dir = args[0] if args else None
    frozen = is_frozen(config_dir)
    print("true" if frozen else "false")
    return 0 if frozen else 1


def _cmd_freeze_gate(args: list[str]) -> int:
    # freeze-gate <current_version> <candidate_version> [config_dir]
    # Echoes TAB-separated: allowed(0|1)  kind  reason
    current = args[0] if len(args) > 0 else ""
    candidate = args[1] if len(args) > 1 else ""
    config_dir = args[2] if len(args) > 2 else None
    g = freeze_gate(current, candidate, is_frozen(config_dir))
    print(f"{1 if g['allowed'] else 0}\t{g['kind']}\t{g['reason']}")
    return 0 if g["allowed"] else 1


def _cmd_host_scope(args: list[str]) -> int:
    # host-scope [hostname] [local_hostname] [config_dir]
    # Exit 0 when this host MAY update, 1 when it is excluded.
    hostname = args[0] if len(args) > 0 and args[0] != "-" else None
    local_hostname = args[1] if len(args) > 1 and args[1] != "-" else None
    config_dir = args[2] if len(args) > 2 else None
    r = excluded_host(hostname, local_hostname, config_dir)
    print(f"{1 if r['excluded'] else 0}\t{r['matched'] or '-'}\t{r['reason']}")
    return 1 if r["excluded"] else 0


_COMMANDS = {
    "channel": _cmd_channel,
    "resolve": _cmd_resolve,
    "guard": _cmd_guard,
    "frozen": _cmd_frozen,
    "freeze-gate": _cmd_freeze_gate,
    "host-scope": _cmd_host_scope,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("usage: channels.py {channel|resolve|guard|frozen|freeze-gate|host-scope} ...", file=sys.stderr)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f"channels.py: unknown command {cmd!r}", file=sys.stderr)
        return 2
    return fn(rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
