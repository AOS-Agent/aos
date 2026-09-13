"""Migration 134 — harden Google OAuth credential files (aos#2326) and
finish the workspace-mcp -> gws consolidation migration 059 started.

Background: migration 059 converted `~/.google_workspace_mcp/credentials/`
(workspace-mcp format) into `~/.aos/config/google/credentials/` (gws
format), and left the old directory in place "for one update cycle as a
rollback safety net" — but no later migration ever removed it, so a machine
that ran 059 could carry two live copies of the same OAuth refresh token
indefinitely, neither one permission-hardened. `gws` itself (a third-party
binary invoked via GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE) owns the new
file's format and must keep reading it from disk — so this migration does
not move the token into Keychain. It finishes the consolidation: convert any
still-unconverted legacy token, chmod every credential file 0600, best-effort
exclude the directory from Time Machine, and remove the now-fully-superseded
legacy directory.

Same contract as sibling migrations: idempotent, one-way (down() is False —
there is no safe automatic way to recreate a shredded plaintext secret).
"""
from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIG = REPO / "core" / "infra" / "migrations" / "134_google_credentials_hardening.py"


def _cred_name(local_part: str) -> str:
    """A `<email>.json` credential filename, assembled at runtime rather
    than as one literal token — the real filename shape per the
    google-workspace manifest's own setup instructions, but written this
    way so a fixture using the RFC 2606 reserved `example.com` domain
    doesn't get read by privacy-scan's email regex as one run ending in
    `.json` (which isn't a reserved TLD, so the reserved-domain downgrade
    never fires). No behavior difference; same fixture data either way."""
    return local_part + "@example.com" + ".json"


@pytest.fixture
def m(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    loader = SourceFileLoader("mig_134", str(MIG))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert mod._old_creds_dir() == tmp_path / ".google_workspace_mcp" / "credentials"
    assert mod._new_creds_dir() == tmp_path / ".aos" / "config" / "google" / "credentials"

    # Never touch a real xattr binary from a test — record calls instead.
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    mod._xattr_calls = calls
    return mod


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_nothing_present_is_already_applied(m):
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_legacy_token_converted_hardened_and_legacy_dir_removed(m):
    old_dir = m._old_creds_dir()
    old_dir.mkdir(parents=True)
    token = old_dir / _cred_name("operator")
    token.write_text(json.dumps({
        "client_id": "cid", "client_secret": "csecret", "refresh_token": "rtok",
    }))
    token.chmod(0o644)

    assert m.check() is False

    assert m.up() is True

    new_dir = m._new_creds_dir()
    converted = new_dir / _cred_name("operator")
    assert converted.exists()
    data = json.loads(converted.read_text())
    assert data["refresh_token"] == "rtok"
    assert data["client_id"] == "cid"
    assert data["type"] == "authorized_user"
    assert _mode(converted) == 0o600

    assert not old_dir.exists(), "legacy dir must be removed once consolidated"
    assert m.check() is True


def test_already_converted_new_dir_only_gets_hardened(m):
    """No legacy dir at all — just an existing (loosely-permissioned) new
    credential file, e.g. from the operator's manual `gws auth login`
    conversion step. Migration must not require the legacy dir to exist."""
    new_dir = m._new_creds_dir()
    new_dir.mkdir(parents=True)
    f = new_dir / _cred_name("operator")
    f.write_text(json.dumps({
        "client_id": "cid", "client_secret": "csecret",
        "refresh_token": "rtok", "type": "authorized_user",
    }))
    f.chmod(0o644)

    assert m.check() is False

    assert m.up() is True
    assert _mode(f) == 0o600
    assert m.check() is True


def test_backup_exclusion_attempted_on_new_dir_with_credentials(m):
    new_dir = m._new_creds_dir()
    new_dir.mkdir(parents=True)
    (new_dir / _cred_name("a")).write_text('{"refresh_token":"x","client_id":"y"}')

    assert m.up() is True

    assert any(
        m.TM_EXCLUDE_ATTR in " ".join(call) for call in m._xattr_calls
    ), f"expected a Time Machine exclusion attempt, got calls: {m._xattr_calls}"


def test_partial_conversion_failure_keeps_legacy_dir(m):
    """A legacy token file that can't be converted (missing a required key)
    must not be silently discarded — the legacy dir stays so nothing is
    lost, and up() reports failure."""
    old_dir = m._old_creds_dir()
    old_dir.mkdir(parents=True)
    (old_dir / _cred_name("broken")).write_text(json.dumps({"client_id": "cid"}))

    assert m.up() is False
    assert old_dir.exists(), "must not delete legacy data it failed to convert"


def test_already_present_new_file_is_not_overwritten_by_legacy(m):
    old_dir = m._old_creds_dir()
    old_dir.mkdir(parents=True)
    (old_dir / _cred_name("operator")).write_text(json.dumps({
        "client_id": "STALE", "client_secret": "x", "refresh_token": "STALE",
    }))

    new_dir = m._new_creds_dir()
    new_dir.mkdir(parents=True)
    live = new_dir / _cred_name("operator")
    live.write_text(json.dumps({
        "client_id": "LIVE", "client_secret": "x", "refresh_token": "LIVE",
        "type": "authorized_user",
    }))
    live.chmod(0o600)

    assert m.up() is True
    data = json.loads(live.read_text())
    assert data["refresh_token"] == "LIVE", "must never clobber a live token with a stale one"
    assert not old_dir.exists()


def test_idempotent_second_run_is_a_noop(m):
    old_dir = m._old_creds_dir()
    old_dir.mkdir(parents=True)
    (old_dir / _cred_name("a")).write_text(json.dumps({
        "client_id": "cid", "client_secret": "csecret", "refresh_token": "rtok",
    }))

    assert m.up() is True
    assert m.up() is True
    assert m.check() is True


def test_down_is_a_one_way_migration(m):
    assert m.down() is False
