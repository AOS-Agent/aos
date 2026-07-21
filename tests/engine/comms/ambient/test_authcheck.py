"""Auth detection: signature matching + engine pauses cleanly (exit 42)."""
from __future__ import annotations

import os

from core.engine.comms.enrich import extract as extract_mod
from core.engine.comms.enrich.authcheck import is_auth_failure
from core.engine.comms.enrich.config import EnrichConfig
from core.engine.comms.enrich.engine import EnrichEngine

from ..enrich._helpers import make_comms_db, msg


def test_signatures_match():
    assert is_auth_failure("Please run /login to continue")
    assert is_auth_failure("", "Invalid API key")
    assert is_auth_failure("oauth token expired")
    assert is_auth_failure("HTTP 401 Unauthorized")
    assert is_auth_failure("Credit balance is too low")


def test_ordinary_errors_do_not_match():
    assert not is_auth_failure("rc=1")
    assert not is_auth_failure("timeout")
    assert not is_auth_failure("parse_fail", "some json broke")
    assert not is_auth_failure(None, "")


class _AuthFailProc:
    """Fake claude process that returns an auth error on stderr, nonzero rc."""
    def __init__(self):
        self.pid = os.getpid()
        self.returncode = 1

    def communicate(self, input=None, timeout=None):
        return "", "Please run /login — your session has expired"

    def wait(self, timeout=None):
        return 1


def _cfg(**kw):
    base = dict(min_batch_msgs=1, max_batch_msgs=40, max_comms_db_bytes=10**12,
                min_disk_free_bytes=0, store_min=0.60, surface_min=0.80)
    base.update(kw)
    return EnrichConfig(**base)


def test_run_batch_flags_auth_failure(monkeypatch):
    monkeypatch.setattr(extract_mod, "_SPAWN", lambda cmd: _AuthFailProc())

    class _B:
        batch_key = "b1"
        messages = [{"id": "m1", "content": "hi", "direction": "inbound"}]
        n = 1
        channel = "whatsapp"

    res = extract_mod.run_batch(_B(), model="haiku", timeout_s=5,
                                max_msg_chars=600, live=extract_mod.LiveGroups())
    assert res["ok"] is False
    assert res["error"] == "auth_failure"
    assert res["auth_failure"] is True


def test_engine_pauses_on_auth_failure(tmp_path, monkeypatch):
    msgs = [msg(f"m{i}", f"content {i}", ts=f"2026-03-09T10:0{i}:00", person_id="p1")
            for i in range(3)]
    comms = make_comms_db(tmp_path / "comms.db", msgs)
    monkeypatch.setattr(extract_mod, "_SPAWN", lambda cmd: _AuthFailProc())

    engine = EnrichEngine(_cfg(), db_path=comms)
    stats = engine.run(mode="nightly")
    assert stats["auth_paused"] is True
    assert stats["stopped_early"] is True
    # nothing extracted (all batches auth-failed → not watermarked → re-selectable)
    assert stats["entities_stored"] == 0
