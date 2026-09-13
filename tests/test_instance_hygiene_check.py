"""InstanceHygieneCheck (aos#2346): an artifact is an orphan only if it
carries an AOS label prefix (com.aos.*, com.agent.*) AND no
manifest/preserved-services entry claims it.

`aos hygiene --apply` was offering to delete the operator's own live
LaunchAgents — foreign business tooling (nearpay/oaks/sales-coach on
Faisal's Mini) and personal portal agents (am.hish.* here) — because
"not shipped by the framework" and "abandoned artifact" were collapsed
into one category. A LaunchAgent outside the AOS namespace is not ours
to have an opinion about, whatever the framework does or doesn't
declare; it must never even be listed, dismissed, or reasoned about.

Pins: a foreign plist (com.example.foo) is never an orphan, under any
category, regardless of declarations. A com.aos.* (or com.agent.*)
label with no declaration anywhere IS an orphan — the invariant this
check exists to enforce still holds. config/preserved-services.yaml
still protects a declared label (0.7.7 moved com.aos.envoy protection
there).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core/infra/reconcile"))
sys.path.insert(0, str(REPO / "core/infra/reconcile/checks"))

import instance_hygiene
from instance_hygiene import find_orphans


def _fake_home(monkeypatch, tmp_path):
    """An empty fake HOME with just enough structure that every category
    except LaunchAgents finds nothing, so tests only exercise the
    LaunchAgent classification this issue is about."""
    (tmp_path / "aos" / "core" / "services").mkdir(parents=True)
    (tmp_path / "aos" / "config").mkdir(parents=True)
    (tmp_path / ".aos" / "config").mkdir(parents=True)
    (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(instance_hygiene, "AOS", tmp_path / "aos")
    monkeypatch.setattr(instance_hygiene, "USER", tmp_path / ".aos")
    monkeypatch.setattr(instance_hygiene, "CLAUDE", tmp_path / ".claude")
    monkeypatch.setattr(
        instance_hygiene, "PRESERVED_SERVICES_FILE",
        tmp_path / "aos" / "config" / "preserved-services.yaml",
    )
    monkeypatch.setattr(
        instance_hygiene, "HYGIENE_STATE_FILE",
        tmp_path / ".aos" / "config" / "hygiene-known.yaml",
    )

    # Deterministic regardless of what's actually loaded/registered on the
    # machine running this test.
    monkeypatch.setattr(instance_hygiene, "_loaded_labels", lambda: set())
    monkeypatch.setattr(instance_hygiene, "_registry_service_names", lambda: set())
    monkeypatch.setattr(instance_hygiene, "_registry_labels", lambda: set())

    return tmp_path


def _install_plist(tmp_path, label):
    (tmp_path / "Library" / "LaunchAgents" / f"{label}.plist").write_text("<plist/>")


def _all_orphan_labels(orphans):
    return {label for items in orphans.values() for _, label, _ in items}


def _launchagent_orphan_labels(orphans):
    return {label for _, label, _ in orphans.get("launchagents", [])}


def test_foreign_plist_never_an_orphan(monkeypatch, tmp_path):
    _fake_home(monkeypatch, tmp_path)
    _install_plist(tmp_path, "com.example.foo")

    orphans = find_orphans()
    assert "com.example.foo" not in _launchagent_orphan_labels(orphans)
    # Not merely excluded from the launchagents bucket — never listed
    # under any category at all.
    assert "com.example.foo" not in _all_orphan_labels(orphans)


def test_undeclared_aos_plist_is_an_orphan(monkeypatch, tmp_path):
    _fake_home(monkeypatch, tmp_path)
    _install_plist(tmp_path, "com.aos.retired-thing")

    orphans = find_orphans()
    assert "com.aos.retired-thing" in _launchagent_orphan_labels(orphans)


def test_undeclared_agent_prefixed_plist_is_an_orphan(monkeypatch, tmp_path):
    """com.agent.* is the other AOS namespace (aos#2346's proposed fix
    names both prefixes) — a genuinely orphaned one must still be
    caught, not silently excluded the way com.example.foo is."""
    _fake_home(monkeypatch, tmp_path)
    _install_plist(tmp_path, "com.agent.retired-thing")

    orphans = find_orphans()
    assert "com.agent.retired-thing" in _launchagent_orphan_labels(orphans)


def test_preserved_service_is_not_an_orphan(monkeypatch, tmp_path):
    _fake_home(monkeypatch, tmp_path)
    _install_plist(tmp_path, "com.aos.envoy")
    (tmp_path / "aos" / "config" / "preserved-services.yaml").write_text(
        "services: {}\n"
        "launchagents:\n"
        "  com.aos.envoy:\n"
        "    reason: test fixture\n"
    )

    orphans = find_orphans()
    assert "com.aos.envoy" not in _launchagent_orphan_labels(orphans)


def test_loaded_aos_plist_is_not_an_orphan(monkeypatch, tmp_path):
    """Independent of the prefix gate: a loaded job is never an orphan,
    whatever the declarations say (aos#2351, still honoured)."""
    _fake_home(monkeypatch, tmp_path)
    _install_plist(tmp_path, "com.aos.morning-briefing")
    monkeypatch.setattr(instance_hygiene, "_loaded_labels", lambda: {"com.aos.morning-briefing"})

    orphans = find_orphans()
    assert "com.aos.morning-briefing" not in _launchagent_orphan_labels(orphans)
