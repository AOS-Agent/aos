"""
Tests the `aos self-test` bridge-topics-config message
(core/bin/cli/aos, inside the `self-test)` case).

Before this change the "missing" branch was a single unconditional line —
"Bridge topics config missing (created on first use)" — with no first use
that ever created it, and no command to run. This exercises the REAL shipped
bash block (extracted verbatim from core/bin/cli/aos, not a hand-copied
duplicate — any future edit to that block is covered automatically) against
a fake $HOME, in all three states: nothing configured, projects.yaml only,
and fully bootstrapped.

`aos self-test` as a whole is not sandboxable in a unit test (it touches the
live Python resolver, running services, etc.) — this isolates just the
three-way file check, which is fully self-contained.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AOS_CLI = REPO / "core" / "bin" / "cli" / "aos"

START_MARKER = "# Check bridge topics config."
END_MARKER = "\n        fi\n"


def _extract_block() -> str:
    text = AOS_CLI.read_text()
    start = text.index(START_MARKER)
    end = text.index(END_MARKER, start) + len(END_MARKER)
    return text[start:end]


def _run_block(home: Path) -> str:
    block = _extract_block()
    result = subprocess.run(
        ["bash", "-c", block],
        capture_output=True, text=True, timeout=10,
        env={"HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_block_is_present_and_extractable():
    """Guards the extraction itself — if this fails, the markers drifted."""
    block = _extract_block()
    assert "bridge-topics.yaml" in block
    assert block.strip().endswith("fi")


def test_neither_file_present_points_at_the_fix_command(tmp_path):
    (tmp_path / ".aos" / "config").mkdir(parents=True)
    out = _run_block(tmp_path)
    assert "aos bridge-topics init" in out
    assert "✓" not in out


def test_projects_yaml_only_still_points_at_the_fix_command(tmp_path):
    cfg = tmp_path / ".aos" / "config"
    cfg.mkdir(parents=True)
    (cfg / "projects.yaml").write_text("system: {}\n")
    out = _run_block(tmp_path)
    assert "aos bridge-topics init" in out
    assert "✓" not in out


def test_fully_bootstrapped_reports_success_with_no_fix_command(tmp_path):
    cfg = tmp_path / ".aos" / "config"
    cfg.mkdir(parents=True)
    (cfg / "projects.yaml").write_text("system: {}\n")
    (cfg / "bridge-topics.yaml").write_text("topics: {}\n")
    out = _run_block(tmp_path)
    assert "✓" in out
    assert "aos bridge-topics init" not in out
