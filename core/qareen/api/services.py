"""Qareen API — Service management routes.

List services, check status, restart, and tail logs.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Path as PathParam, Query, Request
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

# Known AOS services with their ports
KNOWN_SERVICES = {
    "bridge": {"port": None, "label": "com.aos.bridge"},
    "dashboard": {"port": 4096, "label": "com.aos.dashboard"},
    "eventd": {"port": 4097, "label": "com.aos.eventd"},
    "listen": {"port": 7600, "label": "com.aos.listen"},
    "whatsmeow": {"port": 7601, "label": "com.aos.whatsmeow"},
    "transcriber": {"port": 7602, "label": "com.aos.transcriber"},
}


def _check_launchctl(label: str) -> dict[str, Any]:
    """Check if a LaunchAgent is running via launchctl."""
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return {"status": "unknown", "pid": None}

        for line in result.stdout.splitlines():
            if label in line:
                parts = line.split("\t")
                pid_str = parts[0].strip() if len(parts) > 0 else "-"
                pid = int(pid_str) if pid_str != "-" and pid_str.isdigit() else None
                status_code = parts[1].strip() if len(parts) > 1 else "0"
                status = "running" if pid else ("error" if status_code != "0" else "stopped")
                return {"status": status, "pid": pid}

        return {"status": "stopped", "pid": None}
    except Exception:
        return {"status": "unknown", "pid": None}


def _get_service_status(name: str, config: dict[str, Any]) -> ServiceResponse:
    """Get the status of a single service."""
    label = config.get("label", f"com.aos.{name}")
    info = _check_launchctl(label)

    return ServiceResponse(
        name=name,
        status=info["status"],
        port=config.get("port"),
        pid=info.get("pid"),
        last_check=datetime.now(),
    )


@router.get("", response_model=ServiceListResponse)
async def list_services(request: Request) -> ServiceListResponse:
    """List all services with their current status."""
    services = []

    for name, config in KNOWN_SERVICES.items():
        service = _get_service_status(name, config)
        services.append(service)

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
    if service not in KNOWN_SERVICES:
        return JSONResponse({"error": f"Unknown service: {service}"}, status_code=404)

    config = KNOWN_SERVICES[service]
    label = config.get("label", f"com.aos.{service}")

    # Try to kickstart the service via launchctl
    try:
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{_get_uid()}/{label}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        logger.exception("Failed to restart service %s", service)

    return _get_service_status(service, config)


@router.get("/{service}/logs", response_model=ServiceLogsResponse)
async def get_service_logs(
    request: Request,
    service: str = PathParam(..., description="Service name"),
    lines: int = Query(100, description="Number of log lines to return", ge=1, le=1000),
) -> ServiceLogsResponse | JSONResponse:
    """Tail recent log lines from a service."""
    if service not in KNOWN_SERVICES:
        return JSONResponse({"error": f"Unknown service: {service}"}, status_code=404)

    # Look for log files in standard locations
    log_paths = [
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
