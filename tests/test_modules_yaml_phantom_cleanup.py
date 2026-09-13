"""
config/modules.yaml phantom module cleanup (aos#236.8).

Dangling-wires audit (2026-09-13): envoy, sana-watch, and qareen-deploy were
declared as modules with no service dir, template, plist, or launchd load
anywhere — "envoy already works fine as a skill/CLI without a daemon"; the
other two are instance-only on one Mini with no framework backing.

qareen-deploy was already gone (migration 108's decommission, before this
task started) — confirmed by grep, nothing to do.

envoy and sana-watch needed care before deleting: core/infra/reconcile/
checks/instance_hygiene.py's _manifest_labels() reads config/modules.yaml
specifically to stop false "orphan" reports for services that have no
core/services/ dir. envoy's CLI (core/engine/comms/envoy/cli.py) DOES have a
real, working cmd_install_daemon that self-installs com.aos.envoy — so
removing modules.yaml's declaration outright would reopen exactly the
false-orphan bug the check exists to prevent, the first time anyone actually
ran it. Moved that protection to config/preserved-services.yaml instead (the
purpose-built mechanism for "instance artifact, no framework template, but
real code depends on the label"). sana-watch has zero framework code
anywhere (confirmed by grep) — no protection needed or added.

Also checked core/infra/lib/default_off.py (per instructions) before
touching anything: DEFAULT_OFF and the opt-out enforcement path never read
modules.yaml at all, so there was no `status_note`-shaped key to preserve
for that mechanism specifically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
MODULES_YAML = REPO_ROOT / "config" / "modules.yaml"
PRESERVED_SERVICES_YAML = REPO_ROOT / "config" / "preserved-services.yaml"

sys.path.insert(0, str(REPO_ROOT / "core" / "infra" / "reconcile" / "checks"))
sys.path.insert(0, str(REPO_ROOT / "core" / "infra" / "reconcile"))
sys.path.insert(0, str(REPO_ROOT / "core" / "infra"))

import instance_hygiene  # noqa: E402


def _module_ids() -> set[str]:
    data = yaml.safe_load(MODULES_YAML.read_text())
    return {m["id"] for m in (data.get("modules") or [])}


def test_envoy_sana_watch_qareen_deploy_are_not_declared_modules():
    ids = _module_ids()
    assert "envoy" not in ids
    assert "sana-watch" not in ids
    assert "qareen-deploy" not in ids  # was already gone; confirms it stays gone


def test_ios_deploy_module_entry_is_untouched():
    """Same phantom shape as sana-watch (instance-only, not installable),
    but NOT in this task's delete list — must survive unchanged."""
    ids = _module_ids()
    assert "ios-deploy" in ids


def test_no_remaining_live_declaration_of_envoy_or_sana_watch_in_modules_yaml():
    """The removal note is allowed to mention the retired labels in prose
    (same as migration docstrings referencing decommissioned services for
    history) — what must be gone is a live YAML declaration: a
    `services: [...]` list entry actually naming them."""
    data = yaml.safe_load(MODULES_YAML.read_text())
    declared_services = {
        s
        for m in (data.get("modules") or [])
        for s in (m.get("services") or [])
    }
    assert "com.aos.envoy" not in declared_services
    assert "com.aos.sana-watch" not in declared_services


def _real_instance_hygiene():
    """instance_hygiene.py's module-level AOS/PRESERVED_SERVICES_FILE constants
    are Path.home()-derived and would resolve to whatever ~/aos happens to be
    on the machine running the test (a stale release, in a dev worktree) —
    redirect them at this repo so the check reads OUR modules.yaml and
    preserved-services.yaml, read-only."""
    instance_hygiene.AOS = REPO_ROOT
    instance_hygiene.PRESERVED_SERVICES_FILE = PRESERVED_SERVICES_YAML
    return instance_hygiene


def test_envoy_launchagent_label_is_still_framework_known_via_preserved_services():
    """The false-orphan protection instance_hygiene.py's _manifest_labels()
    docstring warns about must not regress for envoy specifically — its
    daemon-install code path (cli.py's cmd_install_daemon) is real."""
    ih = _real_instance_hygiene()
    assert "com.aos.envoy" not in ih._manifest_labels()  # no longer via modules.yaml
    assert "com.aos.envoy" in ih._load_preserved()["launchagents"]  # via preserved-services.yaml instead
    assert "com.aos.envoy" in ih._framework_launchagents()  # covered either way


def test_sana_watch_is_not_protected_anywhere_by_design():
    """No framework code anywhere creates com.aos.sana-watch — it should not
    be in modules.yaml, preserved-services.yaml, or the combined framework
    launchagent set. If it is ever installed on some machine, that is
    correctly an orphan report, not a framework-owned artifact."""
    ih = _real_instance_hygiene()
    assert "com.aos.sana-watch" not in ih._manifest_labels()
    assert "com.aos.sana-watch" not in ih._load_preserved()["launchagents"]
    assert "com.aos.sana-watch" not in ih._framework_launchagents()


def test_default_off_mechanism_never_reads_modules_yaml():
    """Confirms the investigation finding: the opt-out enforcement path
    (core/infra/lib/default_off.py + service_registry.is_disabled) is
    entirely independent of modules.yaml, so deleting these module entries
    cannot affect whether envoy/sana-watch are treated as disabled."""
    default_off_src = (REPO_ROOT / "core" / "infra" / "lib" / "default_off.py").read_text()
    assert "modules.yaml" not in default_off_src
    registry_src = (REPO_ROOT / "core" / "infra" / "lib" / "service_registry.py").read_text()
    assert "modules.yaml" not in registry_src
