"""
Migration 127: comms-extract, people-intel-refresh, loop-sensors off by
default (v0.7.7).

Cross-machine usage audit (~/vault/knowledge/references/never-used-2026-09-13.md):

  comms-extract          27 consecutive nights failing — whatsapp_local
                         adapter hangs to its 302s timeout; imessage/telegram
                         adapters report "Not available".
  people-intel-refresh   28 consecutive nights failing — "Operation not
                         permitted: ~/Library/Mail" (TCC denial), times out
                         at 602s.
  loop-sensors           blocked by scheduling contention with enrich-comms
                         27 of the last 28 nights (enrich-comms holds the
                         serial tick for ~46 min; this job's 5-minute window
                         has almost always already closed by the time the
                         scheduler is free — 1 run in 28 days).

This release ships config/crons.yaml with `enabled: false` set directly on
all three jobs, the same mechanism already used for ascbuild-sync/islah-mirror
(a per-job key the scheduler itself honours — see core/bin/internal/scheduler
and core/infra/reconcile/checks/cron_health.py's `_enabled_crons()`). Unlike
migration 112 (sentinel/converse/envoy), there is no separate instance-side
services.yaml for crons — the scheduler reads ~/aos/config/crons.yaml directly,
so the shipped file is normally already correct the moment a release lands.

This migration exists for the case that isn't: a machine whose already-running
`~/aos/config/crons.yaml` predates this release reaching it (an intermediate
migrate step, a partial sync, or any other path where the old file is still on
disk when migrations run). It patches that live file in place, line-based
rather than a full YAML round-trip — crons.yaml is a long, hand-commented file
and a yaml.safe_load/safe_dump cycle would silently discard every comment in
it (the same reasoning core/infra/lib/default_off.py's docstring gives for its
own line-based fallback writer).

**Respects an explicit opt-in.** If a job block already carries an explicit
`enabled: true` — an operator (or a previous run) deliberately turned it back
on — this migration leaves it alone and does not re-disable it, on this run or
any later one. Only a job with NO `enabled:` key at all (the old implicit-on
default) gets `enabled: false` inserted. A job already `enabled: false` is a
no-op.

Idempotent: check() passes once every target job is either explicitly
`enabled: true` (operator's declaration) or `enabled: false`.
Reversible: flip the job's `enabled:` line to `true` (or delete it, for
loop-sensors/comms-extract/people-intel-refresh specifically the absence of
the key is no longer the shipped default, so `true` is the clearer opt-in).

SECOND, UNRELATED CLEANUP IN THE SAME MIGRATION (same audit, same release —
grouped here rather than given their own migration number because each is a
one-line/one-block deletion with no reversible state to design for): four
config keys confirmed by grep to have zero readers anywhere in core, config,
install.sh, or docs:

  accounts.yaml  schema_version    — no code reads it; migration 116's
                                     unrelated SCHEMA_VERSION is a qren-
                                     readiness *report* field, not this key.
  goals.yaml     recurring_responsibilities — no code reads it. Wider finding:
                                     every reader of "goals.yaml" in the
                                     framework (bridge/heartbeat.py,
                                     bridge/daily_briefing.py,
                                     bridge/intent_classifier.py,
                                     reconcile/checks/bridge_poll_liveness.py)
                                     resolves it at ~/aos/config/goals.yaml
                                     (the framework path, which does not
                                     exist) — not ~/.aos/config/goals.yaml
                                     (the instance path this key actually
                                     lives in). That path mismatch is a
                                     separate, pre-existing bug outside this
                                     migration's scope; it's noted here only
                                     because it's independent confirmation
                                     that this specific key has no reader.
  state.yaml     machine_user,     — no code reads OR writes either key
                 voice_models        (confirmed by grep); pure historical
                                     residue.

config/accounts.example.yaml (the framework template) already drops
schema_version in this same commit. This migration strips the same four keys
from any instance file that still has them, respecting the "framework
declares, instance drifts" split: since goals.yaml and state.yaml have no
framework template at all, stripping the instance copy directly is the only
way to make the removal real for a machine that already exists.

Line-based, not a YAML round-trip, for the same reason as the crons.yaml
patch above: these are hand-maintained files (goals.yaml carries operator
prose) and a safe_load/safe_dump cycle would reformat/reflow content that
has nothing to do with this cleanup.
"""

from __future__ import annotations

DESCRIPTION = (
    "comms-extract/people-intel-refresh/loop-sensors off by default; "
    "drop 4 unread config keys (accounts.schema_version, "
    "goals.recurring_responsibilities, state.machine_user/voice_models)"
)

import re
from pathlib import Path

HOME = Path.home()
CRONS_YAML = HOME / "aos" / "config" / "crons.yaml"
ACCOUNTS_YAML = HOME / ".aos" / "config" / "accounts.yaml"
GOALS_YAML = HOME / ".aos" / "config" / "goals.yaml"
STATE_YAML = HOME / ".aos" / "config" / "state.yaml"

