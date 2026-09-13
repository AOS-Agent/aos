"""
Tests for the v0.8.0 update gate: freeze.

The gate decides whether an update runs at all, so it is written against the
pure functions in core/lib/channels.py rather than the bash that calls them —
a decision that important should be provable without a network or a release.

Two asymmetries carry the weight. A frozen machine must still take patches, or
a security fix never lands on a system nobody is developing any more; and a
machine whose candidate version cannot be read must be offered *nothing*,
because failing open here pushes an unidentified release onto a machine that
asked to stop receiving them. Everything else — a missing file, malformed
YAML, a non-boolean flag — must read as "not frozen", since an accidental
freeze is silent and lasts until someone notices months of missed patches.

The companion host-scope guard was removed in the single-node cleanup: AOS runs
on one machine, so there is no other machine to exclude.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "lib"))

import channels  # noqa: E402

# ── Version parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("0.8.0", (0, 8, 0)),
    ("v0.8.0", (0, 8, 0)),
    ("v0.8.1-dff5c0d", (0, 8, 1)),
    ("v0.7.6+build", (0, 7, 6)),
    ("  v1.2.3\n", (1, 2, 3)),
])
def test_parse_version(raw, expected):
    assert channels.parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "0.8", "unknown", "vX.Y.Z", "latest"])
def test_parse_version_rejects_junk(raw):
    assert channels.parse_version(raw) is None


# ── Freeze gate ──────────────────────────────────────────────────────────────


def test_not_frozen_allows_anything():
    g = channels.freeze_gate("0.8.0", "0.9.0", frozen=False)
    assert g["allowed"] is True
    assert g["kind"] == "not-frozen"


def test_frozen_allows_patch():
    g = channels.freeze_gate("0.8.0", "0.8.1", frozen=True)
    assert g["allowed"] is True
    assert g["kind"] == "patch"


def test_frozen_blocks_minor_bump():
    g = channels.freeze_gate("0.8.0", "0.9.0", frozen=True)
    assert g["allowed"] is False
    assert g["kind"] == "feature"


def test_frozen_blocks_major_bump():
    assert channels.freeze_gate("0.8.0", "1.0.0", frozen=True)["allowed"] is False


def test_frozen_blocks_downgrade():
    assert channels.freeze_gate("0.8.1", "0.8.0", frozen=True)["allowed"] is False


def test_frozen_blocks_same_version():
    assert channels.freeze_gate("0.8.0", "0.8.0", frozen=True)["allowed"] is False


def test_frozen_fails_closed_on_unreadable_version():
    """A frozen machine that cannot compare versions offers nothing.

    The services opt-out fails OPEN so a typo cannot take the bridge down. This
    gate fails CLOSED, and the asymmetry is chosen: failing open here would push
    an unidentified release onto a machine that asked to stop receiving them.
    """
    g = channels.freeze_gate("0.8.0", "", frozen=True)
    assert g["allowed"] is False
    assert g["kind"] == "unknown"


# ── is_frozen: the file contract ─────────────────────────────────────────────


def test_is_frozen_true(tmp_path):
    (tmp_path / "channel-update.yaml").write_text("frozen: true\n")
    assert channels.is_frozen(tmp_path) is True


def test_is_frozen_false_when_file_absent(tmp_path):
    assert channels.is_frozen(tmp_path) is False


@pytest.mark.parametrize("body", [
    "frozen: false\n",
    "frozen: yes-please\n",     # not a bool
    "frozen:\n",                 # null
    "notfrozen: true\n",
    "",
    "this: [is, not: valid: yaml\n",
])
def test_is_frozen_never_freezes_by_accident(tmp_path, body):
    """Only a literal `frozen: true` freezes a machine.

    An accidental freeze is silent and lasts until someone notices months of
    missed patches, so every ambiguous input must resolve to "not frozen".
    """
    (tmp_path / "channel-update.yaml").write_text(body)
    assert channels.is_frozen(tmp_path) is False


# ── CLI shim (what the bash actually calls) ──────────────────────────────────


def test_cli_freeze_gate_exit_codes(tmp_path):
    (tmp_path / "channel-update.yaml").write_text("frozen: true\n")
    assert channels.main(["freeze-gate", "0.8.0", "0.8.1", str(tmp_path)]) == 0
    assert channels.main(["freeze-gate", "0.8.0", "0.9.0", str(tmp_path)]) == 1


def test_cli_frozen_exit_codes(tmp_path):
    assert channels.main(["frozen", str(tmp_path)]) == 1
    (tmp_path / "channel-update.yaml").write_text("frozen: true\n")
    assert channels.main(["frozen", str(tmp_path)]) == 0
