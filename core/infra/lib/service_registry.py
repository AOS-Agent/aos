#!/usr/bin/env python3
"""
The AOS service registry — one declarative manifest per service, everything
derives.

Service identity used to be scattered across 6+ independently-drifting places
(watchdog hardcoded lists, state.yaml, reconcile-check port constants,
heartbeat probes, intent_classifier menus, instance_hygiene allowlists). Every
service incident in the aos#180 batch traced to that scatter: a transcriber
killed by a wrong-port health check, a bridge health-probed on an endpoint it
doesn't serve, `listen` dead for months because monitoring derived from
deployed plists instead of intent. The fix is a single source of truth: each
``core/services/<name>/service.yaml`` (plus ``core/qareen/service.yaml`` and
``config/services.d/*.yaml`` for services without a code dir) declares the
service once, and every consumer reads it from here.

    from lib.service_registry import load_registry, health_url
    reg = load_registry()
    for m in reg.active_residents():
        ...

──────────────────────────────────────────────────────────────────────────────
MANIFEST SCHEMA  (also documented in core/services/README.md)
──────────────────────────────────────────────────────────────────────────────
Required keys (every manifest):
  name          str    short service name (must match the directory name for
                       core/services manifests)
  purpose       str    one-line description of what the service does
  status        enum   active | retired | optional
                         active   — should be deployed + monitored on every node
                         optional — may be deployed (feature-gated / per-node)
                         retired  — must NOT be loaded; dir kept as an archive
  type          enum   resident | interval | oneshot
                         resident — long-running daemon, KeepAlive
                         interval — StartInterval cron tick, not resident
                         oneshot  — RunAtLoad once, no KeepAlive
  owner_layer   enum   framework | instance
                         framework — ships in ~/aos, git-tracked
                         instance  — lives in ~/.aos, per-machine
  liveness      enum   http | poll_timestamp | keepalive | interval | none
                         http           — probe health_endpoint over HTTP
                         poll_timestamp — a heartbeat file, not an HTTP endpoint
                                          (the bridge: its own poll-liveness
                                          check owns wedge detection)
                         keepalive      — launchd KeepAlive is the only signal;
                                          "loaded" is enough
                         interval       — a periodic job; "loaded" is enough
                         none           — no liveness signal (stdio, etc.)

Conditionally required:
  port            int   REQUIRED when liveness == http. May also be present
                        (informational) for other liveness strategies (e.g. the
                        bridge binds :4098 for an API but liveness is
                        poll_timestamp). Otherwise omit / null.
  health_endpoint str   REQUIRED when liveness == http (e.g. "/health",
                        "/api/health"). MUST be absent for any other liveness.
  start_interval  int   REQUIRED when type == interval (seconds).

Optional:
  plist_template  str   basename under config/launchagents/ (e.g.
                        "com.aos.bridge.plist.template"), the literal
                        "generated" for a plist produced by an installer, or
                        null for services with no framework plist.
  label           str   launchd label (defaults to "com.aos.<name>").
  keepalive       bool  whether the plist sets KeepAlive.
  depends_on      list  service names this one needs up first.

Strict validation: an unknown key is an error, a missing required key is an
error, a wrong enum value is an error. A manifest that does not validate makes
the whole registry raise — a service must declare itself correctly or not ship.

No dependencies beyond PyYAML + the standard library.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# core/infra/lib/service_registry.py → parents[3] == repo root (~/aos at runtime,
# the worktree in dev). Computed from __file__ so it is correct in both.
AOS_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = AOS_ROOT / "core" / "services"
QAREEN_MANIFEST = AOS_ROOT / "core" / "qareen" / "service.yaml"
SERVICES_D = AOS_ROOT / "config" / "services.d"

_STATUSES = {"active", "retired", "optional"}
_TYPES = {"resident", "interval", "oneshot"}
_LAYERS = {"framework", "instance"}
_LIVENESS = {"http", "poll_timestamp", "keepalive", "interval", "none"}

_REQUIRED = {"name", "purpose", "status", "type", "owner_layer", "liveness"}
_OPTIONAL = {
    "port", "health_endpoint", "start_interval",
    "plist_template", "label", "keepalive", "depends_on",
}
_ALLOWED = _REQUIRED | _OPTIONAL


class ManifestError(ValueError):
    """A service.yaml is missing, malformed, or violates the schema."""


@dataclass(frozen=True)
class ServiceManifest:
    name: str
    purpose: str
    status: str
    type: str
    owner_layer: str
    liveness: str
    label: str
    port: int | None = None
    health_endpoint: str | None = None
    start_interval: int | None = None
    plist_template: str | None = None
    keepalive: bool | None = None
    depends_on: list[str] = field(default_factory=list)
    source: str = ""  # path the manifest was loaded from (diagnostics)

    # ── Derived helpers ──────────────────────────────────────────────────────

    @property
    def health_url(self) -> str | None:
        """The HTTP health URL, or None when liveness is not http."""
        if self.liveness == "http" and self.port and self.health_endpoint:
            return f"http://127.0.0.1:{self.port}{self.health_endpoint}"
        return None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_retired(self) -> bool:
        return self.status == "retired"

    @property
    def is_resident(self) -> bool:
        return self.type == "resident"


def _validate(raw: dict, source: Path) -> ServiceManifest:
    where = source.relative_to(AOS_ROOT) if source.is_relative_to(AOS_ROOT) else source

    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: manifest is not a mapping")

    keys = set(raw)
    unknown = keys - _ALLOWED
    if unknown:
        raise ManifestError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    missing = _REQUIRED - keys
    if missing:
        raise ManifestError(f"{where}: missing required key(s): {', '.join(sorted(missing))}")

    def enum(key: str, allowed: set[str]) -> str:
        val = raw[key]
        if val not in allowed:
            raise ManifestError(
                f"{where}: {key}={val!r} not one of {', '.join(sorted(allowed))}"
            )
        return val

    status = enum("status", _STATUSES)
    stype = enum("type", _TYPES)
    layer = enum("owner_layer", _LAYERS)
    liveness = enum("liveness", _LIVENESS)

    name = raw["name"]
    if not isinstance(name, str) or not name:
        raise ManifestError(f"{where}: name must be a non-empty string")
    purpose = raw["purpose"]
    if not isinstance(purpose, str) or not purpose:
        raise ManifestError(f"{where}: purpose must be a non-empty string")

    port = raw.get("port")
    if port is not None and not isinstance(port, int):
        raise ManifestError(f"{where}: port must be an integer or null")
    health_endpoint = raw.get("health_endpoint")
    if health_endpoint is not None and not isinstance(health_endpoint, str):
        raise ManifestError(f"{where}: health_endpoint must be a string or null")
    start_interval = raw.get("start_interval")
    if start_interval is not None and not isinstance(start_interval, int):
        raise ManifestError(f"{where}: start_interval must be an integer or null")

    # ── Cross-field rules ────────────────────────────────────────────────────
    if liveness == "http":
        if port is None:
            raise ManifestError(f"{where}: liveness=http requires a port")
        if not health_endpoint:
            raise ManifestError(f"{where}: liveness=http requires a health_endpoint")
    else:
        if health_endpoint:
            raise ManifestError(
                f"{where}: health_endpoint is only valid with liveness=http "
                f"(this manifest has liveness={liveness})"
            )
    if stype == "interval" and liveness not in {"interval", "none"}:
        raise ManifestError(
            f"{where}: type=interval requires liveness interval|none (got {liveness})"
        )
    if liveness == "interval" and stype != "interval":
        raise ManifestError(f"{where}: liveness=interval requires type=interval")
    if stype == "interval" and start_interval is None:
        raise ManifestError(f"{where}: type=interval requires start_interval")

    keepalive = raw.get("keepalive")
    if keepalive is not None and not isinstance(keepalive, bool):
        raise ManifestError(f"{where}: keepalive must be a boolean or null")
    plist_template = raw.get("plist_template")
    if plist_template is not None and not isinstance(plist_template, str):
        raise ManifestError(f"{where}: plist_template must be a string or null")
    depends_on = raw.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
        raise ManifestError(f"{where}: depends_on must be a list of strings")

    label = raw.get("label") or f"com.aos.{name}"
    if not isinstance(label, str):
        raise ManifestError(f"{where}: label must be a string")

    return ServiceManifest(
        name=name,
        purpose=purpose,
        status=status,
        type=stype,
        owner_layer=layer,
        liveness=liveness,
        label=label,
        port=port,
        health_endpoint=health_endpoint,
        start_interval=start_interval,
        plist_template=plist_template,
        keepalive=keepalive,
        depends_on=list(depends_on),
        source=str(where),
    )


def _manifest_paths() -> list[Path]:
    """Every service.yaml the registry sources, in a stable order:
    core/services/*/service.yaml, core/qareen/service.yaml, config/services.d/*.yaml."""
    paths: list[Path] = []
    if SERVICES_DIR.exists():
        paths += sorted(SERVICES_DIR.glob("*/service.yaml"))
    if QAREEN_MANIFEST.exists():
        paths.append(QAREEN_MANIFEST)
    if SERVICES_D.exists():
        paths += sorted(SERVICES_D.glob("*.yaml"))
    return paths


