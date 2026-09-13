"""Migration 127 — comms-extract/people-intel-refresh/loop-sensors default-off.

Same contract as the other v0.8.0 default-off migrations (111, 112): idempotent,
respects an explicit operator opt-in, never touches a service/job it wasn't
named for. This one patches ~/aos/config/crons.yaml line-based rather than a
full YAML round-trip, so the extra thing worth locking in is that a re-run
never duplicates an `enabled:` line and never disturbs a neighboring job's
block or its comments.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIG = REPO / "core" / "infra" / "migrations" / "127_comms_crons_default_off.py"

SAMPLE = """\
jobs:
  # ── Tier 1 ───────────────────────────────────────────
  watchdog:
    command: bash ~/aos/core/bin/crons/watchdog
    every: 5m
    tier: 1
    description: Checks services are running

  comms-extract:
    command: python3 ~/aos/core/engine/comms/extract/lifecycle.py
    at: "05:00"
    timeout: 300
    tier: 4
    description: Extracts insights from communications

  comms-patterns:
    command: python3 ~/aos/core/engine/comms/patterns/compute.py
    at: "05:30"
    tier: 4
    description: Computes communication patterns and trends

  people-intel-refresh:
    command: bash ~/aos/core/bin/crons/people-intel-refresh
    at: "02:00"
    timeout: 600
    tier: 4
    description: Refreshes signal_store, person_classification

  loop-sensors:
    command: python3 ~/aos/core/bin/crons/loop-sensors
    at: "03:40"
    timeout: 1800
    tier: 2
    description: Intelligence Loop nightly sensors

    catch_up: true
"""


@pytest.fixture
def m(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mig_127", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CRONS_YAML", tmp_path / "aos" / "config" / "crons.yaml")
    # Redirect the dead-config-key targets into the sandbox too — none of
    # these exist by default, matching a machine that never had them.
    accounts = tmp_path / ".aos" / "config" / "accounts.yaml"
    goals = tmp_path / ".aos" / "config" / "goals.yaml"
    state = tmp_path / ".aos" / "config" / "state.yaml"
    monkeypatch.setattr(mod, "ACCOUNTS_YAML", accounts)
    monkeypatch.setattr(mod, "GOALS_YAML", goals)
    monkeypatch.setattr(mod, "STATE_YAML", state)
    monkeypatch.setattr(mod, "DEAD_KEYS", {
        accounts: ["schema_version"],
        goals: ["recurring_responsibilities"],
        state: ["machine_user", "voice_models"],
    })
    return mod


def _write(m, text: str) -> None:
    m.CRONS_YAML.parent.mkdir(parents=True, exist_ok=True)
    m.CRONS_YAML.write_text(text)


def test_missing_file_is_already_applied(m):
    """A fresh/release install ships crons.yaml pre-set — nothing to patch."""
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_inserts_enabled_false_for_all_three_and_is_idempotent(m):
    _write(m, SAMPLE)
    assert m.check() is False

    assert m.up() is True
    assert m.check() is True

    text = m.CRONS_YAML.read_text()
    assert "comms-extract:\n    enabled: false\n" in text
    assert "people-intel-refresh:\n    enabled: false\n" in text
    assert "loop-sensors:\n    enabled: false\n" in text

    # Untouched jobs keep their exact original blocks — no stray edits leaked
    # into a neighboring job from the line-splice.
    assert "watchdog:\n    command: bash ~/aos/core/bin/crons/watchdog\n    every: 5m\n" in text
    assert "comms-patterns:\n    command: python3 ~/aos/core/engine/comms/patterns/compute.py\n" in text
    # loop-sensors keeps its trailing catch_up line, now after the inserted key.
    assert "enabled: false\n    command: python3 ~/aos/core/bin/crons/loop-sensors\n" in text
    assert "catch_up: true\n" in text

    # Second run: no duplicate `enabled:` lines, no further writes needed.
    before = m.CRONS_YAML.read_text()
    assert m.up() is True
    after = m.CRONS_YAML.read_text()
    assert before == after
    assert after.count("enabled: false") == 3


def test_already_shipped_enabled_false_is_a_noop(m):
    _write(m, SAMPLE.replace(
        "  comms-extract:\n    command:",
        "  comms-extract:\n    enabled: false\n    command:",
    ).replace(
        "  people-intel-refresh:\n    command:",
        "  people-intel-refresh:\n    enabled: false\n    command:",
    ).replace(
        "  loop-sensors:\n    command:",
        "  loop-sensors:\n    enabled: false\n    command:",
    ))
    assert m.check() is True
    before = m.CRONS_YAML.read_text()
    assert m.up() is True
    assert m.CRONS_YAML.read_text() == before


def test_explicit_operator_opt_in_is_respected_and_never_re_disabled(m):
    _write(m, SAMPLE.replace(
        "  people-intel-refresh:\n    command:",
        "  people-intel-refresh:\n    enabled: true\n    command:",
    ))
    # The other two still need patching, so check() is False until up() runs...
    assert m.check() is False
    assert m.up() is True
    text = m.CRONS_YAML.read_text()
    # ...but the opted-in job is untouched, both in state and check()'s verdict.
    assert "people-intel-refresh:\n    enabled: true\n    command:" in text
    assert m.check() is True

    # Re-running never flips the opt-in back off.
    assert m.up() is True
    assert "people-intel-refresh:\n    enabled: true\n    command:" in m.CRONS_YAML.read_text()


def test_job_missing_from_crons_yaml_is_skipped_not_blocking(m):
    """If a target job is ever renamed/removed, this migration doesn't hang."""
    text = SAMPLE.replace(
        '  loop-sensors:\n    command: python3 ~/aos/core/bin/crons/loop-sensors\n'
        '    at: "03:40"\n    timeout: 1800\n    tier: 2\n'
        '    description: Intelligence Loop nightly sensors\n\n    catch_up: true\n',
        "",
    )
    _write(m, text)
    assert "loop-sensors" not in text  # sanity on the fixture itself
    assert m.check() is False  # comms-extract / people-intel-refresh still unpatched
    assert m.up() is True
    assert m.check() is True


