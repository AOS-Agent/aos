"""session-analysis wrote a filename literally named `...-weekly.md` on every
DAILY run (cron audit 2026-09-13, aos#240.3).

Root cause: nightly-pipeline invokes `session-analysis daily`, but main()
only ever recognized `--days N` — a bare positional "daily" was silently
ignored, so `days` stayed at the default 7 and the daily filename branch
(`days == 1 → "daily"`) never fired. Every nightly-pipeline run produced
`session-friction-<date>-weekly.md`, indistinguishable from the real Sunday
weekly report.

`_parse_args` is the extracted, pure fix: a positional "daily"/"weekly" mode
word now sets both the lookback window and the label explicitly, and an
explicit `--days N` still works standalone exactly as it did before.
"""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "core" / "bin" / "crons" / "session-analysis"


def _load():
    loader = SourceFileLoader("session_analysis_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sa = _load()


def test_no_args_defaults_to_weekly_unchanged():
    """The standalone Sunday 22:00 cron calls this with no arguments at all —
    must keep behaving exactly as before."""
    assert sa._parse_args([]) == (7, "weekly")


def test_bare_days_flag_still_works_standalone():
    assert sa._parse_args(["--days", "3"]) == (3, "3d")


def test_daily_positional_sets_one_day_window_and_daily_suffix():
    """This is nightly-pipeline's actual invocation: `session-analysis daily`."""
    assert sa._parse_args(["daily"]) == (1, "daily")


def test_weekly_positional_is_explicit_and_equivalent_to_default():
    assert sa._parse_args(["weekly"]) == (7, "weekly")


def test_days_flag_overrides_the_window_but_mode_word_still_labels_the_file():
    # An explicit --days alongside a mode word: the mode word wins the label,
    # the flag wins the window — no silent surprise either way.
    assert sa._parse_args(["daily", "--days", "2"]) == (2, "daily")


def test_end_to_end_daily_invocation_writes_daily_filename_not_weekly(tmp_path, monkeypatch):
    empty_projects = tmp_path / "no-sessions"
    empty_projects.mkdir()
    monkeypatch.setattr(sa, "CLAUDE_PROJECTS", empty_projects)
    monkeypatch.setattr(sa, "VAULT_FRICTION", tmp_path / "friction")
    monkeypatch.setattr("sys.argv", ["session-analysis", "daily"])

    sa.main()

    written = list((tmp_path / "friction").glob("*.md"))
    assert len(written) == 1
    assert written[0].name.endswith("-daily.md"), written[0].name
    assert "weekly" not in written[0].name


def test_end_to_end_no_args_still_writes_weekly_filename(tmp_path, monkeypatch):
    empty_projects = tmp_path / "no-sessions"
    empty_projects.mkdir()
    monkeypatch.setattr(sa, "CLAUDE_PROJECTS", empty_projects)
    monkeypatch.setattr(sa, "VAULT_FRICTION", tmp_path / "friction")
    monkeypatch.setattr("sys.argv", ["session-analysis"])

    sa.main()

    written = list((tmp_path / "friction").glob("*.md"))
    assert len(written) == 1
    assert written[0].name.endswith("-weekly.md"), written[0].name
