"""
Tests for the v0.8.0 update gates: freeze and host scope.

Both decide whether an update runs at all, on machines these tests cannot
reach, so they are written against the pure functions in core/lib/channels.py
rather than the bash that calls them.

The host-scope tests carry the weight. The excluded machine and the release
machine are both named some variant of "Agent's Mac mini", and the obvious
implementation — normalize the ComputerName and compare — makes them the same
string. A guard with that bug refuses to update the machine the rollout starts
on and silently does nothing, which looks exactly like a guard that works.
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


# ── Host scope ───────────────────────────────────────────────────────────────


def test_excluded_mini_by_hostname(tmp_path):
    r = channels.excluded_host("Agents-Mac-mini.local", "Agents-Mac-mini", tmp_path)
    assert r["excluded"] is True


def test_excluded_mini_by_tailscale_name(tmp_path):
    r = channels.excluded_host("agents-mac-mini-2", None, tmp_path)
    assert r["excluded"] is True


def test_excluded_mini_by_tailnet_fqdn(tmp_path):
    r = channels.excluded_host("agents-mac-mini-2.taila0423e.ts.net", None, tmp_path)
    assert r["excluded"] is True


def test_release_machine_is_not_excluded(tmp_path):
    """The machine the rollout starts on must never match the guard.

    This is the test that would have caught a normalized-ComputerName
    implementation: both minis answer to "Agent's Mac mini", and a guard that
    compares those strings bricks the rollout on the first machine while
    looking like it is working. The release machine's own names are its
    LocalHostName and the bare tailscale name — neither is an excluded pattern.
    """
    assert channels.excluded_host("host-four.local", "host-four", tmp_path)["excluded"] is False
    assert channels.excluded_host("agents-mac-mini", "host-four", tmp_path)["excluded"] is False


@pytest.mark.parametrize("hostname,local", [
    ("host-one", "host-one"),
    ("host-two.local", "host-two"),
    ("host-three-4", "host-three-4"),
    ("agents-mac-mini", "host-four"),  # the release Mini's own tailscale name
])
def test_other_fleet_machines_are_not_excluded(hostname, local, tmp_path):
    """Only the one excluded mini matches — everything else updates normally."""
    assert channels.excluded_host(hostname, local, tmp_path)["excluded"] is False


def test_excluded_host_reports_which_name_matched(tmp_path):
    r = channels.excluded_host("Agents-Mac-mini.local", None, tmp_path)
    assert r["matched"] == "agents-mac-mini.local"
    assert "override" in r["reason"]


def test_override_file_unblocks_an_excluded_machine(tmp_path):
    """The guard is a safe default about someone else's computer, not a lock.

    Whoever owns the excluded machine can opt back in on their own machine.
    """
    (tmp_path / "allow-updates").write_text("")
    r = channels.excluded_host("Agents-Mac-mini.local", "Agents-Mac-mini", tmp_path)
    assert r["excluded"] is False
    assert "overrides" in r["reason"]


def test_no_hostname_is_not_excluded(tmp_path):
    r = channels.excluded_host("", "", tmp_path)
    assert r["excluded"] is False


def test_host_scope_is_case_insensitive(tmp_path):
    assert channels.excluded_host("AGENTS-MAC-MINI.LOCAL", None, tmp_path)["excluded"] is True


# ── CLI shim (what the bash actually calls) ──────────────────────────────────


def test_cli_host_scope_exit_codes(capsys, tmp_path):
    """Exit 1 for excluded, 0 for in-scope — bash branches on this."""
    assert channels.main(["host-scope", "Agents-Mac-mini.local", "-", str(tmp_path)]) == 1
    assert channels.main(["host-scope", "host-four.local", "-", str(tmp_path)]) == 0


def test_cli_host_scope_output_is_tab_separated(capsys, tmp_path):
    channels.main(["host-scope", "Agents-Mac-mini.local", "-", str(tmp_path)])
    fields = capsys.readouterr().out.strip().split("\t")
    assert len(fields) == 3
    assert fields[0] == "1"


def test_cli_freeze_gate_exit_codes(tmp_path):
    (tmp_path / "channel-update.yaml").write_text("frozen: true\n")
    assert channels.main(["freeze-gate", "0.8.0", "0.8.1", str(tmp_path)]) == 0
    assert channels.main(["freeze-gate", "0.8.0", "0.9.0", str(tmp_path)]) == 1


def test_cli_frozen_exit_codes(tmp_path):
    assert channels.main(["frozen", str(tmp_path)]) == 1
    (tmp_path / "channel-update.yaml").write_text("frozen: true\n")
    assert channels.main(["frozen", str(tmp_path)]) == 0
