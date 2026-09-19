"""The statusline names the account the session is actually spending.

With five login lanes on one machine (default plus cld2..cld5), the address is
the one thing the line could not answer, and a session running in a second
account looked exactly like the operator's own. `cld2`/`cld3`/… export
CLAUDE_CONFIG_DIR to an isolated profile directory; plain `cld` leaves it unset
and Claude Code reads ~/.claude.json.

The statusline runs on every render, so the hard requirement is that it never
fails loudly: a missing, unreadable, malformed or logged-out config drops the
segment and still exits 0 with the rest of the line intact.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATUSLINE = REPO / "core" / "bin" / "cli" / "statusline"

ANSI = re.compile(r"\x1b\[[0-9;]*m")

SESSION = {
    "cwd": "/tmp",
    "model": {"id": "claude-opus-5[1m]", "display_name": "Claude Opus 5"},
    "context_window": {"used_percentage": 42.0},
    "cost": {
        "total_cost_usd": 1.23,
        "total_duration_ms": 30000,
        "total_lines_added": 0,
        "total_lines_removed": 0,
    },
}


def _run(home: Path, config_dir: Path | None = None):
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    r = subprocess.run(
        ["bash", str(STATUSLINE)],
        input=json.dumps(SESSION), text=True, capture_output=True,
        env=env, timeout=30,
    )
    return r.returncode, ANSI.sub("", r.stdout)


def _write_config(path: Path, email: str | None):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"someOtherKey": 1}
    if email is not None:
        payload["oauthAccount"] = {"emailAddress": email, "accountUuid": "u"}
    path.write_text(json.dumps(payload))


def test_default_lane_reads_home_config(tmp_path):
    """Plain `cld`: no CLAUDE_CONFIG_DIR, so ~/.claude.json is the account."""
    _write_config(tmp_path / ".claude.json", "operator@example.com")

    code, out = _run(tmp_path)

    assert code == 0
    assert "operator@example.com" in out


def test_named_profile_reads_its_own_config(tmp_path):
    """A cldN lane must report ITS account, never the default's."""
    _write_config(tmp_path / ".claude.json", "operator@example.com")
    profile = tmp_path / ".aos" / "claude-profiles" / "cld4"
    _write_config(profile / ".claude.json", "second@example.org")

    code, out = _run(tmp_path, config_dir=profile)

    assert code == 0
    assert "second@example.org" in out
    assert "operator@example.com" not in out, (
        "a profile session must never display the default account"
    )


def test_profile_created_but_not_logged_in_omits_the_segment(tmp_path):
    """`claude-profile add` seeds .claude.json with every account key
    stripped — there is no address to show until the first /login."""
    _write_config(tmp_path / ".claude.json", "operator@example.com")
    profile = tmp_path / ".aos" / "claude-profiles" / "cld5"
    _write_config(profile / ".claude.json", None)

    code, out = _run(tmp_path, config_dir=profile)

    assert code == 0
    assert "@" not in out
    assert "42%" in out, "the rest of the line must still render"


@pytest.mark.parametrize("broken", ["missing", "malformed", "empty"])
def test_never_fails_loudly(tmp_path, broken):
    """The statusline renders on every turn; a bad config must cost the
    segment, not the line."""
    _write_config(tmp_path / ".claude.json", "operator@example.com")
    profile = tmp_path / ".aos" / "claude-profiles" / "cldx"

    if broken == "malformed":
        profile.mkdir(parents=True)
        (profile / ".claude.json").write_text("{not json at all")
    elif broken == "empty":
        profile.mkdir(parents=True)
        (profile / ".claude.json").write_text("")
    # "missing": the directory is never created at all.

    code, out = _run(tmp_path, config_dir=profile)

    assert code == 0
    assert "42%" in out
    assert "operator@example.com" not in out, (
        "must not silently fall back to the default account — that would "
        "misattribute the session"
    )
