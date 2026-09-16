"""Tests for the operator-facing iMessage CLIs: comms-send and comms-thread.

Hermetic: every run points AOS_COMMS_CONFIG, AOS_COMMS_LOG and AOS_CHAT_DB at
files under tmp_path. No test reads the operator's real chat.db, comms.yaml or
comms.log, and nothing here can send a message — comms-send is only ever run
with --dry-run or with a recipient the allowlist refuses.

The fake chat.db builder is imported from tests/test_comms_scope.py so the two
suites agree on the schema.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "core" / "bin" / "cli"
_TESTS = _REPO / "tests"

if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
# The repo root, not core/: tests/engine/ is a test package that shadows
# `engine` once pytest puts tests/ on sys.path, so the registry is imported
# as core.engine.comms.registry here.
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from test_comms_scope import (  # noqa: E402
    HISHAM_EMAIL,
    HISHAM_PHONE,
    OTHER_PHONE,
    _build_chat_db,
)

HISHAM_DIGITS = HISHAM_PHONE.lstrip("+")  # "14165550100"

ALLOWLIST_YAML = f"""
imessage:
  access: allowlist
  allowed:
    - name: Hisham
      handles:
        - "{HISHAM_PHONE}"
        - "{HISHAM_EMAIL}"
"""


@pytest.fixture
def sandbox(tmp_path: Path):
    """Config, log and chat.db redirected under tmp_path; returns the env."""
    cfg = tmp_path / "comms.yaml"
    cfg.write_text(ALLOWLIST_YAML)
    log = tmp_path / "logs" / "comms.log"
    db = tmp_path / "chat.db"
    _build_chat_db(db)
    env = {
        **os.environ,
        "AOS_COMMS_CONFIG": str(cfg),
        "AOS_COMMS_LOG": str(log),
        "AOS_CHAT_DB": str(db),
    }
    return {"env": env, "cfg": cfg, "log": log, "db": db}


def _run(tool: str, *args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CLI / tool), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# ── comms-send ────────────────────────────────────────────────────────────


def test_dry_run_by_name_resolves_to_handle(sandbox):
    p = _run("comms-send", "--dry-run", "Hisham", "hello", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"would send to Hisham ({HISHAM_PHONE})"


def test_dry_run_name_is_case_insensitive(sandbox):
    p = _run("comms-send", "--dry-run", "hisham", "hello", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"would send to Hisham ({HISHAM_PHONE})"


def test_dry_run_by_digits_only_number_resolves_to_name(sandbox):
    p = _run("comms-send", "--dry-run", HISHAM_DIGITS, "hello", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    # The raw handle is what gets sent to; the name is looked up for the label.
    assert p.stdout.strip() == f"would send to Hisham ({HISHAM_DIGITS})"


def test_dry_run_by_email_resolves_to_name(sandbox):
    p = _run("comms-send", "--dry-run", HISHAM_EMAIL.upper(), "hi", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"would send to Hisham ({HISHAM_EMAIL.upper()})"


def test_denied_handle_exits_2_with_reason(sandbox):
    p = _run("comms-send", "--dry-run", OTHER_PHONE, "hello", env=sandbox["env"])
    assert p.returncode == 2
    assert p.stdout == ""
    assert "denied:" in p.stderr
    assert OTHER_PHONE in p.stderr
    assert "allowlist" in p.stderr


def test_denied_without_dry_run_never_reaches_the_adapter(sandbox):
    # No --dry-run: the allowlist refuses before any adapter is loaded, so this
    # is safe to run and must still exit 2.
    p = _run("comms-send", OTHER_PHONE, "hello", env=sandbox["env"])
    assert p.returncode == 2
    assert "denied:" in p.stderr


def test_unknown_name_is_treated_as_handle_and_denied(sandbox):
    p = _run("comms-send", "--dry-run", "Nobody", "hello", env=sandbox["env"])
    assert p.returncode == 2
    assert "Nobody" in p.stderr


def test_empty_text_is_a_usage_error(sandbox):
    p = _run("comms-send", "--dry-run", "Hisham", "   ", env=sandbox["env"])
    assert p.returncode == 2
    assert "empty" in p.stderr


def test_send_log_line_has_result_and_no_text(sandbox):
    secret = "the launch code is 4471"
    p = _run("comms-send", "--dry-run", "Hisham", secret, env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    log = sandbox["log"].read_text()
    send_lines = [line for line in log.splitlines() if " send " in line]
    assert len(send_lines) == 1
    line = send_lines[0]
    assert f"to={HISHAM_PHONE}" in line
    assert "name=Hisham" in line
    assert f"chars={len(secret)}" in line
    assert "result=dry-run" in line
    assert secret not in log
    assert "4471" not in log


def test_denied_send_is_logged_as_denied(sandbox):
    _run("comms-send", "--dry-run", OTHER_PHONE, "hello", env=sandbox["env"])
    log = sandbox["log"].read_text()
    assert "result=denied" in log
    assert f"to={OTHER_PHONE}" in log


def test_no_config_means_unrestricted_dry_run(sandbox):
    sandbox["cfg"].unlink()
    p = _run("comms-send", "--dry-run", OTHER_PHONE, "hello", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"would send to {OTHER_PHONE}"


# ── comms-thread ──────────────────────────────────────────────────────────


def test_thread_prints_hisham_messages_only(sandbox):
    # Fixture dates are seconds since 2001 → far in the past; widen the window.
    p = _run("comms-thread", "Hisham", "--days", "20000", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    out = p.stdout
    assert out.splitlines()[0].startswith(f"Hisham ({HISHAM_PHONE}) — 4 messages")
    # Both of Hisham's 1:1 chats (phone + email), oldest first.
    assert "Hisham: hey" in out
    assert "Me: hey back" in out
    assert "Hisham: pr is up" in out
    assert "Hisham: email hello" in out
    # Nothing from anyone else, and nothing from the group chat.
    assert "other person" not in out
    assert "reply to other" not in out
    assert "group msg" not in out
    assert "your code is" not in out


def test_thread_order_is_oldest_first_and_limit_keeps_most_recent(sandbox):
    p = _run(
        "comms-thread", "Hisham", "--days", "20000", "--limit", "2", env=sandbox["env"]
    )
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    assert lines[0].startswith(f"Hisham ({HISHAM_PHONE}) — 2 messages")
    assert lines[1].endswith("Hisham: pr is up")
    assert lines[2].endswith("Hisham: email hello")


def test_thread_json_output(sandbox):
    import json

    p = _run("comms-thread", "Hisham", "--days", "20000", "--json", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data["contact"] == "Hisham"
    assert data["handle"] == HISHAM_PHONE
    assert data["count"] == 4
    texts = [m["text"] for m in data["messages"]]
    assert texts == ["hey", "hey back", "pr is up", "email hello"]
    assert [m["from_me"] for m in data["messages"]] == [False, True, False, False]


def test_thread_window_excludes_old_messages(sandbox):
    p = _run("comms-thread", "Hisham", "--days", "7", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    assert (
        p.stdout.splitlines()[0] == f"Hisham ({HISHAM_PHONE}) — 0 messages, last 7 days"
    )


def test_thread_for_denied_handle_exits_2(sandbox):
    p = _run("comms-thread", OTHER_PHONE, "--days", "20000", env=sandbox["env"])
    assert p.returncode == 2
    assert "denied:" in p.stderr
    # And the gate was never opened for it: no scope line for comms-thread.
    log = sandbox["log"].read_text() if sandbox["log"].exists() else ""
    assert "source=comms-thread" not in log


def test_thread_open_is_logged_through_the_gate(sandbox):
    _run("comms-thread", "Hisham", "--days", "20000", env=sandbox["env"])
    log = sandbox["log"].read_text()
    assert "scope=allowlist source=comms-thread" in log


def test_thread_missing_db_exits_1(sandbox):
    env = {**sandbox["env"], "AOS_CHAT_DB": str(sandbox["db"].parent / "missing.db")}
    p = _run("comms-thread", "Hisham", env=env)
    assert p.returncode == 1
    assert "chat.db: not found" in p.stderr


def test_thread_unrestricted_config_still_shows_only_that_contact(sandbox):
    # access: all → the gate installs nothing; comms-thread must still narrow
    # to the requested contact's 1:1 chats on its own.
    sandbox["cfg"].write_text("imessage:\n  access: all\n")
    p = _run("comms-thread", HISHAM_PHONE, "--days", "20000", env=sandbox["env"])
    assert p.returncode == 0, p.stderr
    out = p.stdout
    assert "hey" in out and "pr is up" in out
    assert "other person" not in out
    assert "group msg" not in out
    assert "email hello" not in out  # no allowlist → no name → phone chat only


# ── registry ──────────────────────────────────────────────────────────────


def test_registry_loads_imessage_adapter():
    from core.engine.comms import registry

    adapter = registry.load_adapter("messages")
    assert adapter is not None
    assert type(adapter).__name__ == "iMessageAdapter"


def test_registry_unknown_channel_is_none():
    from core.engine.comms import registry

    assert registry.load_adapter("carrier-pigeon") is None
