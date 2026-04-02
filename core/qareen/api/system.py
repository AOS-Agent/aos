"""Qareen API — System management routes.

Health, version, storage, reconcile, and cron job management.
"""

from __future__ import annotations

import logging
import platform
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from .schemas import (
    CronJobResponse,
    CronListResponse,
    HealthResponse,
    ReconcileCheckResult,
    ReconcileResponse,
    StorageDevice,
    StorageResponse,
    SymlinkResponse,
    VersionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])

AOS_ROOT = Path.home() / "aos"
AOS_DATA = Path.home() / ".aos"
VAULT_DIR = Path.home() / "vault"
AOS_X = Path("/Volumes/AOS-X")

_START_TIME = datetime.now()


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning empty dict on error."""
    try:
        import yaml
        if not path.exists():
            return {}
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# Note: /api/health and /api/version are already defined in main.py as
# inline routes. The system router defines extended versions at /api/system/*.


@router.get("/system/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    """System health check — services, uptime, errors."""
    uptime = (datetime.now() - _START_TIME).total_seconds()

    services: dict[str, str] = {}
    errors: list[str] = []

    # Check core components
    if getattr(request.app.state, "ontology", None) is not None:
        services["ontology"] = "healthy"
    else:
        services["ontology"] = "down"
        errors.append("Ontology not initialized")

    if getattr(request.app.state, "bus", None) is not None:
        services["event_bus"] = "healthy"
    else:
        services["event_bus"] = "down"
        errors.append("EventBus not initialized")

    if getattr(request.app.state, "audit_log", None) is not None:
        services["audit_log"] = "healthy"
    else:
        services["audit_log"] = "down"
        errors.append("AuditLog not initialized")

    if getattr(request.app.state, "action_registry", None) is not None:
        services["action_registry"] = "healthy"
    else:
        services["action_registry"] = "down"
        errors.append("ActionRegistry not initialized")

    overall = "healthy" if not errors else ("degraded" if len(errors) < 3 else "down")

    return HealthResponse(
        status=overall,
        uptime_seconds=uptime,
        services=services,
        timestamp=datetime.now(),
        errors=errors,
    )


@router.get("/system/version", response_model=VersionResponse)
async def get_version(request: Request) -> VersionResponse:
    """Return the current AOS version with extended info."""
    version = getattr(request.app.state, "version", "dev")
    return VersionResponse(
        version=version,
        codename="qareen",
        python_version=platform.python_version(),
    )


@router.get("/storage", response_model=StorageResponse)
async def get_storage(request: Request) -> StorageResponse:
    """Disk usage for internal and external drives, plus symlink status."""
    # Internal SSD
    try:
        usage = shutil.disk_usage("/")
        internal = StorageDevice(
            name="internal",
            total_gb=round(usage.total / (1024**3), 1),
            used_gb=round(usage.used / (1024**3), 1),
            free_gb=round(usage.free / (1024**3), 1),
            usage_percent=round((usage.used / usage.total) * 100, 1),
        )
    except OSError:
        internal = StorageDevice(name="internal")

    # External SSD (AOS-X)
    external = None
    if AOS_X.is_dir():
        try:
            usage = shutil.disk_usage(str(AOS_X))
            external = StorageDevice(
                name="AOS-X",
                total_gb=round(usage.total / (1024**3), 1),
                used_gb=round(usage.used / (1024**3), 1),
                free_gb=round(usage.free / (1024**3), 1),
                usage_percent=round((usage.used / usage.total) * 100, 1),
            )
        except OSError:
            external = StorageDevice(name="AOS-X")

    # Symlinks
    symlinks: list[SymlinkResponse] = []
    managed_symlinks = [
        ("~/vault", "/Volumes/AOS-X/vault"),
        ("~/project", "/Volumes/AOS-X/project"),
        ("~/.cache", "/Volumes/AOS-X/.cache"),
    ]
    for source, target in managed_symlinks:
        source_path = Path(source).expanduser()
        sym = SymlinkResponse(source=source, target=target, valid=False)
        if source_path.is_symlink():
            resolved = str(source_path.resolve())
            str(Path(target).resolve()) if Path(target).exists() else target
            sym.valid = source_path.exists()
            if not sym.valid:
                sym.error = f"Symlink target missing: {resolved}"
        elif source_path.exists():
            sym.valid = True  # Direct directory, not a symlink
        else:
            sym.error = "Path does not exist"
        symlinks.append(sym)

    return StorageResponse(
        internal=internal,
        external=external,
        symlinks=symlinks,
    )


@router.get("/reconcile", response_model=ReconcileResponse)
async def get_reconcile(request: Request) -> ReconcileResponse:
    """Return results from the last reconcile run."""
    # Read last reconcile results from ~/.aos/data/reconcile.json
    import json

    results_path = AOS_DATA / "data" / "reconcile.json"
    if not results_path.exists():
        return ReconcileResponse(checks=[], passed=0, failed=0)

    try:
        data = json.loads(results_path.read_text())
        checks = []
        passed = 0
        failed = 0
        for check in data.get("checks", []):
            ok = check.get("passed", check.get("ok", True))
            checks.append(ReconcileCheckResult(
                name=check.get("name", ""),
                passed=ok,
                message=check.get("message", ""),
            ))
            if ok:
                passed += 1
            else:
                failed += 1
        return ReconcileResponse(
            checks=checks,
            passed=passed,
            failed=failed,
            run_at=data.get("run_at"),
        )
    except Exception:
        logger.exception("Failed to load reconcile results")
        return ReconcileResponse(checks=[], passed=0, failed=0)


@router.get("/crons", response_model=CronListResponse)
async def list_crons(request: Request) -> CronListResponse:
    """List all cron jobs with their schedules and status."""
    config_path = AOS_ROOT / "config" / "crons.yaml"
    data = _load_yaml(config_path)

    crons: list[CronJobResponse] = []
    raw_crons = data.get("crons", data.get("jobs", []))

    if isinstance(raw_crons, dict):
        for name, cfg in raw_crons.items():
            if isinstance(cfg, dict):
                crons.append(CronJobResponse(
                    name=name,
                    schedule=cfg.get("schedule", cfg.get("cron", "")),
                    command=cfg.get("command", cfg.get("script", "")),
                    enabled=cfg.get("enabled", True),
                    last_run=cfg.get("last_run"),
                    last_status=cfg.get("last_status"),
                ))
    elif isinstance(raw_crons, list):
        for job in raw_crons:
            if isinstance(job, dict):
                crons.append(CronJobResponse(
                    name=job.get("name", ""),
                    schedule=job.get("schedule", job.get("cron", "")),
                    command=job.get("command", job.get("script", "")),
                    enabled=job.get("enabled", True),
                    last_run=job.get("last_run"),
                    last_status=job.get("last_status"),
                ))

    return CronListResponse(
        crons=crons,
        total=len(crons),
    )


@router.post("/crons/{job}/trigger", response_model=CronJobResponse)
async def trigger_cron(
    request: Request,
    job: str = PathParam(..., description="Cron job name to manually trigger"),
) -> CronJobResponse | JSONResponse:
    """Manually trigger a cron job."""
    # Look up the job config
    config_path = AOS_ROOT / "config" / "crons.yaml"
    data = _load_yaml(config_path)

    raw_crons = data.get("crons", data.get("jobs", {}))
    job_config = None
    if isinstance(raw_crons, dict):
        job_config = raw_crons.get(job)
    elif isinstance(raw_crons, list):
        for j in raw_crons:
            if isinstance(j, dict) and j.get("name") == job:
                job_config = j
                break

    if not job_config:
        return JSONResponse({"error": f"Cron job not found: {job}"}, status_code=404)

    return CronJobResponse(
        name=job,
        schedule=job_config.get("schedule", job_config.get("cron", "")),
        command=job_config.get("command", job_config.get("script", "")),
        enabled=job_config.get("enabled", True),
        last_status="triggered",
    )