# {path: [dead top-level keys]}
DEAD_KEYS = {
    ACCOUNTS_YAML: ["schema_version"],
    GOALS_YAML: ["recurring_responsibilities"],
    STATE_YAML: ["machine_user", "voice_models"],
}

TARGET_JOBS = ("comms-extract", "people-intel-refresh", "loop-sensors")

# A top-level (column-0) `key:` line — no leading whitespace, so it never
# matches a nested field inside another key's block.
_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][\w.-]*):.*$")

# Matches a top-level job header, e.g. "  comms-extract:" — two-space indent,
# no further indentation, so it never matches a field line inside a block.
_JOB_HEADER = re.compile(r"^  (\S+):\s*$")
_ENABLED_LINE = re.compile(r"^\s*enabled:\s*(true|false)\s*$", re.IGNORECASE)


def _job_blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """{job_name: (header_line_index, block_end_index)} — end is exclusive,
    the index of the next top-level header (or len(lines))."""
    headers = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := _JOB_HEADER.match(line))
    ]
    blocks = {}
    for idx, (line_no, name) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        blocks[name] = (line_no, end)
    return blocks


def _job_enabled_state(lines: list[str], start: int, end: int) -> bool | None:
    """True/False if an explicit `enabled:` line is in this job's block, else None."""
    for line in lines[start + 1:end]:
        m = _ENABLED_LINE.match(line)
        if m:
            return m.group(1).lower() == "true"
    return None


def _read_lines(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    try:
        return path.read_text().splitlines(keepends=True)
    except Exception:  # noqa: BLE001
        return None


def _has_top_level_key(lines: list[str], key: str) -> bool:
    return any(
        (m := _TOP_LEVEL_KEY.match(line)) and m.group(1) == key
        for line in lines
    )


def _remove_top_level_key(lines: list[str], key: str) -> list[str]:
    """Delete a top-level `key:` line and everything indented/blank beneath
    it, up to the next top-level key (or EOF). No-op if key isn't present."""
    start = None
    for i, line in enumerate(lines):
        m = _TOP_LEVEL_KEY.match(line)
        if m and m.group(1) == key:
            start = i
            break
    if start is None:
        return lines
    end = start + 1
    while end < len(lines) and not _TOP_LEVEL_KEY.match(lines[end]):
        end += 1
    return lines[:start] + lines[end:]


def _dead_keys_present() -> dict[Path, list[str]]:
    """{path: [keys still present]} across DEAD_KEYS — empty dict if clean."""
    present: dict[Path, list[str]] = {}
    for path, keys in DEAD_KEYS.items():
        lines = _read_lines(path)
        if lines is None:
            continue
        found = [k for k in keys if _has_top_level_key(lines, k)]
        if found:
            present[path] = found
    return present


def check() -> bool:
    """Applied once crons.yaml's target jobs are all explicit, and none of
    the four dead config keys remain in whichever instance files have them."""
    lines = _read_lines(CRONS_YAML)
    if lines is not None:
        blocks = _job_blocks(lines)
        for job in TARGET_JOBS:
            if job not in blocks:
                continue  # job renamed/removed — not this migration's concern
            start, end = blocks[job]
            if _job_enabled_state(lines, start, end) is None:
                return False
    # else: nothing to patch — a fresh/release install ships the file already correct

    return not _dead_keys_present()


def up() -> bool:
    lines = _read_lines(CRONS_YAML)
    if lines is None:
        print(f"  · {CRONS_YAML} not found — nothing to patch (release ships it pre-set)")
    else:
        blocks = _job_blocks(lines)
        changed = False
        for job in TARGET_JOBS:
            if job not in blocks:
                print(f"  · {job}: not in crons.yaml — skipped")
                continue
            start, end = blocks[job]
            state = _job_enabled_state(lines, start, end)
            if state is True:
                print(f"  ✓ {job}: explicitly opted in (`enabled: true`) — left as-is")
                continue
            if state is False:
                print(f"  ✓ {job}: already `enabled: false`")
                continue
            # No explicit key — insert right after the job header, matching
            # the indentation the rest of the file's `enabled: false` jobs use.
            indent = "    "
            lines.insert(start + 1, f"{indent}enabled: false\n")
            # Every later block's start index shifted by one insertion.
            blocks = _job_blocks(lines)
            changed = True
            print(f"  ✓ {job}: inserted `enabled: false`")

        if changed:
            CRONS_YAML.write_text("".join(lines))
            print(f"     Wrote {CRONS_YAML}")
            print("     Opt back in: set `enabled: true` under the job in crons.yaml")

    for path, keys in DEAD_KEYS.items():
        file_lines = _read_lines(path)
        if file_lines is None:
            continue
        removed = []
        for key in keys:
            if _has_top_level_key(file_lines, key):
                file_lines = _remove_top_level_key(file_lines, key)
                removed.append(key)
        if removed:
            path.write_text("".join(file_lines))
            print(f"  ✓ {path}: removed unread key(s) {', '.join(removed)}")

    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 127 already applied" if check() else ("Done" if up() else "Failed"))
