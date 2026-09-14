"""
Tests for `claude-profile` (aos#244.1) — isolated Claude Code login profiles
that share the AOS framework layer in ~/.claude but keep per-account state
(the seeded .claude.json) separate.

claude-profile is a bare CLI script (no .py extension), loaded here by file
path — the same trick tests/test_aos_report_2323_gh_repo.py uses for the
other extensionless scripts under core/bin/cli/. Because the module defines
@dataclass return types, it must be registered in sys.modules *before*
exec_module() runs — dataclass's own type resolution looks the module up by
name via sys.modules, and without this the import raises AttributeError.

Every test runs against a fake ~/.claude tree and a fake ~/.claude.json under
pytest's tmp_path, with $HOME pointed there before the module (whose path
constants resolve Path.home() once, at import time) is loaded. Nothing here
ever reads or writes the operator's real ~/.claude, ~/.claude.json, or
Keychain — see conftest.py's session-wide `_live_instance_is_never_touched`
backstop, which would fail the whole run if it did.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import textwrap
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PROFILE = REPO_ROOT / "core" / "bin" / "cli" / "claude-profile"


def _load_claude_profile():
    loader = SourceFileLoader("claude_profile_under_test", str(CLAUDE_PROFILE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = mod  # dataclass field resolution needs this
    loader.exec_module(mod)
    return mod


def _ns(**kwargs) -> types.SimpleNamespace:
    """A stand-in for the argparse.Namespace cmd_* functions expect."""
    return types.SimpleNamespace(**kwargs)


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """A sandboxed $HOME with a fake ~/.claude tree and ~/.claude.json.

    Deliberately leaves hooks/, projects/, plugins/, and keybindings.json
    absent, so `add`'s "only if the target exists" rule has something to
    prove against every run.
    """
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    (claude_dir / "skills").mkdir(parents=True)
    (claude_dir / "agents").mkdir(parents=True)
    (claude_dir / "rules").mkdir(parents=True)
    (claude_dir / "commands").mkdir(parents=True)
    (claude_dir / "CLAUDE.md").write_text("# fake CLAUDE.md\n")
    (claude_dir / "settings.json").write_text("{}\n")
    (claude_dir / "statusline.sh").write_text("#!/bin/sh\necho ok\n")

    (home / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"email": "op@example.invalid"},
        "mcpServers": {"filesystem": {"command": "npx"}},
        "cachedExtraUsageDisabledReason": "grandfathered",
        "preferences": {"theme": "dark"},
        "installMethod": "homebrew",
    }))

    monkeypatch.setenv("HOME", str(home))
    mod = _load_claude_profile()
    return {"home": home, "claude_dir": claude_dir, "mod": mod}


@pytest.fixture()
def mod(fake_home):
    return fake_home["mod"]


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

class TestNameValidation:
    def test_valid_names(self, mod):
        assert mod.is_valid_name("work")
        assert mod.is_valid_name("client-b")
        assert mod.is_valid_name("a1")

    def test_rejects_uppercase(self, mod):
        assert not mod.is_valid_name("Work")

    def test_rejects_leading_digit(self, mod):
        assert not mod.is_valid_name("1abc")

    def test_rejects_too_short(self, mod):
        assert not mod.is_valid_name("a")

    def test_rejects_too_long(self, mod):
        assert not mod.is_valid_name("a" * 33)

    def test_rejects_underscore_and_slash(self, mod):
        assert not mod.is_valid_name("has_underscore")
        assert not mod.is_valid_name("has/slash")


# ---------------------------------------------------------------------------
# add — symlinks
# ---------------------------------------------------------------------------

class TestAddSymlinks:
    def test_links_only_targets_that_exist_in_fake_claude(self, mod, fake_home):
        result = mod.create_profile("work")
        claude_dir = fake_home["claude_dir"]

        present = ("skills", "agents", "rules", "commands", "CLAUDE.md",
                   "settings.json", "statusline.sh")
        for name in present:
            link = result.profile_dir / name
            assert link.is_symlink(), f"{name} should be a symlink"
            assert link.resolve() == (claude_dir / name).resolve()
            assert name in result.linked

        absent = ("hooks", "projects", "plugins", "keybindings.json")
        for name in absent:
            assert not (result.profile_dir / name).exists()
            assert name in result.skipped

    def test_cli_add_reports_linked_and_skipped(self, mod, capsys):
        rc = mod.cmd_add(_ns(name="work"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Linked: skills" in out
        assert "skipped: hooks" in out

    def test_add_prints_next_step_instructions(self, mod, capsys):
        mod.cmd_add(_ns(name="work"))
        out = capsys.readouterr().out
        assert "cld --profile work" in out
        assert "CLAUDE_CONFIG_DIR=" not in out
        assert "Part 2" not in out
        assert "/login" in out

    def test_launcher_hint_prefers_named_launcher_that_is_cld(self, mod, monkeypatch, tmp_path):
        import shutil

        real = tmp_path / "cld-real"
        real.write_text("")
        for name in ("cld", "cld2"):
            (tmp_path / name).symlink_to(real)
        (tmp_path / "git").write_text("")  # on PATH, but not cld
        monkeypatch.setattr(
            shutil, "which",
            lambda n: str(tmp_path / n) if (tmp_path / n).exists() else None,
        )
        assert mod._launcher_for("cld2") == "cld2"
        assert mod._launcher_for("git") == "cld --profile git"
        assert mod._launcher_for("work") == "cld --profile work"


# ---------------------------------------------------------------------------
# add — seeded .claude.json
# ---------------------------------------------------------------------------

class TestSeededClaudeJson:
    def test_strips_oauth_and_marked_keys_keeps_mcp_servers(self, mod):
        result = mod.create_profile("work")
        seeded = json.loads(result.claude_json_path.read_text())

        assert "oauthAccount" not in seeded
        assert "cachedExtraUsageDisabledReason" not in seeded
        assert seeded["mcpServers"] == {"filesystem": {"command": "npx"}}
        assert seeded["preferences"] == {"theme": "dark"}
        assert seeded["installMethod"] == "homebrew"

    def test_no_remaining_key_matches_any_strip_substring(self, mod):
        result = mod.create_profile("work")
        seeded = json.loads(result.claude_json_path.read_text())
        for key in seeded:
            lowered = key.lower()
            for bad in mod.STRIP_SUBSTRINGS:
                assert bad not in lowered, f"{key!r} should have been stripped ({bad!r})"

    def test_claude_json_written_atomically_mode_0600(self, mod):
        result = mod.create_profile("work")
        mode = stat.S_IMODE(result.claude_json_path.stat().st_mode)
        assert mode == 0o600
        leftovers = [p.name for p in result.profile_dir.iterdir()
                     if p.name.startswith(".claude-profile-tmp-")]
        assert leftovers == []

    def test_strips_onboarding_flags_so_first_launch_offers_login(self, mod, fake_home):
        source = fake_home["home"] / ".claude.json"
        data = json.loads(source.read_text())
        data.update({
            "hasCompletedOnboarding": True,
            "lastOnboardingVersion": "2.1.251",
            "hasCompletedClaudeInChromeOnboarding": True,
        })
        source.write_text(json.dumps(data))

        seeded = json.loads(mod.create_profile("work").claude_json_path.read_text())

        assert "hasCompletedOnboarding" not in seeded
        assert "lastOnboardingVersion" not in seeded
        assert seeded["hasCompletedClaudeInChromeOnboarding"] is True  # feature tips stay

    def test_missing_source_claude_json_seeds_empty_dict(self, mod, fake_home):
        (fake_home["home"] / ".claude.json").unlink()
        result = mod.create_profile("work")
        assert json.loads(result.claude_json_path.read_text()) == {}


# ---------------------------------------------------------------------------
# add — refusals
# ---------------------------------------------------------------------------

class TestAddRefusals:
    def test_refuses_duplicate(self, mod):
        mod.create_profile("work")
        with pytest.raises(mod.ProfileError, match="already exists"):
            mod.create_profile("work")

    def test_refuses_bad_name(self, mod):
        with pytest.raises(mod.ProfileError, match="invalid profile name"):
            mod.create_profile("Bad_Name")

    def test_refuses_reserved_default_name(self, mod):
        with pytest.raises(mod.ProfileError, match="reserved"):
            mod.create_profile("default")

    def test_cli_add_duplicate_exits_1_without_relinking(self, mod, fake_home):
        mod.create_profile("work")
        marker = fake_home["home"] / ".aos" / "claude-profiles" / "work" / ".claude.json"
        before = marker.read_bytes()
        rc = mod.cmd_add(_ns(name="work"))
        assert rc == 1
        assert marker.read_bytes() == before  # untouched by the refused re-add

    def test_cli_add_bad_name_exits_1(self, mod):
        rc = mod.cmd_add(_ns(name="Bad"))
        assert rc == 1


# ---------------------------------------------------------------------------
# README, written on first use only
# ---------------------------------------------------------------------------

class TestReadme:
    def _readme_path(self, fake_home):
        return fake_home["home"] / ".aos" / "claude-profiles" / "README.md"

    def test_written_on_first_add(self, mod, fake_home):
        readme = self._readme_path(fake_home)
        assert not readme.exists()
        mod.create_profile("work")
        assert readme.exists()
        assert len(readme.read_text().splitlines()) == 3

    def test_not_overwritten_on_second_add(self, mod, fake_home):
        mod.create_profile("work")
        readme = self._readme_path(fake_home)
        readme.write_text("operator edited this\n")
        mod.create_profile("other")
        assert readme.read_text() == "operator edited this\n"


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

class TestRemove:
    def test_deletes_only_profile_dir_leaves_shared_targets(self, mod, fake_home):
        result = mod.create_profile("work")
        claude_dir = fake_home["claude_dir"]

        removed = mod.remove_profile("work")

        assert removed == result.profile_dir
        assert not result.profile_dir.exists()
        assert (claude_dir / "skills").is_dir()
        assert (claude_dir / "agents").is_dir()
        assert (claude_dir / "CLAUDE.md").read_text() == "# fake CLAUDE.md\n"

    def test_refuses_default(self, mod):
        with pytest.raises(mod.ProfileError, match="default"):
            mod.remove_profile("default")

    def test_refuses_missing_profile(self, mod):
        with pytest.raises(mod.ProfileError, match="does not exist"):
            mod.remove_profile("ghost")

    def test_cli_remove_mentions_keychain_left_for_operator(self, mod, capsys):
        mod.create_profile("work")
        rc = mod.cmd_remove(_ns(name="work"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Keychain" in out
        assert "/logout" in out

    def test_cli_remove_default_refused_with_exit_1(self, mod, capsys):
        rc = mod.cmd_remove(_ns(name="default"))
        assert rc == 1


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------

class TestPath:
    def test_prints_profile_dir(self, mod, capsys):
        result = mod.create_profile("work")
        rc = mod.cmd_path(_ns(name="work"))
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(result.profile_dir)

    def test_default_prints_claude_dir(self, mod, fake_home, capsys):
        rc = mod.cmd_path(_ns(name="default"))
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(fake_home["claude_dir"])

    def test_missing_profile_errors(self, mod, capsys):
        rc = mod.cmd_path(_ns(name="ghost"))
        assert rc == 1


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:
    def test_default_profile_listed_first(self, mod):
        mod.create_profile("work")
        mod.create_profile("client-b")
        rows = mod.list_profiles()
        assert rows[0].name == "default"
        assert [r.name for r in rows[1:]] == sorted(["work", "client-b"])

    def test_handles_missing_claude_binary(self, mod, monkeypatch):
        mod.create_profile("work")
        monkeypatch.setattr(mod.shutil, "which", lambda name: None)
        rows = mod.list_profiles()
        assert rows, "expected at least the default row"
        assert all(r.status == "claude not found" for r in rows)

    def test_parses_logged_in_true_and_false(self, mod, monkeypatch):
        mod.create_profile("work")
        monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/claude")

        def fake_run(cmd, **kwargs):
            # default is probed with NO CLAUDE_CONFIG_DIR; named profiles with it
            cfg = kwargs["env"].get("CLAUDE_CONFIG_DIR", "")
            logged_in = cfg.endswith("work")
            payload = json.dumps({"loggedIn": logged_in})
            return subprocess.CompletedProcess(cmd, 0 if logged_in else 1, payload, "")

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        statuses = {r.name: r.status for r in mod.list_profiles()}
        assert statuses["work"] == "logged in"
        assert statuses["default"] == "not logged in"

    def test_handles_timeout(self, mod, monkeypatch):
        monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/claude")

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=10)

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        assert mod._login_status(mod.CLAUDE_DIR) == "timed out"

    def test_uses_a_ten_second_timeout(self, mod, monkeypatch):
        monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/claude")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, '{"loggedIn": true}', "")

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        mod._login_status(mod.CLAUDE_DIR)
        assert captured["timeout"] == 10
        # The default profile is probed WITHOUT CLAUDE_CONFIG_DIR — setting it
        # explicitly to ~/.claude makes `claude auth status` report a logged-in
        # account as logged out (verified live on 0.7.10).
        assert "CLAUDE_CONFIG_DIR" not in captured["env"]


# ---------------------------------------------------------------------------
# Keychain — must never be touched
# ---------------------------------------------------------------------------

class TestNeverTouchesKeychain:
    def test_add_never_shells_out(self, mod, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("add must never invoke a subprocess")

        monkeypatch.setattr(mod.subprocess, "run", boom)
        mod.create_profile("work")  # should not raise

    def test_source_never_invokes_the_security_cli(self):
        # "Keychain" appears in comments/messages (informational — remove
        # tells the operator a credential is left behind); what must never
        # appear is an actual invocation of the macOS `security` binary.
        text = CLAUDE_PROFILE.read_text()
        assert '"security"' not in text
        assert "'security'" not in text


# ---------------------------------------------------------------------------
# End-to-end: real subprocess, stub `claude` on PATH, sandboxed HOME
# ---------------------------------------------------------------------------

_STUB_CLAUDE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    if sys.argv[1:3] == ["auth", "status"]:
        cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
        logged_in = cfg.endswith("logged-in-profile")
        print(json.dumps({"loggedIn": logged_in}))
        sys.exit(0 if logged_in else 1)
    sys.exit(1)
    """)


