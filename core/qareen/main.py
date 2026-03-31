"""Qareen — AI-powered operating system.

Single FastAPI process serving:
- API routes (all management + data endpoints)
- SSE stream (real-time events to browser)
- WebSocket (audio streaming from browser)
- Static files (pre-built Vite+React frontend)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

AOS_ROOT = Path.home() / "aos"
AOS_DATA = Path.home() / ".aos"
VAULT_DIR = Path.home() / "vault"
VERSION_FILE = AOS_ROOT / "VERSION"

SCREEN_DIST = Path(__file__).parent / "screen" / "dist"


def _read_version() -> str:
    """Read the AOS version from ~/aos/VERSION."""
    try:
        return VERSION_FILE.read_text().strip()
    except FileNotFoundError:
        return "dev"


# ---------------------------------------------------------------------------
# Lifespan — wire the runtime
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown.

    Startup:
        1. Create Ontology instance (semantic layer over AOS data)
        2. Create EventBus instance (async pub/sub)
        3. Create AuditLog instance (append-only action log)
        4. Create ActionRegistry instance (governed mutations)
        5. Wire them together (ontology.wire_events(bus))
        6. Wire SSE manager to the bus
        7. Store everything in app.state for route access

    Shutdown:
        1. Clean up event bus
        2. Log shutdown
    """
    logger.info("Qareen starting up — version %s", _read_version())

    # -- Core instances ---------------------------------------------------

    # Ontology
    try:
        from qareen.ontology.model import Ontology

        ontology = Ontology(
            config_dir=str(AOS_DATA / "config"),
            data_dir=str(AOS_DATA / "data"),
            vault_dir=str(VAULT_DIR),
        )
    except Exception:
        logger.exception("Failed to create Ontology — running degraded")
        ontology = None

    # EventBus
    try:
        from qareen.events.bus import EventBus

        bus = EventBus()
    except Exception:
        logger.exception("Failed to create EventBus — running degraded")
        bus = None

    # AuditLog
    try:
        from qareen.events.audit import AuditLog

        audit_log = AuditLog(
            db_path=str(AOS_DATA / "data" / "actions.db"),
        )
    except Exception:
        logger.exception("Failed to create AuditLog — running degraded")
        audit_log = None

    # ActionRegistry
    try:
        from qareen.events.actions import ActionRegistry

        if bus and audit_log:
            action_registry = ActionRegistry(bus=bus, audit_log=audit_log)
        else:
            action_registry = None
            logger.warning("ActionRegistry skipped — bus or audit_log unavailable")
    except Exception:
        logger.exception("Failed to create ActionRegistry — running degraded")
        action_registry = None

    # -- Wiring -----------------------------------------------------------

    if ontology and bus:
        try:
            ontology.wire_events(bus)
            logger.info("Ontology wired to EventBus")
        except Exception:
            logger.exception("Failed to wire ontology to bus")

    # Wire SSE manager
    if bus:
        try:
            from qareen.sse import sse_manager

            sse_manager.wire(bus)
            logger.info("SSE manager wired to EventBus")
        except Exception:
            logger.exception("Failed to wire SSE manager")

    # Voice manager
    try:
        from qareen.voice.manager import VoiceManager

        voice_manager = VoiceManager(bus=bus)
        app.state.voice_manager = voice_manager
        logger.info("Voice manager initialized (engine=%s)", voice_manager._stt_engine)
    except Exception:
        logger.exception("Failed to create VoiceManager")
        app.state.voice_manager = None

    # -- Register adapters -----------------------------------------------

    if ontology:
        try:
            from qareen.ontology.adapters.work import WorkAdapter
            from qareen.ontology.types import ObjectType

            work_adapter = WorkAdapter(db_path=str(AOS_DATA / "data" / "qareen.db"))
            ontology.register_adapter(ObjectType.TASK, work_adapter)
            ontology.register_adapter(ObjectType.PROJECT, work_adapter)
            ontology.register_adapter(ObjectType.GOAL, work_adapter)
            logger.info("Work adapter registered (TASK, PROJECT, GOAL)")
        except Exception:
            logger.exception("Failed to register work adapter")

        try:
            from qareen.ontology.adapters.people import PeopleAdapter
            from qareen.ontology.types import ObjectType

            people_adapter = PeopleAdapter(
                people_db_path=str(AOS_DATA / "data" / "people.db"),
                qareen_db_path=str(AOS_DATA / "data" / "qareen.db"),
            )
            ontology.register_adapter(ObjectType.PERSON, people_adapter)
            logger.info("People adapter registered (PERSON)")
        except Exception:
            logger.exception("Failed to register people adapter")

        try:
            from qareen.ontology.adapters.vault import VaultAdapter
            from qareen.ontology.types import ObjectType

            vault_adapter = VaultAdapter(
                vault_dir=str(VAULT_DIR),
                qareen_db_path=str(AOS_DATA / "data" / "qareen.db"),
            )
            ontology.register_adapter(ObjectType.NOTE, vault_adapter)
            ontology.register_adapter(ObjectType.DECISION, vault_adapter)
            logger.info("Vault adapter registered (NOTE, DECISION)")
        except Exception:
            logger.exception("Failed to register vault adapter")

    # -- Initialize audit log --------------------------------------------

    if audit_log:
        try:
            await audit_log.initialize()
            logger.info("Audit log initialized")
        except Exception:
            logger.exception("Failed to initialize audit log")

    # -- Register core actions -------------------------------------------

    if action_registry and ontology:
        try:
            from qareen.actions.work import (
                complete_task,
                create_goal,
                create_inbox,
                create_project,
                create_task,
                delete_inbox,
                delete_project,
                delete_task,
                update_task,
                write_handoff,
            )

            action_registry.register(create_task)
            action_registry.register(update_task)
            action_registry.register(complete_task)
            action_registry.register(delete_task)
            action_registry.register(write_handoff)
            action_registry.register(create_project)
            action_registry.register(delete_project)
            action_registry.register(create_goal)
            action_registry.register(create_inbox)
            action_registry.register(delete_inbox)
            logger.info("Core actions registered (%d)", len(action_registry.list_actions()))
        except Exception:
            logger.exception("Failed to register core actions")

    # -- Store in app.state for route access ------------------------------

    app.state.ontology = ontology
    app.state.bus = bus
    app.state.audit_log = audit_log
    app.state.action_registry = action_registry
    app.state.version = _read_version()

    logger.info("Qareen startup complete")

    yield

    # -- Shutdown ---------------------------------------------------------

    logger.info("Qareen shutting down")

    if bus:
        bus.clear()
        logger.info("EventBus cleared")

    logger.info("Qareen shutdown complete")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Qareen",
    version=_read_version(),
    description="AOS intelligence core — the AI layer of the Agentic Operating System.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "http://localhost:3000",   # Legacy / alternative dev
        "tauri://localhost",       # Tauri desktop app
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Core inline routes (always available, no dependencies)
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Health check — always responds, even if subsystems are degraded.

    Returns component status for each runtime subsystem.
    """
    voice_mgr = getattr(app.state, "voice_manager", None)
    return {
        "status": "ok",
        "version": _read_version(),
        "components": {
            "ontology": getattr(app.state, "ontology", None) is not None,
            "bus": getattr(app.state, "bus", None) is not None,
            "audit_log": getattr(app.state, "audit_log", None) is not None,
            "action_registry": getattr(app.state, "action_registry", None) is not None,
            "voice_manager": voice_mgr is not None,
            "voice_stt": voice_mgr._stt_engine if voice_mgr else None,
        },
    }


@app.get("/api/version")
async def version() -> dict[str, str]:
    """Return the AOS version."""
    return {"version": _read_version()}


# ---------------------------------------------------------------------------
# Include API routers (graceful — missing routers don't crash the app)
# ---------------------------------------------------------------------------

# SSE stream
try:
    from qareen.sse import router as sse_router

    app.include_router(sse_router)
except ImportError:
    logger.warning("SSE router not available")

# Voice WebSocket
try:
    from qareen.voice.websocket import register as register_voice_ws

    register_voice_ws(app)
except ImportError:
    logger.warning("Voice WebSocket router not available")

# API route modules — each is optional
_api_routers = [
    ("qareen.api.work", "work"),
    ("qareen.api.config", "config"),
    ("qareen.api.agents", "agents"),
    ("qareen.api.services", "services"),
    ("qareen.api.people", "people"),
    ("qareen.api.vault", "vault"),
    ("qareen.api.system", "system"),
    ("qareen.api.channels", "channels"),
    ("qareen.api.metrics", "metrics"),
    ("qareen.api.pipelines", "pipelines"),
    ("qareen.api.companion", "companion"),
]

for module_path, name in _api_routers:
    try:
        import importlib

        mod = importlib.import_module(module_path)
        router = getattr(mod, "router", None)
        if router:
            app.include_router(router)
            logger.info("Loaded API router: %s", name)
        else:
            logger.debug("Module %s has no router attribute", module_path)
    except ImportError:
        logger.debug("API router not yet built: %s", name)
    except Exception:
        logger.exception("Failed to load API router: %s", name)


# ---------------------------------------------------------------------------
# Static files (Vite+React SPA)
# ---------------------------------------------------------------------------

if SCREEN_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(SCREEN_DIST / "assets")),
        name="static-assets",
    )

    @app.get("/")
    async def serve_index() -> FileResponse:
        """Serve the SPA index.html."""
        return FileResponse(str(SCREEN_DIST / "index.html"))

    @app.get("/{path:path}")
    async def spa_fallback(path: str, request: Request) -> FileResponse:
        """SPA fallback — serve index.html for all non-API routes.

        Excludes /api, /ws, /companion paths which are handled by their routers.
        """
        # Never intercept WebSocket, API, or companion paths
        if path.startswith(("ws/", "api/", "companion/")):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "not found"}, status_code=404)

        # If the file exists in dist, serve it directly
        file_path = SCREEN_DIST / path
        if file_path.is_file():
            return FileResponse(str(file_path))
        # Otherwise, serve index.html for SPA routing
        return FileResponse(str(SCREEN_DIST / "index.html"))

else:
    @app.get("/")
    async def no_frontend() -> dict[str, str]:
        """No frontend built yet — return a helpful message."""
        return {
            "message": "Qareen API is running. No frontend built yet.",
            "hint": "cd screen && npm run build",
            "health": "/api/health",
            "version": _read_version(),
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "qareen.main:app",
        host="0.0.0.0",
        port=4096,
        reload=True,
        log_level="info",
    )
