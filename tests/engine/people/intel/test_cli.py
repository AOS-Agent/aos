"""Tests for the People Intelligence CLI.

Mocks the SignalExtractor so we don't touch real people.db during tests.
Focused on command dispatch, argparse wiring, and exit codes — not the
exact text format of the output.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.engine.people.intel.cli import build_parser, main
from core.engine.people.intel.extractor import RunReport
from core.engine.people.intel.types import (
    CommunicationSignal,
    PersonSignals,
    VoiceSignal,
)


# ── Parser tests ──────────────────────────────────────────────────────


def test_parser_requires_command():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_coverage():
    parser = build_parser()
    args = parser.parse_args(["coverage"])
    assert args.command == "coverage"


def test_parser_extract_flags():
    parser = build_parser()
    args = parser.parse_args(
        ["extract", "--limit", "5", "--adapters", "apple_messages,whatsapp", "--dry-run"]
    )
    assert args.command == "extract"
    assert args.limit == 5
    assert args.adapters == "apple_messages,whatsapp"
    assert args.dry_run is True


def test_parser_show_requires_person_id():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["show"])


def test_parser_show_accepts_person_id():
    parser = build_parser()
    args = parser.parse_args(["show", "p_abc"])
    assert args.person_id == "p_abc"


# ── Command tests (with mocked extractor) ────────────────────────────


def _fake_coverage_report():
    return {
        "available_count": 2,
        "total_count": 3,
        "coverage_pct": 0.67,
        "signal_types_covered": ["communication", "voice"],
        "signal_types_missing": ["mention"],
        "available": [
            {
                "name": "adapter_a",
                "display_name": "Adapter A",
                "platform": "macos",
                "signal_types": ["communication"],
                "description": "",
                "available": True,
            },
            {
                "name": "adapter_b",
                "display_name": "Adapter B",
                "platform": "any",
                "signal_types": ["voice"],
                "description": "",
                "available": True,
            },
        ],
        "unavailable": [
            {
                "name": "adapter_c",
                "display_name": "Adapter C",
                "platform": "macos",
                "signal_types": ["mention"],
                "description": "",
                "available": False,
            }
        ],
    }


def test_coverage_command(capsys):
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.coverage_report.return_value = _fake_coverage_report()
        rc = main(["coverage"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "adapter coverage" in out
    assert "adapter_a" in out
    assert "adapter_b" in out
    assert "adapter_c" in out


def test_list_adapters_json_output(capsys):
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.coverage_report.return_value = _fake_coverage_report()
        rc = main(["list-adapters"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert isinstance(data, list)
    assert any(a["name"] == "adapter_a" for a in data)
    assert any(a["name"] == "adapter_c" and a["available"] is False for a in data)


def test_extract_command_success(capsys):
    report = RunReport(
        persons_indexed=5,
        persons_extracted=4,
        sources_used=["adapter_a", "adapter_b"],
        sources_skipped=["adapter_c"],
        per_source_persons={"adapter_a": 3, "adapter_b": 4},
        duration_seconds=1.23,
        errors=[],
        dry_run=True,
    )
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.run.return_value = report
        rc = main(["extract", "--limit", "5", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Extraction complete" in out
    assert "5" in out
    assert "adapter_a" in out


def test_extract_command_passes_args_to_run():
    report = RunReport(persons_indexed=0, persons_extracted=0)
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.run.return_value = report
        main(
            [
                "extract",
                "--limit",
                "10",
                "--adapters",
                "a,b",
                "--dry-run",
                "--person",
                "p_xyz",
            ]
        )
        Extractor.return_value.run.assert_called_once_with(
            person_ids=["p_xyz"],
            limit=10,
            adapter_names=["a", "b"],
            dry_run=True,
        )


def test_extract_command_returns_error_when_all_failed(capsys):
    report = RunReport(
        persons_indexed=1,
        persons_extracted=0,
        sources_used=[],
        sources_skipped=[],
        errors=[{"adapter": "adapter_a", "error": "boom"}],
        dry_run=False,
    )
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.run.return_value = report
        rc = main(["extract"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Errors" in out
    assert "boom" in out


def test_extract_json_flag_emits_json(capsys):
    report = RunReport(persons_indexed=0, persons_extracted=0)
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.run.return_value = report
        main(["extract", "--json"])
    out = capsys.readouterr().out
    # The JSON block is emitted as a pretty-printed multiline object at the
    # end. Find the first line that is exactly "{" and parse from there.
    lines = out.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "{":
            start = i
            break
    assert start is not None, f"No JSON block found in output:\n{out}"
    json_text = "\n".join(lines[start:])
    parsed = json.loads(json_text)
    assert "persons_indexed" in parsed


def test_stats_command(capsys):
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.stats.return_value = {
            "total_rows": 42,
            "distinct_persons": 15,
            "by_source": {"adapter_a": 30, "adapter_b": 12},
        }
        rc = main(["stats"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Signal store" in out
    assert "42" in out
    assert "adapter_a" in out


def test_stats_command_empty(capsys):
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.stats.return_value = {
            "total_rows": 0,
            "distinct_persons": 0,
            "by_source": {},
        }
        rc = main(["stats"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no signals stored" in out


def test_show_command_success(capsys):
    ps = PersonSignals(
        person_id="p_test",
        person_name="Fake Person",
        source_coverage=["adapter_a"],
    )
    ps.communication.append(
        CommunicationSignal(source="adapter_a", channel="test", total_messages=10)
    )
    ps.voice.append(VoiceSignal(source="adapter_a", total_calls=3))

    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.get_person_signals.return_value = ps
        rc = main(["show", "p_test"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "p_test" in out
    assert "Fake Person" in out
    assert "Messages:" in out


def test_show_command_not_found(capsys):
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.get_person_signals.return_value = None
        rc = main(["show", "p_missing"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "No signals" in err


def test_show_json_flag_prints_full_signals(capsys):
    ps = PersonSignals(person_id="p_test", person_name="Fake")
    ps.communication.append(
        CommunicationSignal(source="a", channel="test", total_messages=1)
    )
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.return_value.get_person_signals.return_value = ps
        main(["show", "p_test", "--json"])
    out = capsys.readouterr().out
    # Should contain a JSON object
    assert "{" in out
    assert "person_id" in out


def test_main_handles_unknown_command():
    with pytest.raises(SystemExit):
        main(["bogus"])


def test_main_unhandled_exception_returns_2(capsys):
    with patch("core.engine.people.intel.cli.SignalExtractor") as Extractor:
        Extractor.side_effect = RuntimeError("simulated")
        rc = main(["coverage"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Error" in err


# ── Phase 4 command tests ────────────────────────────────────────────


from core.engine.people.intel.profiler import PersonProfile
from core.engine.people.intel.runner import ClassifyRunReport
from core.engine.people.intel.taxonomy import ClassificationResult, Tier


def _fake_profile(person_id: str = "p_test") -> PersonProfile:
    return PersonProfile(
        person_id=person_id,
        person_name="Fake Person",
        source_coverage=["test"],
        total_messages=100,
        total_calls=5,
        channels_active=["imessage", "phone"],
        channel_count=2,
        is_multi_channel=False,
        days_since_last=15,
        density_score=0.45,
        density_rank="medium",
        dominant_pattern="consistent",
    )


def test_profile_command_success(capsys):
    with patch("core.engine.people.intel.profiler.ProfileBuilder") as Builder:
        Builder.return_value.build.return_value = _fake_profile()
        rc = main(["profile", "p_test"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Profile for p_test" in out
    assert "Fake Person" in out
    assert "100" in out
    assert "consistent" in out


def test_profile_command_not_found(capsys):
    with patch("core.engine.people.intel.profiler.ProfileBuilder") as Builder:
        Builder.return_value.build.return_value = None
        rc = main(["profile", "p_missing"])
    assert rc == 1


def test_profile_command_json_flag(capsys):
    with patch("core.engine.people.intel.profiler.ProfileBuilder") as Builder:
        Builder.return_value.build.return_value = _fake_profile()
        main(["profile", "p_test", "--json"])
    out = capsys.readouterr().out
    # Must contain a JSON block at the end
    json_start = None
    for i, line in enumerate(out.splitlines()):
        if line.strip() == "{":
            json_start = i
            break
    assert json_start is not None


def test_classify_command_rule_only(capsys):
    report = ClassifyRunReport(
        persons_profiled=10,
        rule_classifications=10,
        llm_classifications=0,
        persisted=10,
        tier_distribution={"active": 5, "core": 3, "dormant": 2},
        dry_run=False,
        with_llm=False,
    )
    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.run = MagicMock()
        # asyncio.run handles the coroutine — we return a completed value

        async def fake_run(**kwargs):
            return report

        mock_runner.run = fake_run
        get_runner.return_value = mock_runner
        rc = main(["classify", "--limit", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Classification complete" in out
    assert "active" in out
    assert "core" in out


def test_classify_command_passes_with_llm_and_budget(capsys):
    report = ClassifyRunReport(
        persons_profiled=3,
        rule_classifications=3,
        llm_classifications=3,
        persisted=3,
        tier_distribution={"active": 3},
        with_llm=True,
        budget_usd=0.5,
        estimated_cost_usd=0.003,
    )

    captured_kwargs = {}

    async def fake_run(**kwargs):
        captured_kwargs.update(kwargs)
        return report

    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.run = fake_run
        get_runner.return_value = mock_runner
        rc = main(
            [
                "classify",
                "--with-llm",
                "--budget",
                "0.5",
                "--model",
                "sonnet",
                "--limit",
                "3",
            ]
        )
    assert rc == 0
    assert captured_kwargs["with_llm"] is True
    assert captured_kwargs["max_budget_usd"] == 0.5
    assert captured_kwargs["llm_model"] == "sonnet"
    assert captured_kwargs["limit"] == 3


def test_classify_command_aborted_returns_1(capsys):
    report = ClassifyRunReport(
        persons_profiled=5,
        aborted_reason="budget exceeded",
        with_llm=True,
        budget_usd=0.001,
    )

    async def fake_run(**kwargs):
        return report

    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.run = fake_run
        get_runner.return_value = mock_runner
        rc = main(["classify", "--with-llm", "--budget", "0.001"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "ABORTED" in out or "budget exceeded" in out


def test_tiers_command_empty(capsys):
    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.tier_distribution.return_value = {}
        get_runner.return_value = mock_runner
        rc = main(["tiers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no classifications" in out.lower()


def test_tiers_command_shows_distribution(capsys):
    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.tier_distribution.return_value = {
            "active": 20,
            "core": 5,
            "dormant": 15,
        }
        get_runner.return_value = mock_runner
        rc = main(["tiers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Total classifications: 40" in out
    assert "active" in out
    assert "core" in out
    assert "20" in out


def test_correct_command_requires_something(capsys):
    # Neither --tier nor --tags → error
    rc = main(["correct", "p_test"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "tier" in err.lower() or "tags" in err.lower()


def test_correct_command_bad_tier_returns_2(capsys):
    rc = main(["correct", "p_test", "--tier", "not_a_real_tier"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Unknown tier" in err


def test_correct_command_with_tier(capsys):
    result = ClassificationResult(
        person_id="p_test",
        tier=Tier.CORE,
        context_tags=[{"tag": "family_nuclear", "confidence": 1.0}],
    )
    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.record_correction.return_value = result
        get_runner.return_value = mock_runner
        rc = main(
            [
                "correct",
                "p_test",
                "--tier",
                "core",
                "--tags",
                "family_nuclear",
                "--notes",
                "my brother",
            ]
        )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Correction recorded" in out
    assert "core" in out
    assert "family_nuclear" in out


def test_classification_command_success(capsys):
    result = ClassificationResult(
        person_id="p_test",
        tier=Tier.ACTIVE,
        context_tags=[{"tag": "friend", "confidence": 0.8}],
        reasoning="test reason",
        model="test-model",
    )
    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.get_classification.return_value = result
        get_runner.return_value = mock_runner
        rc = main(["classification", "p_test"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Classification for p_test" in out
    assert "active" in out
    assert "friend" in out
    assert "test reason" in out


def test_classification_command_not_found(capsys):
    with patch("core.engine.people.intel.cli._get_runner") as get_runner:
        mock_runner = MagicMock()
        mock_runner.get_classification.return_value = None
        get_runner.return_value = mock_runner
        rc = main(["classification", "p_missing"])
    assert rc == 1
