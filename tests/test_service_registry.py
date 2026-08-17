"""
Tests for the service registry (core/infra/lib/service_registry.py) and the two
guards that keep it the single source of service identity (aos#180):

  1. Every core/services/<dir> MUST ship a service.yaml — a new service cannot be
     added without declaring itself, so it can never drift into the old
     scattered-identity state.
  2. No consumer file may hardcode a multi-service health-URL list — the exact
     pattern (a bridge/listen/transcriber/whatsapp URL menu with :7601 where the
     transcriber is really on :7602) that this whole change deletes.

Plus: the whole live registry loads and validates, and strict validation
actually rejects malformed manifests.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
REGISTRY_PATH = REPO / "core" / "infra" / "lib" / "service_registry.py"


def _load_registry_module():
    spec = importlib.util.spec_from_file_location("service_registry_under_test", REGISTRY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["service_registry_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


reg_mod = _load_registry_module()


# ── The live registry is valid ───────────────────────────────────────────────

def test_live_registry_loads_and_validates():
    reg = reg_mod.load_registry()
    names = {m.name for m in reg}
    # The core/services dirs + qareen (services.d). n8n and eventd were
    # removed: n8n never held a workflow, eventd was retired in April.
    assert {"bridge", "transcriber", "whatsmeow", "listen",
            "crawler", "memory", "companion", "mesh", "qareen"} <= names


def test_known_truth_is_declared_correctly():
    reg = reg_mod.load_registry()
    t = reg.by_name("transcriber")
    assert t.port == 7602 and t.health_url == "http://127.0.0.1:7602/health"
    # The bridge does not serve HTTP health — poll_timestamp, no health URL.
    assert reg.by_name("bridge").liveness == "poll_timestamp"
    assert reg.by_name("bridge").health_url is None
    # Retirement is expressed in the manifest, and the retired set is derived.
    assert {m.name for m in reg.retired()} == {"listen"}
    # whatsmeow is deployed as com.aos.whatsmeow. The manifest used to say
    # com.agent.*, which no plist has ever matched — the watchdog looked up a
    # label that did not exist and alerted 90 times about a healthy service.
    # This assertion encoded that drift as truth; it now encodes the fix.
    assert reg.by_name("whatsmeow").label == "com.aos.whatsmeow"


def test_active_health_urls_excludes_retired_and_non_http():
    reg = reg_mod.load_registry()
    urls = reg.active_health_urls()
    assert "listen" not in urls  # retired
    assert "bridge" not in urls                            # poll_timestamp, no URL
    assert urls["transcriber"] == "http://127.0.0.1:7602/health"


# ── Strict validation ────────────────────────────────────────────────────────

def _validate(raw):
    return reg_mod._validate(raw, REPO / "core" / "services" / "x" / "service.yaml")


def _base():
    return {"name": "x", "purpose": "p", "status": "active", "type": "resident",
            "owner_layer": "framework", "liveness": "keepalive"}


def test_unknown_key_rejected():
    raw = _base() | {"bogus": 1}
    with pytest.raises(reg_mod.ManifestError):
        _validate(raw)


def test_missing_required_key_rejected():
    raw = _base()
    del raw["status"]
    with pytest.raises(reg_mod.ManifestError):
        _validate(raw)


def test_bad_enum_rejected():
    with pytest.raises(reg_mod.ManifestError):
        _validate(_base() | {"status": "halfway"})


def test_http_liveness_requires_port_and_endpoint():
    with pytest.raises(reg_mod.ManifestError):
        _validate(_base() | {"liveness": "http"})  # no port/endpoint


def test_health_endpoint_forbidden_without_http_liveness():
    with pytest.raises(reg_mod.ManifestError):
        _validate(_base() | {"health_endpoint": "/health"})  # liveness keepalive


# ── Guard 1: every service dir declares itself ───────────────────────────────

def test_every_service_dir_has_a_manifest():
    services_dir = REPO / "core" / "services"
    missing = [
        d.name for d in sorted(services_dir.iterdir())
        if d.is_dir() and not (d / "service.yaml").exists()
    ]
    assert not missing, (
        f"service dir(s) without a service.yaml manifest: {missing}. "
        f"Every core/services/<dir> must declare itself — see core/services/README.md."
    )


# ── Guard 2: no consumer hardcodes a multi-service health-URL list ────────────

# Files allowed to enumerate service ports/URLs: the registry itself, the
# manifests, the state.yaml migrations (frozen snapshots by design), and the
# tests. Everything else must derive from the registry.
_PORT_LIST_ALLOWLIST = {
    "core/infra/lib/service_registry.py",
    "core/infra/migrations/082_service_state_rebuild.py",
    "core/infra/migrations/083_service_state_from_registry.py",
}

# A local health URL: http://127.0.0.1:PORT or http://localhost:PORT
_URL_PORT = re.compile(r"(?:127\.0\.0\.1|localhost):(\d+)")


def _strip_comments(text: str, suffix: str) -> str:
    """Drop comment content so a port mentioned only in a comment doesn't count.
    Handles Python/shell `#` line and inline comments (URLs contain no `#`)."""
    out = []
    for line in text.splitlines():
        if suffix in (".py", ".sh", "") and "#" in line:
            line = line[: line.index("#")]
        out.append(line)
    return "\n".join(out)


def _consumer_files() -> list[Path]:
    files: list[Path] = []
    files += (REPO / "core" / "infra" / "reconcile" / "checks").glob("*.py")
    files += (REPO / "core" / "services").glob("*/*.py")
    files.append(REPO / "core" / "bin" / "crons" / "watchdog")
    return [f for f in files if f.is_file()]


def test_no_consumer_hardcodes_multi_service_url_list():
    reg = reg_mod.load_registry()
    service_ports = set(reg.ports().values())

    offenders = {}
    for f in _consumer_files():
        rel = f.relative_to(REPO).as_posix()
        if rel in _PORT_LIST_ALLOWLIST:
            continue
        code = _strip_comments(f.read_text(), f.suffix)
        found = {int(p) for p in _URL_PORT.findall(code) if int(p) in service_ports}
        if len(found) >= 2:
            offenders[rel] = sorted(found)

    assert not offenders, (
        "consumer file(s) hardcode a multi-service health-URL list instead of "
        f"deriving from the registry: {offenders}"
    )


# ── Guard 3: no two services claim the same port ─────────────────────────────

def test_no_two_active_services_declare_the_same_port():
    """Two manifests claiming one port is a race for the socket: whichever
    launchd starts first wins and the other dies. The transcriber shipped
    declaring :7601 — whatsmeow's port — for exactly this reason (aos#180)."""
    reg = reg_mod.load_registry()

    by_port: dict[int, list[str]] = {}
    for name, port in reg.ports().items():
        by_port.setdefault(port, []).append(name)

    collisions = {p: sorted(n) for p, n in by_port.items() if len(n) > 1}
    assert not collisions, (
        f"port collision(s) between active services: {collisions}. "
        "Each service needs its own port — see core/services/<name>/service.yaml."
    )


