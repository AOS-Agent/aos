"""
Tests for `core/bin/cli/cld` learning a profile (aos#244.2): the invoked name
(a symlink like `cld2`/`cld3`) or an explicit `--profile <name>` resolves a
CLAUDE_CONFIG_DIR via `claude-profile path <name>` and exports it before the
final `exec claude ...`. Plain `cld`, with neither, must be byte-for-byte
what it was before profiles existed.

`cld` is a bash script, so these are real subprocess runs — no in-process
import trick applies. A stub `claude` goes first on PATH; it just prints its
own env (`CLAUDE_CONFIG_DIR`, or `<unset>`) and argv, so each test can assert
on exactly what `cld`'s final `exec` line would have handed the real binary.
Everything runs under a sandboxed $HOME with `~/aos` symlinked to this repo
(so `cld`'s references to `$HOME/aos/core/bin/cli/claude-profile` resolve to
the real script) and a fake `~/.aos/config/operator.yaml` — never the
operator's real `~/.claude`, `~/.aos`, or Keychain.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CLD = REPO / "core" / "bin" / "cli" / "cld"

# The real AOS python resolver is bypassed in favor of pointing AOS_PY
# directly at the sandbox's own interpreter — read-only, never modifies
# anything, and sidesteps needing a full `~/aos/core/bin/internal/aos-python`
# resolver stood up under a fake HOME just to run one `import yaml` line.
REAL_PYTHON = sys.executable

_STUB_CLAUDE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    print("CLAUDE_CONFIG_DIR=" + os.environ.get("CLAUDE_CONFIG_DIR", "<unset>"))
    print("ARGV=" + json.dumps(sys.argv[1:]))
    """)


@pytest.fixture()
def sandbox(tmp_path):
    """A sandboxed $HOME + PATH, with the stub `claude` first on PATH and
    `~/aos` symlinked to this repo checkout."""
    home = tmp_path / "home"
    (home / ".aos" / "config").mkdir(parents=True)
    (home / ".local" / "bin").mkdir(parents=True)
    (home / "aos").symlink_to(REPO)
    (home / ".aos" / "config" / "operator.yaml").write_text("agent_name: chief\n")

    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    stub_claude = stub_dir / "claude"
    stub_claude.write_text(_STUB_CLAUDE)
    stub_claude.chmod(0o755)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AOS_PY"] = REAL_PYTHON

    return {"home": home, "env": env, "stub_dir": stub_dir}


def _run(argv0: Path, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(argv0), *args], env=env, capture_output=True, text=True, timeout=15,
    )


def _profile_dir(home: Path, name: str) -> Path:
    return home / ".aos" / "claude-profiles" / name


class TestPlainCldUnchanged:
    def test_no_config_dir_exported(self, sandbox):
        result = _run(CLD, [], sandbox["env"])
        assert result.returncode == 0, result.stderr
        assert "CLAUDE_CONFIG_DIR=<unset>" in result.stdout

    def test_argv_is_exactly_the_pre_existing_flags(self, sandbox):
        result = _run(CLD, [], sandbox["env"])
        assert 'ARGV=["--dangerously-skip-permissions", "--agent", "chief"]' in result.stdout

    def test_extra_arguments_pass_through_untouched(self, sandbox):
        result = _run(CLD, ["do something", "--resume"], sandbox["env"])
        assert result.returncode == 0, result.stderr
        assert "CLAUDE_CONFIG_DIR=<unset>" in result.stdout
        assert '"do something"' in result.stdout
        assert '"--resume"' in result.stdout


class TestSymlinkInvokedProfile:
    def test_cld2_symlink_with_profile_present(self, sandbox, tmp_path):
        _profile_dir(sandbox["home"], "cld2").mkdir(parents=True)
        cld2 = tmp_path / "cld2"
        cld2.symlink_to(CLD)

        result = _run(cld2, [], sandbox["env"])
        assert result.returncode == 0, result.stderr
        expected = str(_profile_dir(sandbox["home"], "cld2"))
        assert f"CLAUDE_CONFIG_DIR={expected}" in result.stdout
        assert result.stdout.rstrip().splitlines()[0].endswith("claude-profiles/cld2")

    def test_cld3_symlink_with_profile_present(self, sandbox, tmp_path):
        _profile_dir(sandbox["home"], "cld3").mkdir(parents=True)
        cld3 = tmp_path / "cld3"
        cld3.symlink_to(CLD)

        result = _run(cld3, [], sandbox["env"])
        assert result.returncode == 0, result.stderr
        assert result.stdout.rstrip().splitlines()[0].endswith("claude-profiles/cld3")

    def test_agent_name_and_flags_still_present_via_symlink(self, sandbox, tmp_path):
        _profile_dir(sandbox["home"], "cld2").mkdir(parents=True)
        cld2 = tmp_path / "cld2"
        cld2.symlink_to(CLD)

        result = _run(cld2, [], sandbox["env"])
        assert 'ARGV=["--dangerously-skip-permissions", "--agent", "chief"]' in result.stdout

    def test_cld2_with_no_profile_dir_exits_2_with_fix_line(self, sandbox, tmp_path):
        cld2 = tmp_path / "cld2"
        cld2.symlink_to(CLD)
        assert not _profile_dir(sandbox["home"], "cld2").exists()

        result = _run(cld2, [], sandbox["env"])
        assert result.returncode == 2
        assert result.stdout == ""  # never reached the exec
        assert "claude-profile add cld2" in result.stderr


class TestExplicitProfileFlag:
    def test_cld_dash_dash_profile_cld3(self, sandbox):
        _profile_dir(sandbox["home"], "cld3").mkdir(parents=True)
        result = _run(CLD, ["--profile", "cld3"], sandbox["env"])
        assert result.returncode == 0, result.stderr
        expected = str(_profile_dir(sandbox["home"], "cld3"))
        assert f"CLAUDE_CONFIG_DIR={expected}" in result.stdout

    def test_remaining_arguments_still_reach_claude(self, sandbox):
        _profile_dir(sandbox["home"], "cld3").mkdir(parents=True)
        result = _run(CLD, ["--profile", "cld3", "do the thing"], sandbox["env"])
        assert result.returncode == 0, result.stderr
        assert '"do the thing"' in result.stdout
        assert '"--profile"' not in result.stdout  # consumed, not forwarded

    def test_profile_flag_with_missing_profile_dir_exits_2(self, sandbox):
        assert not _profile_dir(sandbox["home"], "ghost").exists()
        result = _run(CLD, ["--profile", "ghost"], sandbox["env"])
        assert result.returncode == 2
        assert "claude-profile add ghost" in result.stderr

    def test_profile_flag_only_honored_as_the_first_argument(self, sandbox):
        """`--profile` in any later position is just an ordinary argument
        forwarded to claude, not a profile resolution."""
        result = _run(CLD, ["do something", "--profile", "cld2"], sandbox["env"])
        assert result.returncode == 0, result.stderr
        assert "CLAUDE_CONFIG_DIR=<unset>" in result.stdout
        assert '"--profile"' in result.stdout

    def test_profile_flag_with_no_name_is_a_usage_error(self, sandbox):
        result = _run(CLD, ["--profile"], sandbox["env"])
        assert result.returncode == 2
        assert "Usage" in result.stderr
