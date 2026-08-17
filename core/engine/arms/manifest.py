"""Load and validate config/modules.yaml (schema 2).

The manifest DECLARES. It never reports state — status is computed by probe.py
from the live machine. A declared status would start rotting the moment it was
written, which is precisely how the Arms panel came to show green services that
could not accept a package update.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Health means something different for each of these. Never infer it.
KINDS = {"daemon", "periodic", "oneshot", "resource"}
TIERS = {"core", "experimental"}

# Search order: dev workspace wins when present so framework work is testable
# without shipping; runtime is what an installed machine actually reads.
MANIFEST_PATHS = (
    Path.home() / "project" / "aos" / "config" / "modules.yaml",
    Path.home() / "aos" / "config" / "modules.yaml",
)


@dataclass
class Health:
    port: int | None = None
    venv: str | None = None
    log: str | None = None
    max_silence: int | None = None


@dataclass
class Detect:
    services: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)


@dataclass
class Module:
    id: str
    name: str
    tier: str
    kind: str
    tagline: str = ""
    category: str = "arm"
    ask: str | None = None
    consent: str | None = None
    status_note: str | None = None
    connector: bool = False
    costs: dict[str, str] = field(default_factory=dict)
    services: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    detect: Detect = field(default_factory=Detect)
    health: Health = field(default_factory=Health)
    #: Declarative install recipe. Keys: brew[], dirs[], service, manual[].
    #: Absent means "this module cannot be installed by the engine yet" —
    #: which the plan states out loud rather than silently doing nothing.
    install: dict[str, Any] = field(default_factory=dict)

    @property
    def removable(self) -> bool:
        """Core is the spine. It is never offered for removal — not as a policy
        preference, but because a system that can uninstall its own work engine
        from a settings panel is a footgun with a nice icon."""
        return self.tier != "core"


@dataclass
class Foreign:
    """Observed on this machine, not owned by AOS. Read-only, always."""

    label: str
    name: str
    note: str | None = None


@dataclass
class Manifest:
    schema: int
    modules: list[Module]
    foreign: list[Foreign] = field(default_factory=list)
    source: Path | None = None

    def get(self, module_id: str) -> Module | None:
        return next((m for m in self.modules if m.id == module_id), None)

    @property
    def owned_labels(self) -> set[str]:
        return {s for m in self.modules for s in m.services}


class ManifestError(Exception):
    pass


def _expand(value: str) -> str:
    return os.path.expandvars(value.replace("$HOME", str(Path.home())))


def expand_path(value: str) -> Path:
    return Path(_expand(value)).expanduser()


def _module(raw: dict[str, Any]) -> Module:
    missing = [k for k in ("id", "name", "tier", "kind") if not raw.get(k)]
    if missing:
        raise ManifestError(f"module {raw.get('id', '<no id>')}: missing {', '.join(missing)}")
    if raw["kind"] not in KINDS:
        raise ManifestError(
            f"module {raw['id']}: kind '{raw['kind']}' not one of {sorted(KINDS)}"
        )
    if raw["tier"] not in TIERS:
        raise ManifestError(
            f"module {raw['id']}: tier '{raw['tier']}' not one of {sorted(TIERS)}"
        )
    d = raw.get("detect") or {}
    h = raw.get("health") or {}
    return Module(
        id=raw["id"],
        name=raw["name"],
        tier=raw["tier"],
        kind=raw["kind"],
        tagline=raw.get("tagline", ""),
        category=raw.get("category", "arm"),
        ask=raw.get("ask"),
        consent=raw.get("consent"),
        status_note=raw.get("status_note"),
        connector=bool(raw.get("connector", False)),
        costs=raw.get("costs") or {},
        services=raw.get("services") or [],
        secrets=raw.get("secrets") or [],
        detect=Detect(
            services=d.get("services") or [],
            paths=d.get("paths") or [],
            commands=d.get("commands") or [],
        ),
        health=Health(
            port=h.get("port"),
            venv=h.get("venv"),
            log=h.get("log"),
            max_silence=h.get("max_silence"),
        ),
        install=raw.get("install") or {},
    )


def load_manifest(path: Path | str | None = None) -> Manifest:
    """Load the manifest, preferring an explicit path, then dev, then runtime."""
    candidates = [Path(path)] if path else list(MANIFEST_PATHS)
    src = next((p for p in candidates if p.is_file()), None)
    if src is None:
        raise ManifestError(
            "no modules.yaml found (looked in: "
            + ", ".join(str(p) for p in candidates)
            + ")"
        )

    raw = yaml.safe_load(src.read_text()) or {}
    schema = raw.get("schema", 1)
    if schema < 2:
        raise ManifestError(
            f"{src} is schema {schema}; the arm engine requires schema 2 "
            "(tier/kind/health). Schema 1 cannot express whether a module is a "
            "daemon or a periodic job, which makes its health unjudgeable."
        )

    modules = [_module(m) for m in raw.get("modules") or []]
    ids = [m.id for m in modules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ManifestError(f"duplicate module ids: {sorted(dupes)}")

    foreign = [
        Foreign(label=f["label"], name=f.get("name", f["label"]), note=f.get("note"))
        for f in raw.get("foreign") or []
    ]
    return Manifest(schema=schema, modules=modules, foreign=foreign, source=src)
