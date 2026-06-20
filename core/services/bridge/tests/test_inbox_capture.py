"""Tests for the triage-inbox capture formatter (intent_classifier).

These cover the pure formatting helper so the line shape, trigger-word
stripping, type guessing, and src/project defaults can be verified without the
bridge (or a real inbox file) running.
"""

import re
import sys
from pathlib import Path

# Add bridge directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intent_classifier import format_triage_line, handle_inbox  # noqa: E402

# - <ISO8601> · [<type> · <project> · ...] · src:<source> · <title>
LINE_RE = re.compile(
    r"^- \d{4}-\d{2}-\d{2}T\d{2}:\d{2} · "
    r"\[(?P<type>\w+) · (?P<project>[^\]·]+?)\] · "
    r"src:(?P<src>\w+) · (?P<title>.+)$"
)

FIXED = "2026-06-20T15:42"


def _parse(line: str) -> dict:
    m = LINE_RE.match(line)
    assert m, f"line did not match expected shape: {line!r}"
    return m.groupdict()


def test_line_shape_and_defaults():
    line = format_triage_line("capture look into Redis", now=FIXED)
    g = _parse(line)
    assert g["type"] == "idea"          # default type
    assert g["project"].strip() == "?"  # default project (triage resolves)
    assert g["src"] == "telegram"       # default source
    assert g["title"] == "look into Redis"
    assert line.startswith(f"- {FIXED} · ")


def test_strips_each_trigger_word():
    cases = {
        "capture look into Redis": "look into Redis",
        "inbox look into Redis": "look into Redis",
        "jot down the qibla idea": "the qibla idea",
        "note: ship the bridge fix": "ship the bridge fix",
        "note ship the bridge fix": "ship the bridge fix",
        "remember to renew the domain": "to renew the domain",
    }
    for raw, expected_title in cases.items():
        g = _parse(format_triage_line(raw, now=FIXED))
        assert g["title"] == expected_title, f"{raw!r} -> {g['title']!r}"


def test_source_override_for_voice():
    g = _parse(format_triage_line("capture try the new model", source="voice", now=FIXED))
    assert g["src"] == "voice"


def test_type_guess_task_for_todo_phrasing():
    g = _parse(format_triage_line("remember to call the accountant", now=FIXED))
    assert g["type"] == "task"


def test_type_guess_bug():
    g = _parse(format_triage_line("capture the qibla compass is broken", now=FIXED))
    assert g["type"] == "bug"


def test_type_guess_idea_default():
    g = _parse(format_triage_line("capture a voice-note capture door", now=FIXED))
    assert g["type"] == "idea"


def test_multiline_collapses_to_one_line():
    g = _parse(format_triage_line("capture line one\nline two\n  line three", now=FIXED))
    assert "\n" not in g["title"]
    assert g["title"] == "line one line two line three"


def test_handle_inbox_appends_to_temp_inbox(tmp_path, monkeypatch):
    """handle_inbox should append a formatted line and not read the file back."""
    import intent_classifier as ic

    fake_inbox = tmp_path / "triage" / "inbox.md"
    monkeypatch.setattr(ic, "TRIAGE_INBOX", fake_inbox)

    reply = ic.handle_inbox("capture wire the capture door")
    assert reply == "Captured: wire the capture door"

    contents = fake_inbox.read_text(encoding="utf-8")
    lines = [ln for ln in contents.splitlines() if ln.strip()]
    assert len(lines) == 1
    g = _parse(lines[0])
    assert g["src"] == "telegram"
    assert g["title"] == "wire the capture door"

    # A second capture appends (write-only, does not clobber).
    ic.handle_inbox("capture second thought", source="voice")
    lines = [ln for ln in fake_inbox.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    assert _parse(lines[1])["src"] == "voice"


def test_handle_inbox_empty_capture():
    assert handle_inbox("capture ").startswith("Please provide")
