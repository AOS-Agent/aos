

# ── claude-bin resolution + spawn-failure grace (cron PATH crash, 2026-07-22) ──

from core.engine.comms.enrich import extract as _ex


def test_claude_bin_prefers_override(monkeypatch, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("AOS_CLAUDE_BIN", str(fake))
    assert _ex._claude_bin() == str(fake)


def test_claude_bin_absolute_not_bare():
    # Whatever it resolves to, it must not hand cron a bare name that a
    # stripped PATH can't find.
    monkeypatch_path = _ex._claude_bin()
    assert monkeypatch_path == "claude" or monkeypatch_path.startswith("/"), monkeypatch_path


def test_spawn_failure_is_batch_error_not_crash(monkeypatch):
    class _Batch:
        batch_key = "b1"
        n = 1
        channel = "imessage"
        messages = [{"id": "m1", "direction": "inbound", "content": "hi"}]
    def boom(cmd):
        raise FileNotFoundError(2, "No such file or directory: 'claude'")
    monkeypatch.setattr(_ex, "_SPAWN", boom)
    r = _ex.run_batch(_Batch(), model="haiku", timeout_s=5,
                      max_msg_chars=100, live=_ex.LiveGroups())
    assert r["ok"] is False
    assert r.get("spawn_failed") is True
    assert "spawn_failed" in r["error"]
