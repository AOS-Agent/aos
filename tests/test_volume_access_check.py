"""VolumeAccessCheck (aos#141): TCC canary for the AOS-X data volume.

Pins: healthy volume passes; EPERM on listing fails (never reads as
'empty is fine'); empty-but-readable volume fails (AOS-X is never
legitimately empty); unmounted fails; fix() never claims success (TCC
grants are GUI-only) and always notifies.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core/infra/reconcile"))
sys.path.insert(0, str(REPO / "core/infra/reconcile/checks"))

import volume_access
from volume_access import VolumeAccessCheck
from base import Status


def _with_volume(monkeypatch, tmp_path, populate=True):
    vol = tmp_path / "AOS-X"
    if populate:
        (vol / "vault").mkdir(parents=True)
    monkeypatch.setattr(volume_access, "VOLUME", vol)
    monkeypatch.setattr(volume_access, "CANARY_DIR", vol / ".aos-canary")
    return vol


def test_healthy_volume_passes(monkeypatch, tmp_path):
    _with_volume(monkeypatch, tmp_path)
    assert VolumeAccessCheck().check() is True


def test_unmounted_fails(monkeypatch, tmp_path):
    _with_volume(monkeypatch, tmp_path, populate=False)
    # never created -> exists() False
    import shutil
    assert VolumeAccessCheck().check() is False


def test_empty_volume_fails(monkeypatch, tmp_path):
    vol = tmp_path / "AOS-X"
    vol.mkdir()
    monkeypatch.setattr(volume_access, "VOLUME", vol)
    monkeypatch.setattr(volume_access, "CANARY_DIR", vol / ".aos-canary")
    assert VolumeAccessCheck().check() is False, "empty AOS-X must fail — EPERM often masquerades as empty"


def test_eperm_fails(monkeypatch, tmp_path):
    vol = _with_volume(monkeypatch, tmp_path)
    import os as _os
    real_listdir = _os.listdir
    def deny(path):
        if str(path) == str(vol):
            raise PermissionError(13, "Operation not permitted")
        return real_listdir(path)
    monkeypatch.setattr(volume_access.os, "listdir", deny)
    assert VolumeAccessCheck().check() is False


def test_fix_notifies_never_claims_success(monkeypatch, tmp_path):
    _with_volume(monkeypatch, tmp_path, populate=False)
    r = VolumeAccessCheck().fix()
    assert r.status == Status.NOTIFY
    assert r.notify is True
    assert "System Settings" in (r.detail or "") or "tccutil" in (r.detail or "")
