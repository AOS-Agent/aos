"""claude_lanes — one resolver every headless `claude` spawn calls through.

A "lane" is a login: `default` (the operator's own `~/.claude`) or a Claude
profile name (`core/bin/cli/claude-profile`, aos#244.1/2) — `cld2`, `cld3`,
whatever the operator has added. Lanes exist so a headless caller (the
bridge, Sentinel, the memory-curate cron) can fail over to a second account
when the first has hit a usage limit, instead of going silent until a human
notices and re-logs in.

Configuration is entirely operator-owned:

    ~/.aos/config/claude-lanes.yaml
        lanes: [default, cld2, cld3]

Missing file -> `["default"]` only, i.e. exactly today's behaviour: one
lane, no CLAUDE_CONFIG_DIR override, no failover. This module never creates
that file — `claude-profile add <name>` appends to it *if it already
exists* (see claude-profile's `_append_lane_if_configured`), and otherwise
leaves lane configuration entirely to the operator.

State — which lanes are currently exhausted, and until when — lives at:

    ~/.aos/state/claude-lanes.json
        {"<lane>": {"exhausted_until": "<iso8601>"}}

Two ways to use this module:

  run(argv, *, stdin=None, timeout=None, cwd=None, env=None)
      The one-shot case: a direct replacement for
      `subprocess.run(argv, input=stdin, timeout=timeout, cwd=cwd,
      capture_output=True, text=True)`. Picks the first non-exhausted lane,
      runs it, and — only if THIS run's own output reports a usage-limit
      hit — retries once on the next lane, and so on, never more than
      `len(lanes)` attempts. If every lane is already exhausted before the
      first attempt, it makes exactly one real call anyway (the caller must
      see an honest failure, never a fabricated one) and does not cycle
      through the rest — they are already known-bad.

  The building blocks (`load_lanes`, `load_state`, `pick_lane`,
  `resolve_config_dir`, `detect_limit`, `parse_reset_time`,
  `record_exhaustion`) for a caller that manages its own process — the
  bridge's persistent session holds a long-lived `claude` process rather
  than making one-shot calls, so it applies a lane's env at spawn time
  itself and calls `record_exhaustion()` when a limit is reported, then
  lets its own existing crash-recovery restart pick the next lane. See
  `core/services/bridge/persistent_session.py`.

Every function re-resolves `Path.home()` on each call rather than freezing
it into a module constant — this module gets imported once and kept alive
inside long-running services (the bridge, Sentinel), and a frozen constant
would go stale the moment a test (or a real profile switch) changes HOME
out from under an already-imported process. Same discipline migrations
108+ adopted for the same reason (core/infra/lib/default_off.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger("aos.claude_lanes")

DEFAULT_LANES = ["default"]
DEFAULT_EXHAUSTION = timedelta(hours=5)


def _home() -> Path:
    return Path.home()


def _config_path() -> Path:
    return _home() / ".aos" / "config" / "claude-lanes.yaml"


def _state_path() -> Path:
    return _home() / ".aos" / "state" / "claude-lanes.json"


def _repo_root() -> Path:
    """This file lives at <root>/core/infra/lib/claude_lanes.py — three
    parents up is <root>. Resolved from `__file__`, not `Path.home()/"aos"`,
    so this module always finds ITS OWN sibling `claude-profile` and work
    engine, whether it is running from the shipped `~/aos` or (as in every
    test in this suite) a dev worktree that does not touch `~/aos` at all."""
    return Path(__file__).resolve().parents[3]


def _claude_profile_cli() -> Path:
    return _repo_root() / "core" / "bin" / "cli" / "claude-profile"


# ── lanes.yaml ────────────────────────────────────────────────────────────

def load_lanes() -> list[str]:
    """The configured lane order, deduplicated, or `["default"]` if the
    operator has not created ~/.aos/config/claude-lanes.yaml — the file is
    never auto-created by this module."""
    path = _config_path()
    if not path.exists():
        return list(DEFAULT_LANES)
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:  # noqa: BLE001 — a broken config must degrade, not crash a spawn
        logger.warning("claude_lanes: could not parse %s (%s) — using default lane only", path, e)
        return list(DEFAULT_LANES)
    if not isinstance(data, dict):
        return list(DEFAULT_LANES)
    raw = data.get("lanes")
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_LANES)
    seen: list[str] = []
    for entry in raw:
        name = str(entry)
        if name not in seen:
            seen.append(name)
    return seen or list(DEFAULT_LANES)


# ── state ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".claude-lanes-tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _exhausted_until(lane: str, state: dict) -> datetime | None:
    entry = state.get(lane)
    if not isinstance(entry, dict):
        return None
    raw = entry.get("exhausted_until")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def is_exhausted(lane: str, state: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    until = _exhausted_until(lane, state)
    return until is not None and now < until


def pick_lane(lanes: list[str], state: dict, now: datetime | None = None) -> str | None:
    """The first lane in `lanes` that is not currently exhausted, or None if
    every one of them is."""
    now = now or datetime.now()
    for lane in lanes:
        if not is_exhausted(lane, state, now):
            return lane
    return None


# ── CLAUDE_CONFIG_DIR resolution ────────────────────────────────────────────

def resolve_config_dir(lane: str) -> str | None:
    """The CLAUDE_CONFIG_DIR value for *lane*, or None for the default
    profile (`~/.claude` — no override)."""
    if lane == "default":
        return None
    cli = _claude_profile_cli()
    if not cli.exists():
        return None
    try:
        result = subprocess.run(
            [str(cli), "path", lane], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    return out or None


# ── usage-limit detection ───────────────────────────────────────────────────

_LIMIT_PATTERNS = (
    re.compile(r"you.?ve hit your weekly limit", re.IGNORECASE),
    re.compile(r"you.?ve hit your session limit", re.IGNORECASE),
    # "<model> limit" generically — also matches weekly/session limit, kept
    # as its own pattern anyway so the three documented messages each have
    # an obvious, literal home in this tuple.
    re.compile(r"you.?ve hit your [a-z0-9.\-]+(?: [a-z0-9.\-]+)? limit", re.IGNORECASE),
    re.compile(r"spend limit reached", re.IGNORECASE),
    re.compile(r"usage limit|rate limit.*reset", re.IGNORECASE),
)


def detect_limit(stdout: str, stderr: str = "") -> str | None:
    """The matched phrase if *stdout*/*stderr* report a usage or rate limit,
    else None. Works on raw text (`--output-format text`, the stream-json
    event stream) and on `--output-format json`'s single blob — the phrase
    is present as plain text inside the JSON either way, but a successful
    parse also pulls the `result`/`error`/`message` fields out explicitly so
    a caller that leans on the `is_error` field is covered too."""
    haystack_parts = [stdout or "", stderr or ""]
    try:
        parsed = json.loads((stdout or "").strip())
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        for key in ("result", "error", "message"):
            value = parsed.get(key)
            if isinstance(value, str):
                haystack_parts.append(value)
    haystack = "\n".join(haystack_parts)
    for pattern in _LIMIT_PATTERNS:
        match = pattern.search(haystack)
        if match:
            return match.group(0)
    return None


# ── reset-time parsing ───────────────────────────────────────────────────────

_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
_EPOCH_RE = re.compile(r"resets?\b[^0-9]{0,10}(\d{10,13})\b", re.IGNORECASE)
_RESET_IN_RE = re.compile(
    r"resets?\s+in\s+(?:(\d+)\s*(?:hr|hour)s?\b)?\s*(?:(\d+)\s*(?:min|minute)s?\b)?",
    re.IGNORECASE,
)
_RESET_AT_RE = re.compile(r"resets?\s+at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", re.IGNORECASE)


def parse_reset_time(text: str, now: datetime | None = None) -> datetime:
    """Parse a reset time out of a usage-limit message. Recognizes an ISO
    8601 timestamp, an epoch (10-13 digit) timestamp, "resets in H hr M
    min", and "resets at 3(:30)?(am|pm)?" — in that order. Falls back to
    `now + 5 hours` when the message carries no parseable time at all."""
    now = now or datetime.now()
    text = text or ""

    iso_match = _ISO_RE.search(text)
    if iso_match:
        try:
            return datetime.fromisoformat(iso_match.group(1).replace("Z", "+00:00"))
        except ValueError:
            pass

    epoch_match = _EPOCH_RE.search(text)
    if epoch_match:
        raw = epoch_match.group(1)
        try:
            ts = int(raw) / 1000 if len(raw) >= 13 else int(raw)
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError, OverflowError):
            pass

    in_match = _RESET_IN_RE.search(text)
    if in_match and (in_match.group(1) or in_match.group(2)):
        hours = int(in_match.group(1) or 0)
        minutes = int(in_match.group(2) or 0)
        return now + timedelta(hours=hours, minutes=minutes)

    at_match = _RESET_AT_RE.search(text)
    if at_match:
        hour = int(at_match.group(1))
        minute = int(at_match.group(2) or 0)
        ampm = (at_match.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        candidate = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    return now + DEFAULT_EXHAUSTION


# ── the work-engine inbox note ───────────────────────────────────────────────

_work_backend = None


def _load_work_backend():
    """`core/engine/work/backend.py`'s `add_inbox`/`find_inbox_by_fingerprint`,
    loaded by file path (same trick core/engine/notify/router.py uses) so this
    module needs no package context. None if the work engine is not available
    — a missing inbox note must never cost a lane switch."""
    global _work_backend
    if _work_backend is None:
        try:
            import importlib.util
            path = _repo_root() / "core" / "engine" / "work" / "backend.py"
            spec = importlib.util.spec_from_file_location("aos_claude_lanes_work_backend", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _work_backend = mod
        except Exception as e:  # noqa: BLE001
            logger.warning("claude_lanes: could not load the work engine for an inbox note (%s)", e)
            _work_backend = False
    return _work_backend or None


def _notify_inbox(lane: str, reset_at: datetime, next_lane: str | None) -> None:
    backend = _load_work_backend()
    if backend is None:
        return
    fingerprint = f"{lane}:{reset_at.isoformat()}"
    try:
        if backend.find_inbox_by_fingerprint("lanes", fingerprint):
            return  # already noted — one item per lane per exhaustion
        reset_str = reset_at.strftime("%H:%M")
        if next_lane:
            text = f"[lanes] {lane} exhausted until {reset_str} — switched to {next_lane}"
        else:
            text = f"[lanes] {lane} exhausted until {reset_str} — no lanes available"
        backend.add_inbox(text, source="lanes", fingerprint=fingerprint)
    except Exception as e:  # noqa: BLE001
        logger.warning("claude_lanes: could not write an inbox note for %s (%s)", lane, e)


def record_exhaustion(lane: str, text: str, *, now: datetime | None = None) -> datetime:
    """Mark *lane* exhausted (parsing a reset time out of *text*), persist
    it, and append one deduped inbox line naming the next lane that will be
    tried. Returns the computed `exhausted_until`. Shared by `run()`'s own
    retry loop and by a caller managing a long-lived process of its own
    (persistent_session.py)."""
    now = now or datetime.now()
    reset_at = parse_reset_time(text, now)

    state = load_state()
    state[lane] = {"exhausted_until": reset_at.isoformat()}
    save_state(state)

    remaining = [l for l in load_lanes() if l != lane]
    next_lane = pick_lane(remaining, state, now)
    logger.info("claude_lanes: %s exhausted until %s%s", lane, reset_at.isoformat(),
                f" — switching to {next_lane}" if next_lane else " — no lanes left")
    _notify_inbox(lane, reset_at, next_lane)
    return reset_at


# ── the one-shot entry point ─────────────────────────────────────────────────

def _run_once(argv: list[str], lane: str, *, stdin, timeout, cwd,
              env: dict | None) -> subprocess.CompletedProcess:
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    config_dir = resolve_config_dir(lane)
    if config_dir:
        run_env["CLAUDE_CONFIG_DIR"] = config_dir
    else:
        run_env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        argv, input=stdin, timeout=timeout, cwd=cwd, env=run_env,
        capture_output=True, text=True,
    )


def run(argv: list[str], *, stdin: str | None = None, timeout: float | None = None,
        cwd: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run `claude` (or whatever *argv* is) through the first available
    lane, failing over on a detected usage-limit hit.

    `stdin`/`timeout`/`cwd` mirror `subprocess.run`'s `input`/`timeout`/`cwd`
    exactly — a caller migrating a direct `subprocess.run([...],
    input=..., timeout=..., cwd=..., capture_output=True, text=True)` call
    changes nothing about what it passes in or reads back out. `env` is an
    extra: a dict merged on top of the current process's environment (and
    then the lane's own CLAUDE_CONFIG_DIR) for a caller that needs its own
    additional variables alongside lane resolution (e.g. Sentinel's
    SENTINEL_TRIGGER_ID) — not part of the original spec, added because
    wiring that call site faithfully needs it; see the migration/CHANGELOG
    notes for aos#244.3.

    A TimeoutExpired from the underlying subprocess.run is not caught —
    propagates exactly as it would have before this wrapper existed.
    """
    lanes = load_lanes()
    now = datetime.now()
    state = load_state()

    first_lane = pick_lane(lanes, state, now)
    if first_lane is None:
        # Every lane already looks exhausted before we've even tried one.
        # Still make a real call — the caller must see an honest failure,
        # never a fabricated one — but there is no point cycling through
        # the rest: we already know their state.
        lane = lanes[0]
        result = _run_once(argv, lane, stdin=stdin, timeout=timeout, cwd=cwd, env=env)
        matched = detect_limit(result.stdout or "", result.stderr or "")
        if matched:
            text = (result.stdout or "") + "\n" + (result.stderr or "")
            record_exhaustion(lane, text, now=now)
        return result

    tried: list[str] = []
    result = None
    lane = first_lane
    for _ in range(len(lanes)):
        tried.append(lane)
        result = _run_once(argv, lane, stdin=stdin, timeout=timeout, cwd=cwd, env=env)

        matched = detect_limit(result.stdout or "", result.stderr or "")
        if not matched:
            return result

        text = (result.stdout or "") + "\n" + (result.stderr or "")
        record_exhaustion(lane, text, now=now)
        state = load_state()

        remaining = [l for l in lanes if l not in tried]
        lane = pick_lane(remaining, state, now)
        if lane is None:
            break

    return result