@dataclass(frozen=True)
class Registry:
    manifests: tuple[ServiceManifest, ...]

    def __iter__(self):
        return iter(self.manifests)

    def by_name(self, name: str) -> ServiceManifest | None:
        for m in self.manifests:
            if m.name == name:
                return m
        return None

    def by_label(self, label: str) -> ServiceManifest | None:
        for m in self.manifests:
            if m.label == label:
                return m
        return None

    def active(self) -> list[ServiceManifest]:
        return [m for m in self.manifests if m.status == "active"]

    def active_residents(self) -> list[ServiceManifest]:
        return [m for m in self.manifests if m.status == "active" and m.type == "resident"]

    def retired(self) -> list[ServiceManifest]:
        return [m for m in self.manifests if m.status == "retired"]

    def health_urls(self) -> dict[str, str]:
        """{name: health_url} for every service with an HTTP health endpoint,
        regardless of status. Consumers that monitor live services want
        active_health_urls() instead — a retired service must never be probed."""
        return {m.name: m.health_url for m in self.manifests if m.health_url}

    def active_health_urls(self) -> dict[str, str]:
        """{name: health_url} for ACTIVE services with an HTTP health endpoint —
        the safe map for anything that probes live services (heartbeat,
        intent_classifier menu, watchdog state)."""
        return {
            m.name: m.health_url
            for m in self.manifests
            if m.status == "active" and m.health_url
        }

    def ports(self) -> dict[str, int]:
        """{name: port} for every service that declares a port."""
        return {m.name: m.port for m in self.manifests if m.port}

    def watchdog_map(self) -> list[tuple[str, str, str]]:
        """The intent-based service map the 5-min watchdog monitors: every
        ACTIVE service, as (name, label, health_url). health_url is the HTTP
        URL for liveness=http services, and "" otherwise (bridge poll_timestamp,
        interval jobs) so the watchdog does a loaded-only check for those.

        Intent-based on purpose: an active service with NO deployed plist is a
        silent death (the aos#180 'listen dead 3 months' / 'transcriber
        unloaded' class), so it stays in the map and the watchdog reports it
        DOWN — the exact failure that monitoring-off-deployed-plists missed.
        Optional and retired services are excluded."""
        return [
            (m.name, m.label, m.health_url or "")
            for m in self.active()
        ]


