"""
Migration 083: Rebuild ~/.aos/config/state.yaml from the service REGISTRY.

Supersedes migration 082, which built state.yaml from the deployed plists. That
was still deriving monitoring from *deployment* — the exact mistake behind the
aos#180 silent deaths (a service with no deployed plist simply vanished from the
map, so nothing noticed it was gone). The registry is intent: it declares which
services are ACTIVE regardless of whether their plist is currently loaded, so an
active service that lost its plist now stays in the watchdog's map and is
reported DOWN instead of silently dropping off.

The watchdog reads this file (services.<name>.launchagent / .health). We write:
  - one entry per ACTIVE service in the registry (optional + retired excluded)
  - health = the full HTTP health URL for liveness=http services, "" otherwise
    (bridge is poll_timestamp → "", so the watchdog does a loaded-only check and
    never health-probes an endpoint the bridge doesn't reliably serve)

Reads the manifests DIRECTLY (glob + yaml), NOT via lib.service_registry: a
migration is a frozen historical artifact and must not import evolving lib code
(same rule 082 followed for its health-URL snapshot). The manifest FORMAT is the
stable contract it reads against.

Strict runner contract: check() is a CONTENT check (does the file already equal
what up() would produce?), not a file-exists guard; up() returns True on success
and False only on real failure; idempotent (deterministic rewrite). Any non-
"services" top-level keys in an existing state.yaml are preserved.
"""

DESCRIPTION = "Rebuild ~/.aos/config/state.yaml service map from the service registry (aos#180)"

from pathlib import Path

import yaml

HOME = Path.home()
AOS_ROOT = HOME / "aos"
SERVICES_DIR = AOS_ROOT / "core" / "services"
QAREEN_MANIFEST = AOS_ROOT / "core" / "qareen" / "service.yaml"
SERVICES_D = AOS_ROOT / "config" / "services.d"
STATE_YAML = HOME / ".aos" / "config" / "state.yaml"


def _manifest_paths() -> list[Path]:
    paths: list[Path] = []
    if SERVICES_DIR.exists():
        paths += sorted(SERVICES_DIR.glob("*/service.yaml"))
    if QAREEN_MANIFEST.exists():
        paths.append(QAREEN_MANIFEST)
    if SERVICES_D.exists():
        paths += sorted(SERVICES_D.glob("*.yaml"))
    return paths


def _health_url(m: dict) -> str:
    """Full HTTP health URL for a liveness=http manifest, else ""."""
    if m.get("liveness") == "http" and m.get("port") and m.get("health_endpoint"):
        return f"http://127.0.0.1:{m['port']}{m['health_endpoint']}"
    return ""


def _build_services() -> dict:
    """{name: {launchagent, health}} for every ACTIVE registry service."""
    services: dict[str, dict] = {}
    for path in _manifest_paths():
        try:
            m = yaml.safe_load(path.read_text())
        except Exception:
            continue
        if not isinstance(m, dict) or m.get("status") != "active":
            continue
        name = m.get("name")
        if not name:
            continue
        label = m.get("label") or f"com.aos.{name}"
        services[name] = {"launchagent": label, "health": _health_url(m)}
    return services


def _load_existing() -> dict:
    if not STATE_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(STATE_YAML.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _expected(existing: dict) -> dict:
    data = dict(existing)
    data["services"] = _build_services()
    return data


def check() -> bool:
    """Applied iff the file already equals the deterministic registry rebuild."""
    if not STATE_YAML.exists():
        return False
    existing = _load_existing()
    return existing == _expected(existing)


def up() -> bool:
    try:
        expected = _expected(_load_existing())
        STATE_YAML.parent.mkdir(parents=True, exist_ok=True)
        STATE_YAML.write_text(
            yaml.safe_dump(expected, default_flow_style=False, sort_keys=True)
        )
    except Exception as e:
        print(f"  ✗ Failed to rebuild {STATE_YAML}: {e}")
        return False

    svcs = expected.get("services", {})
    print(f"  ✓ Rebuilt {STATE_YAML} from registry with {len(svcs)} active service(s)")
    for name, cfg in sorted(svcs.items()):
        health = cfg.get("health") or "(loaded-check only)"
        print(f"      {name}: {cfg['launchagent']} → {health}")
    return True


if __name__ == "__main__":
    if check():
        print("Migration 083 already applied")
    else:
        print("Done" if up() else "Failed")
