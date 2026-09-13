"""GoogleWorkspaceCheck (aos#2326): gws CLI health, plus credential-file
permission hardening.

The `gws` CLI (a third-party Homebrew binary) is invoked via
`core/bin/internal/gws-account` with `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`
pointed at `~/.aos/config/google/credentials/<email>.json` — gws itself
requires this file to exist on disk in its own format, so moving the OAuth
refresh token into Keychain would break gws outright. The honest fix (see
migration 134) is to make the file 0600 and keep it out of backup/sync paths
— and have this reconcile check NOTIFY, not silently pass, whenever a
credential file's permissions have drifted looser than that (e.g. after a
`gws` token refresh rewrites it with a permissive umask).

Pins: a world/group-readable credential file is caught even when every other
invariant (binary, wrapper, secrets, legacy MCP) is healthy, and the pre-
existing checks all still behave exactly as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core/infra/reconcile"))
sys.path.insert(0, str(REPO / "core/infra/reconcile/checks"))

from base import Status  # noqa: E402
from google_workspace import GoogleWorkspaceCheck  # noqa: E402


def _cred_name(local_part: str) -> str:
    """A `<email>.json` credential filename, assembled at runtime rather
    than as one literal token — see test_migration_134.py's identical
    helper for why (privacy-scan's email regex otherwise swallows `.json`
    into the domain it checks against the RFC 2606 reserved-domain list)."""
    return local_part + "@example.com" + ".json"


def _make_check(tmp_path, monkeypatch, *, gws_installed=True, wrapper=True,
                 secrets=True, creds=True, legacy_mcp=False):
    check = GoogleWorkspaceCheck()

    monkeypatch.setattr(
        "shutil.which", lambda name: "/opt/homebrew/bin/gws" if gws_installed else None
    )

    wrapper_path = tmp_path / "gws-account"
    if wrapper:
        wrapper_path.write_text("#!/bin/bash\n")
    check.GWS_ACCOUNT = wrapper_path

    monkeypatch.setattr(check, "_has_secret", lambda name: secrets)

    creds_dir = tmp_path / "credentials"
    if creds:
        creds_dir.mkdir(parents=True)
        cred_file = creds_dir / _cred_name("operator")
        cred_file.write_text('{"refresh_token": "x", "client_id": "y"}\n')
        cred_file.chmod(0o600)
    check.CREDS_DIR = creds_dir

    claude_json = tmp_path / ".claude.json"
    if legacy_mcp:
        claude_json.write_text(
            '{"mcpServers": {"google-workspace": {"command": "old"}}}\n'
        )
    else:
        claude_json.write_text("{}\n")
    check.CLAUDE_JSON = claude_json

    return check, creds_dir


def test_fully_healthy_is_ok(tmp_path, monkeypatch):
    check, _ = _make_check(tmp_path, monkeypatch)
    assert check.check() is True
    result = check.fix()
    assert result.status == Status.OK


def test_gws_not_installed_notifies(tmp_path, monkeypatch):
    check, _ = _make_check(tmp_path, monkeypatch, gws_installed=False)
    assert check.check() is False
    result = check.fix()
    assert result.status == Status.NOTIFY
    assert "gws CLI not installed" in result.message


def test_missing_wrapper_notifies(tmp_path, monkeypatch):
    check, _ = _make_check(tmp_path, monkeypatch, wrapper=False)
    assert check.check() is False
    result = check.fix()
    assert result.status == Status.NOTIFY
    assert "gws-account wrapper missing" in result.message


def test_missing_secrets_notifies(tmp_path, monkeypatch):
    check, _ = _make_check(tmp_path, monkeypatch, secrets=False)
    assert check.check() is False
    result = check.fix()
    assert result.status == Status.NOTIFY
    assert "Keychain" in result.message


def test_no_credential_files_notifies(tmp_path, monkeypatch):
    check, creds_dir = _make_check(tmp_path, monkeypatch, creds=False)
    assert check.check() is False
    result = check.fix()
    assert result.status == Status.NOTIFY
    assert "No Google credential files" in result.message


def test_legacy_mcp_registration_is_auto_fixed(tmp_path, monkeypatch):
    check, _ = _make_check(tmp_path, monkeypatch, legacy_mcp=True)
    assert check.check() is False
    result = check.fix()
    assert result.status == Status.FIXED
    import json
    data = json.loads(check.CLAUDE_JSON.read_text())
    assert "google-workspace" not in data.get("mcpServers", {})


# ── aos#2326: permission hardening ───────────────────────────────────────

def test_world_readable_credential_file_notifies_not_silently_ok(tmp_path, monkeypatch):
    check, creds_dir = _make_check(tmp_path, monkeypatch)
    insecure = creds_dir / _cred_name("operator")
    insecure.chmod(0o644)

    assert check.check() is False, "0644 credential file must fail the invariant"
    result = check.fix()
    assert result.status == Status.NOTIFY
    assert "0600" in result.message or "permission" in result.message.lower()
    assert insecure.name in result.message
    assert result.notify is True

    # NOTIFY, never a silent auto-chmod behind the operator's back — the
    # brief is explicit that this check reports, migration 134 repairs.
    assert (insecure.stat().st_mode & 0o777) == 0o644


def test_permission_check_does_not_false_positive_on_hardened_files(tmp_path, monkeypatch):
    check, creds_dir = _make_check(tmp_path, monkeypatch)
    for f in creds_dir.glob("*.json"):
        assert (f.stat().st_mode & 0o777) == 0o600
    assert check.check() is True


def test_multiple_insecure_files_all_named_in_notify(tmp_path, monkeypatch):
    check, creds_dir = _make_check(tmp_path, monkeypatch)
    second = creds_dir / _cred_name("second")
    second.write_text('{"refresh_token": "x", "client_id": "y"}\n')
    second.chmod(0o644)
    (creds_dir / _cred_name("operator")).chmod(0o640)

    result = check.fix()
    assert result.status == Status.NOTIFY
    assert _cred_name("operator") in result.message
    assert _cred_name("second") in result.message
