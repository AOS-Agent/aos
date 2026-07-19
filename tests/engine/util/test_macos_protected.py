"""Tests for the macOS Full Disk Access helpers.

These tests don't actually touch ~/Library — they create temp files
that simulate protected sources, then verify the helper's caching,
detection, and instruction-printing behavior.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.engine.util import macos_protected as mp


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect the nag marker and snapshot cache to a tmp dir."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(mp, "_FDA_NAG_MARKER", state_dir / "fda-nag-shown")

    cache_dir = tmp_path / "snapshots"
    cache_dir.mkdir()
    monkeypatch.setattr(mp, "_cache_dir", lambda: cache_dir)
    return tmp_path


def test_has_full_disk_access_readable(tmp_path, isolated_state):
    f = tmp_path / "src.db"
    f.write_bytes(b"hello")
    assert mp.has_full_disk_access(f) is True


def test_has_full_disk_access_missing(tmp_path, isolated_state):
    assert mp.has_full_disk_access(tmp_path / "nonexistent") is False


def test_has_full_disk_access_no_perm(tmp_path, isolated_state):
    f = tmp_path / "noread.db"
    f.write_bytes(b"x")
    f.chmod(0o000)
    try:
        # On macOS this returns False; on root user it might still succeed
        result = mp.has_full_disk_access(f)
        # If running as root the chmod is ignored
        if os.geteuid() != 0:
            assert result is False
    finally:
        f.chmod(0o644)


def test_safe_snapshot_creates_copy(tmp_path, isolated_state):
    src = tmp_path / "real.db"
    src.write_bytes(b"original-data")
    snap = mp.safe_snapshot(src, cache_key="test-real")
    assert snap is not None
    assert snap.exists()
    assert snap.read_bytes() == b"original-data"


def test_safe_snapshot_caches_within_ttl(tmp_path, isolated_state):
    src = tmp_path / "real.db"
    src.write_bytes(b"v1")

    snap1 = mp.safe_snapshot(src, cache_key="cache-test", max_age_sec=300)
    mtime1 = snap1.stat().st_mtime

    # Modify source AFTER snapshot — cache should still serve old copy
    time.sleep(0.05)
    src.write_bytes(b"v2-newer")

    snap2 = mp.safe_snapshot(src, cache_key="cache-test", max_age_sec=300)
    assert snap2 == snap1
    assert snap2.stat().st_mtime == mtime1
    assert snap2.read_bytes() == b"v1"  # cached, not refreshed


def test_safe_snapshot_refreshes_when_stale(tmp_path, isolated_state):
    src = tmp_path / "real.db"
    src.write_bytes(b"v1")

    snap1 = mp.safe_snapshot(src, cache_key="stale-test", max_age_sec=300)
    # Backdate the snapshot
    old = time.time() - 3600
    os.utime(snap1, (old, old))

    src.write_bytes(b"v2-fresh")
    snap2 = mp.safe_snapshot(src, cache_key="stale-test", max_age_sec=300)
    assert snap2.read_bytes() == b"v2-fresh"


def test_safe_snapshot_copies_wal_sidecars(tmp_path, isolated_state):
    src = tmp_path / "db.sqlite"
    src.write_bytes(b"main")
    (tmp_path / "db.sqlite-wal").write_bytes(b"wal-data")
    (tmp_path / "db.sqlite-shm").write_bytes(b"shm-data")

    snap = mp.safe_snapshot(src, cache_key="sidecar-test")
    assert snap is not None
    assert (Path(str(snap) + "-wal")).read_bytes() == b"wal-data"
    assert (Path(str(snap) + "-shm")).read_bytes() == b"shm-data"


def test_print_grant_instructions_idempotent(tmp_path, isolated_state, capsys):
    mp.print_grant_instructions("test-reason")
    out1 = capsys.readouterr().err
    assert "Full Disk Access" in out1
    assert "test-reason" in out1

    # Second call should be silent (marker exists)
    mp.print_grant_instructions("test-reason")
    out2 = capsys.readouterr().err
    assert out2 == ""


def test_clear_nag_marker_re_enables_print(tmp_path, isolated_state, capsys):
    mp.print_grant_instructions("first")
    capsys.readouterr()
    mp.clear_nag_marker()

    mp.print_grant_instructions("second")
    out = capsys.readouterr().err
    assert "second" in out


def test_ensure_access_returns_true_on_readable(tmp_path, isolated_state):
    f = tmp_path / "src"
    f.write_bytes(b"data")
    assert mp.ensure_access(f, "test source") is True


def test_ensure_access_returns_false_and_prints_on_missing_perm(tmp_path, isolated_state, capsys):
    f = tmp_path / "noread"
    f.write_bytes(b"x")
    f.chmod(0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("Cannot test perm denial as root")
        result = mp.ensure_access(f, "perm test")
        assert result is False
        err = capsys.readouterr().err
        assert "Full Disk Access" in err
    finally:
        f.chmod(0o644)


def test_ensure_access_returns_false_on_missing_file(tmp_path, isolated_state):
    assert mp.ensure_access(tmp_path / "nope", "missing test") is False


def test_cleanup_snapshots(tmp_path, isolated_state):
    src = tmp_path / "x.db"
    src.write_bytes(b"a")
    mp.safe_snapshot(src, cache_key="k1")
    mp.safe_snapshot(src, cache_key="k2")
    n = mp.cleanup_snapshots()
    assert n >= 2
