"""Qareen API — Service management routes.

List services, check status, restart, and tail logs.
"""

from __future__ import annotations

import json
import logging
import plistlib
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from .schemas import (
    ServiceListResponse,
    ServiceLogsResponse,
    ServiceResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/services", tags=["services"])

AOS_DATA = Path.home() / ".aos"
AOS_ROOT = Path.home() / "aos"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

# Labels beginning with these prefixes are AOS-managed services.
_SERVICE_PREFIXES = ("com.aos.", "com.agent.")

# Per-service HINTS — deliberately NOT the service list. The list is discovered
# from ~/Library/LaunchAgents (below) so removed services vanish and new ones
# appear on their own. Hints only supply a cheap outcome-based health probe for
# networked services; anything not listed here falls back to launchctl state.
_SERVICE_HINTS: dict[str, dict[str, Any]] = {
    "whatsmeow": {
        "health": "http://127.0.0.1:7601/health",
        "check": lambda d: bool(d.get("paired")) and bool(d.get("connected")),
        "unhealthy_msg": "socket up but not paired/connected",
    },
    "transcriber": {
        "health": "http://127.0.0.1:7602/health",
        "check": lambda d: str(d.get("status", "")).lower() in ("ready", "ok", "healthy"),
        "unhealthy_msg": "process up but model not ready",
    },
}


def _read_plist(path: Path) -> dict[str, Any]:
    """Parse a LaunchAgent plist, returning {} on any error."""
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _plist_port(data: dict[str, Any]) -> int | None:
    """Extract a --port value from a plist's ProgramArguments, if present."""
    args = data.get("ProgramArguments") or []
    for i, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        if arg in ("--port", "-port") and i + 1 < len(args):
            try:
                return int(str(args[i + 1]))
            except (ValueError, TypeError):
                pass
        if arg.startswith("--port="):
            try:
                return int(arg.split("=", 1)[1])
            except ValueError:
                pass
    return None


def _discover_services() -> list[dict[str, Any]]:
    """Discover AOS services from installed LaunchAgents.

    Never hardcode the service list — read it from disk so services removed
    yesterday disappear and ones added today show up automatically.
    """
    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not LAUNCH_AGENTS_DIR.is_dir():
        return discovered

    for plist in sorted(LAUNCH_AGENTS_DIR.glob("*.plist")):
        data = _read_plist(plist)
        label = data.get("Label") if isinstance(data.get("Label"), str) else plist.stem
        if not label.startswith(_SERVICE_PREFIXES):
            continue
        if label in seen:
            continue
        seen.add(label)
        discovered.append({
            "name": label.split(".")[-1],
            "label": label,
            "port": _plist_port(data),
            "err_log": data.get("StandardErrorPath"),
            "out_log": data.get("StandardOutPath"),
        })
    return discovered


def _launchctl_map() -> dict[str, dict[str, Any]]:
    """Map every loaded LaunchAgent label to its {pid, last_exit}."""
    result_map: dict[str, dict[str, Any]] = {}
    try:
        result = subprocess.run(
            ["/bin/launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return result_map
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pid_str, status_str, label = parts[0].strip(), parts[1].strip(), parts[2].strip()
            pid = int(pid_str) if pid_str.lstrip("-").isdigit() and pid_str != "-" else None
            try:
                last_exit = int(status_str)
            except ValueError:
                last_exit = None
            result_map[label] = {"pid": pid, "last_exit": last_exit}
    except Exception:
        logger.exception("launchctl list failed")
    return result_map


def _outcome_check(name: str) -> tuple[str | None, str | None, float | None]:
    """Probe a service's health endpoint for outcome-based truth.

    Returns (override_status, error_message, uptime_seconds). override_status
    is None when the service has no probe or the probe passes.
    """
    hint = _SERVICE_HINTS.get(name)
    if not hint or not hint.get("health"):
        return None, None, None
    try:
        with urllib.request.urlopen(hint["health"], timeout=1.0) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return "error", "health endpoint unreachable", None

    uptime = data.get("uptime_seconds") if isinstance(data, dict) else None
    check = hint.get("check")
    if check and not check(data):
        return "error", hint.get("unhealthy_msg", "health check failed"), uptime
    return None, None, uptime


def _status_for(svc: dict[str, Any], lc: dict[str, dict[str, Any]]) -> ServiceResponse:
    """Resolve a discovered service's true status: launchctl + outcome probe."""
    label = svc["label"]
    name = svc["name"]
    entry = lc.get(label)

    if entry is None:
        status, pid = "stopped", None
    else:
        pid = entry.get("pid")
        last_exit = entry.get("last_exit")
        if pid:
            status = "running"
        elif last_exit not in (0, None):
            status = "error"  # not running and last exit was a crash
        else:
            status = "stopped"

    error: str | None = None
    uptime: float | None = None

    # Outcome-based override: a live PID isn't proof of health. Probe the
    # service's own endpoint so a hung process reads as error, not green.
    if status == "running":
        override, msg, uptime = _outcome_check(name)
        if override:
            status = override
            error = msg
    elif status == "error":
        error = f"not running; last exit {entry['last_exit']}"

    return ServiceResponse(
        name=name,
        status=status,
        port=svc.get("port"),
        pid=pid,
        uptime_seconds=uptime,
        last_check=datetime.now(),
        error=error,
    )


@router.get("", response_model=ServiceListResponse)
async def list_services(request: Request) -> ServiceListResponse:
    """List all services with their current status, discovered from disk."""
    lc = _launchctl_map()
    services = [_status_for(svc, lc) for svc in _discover_services()]

    healthy = sum(1 for s in services if s.status == "running")

    return ServiceListResponse(
        services=services,
        total=len(services),
        healthy_count=healthy,
    )


@router.post("/{service}/restart", response_model=ServiceResponse)
async def restart_service(
    request: Request,
    service: str = PathParam(..., description="Service name to restart, e.g. 'bridge'"),
) -> ServiceResponse | JSONResponse:
    """Restart a service and return its new status."""
    svc = next((s for s in _discover_services() if s["name"] == service), None)
    if svc is None:
        return JSONResponse({"error": f"Unknown service: {service}"}, status_code=404)

    label = svc["label"]

    # Try to kickstart the service via launchctl
    try:
        subprocess.run(
            ["/bin/launchctl", "kickstart", "-k", f"gui/{_get_uid()}/{label}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        logger.exception("Failed to restart service %s", service)

    return _status_for(svc, _launchctl_map())


@router.get("/{service}/logs", response_model=ServiceLogsResponse)
async def get_service_logs(
    request: Request,
    service: str = PathParam(..., description="Service name"),
    lines: int = Query(100, description="Number of log lines to return", ge=1, le=1000),
) -> ServiceLogsResponse | JSONResponse:
    """Tail recent log lines from a service."""
    svc = next((s for s in _discover_services() if s["name"] == service), None)
    if svc is None:
        return JSONResponse({"error": f"Unknown service: {service}"}, status_code=404)

    # Prefer the log paths the plist actually declares, then fall back to
    # standard locations.
    log_paths = [
        Path(p) for p in (svc.get("err_log"), svc.get("out_log")) if p
    ] + [
        AOS_DATA / "logs" / f"{service}.log",
        AOS_DATA / "logs" / service / "current.log",
        Path.home() / "Library" / "Logs" / f"com.aos.{service}" / "stderr.log",
    ]

    log_lines: list[str] = []
    for log_path in log_paths:
        if log_path.is_file():
            try:
                with open(log_path, "r") as f:
                    all_lines = f.readlines()
                log_lines = [l.rstrip("\n") for l in all_lines[-lines:]]
                break
            except OSError:
                continue

    return ServiceLogsResponse(
        service=service,
        lines=log_lines,
        total_lines=len(log_lines),
        truncated=len(log_lines) >= lines,
    )


def _get_uid() -> int:
    """Get the current user's UID."""
    import os
    return os.getuid()
