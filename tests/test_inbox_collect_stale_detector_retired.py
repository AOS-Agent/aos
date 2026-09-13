"""`inbox-collect` and `stale-detector` are gone (cron audit 2026-09-13,
aos#240.2).

inbox-collect: all three declared sources (calendar/voicememo/notes) report
"not available" every run — the reader modules it imports
(calendar_reader/voicememo_reader/notes_reader) don't exist anywhere in this
tree, so 159 of 160 daily notes were the bare unfilled template. Its
`_ensure_daily_note()` duplicated what core/services/bridge/daily_briefing.py
already creates independently.

stale-detector: writes ~/.aos/work/stale-report.yaml and never notifies;
grep confirmed zero readers anywhere in core, config, or docs.
stale-initiatives already covers the notifying half of "stale" (initiatives
untouched 3+ days, verified Telegram send).

Deleted rather than disabled — same posture test_channel_update_retired.py
pins for channel-update: a script nobody can run and nobody reads the
output of is not worth keeping around commented out. stale-report.yaml
itself (instance data, not framework) is archived by migration 131 rather
than deleted outright.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_inbox_collect_script_is_gone():
    assert not (REPO / "core" / "bin" / "crons" / "inbox-collect").exists()
    assert not (REPO / "core" / "bin" / "inbox-collect").exists()


def test_stale_detector_script_is_gone():
    assert not (REPO / "core" / "bin" / "crons" / "stale-detector").exists()
    assert not (REPO / "core" / "bin" / "stale-detector").exists()


def test_crons_yaml_has_no_inbox_collect_entry():
    text = (REPO / "config" / "crons.yaml").read_text()
    assert "inbox-collect" not in text


def test_crons_yaml_has_no_stale_detector_entry():
    text = (REPO / "config" / "crons.yaml").read_text()
    assert "stale-detector" not in text


def test_nightly_pipeline_no_longer_calls_stale_detector():
    """A caller of the deleted script is a dead reader too — must be removed
    in the same change, not left to fail every night."""
    for path in (
        REPO / "core" / "bin" / "crons" / "nightly-pipeline",
        REPO / "core" / "bin" / "nightly-pipeline",
    ):
        if path.exists():
            assert "stale-detector" not in path.read_text()


def test_stale_initiatives_still_covers_the_useful_half():
    """The one part of "stale" that had a real consumer (Telegram notify)
    survives — this isn't a silent feature loss."""
    assert (REPO / "core" / "bin" / "crons" / "stale-initiatives").exists()
    text = (REPO / "config" / "crons.yaml").read_text()
    assert "stale-initiatives" in text
