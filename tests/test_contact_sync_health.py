"""Tests for the contact_sync_health reconcile check.

contact-sync (core/engine/comms/sync_contacts.py) now records "ok" /
"denied" / "not_found" to ~/.aos/data/contact-sync-status.json on every run
(aos#240.4). The script itself always exits 0 on a denial — a TCC denial is
an expected, recoverable state, not a crash — so the generic exit-code-based
cron_health check would never flag it. This check reads the status file
directly and is the one thing that turns a persistent AddressBook access
failure into a NOTIFY, instead of it reading as quiet success forever (the
exact 90%-of-runs blind spot the 2026-09-13 cron audit found).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECK_PATH = REPO / "core" / "infra" / "reconcile" / "checks" / "contact_sync_health.py"


def _mod():
    sys.path.insert(0, str(REPO / "core" / "infra" / "reconcile"))
    spec = importlib.util.spec_from_file_location("contact_sync_health_under_test", CHECK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["contact_sync_health_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def csh(tmp_path, monkeypatch):
    mod = _mod()
    status_path = tmp_path / "contact-sync-status.json"
    monkeypatch.setattr(mod, "STATUS_FILE", status_path)
    return mod, status_path


def _write(path: Path, result: str, detail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_run": "2026-09-13T05:30:00+00:00",
                                 "result": result, "detail": detail}))


def test_never_run_yet_is_ok_not_broken(csh):
    mod, status_path = csh
    assert not status_path.exists()
    assert mod.ContactSyncHealthCheck().check() is True


def test_ok_result_passes(csh):
    mod, status_path = csh
    _write(status_path, "ok", "12 contact(s) read")
    assert mod.ContactSyncHealthCheck().check() is True


def test_not_found_is_not_treated_as_broken(csh):
    """A machine with genuinely no AddressBook (no Contacts ever configured)
    isn't a health problem to keep NOTIFYing about."""
    mod, status_path = csh
    _write(status_path, "not_found", "no AddressBook-v*.abcddb anywhere")
    assert mod.ContactSyncHealthCheck().check() is True


def test_denied_result_fails_check(csh):
    mod, status_path = csh
    _write(status_path, "denied", "AddressBook access denied — grant Contacts/FDA")
    assert mod.ContactSyncHealthCheck().check() is False


def test_fix_notifies_with_the_recorded_detail(csh):
    mod, status_path = csh
    _write(status_path, "denied", "AddressBook access denied — grant Contacts/FDA")
    result = mod.ContactSyncHealthCheck().fix()
    assert result.status == mod.Status.NOTIFY
    assert "denied" in result.message.lower() or "denied" in (result.detail or "").lower()


def test_fix_is_ok_when_not_denied(csh):
    mod, status_path = csh
    _write(status_path, "ok", "5 contact(s) read")
    result = mod.ContactSyncHealthCheck().fix()
    assert result.status == mod.Status.OK


def test_corrupted_status_file_is_treated_as_unobserved_not_crash(csh):
    mod, status_path = csh
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text("{not json")
    assert mod.ContactSyncHealthCheck().check() is True
