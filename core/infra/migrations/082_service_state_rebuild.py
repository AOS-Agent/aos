"""
Migration 082: Rebuild ~/.aos/config/state.yaml from the deployed plists.

The watchdog reads its service map from the instance-layer state.yaml. That map
had gone stale and wrong (aos#180):
  - it listed dead/retired services (dashboard, phoenix, claude-remote) and
    OMITTED live ones (transcriber, qareen, n8n, sentinel, …);
  - the bridge entry had an empty health URL, so a wedged-but-loaded bridge was
    invisible to the watchdog.

This migration rebuilds the file from the services actually deployed on THIS
machine — discovered from ~/Library/LaunchAgents/com.aos.*.plist and
com.agent.*.plist (discover, never hardcode; per CLAUDE.md) — attaching a known
health URL per service so the watchdog can catch a wedged-but-loaded service.
Dead entries with no plist simply don't reappear.

It writes ONLY a config file — no launchctl, no service restart — so there is no
kickstart/drain-blocking concern here. Any non-"services" top-level keys in an
existing state.yaml are preserved; only the services map is rebuilt.

Strict runner contract: check() is a CONTENT check (does the file already equal
what up() would produce?), not a file-exists guard; up() returns True on success
and False only on real failure. Idempotent: up() is a deterministic rewrite.

The health-URL map is a frozen point-in-time snapshot ON PURPOSE — a migration
is a one-time historical artifact and must not import evolving lib code
(lib.service_ctl.KNOWN_HEALTH_URLS is the living copy the runtime shares).
Ports verified against service code at write time (aos#180): bridge :4098,
transcriber :7602, qareen :4096, n8n :5678, listen :7600, whatsmeow :7601.
"""

DESCRIPTION = "Rebuild ~/.aos/config/state.yaml service map from deployed plists (aos#180)"

from pathlib import Path

import yaml

HOME = Path.home()
STATE_YAML = HOME / ".aos" / "config" / "state.yaml"
LA_DIR = HOME / "Library" / "LaunchAgents"

# Frozen snapshot — see module docstring. Keyed by service short name.
KNOWN_HEALTH_URLS = {
    "bridge": "http://127.0.0.1:4098/health",
    "transcriber": "http://127.0.0.1:7602/health",
    "qareen": "http://127.0.0.1:4096/api/health",
    "n8n": "http://127.0.0.1:5678/healthz",
    "listen": "http://127.0.0.1:7600/health",
    "whatsmeow": "http://127.0.0.1:7601/health",
}

_PREFIXES = ("com.aos.", "com.agent.")


def _short_name(stem: str) -> str:
    for p in _PREFIXES:
        if stem.startswith(p):
            return stem[len(p):]
    return stem


def _build_services() -> dict:
    """Discover deployed AOS LaunchAgents and build the service map.

    {name: {launchagent: <label>, health: <url or "">}} — exactly the shape the
    watchdog parses (services.<name>.launchagent / .health)."""
    services: dict[str, dict] = {}
    if not LA_DIR.exists():
        return services
    plists = sorted(LA_DIR.glob("com.aos.*.plist")) + sorted(LA_DIR.glob("com.agent.*.plist"))
    for plist in plists:
        label = plist.stem  # e.g. com.aos.bridge
        name = _short_name(label)
        services[name] = {
            "launchagent": label,
            "health": KNOWN_HEALTH_URLS.get(name, ""),
        }
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
    """What the file should contain: existing non-service keys preserved, the
    services map rebuilt from the deployed plists."""
    data = dict(existing)
    data["services"] = _build_services()
    return data


def check() -> bool:
    """Applied iff the file already equals the deterministic rebuild (content
    check — not mere existence)."""
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
    print(f"  ✓ Rebuilt {STATE_YAML} with {len(svcs)} discovered service(s)")
    for name, cfg in sorted(svcs.items()):
        health = cfg.get("health") or "(loaded-check only)"
        print(f"      {name}: {cfg['launchagent']} → {health}")
    return True


if __name__ == "__main__":
    if check():
        print("Migration 082 already applied")
    else:
        print("Done" if up() else "Failed")