# ── Dead config keys (accounts.schema_version, goals.recurring_responsibilities,
#    state.machine_user/voice_models) ─────────────────────────────────────────

GOALS_SAMPLE = """\
quarterly_objectives:
  - name: "Telegram Pipeline"
    weight: 30

recurring_responsibilities:
  - "Morning briefing at 08:00"
  - "Heartbeat monitoring every 30 minutes"

boundaries:
  - "Never send external emails without confirmation"
"""

STATE_SAMPLE = """\
last_updated: '2026-03-31'
machine_user: agentalhadi
services:
  bridge:
    health: ''
    launchagent: com.aos.bridge
tailscale_ip: 100.64.0.1
voice_models:
  stt:
    whisper:
      size_gb: 1.4
"""

ACCOUNTS_SAMPLE = """\
operator:
  name: ""
discovered: []
schema_version: 1
"""


def test_no_instance_config_files_is_already_applied(m):
    """A machine that never had these files (or never had these keys) is clean."""
    assert m.check() is True
    assert m.up() is True
    assert m.check() is True


def test_strips_all_four_dead_keys_and_leaves_everything_else_intact(m):
    m.ACCOUNTS_YAML.parent.mkdir(parents=True, exist_ok=True)
    m.ACCOUNTS_YAML.write_text(ACCOUNTS_SAMPLE)
    m.GOALS_YAML.write_text(GOALS_SAMPLE)
    m.STATE_YAML.write_text(STATE_SAMPLE)

    assert m.check() is False
    assert m.up() is True
    assert m.check() is True

    accounts = m.ACCOUNTS_YAML.read_text()
    assert "schema_version" not in accounts
    assert 'operator:\n  name: ""\n' in accounts
    assert "discovered: []\n" in accounts

    goals = m.GOALS_YAML.read_text()
    assert "recurring_responsibilities" not in goals
    assert "Morning briefing" not in goals
    assert 'quarterly_objectives:\n  - name: "Telegram Pipeline"\n    weight: 30\n' in goals
    assert 'boundaries:\n  - "Never send external emails without confirmation"\n' in goals

    state = m.STATE_YAML.read_text()
    assert "machine_user" not in state
    assert "voice_models" not in state
    assert "whisper" not in state
    assert "last_updated: '2026-03-31'\n" in state
    assert "services:\n  bridge:\n    health: ''\n    launchagent: com.aos.bridge\n" in state
    assert "tailscale_ip: 100.64.0.1\n" in state

    # Idempotent: a second run makes no further changes.
    before = (accounts, goals, state)
    assert m.up() is True
    after = (
        m.ACCOUNTS_YAML.read_text(),
        m.GOALS_YAML.read_text(),
        m.STATE_YAML.read_text(),
    )
    assert before == after


def test_partial_cleanup_is_idempotent_too(m):
    """A file with only SOME dead keys already stripped (e.g. by hand) is fine."""
    m.STATE_YAML.parent.mkdir(parents=True, exist_ok=True)
    m.STATE_YAML.write_text(STATE_SAMPLE.replace("machine_user: agentalhadi\n", ""))
    assert "machine_user" not in m.STATE_YAML.read_text()

    assert m.check() is False  # voice_models still present
    assert m.up() is True
    assert m.check() is True
    state = m.STATE_YAML.read_text()
    assert "machine_user" not in state
    assert "voice_models" not in state
