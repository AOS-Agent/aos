"""Reconcile → work inbox sink (aos#239).

A NOTIFY that only ever reaches ``~/.aos/logs/reconcile.jsonl`` (and, for a
handful of checks, a Telegram ping that gets deduped into silence after the
first occurrence) has no consumer. Measured 2026-09-13: ``dead_code``,
``storage_layout``, ``vault_contract``, ``instance_hygiene``,
``arms_coverage`` and ``context_freshness`` had returned NOTIFY on
essentially every run for months, and nobody had ever acted on one — a
finding with no to-do is silence with extra steps.

This module is the bridge: every NOTIFY becomes exactly one row in the work
inbox (``core/engine/work/backend.py``), keyed by (check name, a stable
fingerprint of its message) so a standing condition updates one row's
``count``/``last_seen`` instead of flooding the inbox with a fresh capture
every cycle. When a check stops NOTIFYing — it resolves to OK or gets
auto-FIXED — its standing inbox row(s) are dropped, so the inbox reflects
what is *currently* true, not everything that was ever wrong.

The item text is built from ``alert_copy.humanize_finding`` — the same
human-copy layer that renders the Telegram alert — plus a fix hint pulled
from any backtick-quoted command in the finding's detail/message. No raw
check slug or traceback ever lands in the inbox verbatim (alert_copy's
jargon-stripping fallback guarantees that even for an untemplated check).

Called from runner.run_all() as a best-effort step: a broken work.db must
never take reconcile down with it, so the call site wraps this in
try/except, same as the Telegram notification.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from base import CheckResult

# reconcile/ itself — for alert_copy, and for `from base import Status` (the
# runner already has this on sys.path, but this module is also imported
# directly by tests, so it cannot assume the caller set this up).
_RECONCILE_DIR = Path(__file__).resolve().parent
if str(_RECONCILE_DIR) not in sys.path:
    sys.path.insert(0, str(_RECONCILE_DIR))

from alert_copy import humanize_finding  # noqa: E402
from base import Status  # noqa: E402

# checks/ -> reconcile/ -> infra/ -> core/ … then core/engine/work holds the
# work backend. Resolved relative to this file, not $HOME, so it works
# whether this is ~/aos, ~/project/aos, or a repo checkout under pytest.
_WORK_DIR = _RECONCILE_DIR.parent.parent / "engine" / "work"
if str(_WORK_DIR) not in sys.path:
    sys.path.insert(0, str(_WORK_DIR))

import backend as work_backend  # noqa: E402

SOURCE_PREFIX = "reconcile"

_DIGIT_RE = re.compile(r"\d[\d,]*")
_CMD_RE = re.compile(r"`([^`]+)`")
_WS_RE = re.compile(r"\s+")


def _fingerprint(summary: str) -> str:
    """A stable hash of a finding's *shape*, not its exact text.

    Fingerprints the humanized summary (alert_copy.humanize_finding), not the
    raw ``CheckResult.message`` — the raw message often embeds the specific
    example that varies every run (dead_code's actual script names,
    instance_hygiene's exact byte count), while the humanized copy already
    generalizes most of that away ("Found N old scripts…"). Digits are then
    normalized out too, for the templates that keep a bare count (e.g.
    arms_coverage's "2 unmanaged, 1 broken"). Without both steps, a check
    whose count/size naturally drifts every run would mint a new inbox row
    every run — exactly the flood this module exists to prevent.
    """
    normalized = _WS_RE.sub(" ", _DIGIT_RE.sub("#", summary or "")).strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _fix_hint(message: str | None, detail: str | None) -> str | None:
    """Pull a backtick-quoted command out of detail/message, if one exists.

    Several checks already spell out the fix as a command (storage_layout:
    "Run `aos storage reconcile` to fix.", instance_hygiene: "Run `aos
    hygiene` to review and clean."). Reusing it beats inventing new copy,
    and it's honest about which checks actually hand the operator a command
    versus which ones just describe the problem.
    """
    for text in (detail, message):
        if not text:
            continue
        m = _CMD_RE.search(text)
        if m:
            return f"run `{m.group(1)}`"
    return None


def _item_text(name: str, summary: str, message: str, detail: str | None) -> str:
    """Check name + human summary + fix hint, in one actionable line.

    ``summary`` is already routed through alert_copy so the inbox never
    carries a raw slug/path/traceback — the same bar the Telegram alert
    holds itself to.
    """
    hint = _fix_hint(message, detail)
    text = f"{name}: {summary}"
    if hint:
        text = f"{text} ({hint})"
    return text


def sync(results: "list[CheckResult]") -> None:
    """Sync one reconcile run's findings into the work inbox.

    - Every result with ``status == Status.NOTIFY`` becomes (or updates) one
      inbox row, deduplicated by (``source=reconcile:<check>``, a stable
      fingerprint of its message).
    - A repeat NOTIFY for the same finding bumps the existing row's count
      and last_seen and refreshes its text — it never adds a second row.
    - A check that resolves this run (OK or FIXED) has every standing
      reconcile-sourced inbox row for that check name dropped: the inbox
      should reflect current reality, not a history of everything that was
      ever wrong.
    """
    notify_names: set[str] = set()

    for r in results:
        if r.status != Status.NOTIFY:
            continue
        notify_names.add(r.name)
        source = f"{SOURCE_PREFIX}:{r.name}"
        summary = humanize_finding(r.name, r.status.value, r.message, r.detail)
        fp = _fingerprint(summary)
        text = _item_text(r.name, summary, r.message, r.detail)
        existing = work_backend.find_inbox_by_fingerprint(source, fp)
        if existing:
            work_backend.touch_inbox(existing["id"], text=text)
        else:
            work_backend.add_inbox(text, source=source, fingerprint=fp)

    resolved_names = {
        r.name for r in results
        if r.status in (Status.OK, Status.FIXED) and r.name not in notify_names
    }
    if not resolved_names:
        return

    for item in work_backend.get_inbox(include_snoozed=True):
        source = item.get("source") or ""
        if not source.startswith(f"{SOURCE_PREFIX}:"):
            continue
        check_name = source.split(":", 1)[1]
        if check_name in resolved_names:
            work_backend.delete_inbox(item["id"])
