"""Compute the real state of every module from the live machine.

The rule this file exists to enforce: WHAT a thing is decides what healthy MEANS.

On 2026-08-17 an audit judged every launchd service by "is there a live PID"
and reported five modules BROKEN. All five were wrong — four were StartInterval
periodic jobs that are supposed to be idle between ticks, and the fifth was a
daemon that was up but had lost its HTTP API. Meanwhile two genuinely degraded
services went unreported because nothing checked whether their venv could still
install a package. Both classes of error are handled here, deliberately.
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest, Module, expand_path, load_manifest

ACTIVE = "active"
DEGRADED = "degraded"
BROKEN = "broken"
ABSENT = "absent"


@dataclass
class ModuleState:
    id: str
    name: str
    tier: str
    kind: str
    status: str
    why: str = ""
    services: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in (ACTIVE, ABSENT)


def _launchctl() -> dict[str, tuple[str, str]]:
    """label -> (pid, last_exit). pid '-' means loaded but not currently running,
    which is NORMAL for a periodic job and FATAL for a daemon."""
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=15
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table: dict[str, tuple[str, str]] = {}
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            table[parts[2]] = (parts[0], parts[1])
    return table


def _port_listening(port: int, timeout: float = 0.5) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _command_exists(name: str) -> bool:
    dirs = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")
    home = Path.home()
    extra = (home / ".bun/bin", home / ".local/bin", home / ".cargo/bin")
    if any((Path(d) / name).exists() for d in dirs) or any((d / name).exists() for d in extra):
        return True
    return (
        subprocess.run(["which", name], capture_output=True, timeout=10).returncode == 0
    )


def venv_usable(venv: Path) -> bool | None:
    """Can this venv still install a package?

    Returns None when there is no venv to judge. A venv whose interpreter fails
    this probe looks perfectly green to launchctl and to `python --version`, but
    both pip and uv refuse to touch it — so the service is frozen at whatever
    packages it happened to have. That is how the bridge lost its :4098 API
    while every dashboard showed it running.
    """
    python = venv / "bin" / "python"
    if not python.exists():
        return None
    try:
        return (
            subprocess.run(
                [
                    str(python),
                    "-c",
                    "import platform, pyexpat, sys;"
                    "sys.exit(0 if platform.mac_ver()[0] else 1)",
                ],
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _installed(mod: Module, loaded: dict[str, tuple[str, str]]) -> bool:
    d = mod.detect
    if d.services:
        return any(s in loaded for s in d.services)
    if d.paths:
        return any(expand_path(p).exists() for p in d.paths)
    if d.commands:
        return any(_command_exists(c) for c in d.commands)
    return False


def probe_module(mod: Module, loaded: dict[str, tuple[str, str]] | None = None) -> ModuleState:
    loaded = _launchctl() if loaded is None else loaded

    def state(status: str, why: str = "") -> ModuleState:
        return ModuleState(
            id=mod.id, name=mod.name, tier=mod.tier, kind=mod.kind,
            status=status, why=why, services=tuple(mod.services),
        )

    if not _installed(mod, loaded):
        return state(ABSENT)

    why: list[str] = []

    if mod.kind == "daemon":
        # A daemon with no live process is broken, full stop.
        for label in mod.services:
            pid, _exit = loaded.get(label, ("-", "0"))
            if pid == "-":
                return state(BROKEN, f"{label} is not running")
        if mod.health.port and not _port_listening(mod.health.port):
            why.append(f"port {mod.health.port} not listening")

    elif mod.kind == "periodic":
        # Idle between ticks is CORRECT. Judge by log freshness, never liveness.
        log, max_silence = mod.health.log, mod.health.max_silence
        if log and max_silence:
            p = expand_path(log)
            if p.exists():
                age = int(time.time() - p.stat().st_mtime)
                if age > max_silence:
                    why.append(f"last ran {age // 60} min ago")

    # 'oneshot' and 'resource' are judged by presence alone — reaching here
    # means they are present, so they are fine unless a probe below says else.

    if mod.health.venv:
        if venv_usable(expand_path(mod.health.venv)) is False:
            why.append("venv interpreter broken — cannot update")

    return state(DEGRADED if why else ACTIVE, "; ".join(why))


def probe_all(manifest: Manifest | None = None) -> list[ModuleState]:
    manifest = manifest or load_manifest()
    loaded = _launchctl()
    return [probe_module(m, loaded) for m in manifest.modules]


def unmanaged_labels(manifest: Manifest) -> list[str]:
    """AOS-looking LaunchAgents on this machine that no module claims.

    The manifest is only a source of truth if it covers the machine. When this
    returns anything, it is a coverage bug in the manifest, not a system fault.
    """
    known = manifest.owned_labels | {f.label for f in manifest.foreign}
    agents = Path.home() / "Library" / "LaunchAgents"
    if not agents.is_dir():
        return []
    return sorted(
        p.stem
        for p in agents.glob("*.plist")
        if p.stem.startswith(("com.aos.", "com.agent.")) and p.stem not in known
    )
