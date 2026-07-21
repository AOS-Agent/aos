"""Apps registry — app id → repo path / scheme / bundle id, for bug-class tasks.

Replaces islah's hardcoded ``APPS`` / ``REPOS`` (aos#164 debt). A bug task's
``fields.app`` names an app id; the runner (Phase 5) and the board resolve that
id here to know which repo to worktree, which scheme to build, which bundle to
symbolicate.

Two layers, per the component-lifecycle rule:

  * FRAMEWORK template — ``config/apps.yaml`` in the repo — ships EXAMPLE entries
    only. No real repo paths, bundle ids, or domains live in the framework tree
    (privacy: the framework is git-tracked and shipped to every machine).
  * INSTANCE override — ``~/.aos/config/apps.yaml`` — carries the operator's real
    apps. It fully replaces the framework ``apps:`` map when present (the example
    entries are a shape reference, not a base to merge onto).

Missing instance file → the example apps resolve (so the board renders and tests
pass); an unknown app id resolves to ``None`` and the caller degrades. No config
is never a crash.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - yaml is a hard dep, defensive only
    yaml = None


@dataclass(frozen=True)
class AppEntry:
    id: str
    name: str
    repo: str | None = None       # absolute repo path (instance-side)
    scheme: str | None = None     # Xcode scheme / build target
    bundle_id: str | None = None  # e.g. com.example.app
    release_branch: str = "main"
    # App Store Connect intake (ascbuild). platform='web' apps are skipped by
    # the ASC intake (no TestFlight); asc_app_id is an explicit ASC numeric id
    # that wins over bundle_id / name matching when present.
    platform: str = "ios"
    asc_app_id: str | None = None


def _framework_path() -> Path:
    # …/core/engine/work/apps_registry.py → repo root is parents[3].
    return Path(__file__).resolve().parents[3] / "config" / "apps.yaml"


def _instance_path() -> Path | None:
    override = os.environ.get("AOS_CONFIG_DIR")
    if override:
        p = Path(override) / "apps.yaml"
        return p if p.exists() else None
    p = Path.home() / ".aos" / "config" / "apps.yaml"
    return p if p.exists() else None


def _load_raw(path: Path | None) -> dict:
    if path is None or yaml is None or not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def load_apps() -> dict[str, AppEntry]:
    """Resolve the effective apps map: instance override wins wholesale, else
    the framework example entries."""
    instance = _load_raw(_instance_path())
    raw = instance if instance.get("apps") else _load_raw(_framework_path())
    out: dict[str, AppEntry] = {}
    for app_id, spec in (raw.get("apps") or {}).items():
        spec = spec or {}
        out[app_id] = AppEntry(
            id=app_id,
            name=spec.get("name", app_id),
            repo=spec.get("repo"),
            scheme=spec.get("scheme"),
            bundle_id=spec.get("bundle_id"),
            release_branch=spec.get("release_branch", "main"),
            platform=spec.get("platform", "ios"),
            asc_app_id=(str(spec["asc_app_id"]) if spec.get("asc_app_id") else None),
        )
    return out


def get_app(app_id: str | None) -> AppEntry | None:
    """Resolve one app id to its entry, or None if unknown / unset."""
    if not app_id:
        return None
    return load_apps().get(app_id)
