"""Migration 118 — remove the retired desktop app — run twice, on every shape
of /Applications/AOS.app it can meet. Paths are redirected into tmp_path; the
module's own guards (macOS only, bundle-id match) are exercised as written."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIG = REPO / "core" / "infra" / "migrations" / "118_remove_aos_desktop_app.py"


@pytest.fixture
def m(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mig_118", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "APP", tmp_path / "Applications" / "AOS.app")
    monkeypatch.setattr(mod, "CACHE", tmp_path / "Caches" / "am.hish.aos")
    monkeypatch.setattr(sys, "platform", "darwin")
    return mod


def _bundle(app: Path, bundle_id: bytes | None) -> None:
    (app / "Contents" / "MacOS").mkdir(parents=True)
    if bundle_id is not None:
        (app / "Contents" / "Info.plist").write_bytes(
            b"<plist><dict><key>CFBundleIdentifier</key><string>" + bundle_id + b"</string></dict></plist>"
        )


def test_nothing_installed_is_already_applied(m):
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_removes_our_bundle_and_cache_then_is_a_noop(m):
    _bundle(m.APP, b"am.hish.aos")
    m.CACHE.mkdir(parents=True)
    (m.CACHE / "blob").write_text("x")
    assert m.check() is False
    assert m.up() is True
    assert not m.APP.exists() and not m.CACHE.exists()
    assert m.check() is True
    assert m.up() is True  # second run: nothing to do, still True


def test_foreign_bundle_is_left_alone(m):
    _bundle(m.APP, b"com.example.other")
    assert m.check() is True
    assert m.up() is True
    assert m.APP.exists()


def test_half_deleted_bundle_without_plist_counts_as_ours(m):
    _bundle(m.APP, None)
    assert m.check() is False
    assert m.up() is True
    assert not m.APP.exists()


def test_non_macos_is_a_noop(m, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _bundle(m.APP, b"am.hish.aos")
    assert m.check() is True
    assert m.up() is True
    assert m.APP.exists()
