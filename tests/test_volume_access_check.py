"""VolumeAccessCheck (aos#141, aos#2343): TCC canary for the configured
external data volume.

aos#2343: the volume path used to be hardcoded to `/Volumes/AOS-X`, so a
machine with no external data drive (Faisal's Mini) got a permanent,
un-clearable NOTIFY every reconcile cycle. The check now gates on
`config/storage.yaml`'s `data_drive` key — the same source of truth
`storage_layout` already reads — and treats "no data drive declared" as
nothing to verify, not as a broken volume.

Pins: no data drive configured -> OK; configured and present -> OK (healthy
canary read/write); configured and absent -> NOTIFY (unmounted, empty, or
EPERM all still fail — an empty or permission-denied volume is never "fine",
and fix() never claims success since TCC grants are GUI-only).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core/infra/reconcile"))
sys.path.insert(0, str(REPO / "core/infra/reconcile/checks"))

import volume_access
from base import Status
from volume_access import VolumeAccessCheck


def _configure(monkeypatch, drive: str):
    """Stub the resolved data_drive config value directly, bypassing the
    YAML file. `drive=""` simulates no data_drive declared at all."""
    monkeypatch.setattr(volume_access, "_configured_data_drive", lambda: drive)


# ── Config gate (aos#2343) ──────────────────────────────────────────────

def test_no_volume_configured_is_ok(monkeypatch):
    _configure(monkeypatch, "")
    check = VolumeAccessCheck()
    assert check.check() is True

    # Driven directly (as the runner never would, since check() was True):
    # must never claim there is anything to notify about.
    result = check.fix()
    assert result.status == Status.OK
    assert "no external data volume configured" in result.message
    assert result.notify is False


def test_configured_data_drive_reads_storage_yaml(monkeypatch, tmp_path):
    cfg = tmp_path / "storage.yaml"
    cfg.write_text("data_drive: /Volumes/AOS-X\nrelocations: {}\n")
    monkeypatch.setattr(volume_access, "STORAGE_CONFIG", cfg)
    assert volume_access._configured_data_drive() == "/Volumes/AOS-X"


def test_configured_data_drive_empty_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(volume_access, "STORAGE_CONFIG", tmp_path / "does-not-exist.yaml")
    assert volume_access._configured_data_drive() == ""


def test_configured_data_drive_empty_when_key_absent(monkeypatch, tmp_path):
    cfg = tmp_path / "storage.yaml"
    cfg.write_text("relocations: {}\n")
    monkeypatch.setattr(volume_access, "STORAGE_CONFIG", cfg)
    assert volume_access._configured_data_drive() == ""


def test_configured_data_drive_empty_on_malformed_yaml(monkeypatch, tmp_path):
    cfg = tmp_path / "storage.yaml"
    cfg.write_text(":::not yaml:::\n  - [oops\n")
    monkeypatch.setattr(volume_access, "STORAGE_CONFIG", cfg)
    assert volume_access._configured_data_drive() == ""


# ── Configured-and-present / configured-and-absent (the real canary) ────

def test_configured_and_present_passes(monkeypatch, tmp_path):
    vol = tmp_path / "AOS-X"
    (vol / "vault").mkdir(parents=True)
    _configure(monkeypatch, str(vol))
    assert VolumeAccessCheck().check() is True


def test_configured_and_unmounted_is_notify(monkeypatch, tmp_path):
    vol = tmp_path / "AOS-X"  # never created
    _configure(monkeypatch, str(vol))
    check = VolumeAccessCheck()
    assert check.check() is False
    result = check.fix()
    assert result.status == Status.NOTIFY
    assert result.notify is True


def test_configured_and_empty_volume_fails(monkeypatch, tmp_path):
    vol = tmp_path / "AOS-X"
    vol.mkdir()
    _configure(monkeypatch, str(vol))
    assert VolumeAccessCheck().check() is False, "empty configured volume must fail — EPERM often masquerades as empty"


def test_configured_and_eperm_fails(monkeypatch, tmp_path):
    vol = tmp_path / "AOS-X"
    (vol / "vault").mkdir(parents=True)
    _configure(monkeypatch, str(vol))
    import os as _os
    real_listdir = _os.listdir

    def deny(path):
        if str(path) == str(vol):
            raise PermissionError(13, "Operation not permitted")
        return real_listdir(path)

    monkeypatch.setattr(volume_access.os, "listdir", deny)
    assert VolumeAccessCheck().check() is False


def test_fix_notifies_never_claims_success(monkeypatch, tmp_path):
    vol = tmp_path / "AOS-X"  # never created -> not mounted
    _configure(monkeypatch, str(vol))
    r = VolumeAccessCheck().fix()
    assert r.status == Status.NOTIFY
    assert r.notify is True
    assert "System Settings" in (r.detail or "") or "tccutil" in (r.detail or "")
