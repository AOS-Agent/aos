"""
Migration 116: write a Qren-readiness inventory for this machine.

Qren succeeds AOS eventually, and whatever its installer turns out to be, it
will need to know what it is landing on. The moment to record that is now, on a
machine running a system that still knows itself, rather than later from an
installer poking at a half-migrated tree. AOS carries on being developed in the
meantime; this report is a starting point, not a final accounting.

Writes ~/.aos/data/qren-readiness.json:

    machine    hostname, ComputerName, macOS, arch
    aos        version, migration level, install shape (release vs git clone)
    engines    which agent CLIs are installed — claude, codex, kimi — with
               resolved path and version
    services   every registry manifest, its status, and whether the operator
               has it disabled
    modules    ids and tiers from config/modules.yaml
    data       per-database byte sizes under ~/.aos/data
    qren       { invite_token: "" } — the placeholder hook

Read-only about the system: it inspects and records, and the only thing it
writes is the report plus the empty invite-token key. Nothing here decides
anything, which is deliberate — a readiness probe that also acts is a migration
that can fail halfway and leave the machine in a state its own report denies.

**Engine detection is version-only, never a login probe.** `claude --version`
costs milliseconds and touches nothing; anything that would report login state
means an authenticated round-trip on a machine that did not ask for one, in a
migration the operator did not watch run. The task allowed "if cheaply
detectable" — it is not, so `authenticated` is recorded as null rather than
guessed at. Each probe runs under a hard timeout and a missing CLI is a normal
result, not a failure.

`qren.invite_token` stays empty. It is the seam where an invite lands later; a
migration that generated one would be minting credentials nobody asked for.

Idempotent: re-running refreshes the report in place. check() passes when the
file exists and carries the current schema version.
"""

from __future__ import annotations

DESCRIPTION = "Write the Qren-readiness inventory (engines, services, data sizes)"

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _home() -> Path:
    return Path.home()


def _aos_root() -> Path:
    return _home() / "aos"


def _data_dir() -> Path:
    return _home() / ".aos" / "data"


def _report() -> Path:
    return _data_dir() / "qren-readiness.json"


def _config_dir() -> Path:
    return _home() / ".aos" / "config"


SCHEMA_VERSION = 1

# CLI name → the flag that prints a version cheaply.
ENGINES = {
    "claude": ["--version"],
    "codex": ["--version"],
    "kimi": ["--version"],
}

PROBE_TIMEOUT_S = 15


def _run(cmd: list[str], timeout: int = PROBE_TIMEOUT_S) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or r.stderr or "").strip()
    return out.splitlines()[0].strip() if out else None


def _which(name: str) -> str | None:
    from shutil import which
    return which(name)


def _engines() -> dict:
    found = {}
    for name, flag in ENGINES.items():
        path = _which(name)
        if path is None:
            found[name] = {"installed": False, "path": None, "version": None,
                           "authenticated": None}
            continue
        found[name] = {
            "installed": True,
            "path": path,
            "version": _run([path, *flag]),
            # Never probed — see module docstring. null means "not determined",
            # which is honest; false would claim a check that never ran.
            "authenticated": None,
        }
    return found


def _aos_info() -> dict:
    aos_root = _aos_root()
    home = _home()
    data_dir = _data_dir()
    version = None
    vf = aos_root / "VERSION"
    if vf.exists():
        version = vf.read_text().strip()

    migration_level = None
    mf = home / ".aos" / ".version"
    if mf.exists():
        try:
            migration_level = int(mf.read_text().strip())
        except ValueError:
            pass

    if aos_root.is_symlink():
        shape = "release"
    elif (aos_root / ".git").exists():
        shape = "git-clone"
    else:
        shape = "unknown"

    deployed_hash = None
    dh = data_dir / "deployed-hash"
    if dh.exists():
        deployed_hash = dh.read_text().strip() or None

    return {
        "version": version,
        "migration_level": migration_level,
        "install_shape": shape,
        "deployed_hash": deployed_hash,
        "root": str(aos_root.resolve()) if aos_root.exists() else None,
    }


def _services() -> list[dict]:
    sys.path.insert(0, str(_aos_root() / "core" / "infra" / "lib"))
    try:
        from service_registry import disabled_services, load_registry
    except Exception:  # noqa: BLE001 — no registry is a finding, not a crash
        return []
    try:
        reg = load_registry()
        off = disabled_services()
    except Exception:  # noqa: BLE001
        return []

    out = []
    for m in sorted(reg.manifests, key=lambda x: x.name):
        out.append({
            "name": m.name,
            "status": m.status,
            "type": m.type,
            "label": m.label,
            "operator_disabled": m.name in off,
        })
    return out


def _modules() -> list[dict]:
    path = _aos_root() / "config" / "modules.yaml"
    if not path.exists():
        return []
    try:
        import yaml
        raw = yaml.safe_load(path.read_text()) or {}
    except Exception:  # noqa: BLE001
        return []
    mods = raw.get("modules")
    if not isinstance(mods, list):
        return []
    return [
        {"id": m.get("id"), "tier": m.get("tier"), "kind": m.get("kind")}
        for m in mods if isinstance(m, dict)
    ]


def _data_sizes() -> dict:
    sizes = {}
    data_dir = _data_dir()
    if not data_dir.exists():
        return sizes
    for p in sorted(data_dir.glob("*.db")):
        try:
            sizes[p.name] = p.stat().st_size
        except OSError:
            continue
    return sizes


def _machine() -> dict:
    return {
        "hostname": platform.node(),
        "computer_name": _run(["scutil", "--get", "ComputerName"], timeout=5),
        "local_hostname": _run(["scutil", "--get", "LocalHostName"], timeout=5),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "uid": os.getuid(),
    }


def _build_report() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "machine": _machine(),
        "aos": _aos_info(),
        "engines": _engines(),
        "services": _services(),
        "modules": _modules(),
        "data": _data_sizes(),
        # The invite hook. Stays empty — a migration that generated a token
        # would be minting a credential nobody asked for.
        "qren": {"invite_token": ""},
    }


def check() -> bool:
    report = _report()
    if not report.exists():
        return False
    try:
        raw = json.loads(report.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return raw.get("schema_version") == SCHEMA_VERSION


def up() -> bool:
    _data_dir().mkdir(parents=True, exist_ok=True)
    report_path = _report()
    report = _build_report()

    tmp = report_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n")
    os.replace(tmp, report_path)

    engines = [f"{n} {d['version'] or '?'}" for n, d in report["engines"].items()
               if d["installed"]]
    print(f"  ✓ Wrote {report_path}")
    print(f"     Engines:  {', '.join(engines) if engines else 'none detected'}")
    print(f"     Services: {len(report['services'])} declared, "
          f"{sum(1 for s in report['services'] if s['operator_disabled'])} disabled")
    print(f"     Data:     {sum(report['data'].values()) / 1e6:.0f} MB across "
          f"{len(report['data'])} database(s)")
    print("     qren.invite_token: (empty — placeholder)")
    return check()


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 116 already applied" if check() else ("Done" if up() else "Failed"))