class TestEndToEndSubprocess:
    def test_add_then_list_via_real_subprocess_with_stub_claude(self, fake_home, tmp_path):
        stub_dir = tmp_path / "stubbin"
        stub_dir.mkdir()
        stub = stub_dir / "claude"
        stub.write_text(_STUB_CLAUDE)
        stub.chmod(0o755)

        env = dict(os.environ)
        env["HOME"] = str(fake_home["home"])
        env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"

        add = subprocess.run(
            [sys.executable, str(CLAUDE_PROFILE), "add", "logged-in-profile"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        assert add.returncode == 0, add.stderr

        listed = subprocess.run(
            [sys.executable, str(CLAUDE_PROFILE), "list"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        assert listed.returncode == 0, listed.stderr
        assert "logged-in-profile" in listed.stdout
        assert "logged in" in listed.stdout
        assert "not logged in" in listed.stdout  # the default profile

    def test_path_subcommand_via_real_subprocess(self, fake_home):
        env = dict(os.environ)
        env["HOME"] = str(fake_home["home"])

        subprocess.run(
            [sys.executable, str(CLAUDE_PROFILE), "add", "work"],
            env=env, capture_output=True, text=True, timeout=15, check=True,
        )
        result = subprocess.run(
            [sys.executable, str(CLAUDE_PROFILE), "path", "work"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        expected = fake_home["home"] / ".aos" / "claude-profiles" / "work"
        assert result.stdout.strip() == str(expected)


# ---------------------------------------------------------------------------
# Lanes integration (aos#244.3) — `add` appends to an operator-created
# claude-lanes.yaml, and `status` reports lanes/login/exhaustion.
# ---------------------------------------------------------------------------

class TestAddAppendsToLanesConfig:
    def _lanes_path(self, fake_home) -> Path:
        return fake_home["home"] / ".aos" / "config" / "claude-lanes.yaml"

    def test_no_lanes_file_means_add_does_not_create_one(self, mod, fake_home):
        result = mod.create_profile("work")
        assert result.lanes_note is None
        assert not self._lanes_path(fake_home).exists()

    def test_appends_the_new_name_to_an_existing_lanes_file(self, mod, fake_home):
        lanes_path = self._lanes_path(fake_home)
        lanes_path.parent.mkdir(parents=True, exist_ok=True)
        lanes_path.write_text("lanes: [default]\n")

        result = mod.create_profile("work")

        assert "Added 'work'" in result.lanes_note
        data = yaml.safe_load(lanes_path.read_text())
        assert data["lanes"] == ["default", "work"]

    def test_does_not_duplicate_an_already_listed_lane(self, mod, fake_home):
        lanes_path = self._lanes_path(fake_home)
        lanes_path.parent.mkdir(parents=True, exist_ok=True)
        lanes_path.write_text("lanes: [default, work]\n")

        result = mod.create_profile("work")

        assert "already a lane" in result.lanes_note
        data = yaml.safe_load(lanes_path.read_text())
        assert data["lanes"] == ["default", "work"]  # unchanged, not duplicated

    def test_cli_add_prints_the_lanes_note(self, mod, fake_home, capsys):
        lanes_path = self._lanes_path(fake_home)
        lanes_path.parent.mkdir(parents=True, exist_ok=True)
        lanes_path.write_text("lanes: [default]\n")

        mod.cmd_add(_ns(name="work"))
        out = capsys.readouterr().out
        assert "Added 'work'" in out


class TestStatus:
    def test_no_lanes_file_shows_default_only(self, mod, capsys):
        rc = mod.cmd_status(_ns())
        out = capsys.readouterr().out
        assert rc == 0
        assert "default" in out
        assert "work" not in out

    def test_lists_every_configured_lane_with_exhaustion(self, mod, fake_home, capsys):
        lanes_path = fake_home["home"] / ".aos" / "config" / "claude-lanes.yaml"
        lanes_path.parent.mkdir(parents=True, exist_ok=True)
        lanes_path.write_text("lanes: [default, work]\n")
        mod.create_profile("work")

        state_dir = fake_home["home"] / ".aos" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "claude-lanes.json").write_text(
            json.dumps({"default": {"exhausted_until": "2099-01-01T00:00:00"}})
        )

        rc = mod.cmd_status(_ns())
        out = capsys.readouterr().out
        assert rc == 0
        assert "default" in out and "work" in out
        assert "exhausted until" in out