# ── Guard 4: a service's own port declarations match its manifest ────────────

# `<SERVICE>_PORT` as an env-var default in the service's own code, and as a
# literal in its plist template. Both must equal `port:` in its service.yaml.
_ENV_PORT_DEFAULT = re.compile(
    r"""os\.environ\.get\(\s*["'](\w+)_PORT["']\s*,\s*["'](\d+)["']\s*\)"""
)
_PLIST_ENV_PORT = re.compile(
    r"<key>(\w+)_PORT</key>\s*<string>(\d+)</string>", re.MULTILINE
)


def test_service_own_port_declarations_agree_with_manifest():
    reg = reg_mod.load_registry()
    ports = reg.ports()

    offenders: dict[str, str] = {}

    def _check(rel: str, declared_for: str, port: int):
        name = declared_for.lower()
        if name in ports and port != ports[name]:
            offenders[rel] = (
                f"declares {name.upper()}_PORT={port}, "
                f"but service.yaml says {ports[name]}"
            )

    for f in (REPO / "core" / "services").glob("*/*.py"):
        for declared_for, port in _ENV_PORT_DEFAULT.findall(f.read_text()):
            _check(f.relative_to(REPO).as_posix(), declared_for, int(port))

    for f in (REPO / "config" / "launchagents").glob("*.plist.template"):
        for declared_for, port in _PLIST_ENV_PORT.findall(f.read_text()):
            _check(f.relative_to(REPO).as_posix(), declared_for, int(port))

    assert not offenders, (
        "port declaration(s) disagree with the owning service.yaml — the "
        f"manifest is the source of truth: {offenders}"
    )


# ── Guard 5: a `<SERVICE>_URL` constant points at that service's port ────────

_SERVICE_URL_CONST = re.compile(
    r"""(\w+)_URL\s*=\s*["']http://(?:127\.0\.0\.1|localhost):(\d+)"""
)


def test_service_url_constants_point_at_the_right_service():
    """`TRANSCRIBER_URL = "http://127.0.0.1:7601"` is syntactically fine and
    semantically catastrophic — :7601 is whatsmeow, so voice audio was POSTed
    at the WhatsApp bridge. Guard 2 misses it because it only fires when one
    file names two ports."""
    reg = reg_mod.load_registry()
    ports = reg.ports()

    offenders: dict[str, str] = {}
    for f in _consumer_files():
        rel = f.relative_to(REPO).as_posix()
        if rel in _PORT_LIST_ALLOWLIST:
            continue
        code = _strip_comments(f.read_text(), f.suffix)
        for named, port in _SERVICE_URL_CONST.findall(code):
            name = named.lower()
            if name in ports and int(port) != ports[name]:
                owner = next(
                    (s for s, p in ports.items() if p == int(port)), "nothing"
                )
                offenders[f"{rel}:{named}_URL"] = (
                    f"points at :{port} ({owner}), "
                    f"but {name} is on :{ports[name]}"
                )

    assert not offenders, (
        f"service URL constant(s) point at the wrong service: {offenders}"
    )
