"""
Tests the cmux "already installed" detection in install.sh's prereq_editor
(the line that decides whether to `brew install --cask cmux` at all).

The faisal-mini parity audit (aos#237) traced a second-operator machine
ending up with only the brew CLI formula on PATH and no
/Applications/cmux.app to this exact check: `command -v cmux` is true for
the CLI-only formula too, so install.sh saw *a* `cmux` and skipped
installing the cask that actually provides the app `aos start` needs. This
extracts the real shipped condition (not a hand-copied duplicate) and drives
it against a fake PATH and a substituted app-bundle path, so the result
doesn't depend on whether this particular runner happens to have cmux
installed for real.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO / "install.sh"
REAL_APP_PATH = "/Applications/cmux.app"


def _extract_condition() -> str:
    """The `if <condition>` test inside prereq_editor, verbatim, without the
    trailing `; then` — the caller supplies its own then/else/fi."""
    text = INSTALL_SH.read_text()
    marker = "prereq_editor() {"
    start = text.index(marker)
    line_start = text.index("if ", start)
    line_end = text.index("\n", line_start)
    line = text[line_start:line_end]
    then_at = line.rindex("; then")
    return line[len("if "):then_at]


def _cmux_bin_line() -> str:
    text = INSTALL_SH.read_text()
    idx = text.index('CMUX_BIN="/Applications/cmux.app')
    end = text.index("\n", idx)
    return text[idx:end]


def _is_considered_installed(fake_app_root: Path, path_dirs: list[Path]) -> bool:
    """Evaluate the real shipped condition with $PATH faked and the
    hardcoded /Applications/cmux.app substituted for a sandbox path — so
    the result is independent of whether cmux is really installed here."""
    bin_line = _cmux_bin_line().replace(REAL_APP_PATH, str(fake_app_root))
    condition = _extract_condition().replace(REAL_APP_PATH, str(fake_app_root))
    script = f'{bin_line}\nif {condition}; then echo YES; else echo NO; fi\n'

    env = dict(os.environ)
    env["PATH"] = ":".join(str(d) for d in path_dirs)
    result = subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "YES"


def test_condition_is_extractable():
    cond = _extract_condition()
    assert "cmux" in cond


def test_bare_cli_on_path_with_no_app_bundle_is_not_considered_installed(tmp_path):
    """The exact faisal-mini shape: `cmux` resolves on PATH, no .app exists."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli = bin_dir / "cmux"
    fake_cli.write_text("#!/bin/sh\necho fake cli\n")
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)

    fake_app_root = tmp_path / "Applications" / "cmux.app"  # deliberately absent

    assert _is_considered_installed(fake_app_root, [bin_dir]) is False


def test_app_bundle_present_is_considered_installed(tmp_path):
    fake_app_root = tmp_path / "Applications" / "cmux.app"
    (fake_app_root / "Contents" / "Resources" / "bin").mkdir(parents=True)
    cmux_bin = fake_app_root / "Contents" / "Resources" / "bin" / "cmux"
    cmux_bin.write_text("#!/bin/sh\necho real app cli\n")
    cmux_bin.chmod(cmux_bin.stat().st_mode | stat.S_IEXEC)

    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()

    assert _is_considered_installed(fake_app_root, [empty_bin]) is True


def test_bare_cli_plus_app_bundle_is_still_considered_installed(tmp_path):
    """Both present (the common case once cmux is properly installed) still
    counts — this isn't about penalizing the CLI, only about not accepting
    it *alone*."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli = bin_dir / "cmux"
    fake_cli.write_text("#!/bin/sh\necho fake cli\n")
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)

    fake_app_root = tmp_path / "Applications" / "cmux.app"
    (fake_app_root / "Contents" / "Resources" / "bin").mkdir(parents=True)

    assert _is_considered_installed(fake_app_root, [bin_dir]) is True
