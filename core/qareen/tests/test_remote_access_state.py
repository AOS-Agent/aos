"""Tests for RemoteAccessState — single-row remote-access DB CRUD."""

import sys
from pathlib import Path

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.services.remote_access_state import (  # noqa: E402
    SINGLETON_ID,
    RemoteAccessState,
)


def _state(tmp_path: Path) -> RemoteAccessState:
    """Build a RemoteAccessState backed by a throwaway db file."""
    return RemoteAccessState(db_path=tmp_path / "qareen.db")


def test_default_state_when_empty(tmp_path):
    """A fresh table with no row returns the disconnected default."""
    state = _state(tmp_path)
    assert state.get() == {"status": "disconnected"}


def test_upsert_get_round_trip(tmp_path):
    """upsert writes fields that come back through get()."""
    state = _state(tmp_path)
    state.upsert(
        status="connected",
        hostname="aos.example.com",
        domain="example.com",
        tunnel_id="tun-123",
        access_aud="aud-xyz",
    )

    row = state.get()
    assert row["status"] == "connected"
    assert row["hostname"] == "aos.example.com"
    assert row["domain"] == "example.com"
    assert row["tunnel_id"] == "tun-123"
    assert row["access_aud"] == "aud-xyz"
    # updated_at + created_at are stamped automatically
    assert row["updated_at"]
    assert row["created_at"]


def test_singleton_id(tmp_path):
    """Every write targets the single 'singleton' row — never more than one."""
    state = _state(tmp_path)
    state.upsert(status="provisioning")
    state.upsert(status="connected", hostname="aos.example.com")

    assert state.get()["id"] == SINGLETON_ID

    # Exactly one physical row exists in the table.
    with state._conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM remote_access").fetchone()["c"]
    assert count == 1


def test_partial_upsert_preserves_existing(tmp_path):
    """A subsequent partial upsert must not wipe untouched columns."""
    state = _state(tmp_path)
    state.upsert(status="connected", hostname="aos.example.com", tunnel_id="tun-1")
    state.upsert(status="error", error_message="boom")

    row = state.get()
    assert row["status"] == "error"
    assert row["error_message"] == "boom"
    assert row["hostname"] == "aos.example.com"  # preserved
    assert row["tunnel_id"] == "tun-1"           # preserved


def test_allowed_emails_json_round_trip(tmp_path):
    """allowed_emails is stored as JSON TEXT and returned as a Python list."""
    state = _state(tmp_path)
    emails = ["a@example.com", "b@example.com"]
    state.upsert(status="connected", allowed_emails=emails)

    row = state.get()
    assert row["allowed_emails"] == emails
    assert isinstance(row["allowed_emails"], list)

    # The raw stored value is JSON TEXT, not a Python repr.
    with state._conn() as conn:
        raw = conn.execute(
            "SELECT allowed_emails FROM remote_access WHERE id = ?", (SINGLETON_ID,)
        ).fetchone()["allowed_emails"]
    assert raw == '["a@example.com", "b@example.com"]'


def test_set_status_helper(tmp_path):
    """set_status updates status and error_message together."""
    state = _state(tmp_path)
    state.set_status("error", error="token invalid")
    row = state.get()
    assert row["status"] == "error"
    assert row["error_message"] == "token invalid"


def test_clear_resets_to_disconnected(tmp_path):
    """clear() resets status to disconnected and nulls provisioning metadata."""
    state = _state(tmp_path)
    state.upsert(
        status="connected",
        hostname="aos.example.com",
        tunnel_id="tun-123",
        allowed_emails=["a@example.com"],
        access_app_id="app-1",
    )

    cleared = state.clear()
    assert cleared["status"] == "disconnected"
    assert cleared.get("hostname") is None
    assert cleared.get("tunnel_id") is None
    assert cleared.get("access_app_id") is None
    assert cleared.get("allowed_emails") is None
    assert cleared.get("error_message") is None
    # Still a singleton.
    assert cleared["id"] == SINGLETON_ID


def test_ensure_tables_idempotent(tmp_path):
    """Re-instantiating against the same db must not error or lose data."""
    db = tmp_path / "qareen.db"
    RemoteAccessState(db_path=db).upsert(status="connected", hostname="aos.example.com")
    # Second instance re-runs CREATE TABLE IF NOT EXISTS.
    reopened = RemoteAccessState(db_path=db)
    assert reopened.get()["hostname"] == "aos.example.com"
