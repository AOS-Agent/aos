"""Tests for reconcile/inbox_sink.py — the bridge that gives a NOTIFY a
consumer (aos#239).

Contract under test:
  - A NOTIFY becomes exactly one work-inbox row, deduplicated by (check name,
    a stable fingerprint of the message).
  - Re-notifying the same finding updates that row's count/last_seen instead
    of adding another.
  - A finding that resolves (OK or FIXED) has its standing inbox row(s)
    dropped — the inbox reflects current reality, not history.
  - Two different checks never collide into one row.

Isolated: uses the `work_env` fixture (throwaway AOS_WORK_DB), same as every
other work-engine test — inbox_sink imports `backend` under the exact same
module name, so monkeypatching `backend.DB_PATH`/`_adapter` in the fixture
redirects inbox_sink's calls too. Never touches a real DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_RECONCILE_DIR = _ROOT / "core" / "infra" / "reconcile"
if str(_RECONCILE_DIR) not in sys.path:
    sys.path.insert(0, str(_RECONCILE_DIR))

import base  # noqa: E402
import inbox_sink  # noqa: E402

CheckResult = base.CheckResult
Status = base.Status


def _notify(name: str, message: str, detail: str | None = None) -> CheckResult:
    return CheckResult(name, Status.NOTIFY, message, detail=detail, notify=True)


def _ok(name: str) -> CheckResult:
    return CheckResult(name, Status.OK, "ok")


def _fixed(name: str, message: str = "fixed it") -> CheckResult:
    return CheckResult(name, Status.FIXED, message)


def _reconcile_items(eng):
    return [i for i in eng.get_inbox() if (i.get("source") or "").startswith("reconcile:")]


def test_notify_twice_creates_one_item_with_count_2(work_env):
    eng = work_env["engine"]

    inbox_sink.sync([_notify("dead_code", "3 orphaned bin scripts: foo, bar, baz")])
    items = _reconcile_items(eng)
    assert len(items) == 1
    assert items[0]["count"] == 1

    # Re-notify with a slightly different count (byte/item counts drift run to
    # run for a standing finding) — still the same finding.
    inbox_sink.sync([_notify("dead_code", "4 orphaned bin scripts: foo, bar, baz, qux")])
    items = _reconcile_items(eng)
    assert len(items) == 1, "a repeat NOTIFY must update the existing row, not add one"
    assert items[0]["count"] == 2
    assert "dead_code" in items[0]["text"]


def test_ok_after_notify_closes_item(work_env):
    eng = work_env["engine"]

    inbox_sink.sync([_notify("storage_layout", "2 directories not on data drive (1.0GB local)")])
    assert len(_reconcile_items(eng)) == 1

    inbox_sink.sync([_ok("storage_layout")])
    assert _reconcile_items(eng) == [], "a resolved check must drop its standing inbox item"


def test_fixed_after_notify_also_closes_item(work_env):
    """FIXED (auto-repaired) is a resolution too, not just OK."""
    eng = work_env["engine"]

    inbox_sink.sync([_notify("context_freshness", "Would fix: CLAUDE.md dynamic content matches system state")])
    assert len(_reconcile_items(eng)) == 1

    inbox_sink.sync([_fixed("context_freshness", "Updated context: services list -> 5 services")])
    assert _reconcile_items(eng) == []


def test_two_different_checks_create_two_items(work_env):
    eng = work_env["engine"]

    inbox_sink.sync([
        _notify("dead_code", "3 orphaned bin scripts"),
        _notify("vault_contract", "Vault inventory refreshed: 900 docs, 12 with contract violations"),
    ])

    items = _reconcile_items(eng)
    assert len(items) == 2
    names = {i["text"].split(":", 1)[0] for i in items}
    assert names == {"dead_code", "vault_contract"}


def test_one_check_resolving_does_not_touch_another(work_env):
    eng = work_env["engine"]
    inbox_sink.sync([
        _notify("dead_code", "3 orphaned bin scripts"),
        _notify("vault_contract", "900 docs, 12 with contract violations"),
    ])
    assert len(_reconcile_items(eng)) == 2

    # Only dead_code resolves this run; vault_contract isn't mentioned at all
    # (e.g. it didn't run) so it must be left untouched.
    inbox_sink.sync([_ok("dead_code")])

    items = _reconcile_items(eng)
    assert len(items) == 1
    assert items[0]["text"].startswith("vault_contract:")


def test_manual_inbox_captures_are_never_touched(work_env):
    eng = work_env["engine"]
    manual = eng.add_inbox("Buy milk")

    inbox_sink.sync([_notify("dead_code", "3 orphaned bin scripts")])
    inbox_sink.sync([_ok("dead_code")])  # resolves and closes the reconcile row

    remaining = {i["id"] for i in eng.get_inbox()}
    assert manual["id"] in remaining
    assert _reconcile_items(eng) == []


def test_non_notify_statuses_never_create_inbox_items(work_env):
    eng = work_env["engine"]
    inbox_sink.sync([_ok("dead_code"), _fixed("context_freshness")])
    assert eng.get_inbox() == []
