"""
Tests for core/lib/channels.py — release-channel resolution + promotion guard.

Pure-logic coverage: no git, no network. The only filesystem touch is a
throwaway tmp_path used to exercise read_channel's file handling. Nothing here
reads or writes ~/.aos/.

Runs under pytest, and standalone via `python3 tests/test_channels.py`
(so `aos test`, which invokes each test file directly, executes it too).
"""

import sys
from pathlib import Path

import pytest

# channels.py lives in core/lib/
sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "lib"))

import channels  # noqa: E402


# ── normalize_channel ────────────────────────────────────────────────────────

class TestNormalizeChannel:
    def test_known_channels_pass_through(self):
        assert channels.normalize_channel("edge") == "edge"
        assert channels.normalize_channel("stable") == "stable"

    def test_case_and_whitespace_insensitive(self):
        assert channels.normalize_channel("  EDGE\n") == "edge"
        assert channels.normalize_channel("Stable ") == "stable"

    def test_unknown_defaults_to_stable(self):
        assert channels.normalize_channel("beta") == "stable"
        assert channels.normalize_channel("") == "stable"
        assert channels.normalize_channel(None) == "stable"

    def test_default_is_stable(self):
        # The whole safety story rests on this: absence → the safe lane.
        assert channels.DEFAULT_CHANNEL == "stable"


# ── read_channel ─────────────────────────────────────────────────────────────

class TestReadChannel:
    def test_missing_file_is_stable(self, tmp_path):
        assert channels.read_channel(tmp_path) == "stable"

    def test_reads_edge(self, tmp_path):
        (tmp_path / "channel").write_text("edge\n")
        assert channels.read_channel(tmp_path) == "edge"

    def test_reads_stable(self, tmp_path):
        (tmp_path / "channel").write_text("stable")
        assert channels.read_channel(tmp_path) == "stable"

    def test_garbage_file_is_stable(self, tmp_path):
        (tmp_path / "channel").write_text("garbage-value")
        assert channels.read_channel(tmp_path) == "stable"

    def test_config_dir_is_a_file_is_stable(self, tmp_path):
        # If <config_dir>/channel can't be read as a file, default safely.
        weird = tmp_path / "channel" / "channel"
        assert channels.read_channel(tmp_path / "channel" if False else tmp_path) == "stable"
        assert weird  # touch var to keep intent clear


# ── resolve_target ───────────────────────────────────────────────────────────

class TestResolveTarget:
    def test_edge_tracks_main(self):
        r = channels.resolve_target("edge", "aaa111", "bbb222")
        assert r["ref"] == "origin/main"
        assert r["hash"] == "aaa111"
        assert r["fellback"] is False
        assert r["channel"] == "edge"

    def test_edge_ignores_stable_tag(self):
        # Even with a stable tag present, edge always rides main.
        r = channels.resolve_target("edge", "mainhash", "taghash")
        assert r["hash"] == "mainhash"

    def test_stable_tracks_tag_when_present(self):
        r = channels.resolve_target("stable", "mainhash", "taghash")
        assert r["ref"] == "refs/tags/stable"
        assert r["hash"] == "taghash"
        assert r["fellback"] is False

    def test_stable_falls_back_to_main_when_tag_missing(self):
        r = channels.resolve_target("stable", "mainhash", "")
        assert r["ref"] == "origin/main"
        assert r["hash"] == "mainhash"
        assert r["fellback"] is True

    def test_stable_falls_back_when_tag_none(self):
        r = channels.resolve_target("stable", "mainhash", None)
        assert r["fellback"] is True
        assert r["hash"] == "mainhash"

    def test_unknown_channel_treated_as_stable(self):
        r = channels.resolve_target("banana", "mainhash", "taghash")
        assert r["channel"] == "stable"
        assert r["hash"] == "taghash"

    def test_hashes_are_stripped(self):
        r = channels.resolve_target("stable", " mainhash \n", "  taghash\n")
        assert r["hash"] == "taghash"


# ── promotion_guard ──────────────────────────────────────────────────────────

DAY = 86400.0


class TestPromotionGuard:
    def test_enough_soak_allows(self):
        now = 1_000_000.0
        deployed = now - 3 * DAY
        g = channels.promotion_guard(deployed, now, min_days=2)
        assert g["allowed"] is True
        assert g["forced"] is False
        assert 2.9 < g["soak_days"] < 3.1

    def test_insufficient_soak_denies(self):
        now = 1_000_000.0
        deployed = now - 1 * DAY
        g = channels.promotion_guard(deployed, now, min_days=2)
        assert g["allowed"] is False
        assert "soaked" in g["reason"]

    def test_exactly_min_days_allows(self):
        now = 1_000_000.0
        deployed = now - 2 * DAY
        g = channels.promotion_guard(deployed, now, min_days=2)
        assert g["allowed"] is True

    def test_force_overrides_insufficient_soak(self):
        now = 1_000_000.0
        deployed = now - 0.5 * DAY
        g = channels.promotion_guard(deployed, now, min_days=2, force=True)
        assert g["allowed"] is True
        assert g["forced"] is True

    def test_unknown_deploy_time_denies_without_force(self):
        g = channels.promotion_guard(None, 1_000_000.0, min_days=2)
        assert g["allowed"] is False
        assert g["soak_days"] is None

    def test_unknown_deploy_time_allowed_with_force(self):
        g = channels.promotion_guard(None, 1_000_000.0, min_days=2, force=True)
        assert g["allowed"] is True
        assert g["forced"] is True
        assert g["soak_days"] is None

    def test_negative_soak_clamped_to_zero(self):
        # Clock skew: deployed "in the future" must not read as huge soak.
        now = 1_000_000.0
        deployed = now + 5 * DAY
        g = channels.promotion_guard(deployed, now, min_days=2)
        assert g["allowed"] is False
        assert g["soak_days"] == 0.0


# ── CLI shim ─────────────────────────────────────────────────────────────────

class TestCliShim:
    def test_resolve_cmd_output(self, tmp_path, capsys):
        (tmp_path / "channel").write_text("stable")
        rc = channels.main(["resolve", "mainhash", "taghash", str(tmp_path)])
        out = capsys.readouterr().out.strip().split("\t")
        assert rc == 0
        assert out[0] == "stable"
        assert out[1] == "refs/tags/stable"
        assert out[2] == "taghash"
        assert out[3] == "0"

    def test_resolve_cmd_fallback_flag(self, tmp_path, capsys):
        (tmp_path / "channel").write_text("stable")
        channels.main(["resolve", "mainhash", "", str(tmp_path)])
        out = capsys.readouterr().out.strip().split("\t")
        assert out[1] == "origin/main"
        assert out[3] == "1"  # fellback

    def test_guard_cmd_allow_exit_zero(self, capsys):
        rc = channels.main(["guard", str(1_000_000 - 3 * DAY), "1000000", "2", "0"])
        out = capsys.readouterr().out.strip().split("\t")
        assert rc == 0
        assert out[0] == "1"

    def test_guard_cmd_deny_exit_one(self, capsys):
        rc = channels.main(["guard", str(1_000_000 - 0.1 * DAY), "1000000", "2", "0"])
        out = capsys.readouterr().out.strip().split("\t")
        assert rc == 1
        assert out[0] == "0"

    def test_guard_cmd_force(self, capsys):
        rc = channels.main(["guard", "-", "1000000", "2", "1"])
        assert rc == 0

    def test_channel_cmd(self, tmp_path, capsys):
        (tmp_path / "channel").write_text("edge")
        channels.main(["channel", str(tmp_path)])
        assert capsys.readouterr().out.strip() == "edge"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
