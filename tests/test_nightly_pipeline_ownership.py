"""nightly-pipeline owns exactly the one nightly step nothing else runs
(cron audit 2026-09-13, aos#240.3).

Before this fix it re-ran two steps that already had their own standalone
crons firing the same day: stale-detector (08:00, now retired — aos#240.2)
and compile-daily (its own 23:30 cron). Pure waste, and two owners for the
same output. This pins the fixed shape:

  - session-analysis in DAILY mode (Mon-Sat) is owned SOLELY by
    nightly-pipeline — the standalone session-analysis cron only ever runs
    in weekly mode (Sunday 22:00).
  - compile-daily is no longer called here at all — its own 23:30 cron is
    the sole owner.
  - Sunday: nothing to do (the weekly session-analysis already ran via its
    own cron), so nightly-pipeline exits 0 having done nothing, rather than
    silently double-running the daily variant on top of the weekly one.

Runs the real script as a subprocess with $HOME pointed at a fixture tree of
stub `aos-python`/`session-analysis`/`compile-daily` executables, so the
assertions are about what nightly-pipeline actually invokes, not about
reading its source as text.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "core" / "bin" / "crons" / "nightly-pipeline"

_STUB_AOS_PYTHON = """\
#!/bin/bash
# Fake aos-python: just exec the "python script" arg as a shell script,
# recording that it was invoked, then relaying its exit code.
target="$1"
shift
echo "aos-python invoked: $target $*" >> "$CALL_LOG"
"$target" "$@"
exit $?
"""


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fixture_home(tmp_path: Path, *, session_analysis_rc: int = 0) -> Path:
    home = tmp_path / "home"
    bin_dir = home / "aos" / "core" / "bin"
    bin_dir.mkdir(parents=True)

    _make_executable(bin_dir / "aos-python", _STUB_AOS_PYTHON)
    _make_executable(
        bin_dir / "session-analysis",
        f"#!/bin/bash\necho \"session-analysis called with: $*\" >> \"$CALL_LOG\"\nexit {session_analysis_rc}\n",
    )
    _make_executable(
        bin_dir / "compile-daily",
        "#!/bin/bash\necho \"compile-daily called\" >> \"$CALL_LOG\"\nexit 0\n",
    )
    return home


def _run(home: Path, call_log: Path, *, weekday: int) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["CALL_LOG"] = str(call_log)
    # Fake `date +%u` so the test is not at the mercy of the day it runs on:
    # prepend a stub `date` on PATH for weekday-dependent branches only.
    fake_bin = home / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    _make_executable(
        fake_bin / "date",
        f"#!/bin/bash\nif [ \"$1\" = \"+%u\" ]; then echo {weekday}; else /bin/date \"$@\"; fi\n",
    )
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30,
    )


def test_weekday_run_calls_session_analysis_daily_only(tmp_path):
    call_log = tmp_path / "calls.log"
    home = _fixture_home(tmp_path)
    result = _run(home, call_log, weekday=3)  # Wednesday

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text() if call_log.exists() else ""
    assert "session-analysis called with: daily" in calls
    assert "compile-daily called" not in calls
    assert "stale-detector" not in calls


def test_sunday_run_does_nothing(tmp_path):
    call_log = tmp_path / "calls.log"
    home = _fixture_home(tmp_path)
    result = _run(home, call_log, weekday=7)  # Sunday

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text() if call_log.exists() else ""
    assert calls == ""  # nothing invoked — weekly cron already covered it
    assert "Skipping" in result.stdout


def test_session_analysis_failure_propagates_as_nonzero_exit(tmp_path):
    call_log = tmp_path / "calls.log"
    home = _fixture_home(tmp_path, session_analysis_rc=1)
    result = _run(home, call_log, weekday=2)  # Tuesday

    assert result.returncode != 0
    assert "FAILED" in result.stderr or "FAILED" in result.stdout


def test_script_no_longer_invokes_compile_daily_or_stale_detector():
    """Explanatory prose mentioning the two retired steps (why they're gone)
    is fine and expected — an actual invocation of either is not."""
    text = SCRIPT.read_text()
    assert "bin/compile-daily" not in text
    assert "bin/stale-detector" not in text
    assert "compile-daily" in text  # still explained in the header comment
    assert "stale-detector" in text  # ditto
