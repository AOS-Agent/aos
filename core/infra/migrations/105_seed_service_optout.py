"""
Migration 105: Seed ~/.aos/config/services.yaml from existing launchctl state.

Reconcile now reads an operator opt-out from ~/.aos/config/services.yaml and
reports a disabled service as DISABLED instead of restarting it. That file is
new, so on every existing machine it starts empty — and an empty opt-out means
"nothing is disabled", which would hand reconcile a mandate to start services
the operator had already deliberately switched off.

Before this file existed, the only way to turn a service off was
`launchctl disable`. That state is still on disk and is a real, considered
operator decision. This migration reads it and writes it into the new config so
the declaration survives the mechanism change.

Without this, the first reconcile after the upgrade would silently re-enable
every hand-disabled service — the exact override the opt-out was built to stop,
delivered by the change that added it.

Seeds the file with an explanatory header even when nothing is disabled, so the
mechanism is discoverable rather than something an operator has to already know
about to find.

Merges, never clobbers: an operator who already hand-wrote the file keeps every
entry in it; launchctl-derived names are added alongside.

Idempotent: check() passes once every launchctl-disabled AOS service appears in
the config, so a re-run is a no-op.
"""

DESCRIPTION = "Seed operator service opt-out config from existing launchctl-disabled state"

import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
CONFIG_PATH = HOME / ".aos" / "config" / "services.yaml"

_HEADER = """\
# Operator service preferences for THIS machine.
#
# Services listed under `disabled:` are switched off by your choice. Reconcile
# reports them as DISABLED and will not restart them; nothing in AOS will start
# them behind your back until you remove them from this list.
#
# Instance data — never committed, never shared between machines.
#
# disabled:
#   - transcriber
"""


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _yaml():
    """PyYAML, or None if unavailable (migrations run before venv rebuilds)."""
    try:
        import yaml
        return yaml
    except Exception:  # noqa: BLE001
        return None


def _registry():
    """The service registry module, or None if it can't be loaded."""
    lib = AOS_ROOT / "core" / "infra" / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import service_registry
        return service_registry
    except Exception:  # noqa: BLE001
        return None


def _launchctl_disabled_labels() -> set[str]:
    """AOS launchd labels the operator has disabled.

    `launchctl print-disabled gui/<uid>` renders the value as `disabled` on
    some macOS versions and `true` on others, so match either.
    """
    try:
        result = _run(["launchctl", "print-disabled", f"gui/{os.getuid()}"], timeout=5)
    except subprocess.TimeoutExpired:
        return set()
    if result.returncode != 0:
        return set()

    labels = set()
    for line in result.stdout.splitlines():
        m = re.search(r'"(com\.(?:aos|agent)\.[^"]+)"\s*=>\s*(\w+)', line)
        if m and m.group(2).lower() in ("disabled", "true"):
            labels.add(m.group(1))
    return labels


def _disabled_service_names() -> set[str]:
    """Disabled labels mapped to registry service names.

    A label with no manifest is skipped rather than guessed at — writing a name
    the registry doesn't know would put an entry in the config that can never
    match a service, which reads as a silent, permanent opt-out of nothing.
    """
    reg_mod = _registry()
    if reg_mod is None:
        return set()
    try:
        reg = reg_mod.load_registry()
    except Exception:  # noqa: BLE001
        return set()

    names = set()
    for label in _launchctl_disabled_labels():
        m = reg.by_label(label)
        if m is not None:
            names.add(m.name)
    return names


def _existing_disabled() -> set[str]:
    yaml = _yaml()
    if yaml is None or not CONFIG_PATH.exists():
        return set()
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text())
    except Exception:  # noqa: BLE001
        return set()
    if not isinstance(raw, dict):
        return set()
    names = raw.get("disabled")
    if not isinstance(names, list):
        return set()
    return {str(n).strip() for n in names if str(n).strip()}


def check() -> bool:
    """Applied once every launchctl-disabled service is recorded in the config."""
    if _yaml() is None:
        # Can't read or write the config — nothing to assert, don't block.
        return True
    return _disabled_service_names() <= _existing_disabled()


def up() -> bool:
    yaml = _yaml()
    if yaml is None:
        print("  ⚠ PyYAML unavailable — skipping opt-out seed (reconcile defaults to nothing disabled)")
        return True

    from_launchctl = _disabled_service_names()
    existing = _existing_disabled()
    merged = sorted(existing | from_launchctl)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if merged:
        body = "disabled:\n" + "".join(f"  - {n}\n" for n in merged)
    else:
        body = "disabled: []\n"

    CONFIG_PATH.write_text(_HEADER + "\n" + body)

    added = sorted(from_launchctl - existing)
    if added:
        print(f"  ✓ Carried over launchctl-disabled service(s): {', '.join(added)}")
    else:
        print("  ✓ No launchctl-disabled AOS services to carry over")
    print(f"  ✓ Wrote {CONFIG_PATH}")
    return True
