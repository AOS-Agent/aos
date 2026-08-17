"""Operator intent — "I switched this off" — recorded in ONE place.

The arm engine originally wrote its own `~/.aos/config/app-modules.yaml`. That
was a mistake: v0.7.5 had already shipped `~/.aos/config/services.yaml` with a
`disabled:` list, read by reconcile via service_registry.disabled_services().
Two files answering one question is how drift starts — reconcile would have
honoured one and the Arms panel the other, and they would disagree the first
time an operator used the panel instead of the CLI.

So this module writes the file that already exists and already has readers.
Shape, fixed by service_registry and migration 105:

    disabled:
      - transcriber
      - n8n

Names are SERVICE names, not launchd labels and not module ids:
`com.aos.transcriber` is recorded as `transcriber`.

Rules this module keeps:
  * Merge, never clobber — an operator's hand-written entries survive.
  * Preserve the explanatory header, so the file stays self-documenting.
  * Never raise on a malformed file. Reconcile fails OPEN on a parse error by
    deliberate choice; refusing to write is better than silently discarding
    whatever the operator had in there.
"""

from __future__ import annotations

from pathlib import Path

import yaml

SERVICES_CONFIG = Path.home() / ".aos" / "config" / "services.yaml"

HEADER = """\
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


class IntentError(Exception):
    pass


def service_name(label: str) -> str:
    """`com.aos.work-runner` -> `work-runner`. The registry keys on this."""
    for prefix in ("com.aos.", "com.agent."):
        if label.startswith(prefix):
            return label[len(prefix):]
    return label


def module_service_names(module) -> list[str]:
    return [service_name(label) for label in module.services]


def read_disabled() -> set[str]:
    if not SERVICES_CONFIG.exists():
        return set()
    try:
        raw = yaml.safe_load(SERVICES_CONFIG.read_text())
    except Exception:  # noqa: BLE001 — matches reconcile's fail-open contract
        return set()
    if not isinstance(raw, dict):
        return set()
    names = raw.get("disabled")
    if not isinstance(names, list):
        return set()
    return {str(n).strip() for n in names if str(n).strip()}


def _write(disabled: set[str]) -> None:
    SERVICES_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # Keep whatever header is already there; only write ours when creating.
    existing = SERVICES_CONFIG.read_text() if SERVICES_CONFIG.exists() else ""
    header_lines = [ln for ln in existing.splitlines() if ln.startswith("#")]
    header = "\n".join(header_lines) + "\n" if header_lines else HEADER

    body = yaml.safe_dump({"disabled": sorted(disabled)}, sort_keys=True, default_flow_style=False)
    SERVICES_CONFIG.write_text(header + "\n" + body)


def set_disabled(names: list[str], disabled: bool) -> list[str]:
    """Record that these service names are off (or on). Returns the new list."""
    current = read_disabled()
    if disabled:
        current |= set(names)
    else:
        current -= set(names)
    _write(current)
    return sorted(current)
