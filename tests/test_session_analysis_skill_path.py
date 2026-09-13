"""
core/skills/session-analysis/SKILL.md — referenced script must exist
(aos#236.6).

Dangling-wires audit (2026-09-13): the skill told the agent to run
`python3 ~/aos/bin/session-analysis --days 7` — that path has never existed;
the real script is `core/bin/session-analysis` (a symlink to
core/bin/crons/session-analysis, which crons.yaml itself invokes for the
weekly job). Following the skill's own instructions verbatim would have
produced "No such file or directory" every time.

This test extracts the actual `python3 <path>` command from the skill body
and asserts the path — with `~/aos/` translated to this repo's root, since
~/aos is exactly this tree once released — resolves to a real, executable
file. It fails on the old `~/aos/bin/session-analysis` path and passes on
the fixed `~/aos/core/bin/session-analysis`.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILL_MD = REPO_ROOT / "core" / "skills" / "session-analysis" / "SKILL.md"

_COMMAND_RE = re.compile(r"^python3\s+(~/aos/\S+)", re.MULTILINE)


def _referenced_script_path() -> Path:
    text = SKILL_MD.read_text()
    m = _COMMAND_RE.search(text)
    assert m, f"no `python3 ~/aos/...` command found in {SKILL_MD}"
    aos_relative = m.group(1).removeprefix("~/aos/")
    return REPO_ROOT / aos_relative


def test_skill_references_a_script_that_actually_exists():
    script = _referenced_script_path()
    assert script.exists(), (
        f"SKILL.md tells the agent to run {script}, which does not exist — "
        f"this is the exact dangling-wires audit finding"
    )


def test_skill_no_longer_references_the_old_dangling_bin_path():
    text = SKILL_MD.read_text()
    assert "~/aos/bin/session-analysis" not in text, (
        "SKILL.md still references the nonexistent ~/aos/bin/ path"
    )


def test_referenced_script_is_the_real_session_analysis_tool():
    script = _referenced_script_path()
    # Sanity: it's the actual friction-report generator, not some
    # coincidentally-named stand-in.
    assert "friction" in script.read_text().lower()
