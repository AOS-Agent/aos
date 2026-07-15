"""
Role-aware CLAUDE.md managed blocks (aos#169).

Friends' machines run AOS as managed software — they have no ~/project/aos dev
workspace. The shipped AOS:MANAGED rules block used to lecture EVERY machine to
"NEVER edit ~/aos, use the dev workspace". These tests lock in the role split:

  - developer machines keep the dev-workspace rules
  - operator machines get "fix your instance directly, report bugs upstream"
  - the role resolves from operator.yaml, falling back to dev-workspace presence
  - a role flip re-syncs the managed block (content-drift path)
  - migration 081 stamps the role into operator.yaml, idempotently
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
RECONCILE = REPO_ROOT / "core" / "infra" / "reconcile"
CHECK_PATH = RECONCILE / "checks" / "claude_md.py"
MIGRATION_PATH = REPO_ROOT / "core" / "infra" / "migrations" / "081_role_flag.py"


def _load(name: str, path: Path):
    # claude_md.py does `from base import ...` — put reconcile/ on the path.
    for p in (str(RECONCILE), str(RECONCILE / "checks")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cm():
    return _load("claude_md_under_test", CHECK_PATH)


@pytest.fixture
def mig():
    return _load("role_migration_under_test", MIGRATION_PATH)


# ── Role-aware section content ───────────────────────────────────────────────


def test_developer_rules_keep_dev_workspace(cm):
    rules = cm._global_sections("developer")["rules"][1]
    assert "project/aos" in rules
    assert "NEVER edit" in rules


def test_operator_rules_drop_dev_workspace_lecture(cm):
    rules = cm._global_sections("operator")["rules"][1]
    # The operator never hears about a dev workspace they don't have.
    assert "project/aos" not in rules
    assert "NEVER edit" not in rules
    # They're told the actual model: managed software + report upstream.
    assert "managed software" in rules
    assert "report skill" in rules


def test_both_roles_carry_the_cloudflare_network_policy(cm):
    # Operator-approved 2026-07-15: Cloudflare Tunnel is a wizard-gated opt-in,
    # and the line must survive managed-block syncs on both machine types.
    for role in ("developer", "operator"):
        rules = cm._global_sections(role)["rules"][1]
        assert "Cloudflare Tunnel permitted ONLY as explicit operator opt-in" in rules
        assert "remote-access wizard" in rules


def test_both_roles_share_the_rules_version(cm):
    dev_v = cm._global_sections("developer")["rules"][0]
    op_v = cm._global_sections("operator")["rules"][0]
    assert dev_v == op_v
    assert dev_v >= 3  # bumped from 2 when the block became role-aware


def test_only_the_rules_block_differs_by_role(cm):
    dev = cm._global_sections("developer")
    op = cm._global_sections("operator")
    assert dev.keys() == op.keys()
    for name in dev:
        if name == "rules":
            assert dev[name] != op[name]
        else:
            assert dev[name] == op[name]


# ── Role resolution ──────────────────────────────────────────────────────────


def test_resolve_role_reads_operator_yaml(cm, tmp_path, monkeypatch):
    cfg = tmp_path / ".aos" / "config"
    cfg.mkdir(parents=True)
    (cfg / "operator.yaml").write_text("name: Friend\nrole: operator\n")
    monkeypatch.setattr(cm.Path, "home", classmethod(lambda cls: tmp_path))
    assert cm._resolve_role() == "operator"


def test_resolve_role_infers_operator_without_dev_workspace(cm, tmp_path, monkeypatch):
    (tmp_path / ".aos" / "config").mkdir(parents=True)
    (tmp_path / ".aos" / "config" / "operator.yaml").write_text("name: Friend\n")
    monkeypatch.setattr(cm.Path, "home", classmethod(lambda cls: tmp_path))
    assert cm._resolve_role() == "operator"


def test_resolve_role_infers_developer_with_dev_workspace(cm, tmp_path, monkeypatch):
    (tmp_path / ".aos" / "config").mkdir(parents=True)
    (tmp_path / ".aos" / "config" / "operator.yaml").write_text("name: Dev\n")
    (tmp_path / "project" / "aos").mkdir(parents=True)
    monkeypatch.setattr(cm.Path, "home", classmethod(lambda cls: tmp_path))
    assert cm._resolve_role() == "developer"


# ── Role flip re-syncs the managed block ─────────────────────────────────────


def test_role_flip_resyncs_managed_block(cm, tmp_path):
    target = tmp_path / "CLAUDE.md"

    # Start as a developer machine.
    dev_sections = cm._global_sections("developer")
    cm._fix_sections(target, dev_sections, cm.GLOBAL_HEADER)
    assert cm._check_sections(target, dev_sections)
    assert "NEVER edit" in target.read_text()

    # Same machine re-classified as operator: check() must want a re-sync...
    op_sections = cm._global_sections("operator")
    assert not cm._check_sections(target, op_sections)
    # ...and fix() must swap the block, not duplicate it.
    cm._fix_sections(target, op_sections, cm.GLOBAL_HEADER)
    text = target.read_text()
    assert "NEVER edit" not in text
    assert "managed software" in text
    assert text.count("<!-- AOS:MANAGED name=\"rules\"") == 1
    assert cm._check_sections(target, op_sections)


# ── Migration 081 ────────────────────────────────────────────────────────────


def test_migration_stamps_operator_when_no_dev_workspace(mig, tmp_path, monkeypatch):
    oper = tmp_path / "operator.yaml"
    oper.write_text("name: Friend\ntimezone: UTC\n")
    monkeypatch.setattr(mig, "OPERATOR_YAML", oper)
    monkeypatch.setattr(mig, "DEV_WORKSPACE", tmp_path / "project" / "aos")

    assert mig.check() is False
    assert mig.up() is True
    data = yaml.safe_load(oper.read_text())
    assert data["role"] == "operator"
    assert data["name"] == "Friend"  # existing content preserved
    assert mig.check() is True


def test_migration_stamps_developer_with_dev_workspace(mig, tmp_path, monkeypatch):
    oper = tmp_path / "operator.yaml"
    oper.write_text("name: Dev\n")
    dev_ws = tmp_path / "project" / "aos"
    dev_ws.mkdir(parents=True)
    monkeypatch.setattr(mig, "OPERATOR_YAML", oper)
    monkeypatch.setattr(mig, "DEV_WORKSPACE", dev_ws)

    assert mig.up() is True
    assert yaml.safe_load(oper.read_text())["role"] == "developer"


def test_migration_never_overwrites_existing_role(mig, tmp_path, monkeypatch):
    oper = tmp_path / "operator.yaml"
    oper.write_text("name: Dev\nrole: developer\n")
    monkeypatch.setattr(mig, "OPERATOR_YAML", oper)
    monkeypatch.setattr(mig, "DEV_WORKSPACE", tmp_path / "nope")  # would infer operator

    assert mig.check() is True
    assert mig.up() is True
    assert yaml.safe_load(oper.read_text())["role"] == "developer"  # untouched