def load_registry() -> Registry:
    """Discover, validate, and return every service manifest. Raises
    ManifestError on any schema violation and on a duplicate service name."""
    manifests: list[ServiceManifest] = []
    seen: dict[str, str] = {}
    for path in _manifest_paths():
        try:
            raw = yaml.safe_load(path.read_text())
        except Exception as e:  # noqa: BLE001 — surface the file that failed
            raise ManifestError(f"{path}: could not parse YAML: {e}") from e
        m = _validate(raw, path)
        if m.name in seen:
            raise ManifestError(
                f"duplicate service name {m.name!r}: {seen[m.name]} and {m.source}"
            )
        seen[m.name] = m.source
        manifests.append(m)
    return Registry(manifests=tuple(manifests))


def health_url(name: str) -> str | None:
    """Convenience: the HTTP health URL for ``name``, or None."""
    m = load_registry().by_name(name)
    return m.health_url if m else None


def _main(argv: list[str]) -> int:
    """`service_registry.py [list|validate|health]` — inspect the registry."""
    cmd = argv[0] if argv else "list"
    try:
        reg = load_registry()
    except ManifestError as e:
        print(f"registry INVALID: {e}", file=sys.stderr)
        return 1

    if cmd == "validate":
        print(f"registry OK — {len(reg.manifests)} manifest(s) valid")
        return 0
    if cmd == "health":
        for name, url in sorted(reg.health_urls().items()):
            print(f"{name}\t{url}")
        return 0
    if cmd == "watchdog-map":
        # name|label|health_url per active service — consumed by the watchdog's
        # degraded-mode fallback so even that path derives from the registry
        # instead of a hardcoded list.
        for name, label, url in reg.watchdog_map():
            print(f"{name}|{label}|{url}")
        return 0
    # list
    print(f"{'NAME':<14}{'STATUS':<10}{'TYPE':<10}{'LIVENESS':<15}{'PORT':<7}LABEL")
    for m in reg.manifests:
        print(f"{m.name:<14}{m.status:<10}{m.type:<10}{m.liveness:<15}"
              f"{str(m.port or ''):<7}{m.label}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
