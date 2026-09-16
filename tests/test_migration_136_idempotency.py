"""Migration 136 — `~/.aos/config/comms.yaml` seeded for the iMessage scope
gate — run twice, against a sandboxed HOME.

Same harness shape as `tests/test_migration_135_idempotency.py`: `Path.home()`
is sandboxed persistently for the whole test, because the migration resolves
HOME through functions (`_home()`, `_instance_config()`, `_template()`) on
every call rather than module-level constants.

The one property that matters most is the negative one: an EXISTING instance
file — the operator's allowlist — is never touched, byte-for-byte, however
many times the migration runs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "core" / "infra" / "migrations" / "136_comms_scope_config.py"
TEMPLATE = REPO / "config" / "defaults" / "comms.yaml"

OPERATOR_ALLOWLIST = """# operator-written — must survive migration 136 untouched
imessage:
  access: allowlist
  include_groups: false
  allowed:
    - name: Hisham
      handles: ["+14165550142"]
"""


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def home(tmp_path, monkeypatch):
    """A sandbox HOME with the framework tree symlinked in as `~/aos`, and an
    empty `~/.aos/config/` — the shape of an instance that predates 136."""
    h = tmp_path / "home"
    (h / ".aos" / "config").mkdir(parents=True)
    (h / "aos").symlink_to(REPO)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: h))
    return h


@pytest.fixture
def m(home):
    """Import migration 136 with `Path.home()` already pointing at the sandbox."""
    spec = importlib.util.spec_from_file_location(f"mig_136_{home.parent.name}",
                                                    MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._home() == home, "the migration resolved HOME outside the sandbox"
    return mod


# ── (a) absent → seeded from the template ────────────────────────────────────

def test_absent_file_is_seeded_from_template(m, home):
    dest = home / ".aos" / "config" / "comms.yaml"
    assert m.check() is False
    assert not dest.exists()

    assert m.up() is True
    assert m.check() is True
    assert dest.read_bytes() == TEMPLATE.read_bytes()

    cfg = yaml.safe_load(dest.read_text())
    assert cfg["imessage"]["access"] == "all"
    assert cfg["imessage"]["include_groups"] is False


# ── (b) runs twice → unchanged ───────────────────────────────────────────────

def test_runs_twice_unchanged(m, home):
    dest = home / ".aos" / "config" / "comms.yaml"
    assert m.up() is True
    first = dest.read_bytes()
    first_mtime = dest.stat().st_mtime_ns

    assert m.check() is True
    assert m.up() is True  # direct call, bypassing the runner's check() guard
    assert dest.read_bytes() == first
    assert dest.stat().st_mtime_ns == first_mtime


# ── (c) operator's allowlist → untouched byte-for-byte ───────────────────────

def test_existing_operator_file_untouched(m, home):
    dest = home / ".aos" / "config" / "comms.yaml"
    dest.write_text(OPERATOR_ALLOWLIST)
    before = dest.read_bytes()
    before_mtime = dest.stat().st_mtime_ns

    assert m.check() is True  # runner would skip up() entirely
    assert m.up() is True     # and even a direct call changes nothing
    assert dest.read_bytes() == before
    assert dest.stat().st_mtime_ns == before_mtime

    cfg = yaml.safe_load(dest.read_text())
    assert cfg["imessage"]["access"] == "allowlist"
    assert cfg["imessage"]["allowed"][0]["name"] == "Hisham"


# ── (d) template missing → inline fallback, still valid ──────────────────────

def test_missing_template_uses_inline_fallback(m, home, monkeypatch):
    missing = home / "nowhere" / "comms.yaml"
    monkeypatch.setattr(m, "_template", lambda: missing)
    assert not missing.exists()

    dest = home / ".aos" / "config" / "comms.yaml"
    assert m.up() is True
    assert dest.is_file()

    cfg = yaml.safe_load(dest.read_text())
    assert cfg["imessage"]["access"] == "all"
    assert cfg["imessage"]["include_groups"] is False
    # The inline copy must not drift from the shipped template.
    assert dest.read_text() == TEMPLATE.read_text()


# ── contract bits the runner relies on ───────────────────────────────────────

def test_down_is_refused(m):
    assert m.down() is False


def test_description_present(m):
    assert isinstance(m.DESCRIPTION, str) and "comms.yaml" in m.DESCRIPTION
