#!/usr/bin/env python3
"""
Default-off services: the declaration, and the one way to write it.

v0.8.0 moves four services from "running unless you stopped it" to "stopped
unless you asked for it": the work runner (zero recorded runs, ever) and the
three autonomous-comms arms — sentinel, converse, envoy. Autonomous outbound
communication becomes a future Qren arm; it does not ship on by default in the
system's final release.

Two lists, one file (~/.aos/config/services.yaml), and a clear precedence:

    enabled:   the operator's explicit opt-in. NOTHING here is ever
               auto-disabled — not by a migration, not by reconcile.
    disabled:  the opt-out reconcile already reads (migration 105).

`enabled:` wins. That asymmetry is the point: a default the framework picks
must never overwrite a choice the operator made, but the framework is allowed
to pick a default for a machine that never expressed one. Without the opt-in
list, "off by default" and "off by decree" are the same code path, and the
operator who wants Sentinel gets it switched off under them on every update.

Every writer goes through `disable_service()` so the merge semantics live in
exactly one place: read, add, sort, write, never clobber a hand-written file.
PyYAML is optional — migrations can run before a venv rebuild — and the
fallback writer is line-based so a missing wheel degrades to "does nothing"
rather than "corrupts the operator's config".
"""

from __future__ import annotations

from pathlib import Path


# Resolved on every call, never captured at import.
#
# A module-level `Path.home()` is frozen at the moment of first import, and
# this module is imported by migrations, by a reconcile check, and by tests
# that redirect HOME to a sandbox. Whichever caller imported it first would
# decide, for the whole process, which machine's config every later caller
# wrote to — and in a test run that means writing to the operator's real
# services.yaml.
def _config_path() -> Path:
    return Path.home() / ".aos" / "config" / "services.yaml"


class _ConfigPath:
    """`SERVICES_CONFIG` as a live value, for callers that print or stat it."""

    def __truediv__(self, other):
        return _config_path() / other

    def __getattr__(self, name):
        return getattr(_config_path(), name)

    def __fspath__(self):
        return str(_config_path())

    def __str__(self):
        return str(_config_path())

    def __repr__(self):
        return repr(_config_path())

    def __eq__(self, other):
        return _config_path() == other


SERVICES_CONFIG = _ConfigPath()

# The v0.8.0 default-off set. work-runner: 0 rows in task_runs, ever.
# sentinel/converse/envoy: autonomous comms, deferred to Qren.
DEFAULT_OFF = ("work-runner", "sentinel", "converse", "envoy")

_HEADER = """\
# Operator service preferences for THIS machine.
#
# disabled:  services switched off. Reconcile reports them DISABLED and will
#            not restart them.
# enabled:   services you explicitly want ON. Nothing in AOS ever auto-disables
#            a name in this list — it outranks any framework default.
#
# As of v0.8.0 these ship off by default: work-runner, sentinel, converse,
# envoy. To run one anyway, add it under `enabled:` and remove it from
# `disabled:`.
#
# Instance data — never committed, never shared between machines.
"""


def _yaml():
    try:
        import yaml
        return yaml
    except Exception:  # noqa: BLE001
        return None


def _read() -> dict:
    """The config as a dict. Unreadable / malformed / absent → {}."""
    yaml = _yaml()
    if yaml is None or not _config_path().exists():
        return {}
    try:
        raw = yaml.safe_load(_config_path().read_text())
    except Exception:  # noqa: BLE001
        return {}
    return raw if isinstance(raw, dict) else {}


def _names(key: str) -> set[str]:
    values = _read().get(key)
    if not isinstance(values, list):
        return set()
    return {str(v).strip() for v in values if str(v).strip()}


def enabled_services() -> set[str]:
    """Names the operator explicitly opted in to. Never auto-disabled."""
    return _names("enabled")


def disabled_services() -> set[str]:
    """Names recorded as off (the list reconcile reads)."""
    return _names("disabled")


def is_opted_in(name: str) -> bool:
    return name in enabled_services()


def is_recorded_off(name: str) -> bool:
    return name in disabled_services()


def needs_disabling(names=DEFAULT_OFF) -> list[str]:
    """Which of `names` are neither opted in nor already recorded off."""
    opted_in = enabled_services()
    off = disabled_services()
    return [n for n in names if n not in opted_in and n not in off]


def disable_service(name: str) -> bool:
    """Record `name` under `disabled:`. Returns True if it was added.

    Never touches `enabled:` — an opted-in service is left alone and reported
    as "not added", so callers can say so rather than silently doing nothing.
    Merges into whatever the operator already wrote; other keys survive.
    """
    if is_opted_in(name) or is_recorded_off(name):
        return False

    yaml = _yaml()
    if yaml is None:
        return False

    data = _read()
    current = data.get("disabled")
    names = sorted(set(current) | {name}) if isinstance(current, list) else [name]
    data["disabled"] = names

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    path.write_text(_HEADER + "\n" + body)
    return True


def enable_service(name: str) -> bool:
    """Opt `name` in: add to `enabled:`, drop from `disabled:`. The reversal.

    This is what an operator (or `work runner enable`) calls to override the
    v0.8.0 default. Returns True if anything changed.
    """
    yaml = _yaml()
    if yaml is None:
        return False

    data = _read()
    enabled = data.get("enabled")
    enabled = set(enabled) if isinstance(enabled, list) else set()
    disabled = data.get("disabled")
    disabled = set(disabled) if isinstance(disabled, list) else set()

    if name in enabled and name not in disabled:
        return False

    enabled.add(name)
    disabled.discard(name)
    data["enabled"] = sorted(enabled)
    data["disabled"] = sorted(disabled)

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _HEADER + "\n" + yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    )
    return True
