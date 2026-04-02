"""Qareen — AI-powered operating system.

Single FastAPI process serving:
- API routes (all management + data endpoints)
- SSE stream (real-time events to browser)
- WebSocket (audio streaming from browser)
- Static files (pre-built Vite+React frontend)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
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

    # Session manager + Intelligence engine
    intelligence_engine = None
    try:
        from qareen.intelligence.session import SessionManager
        from qareen.intelligence.engine import CompanionIntelligenceEngine
        from qareen.api.companion import _push_companion_event

        session_manager = SessionManager(db_path=str(AOS_DATA / "data" / "qareen.db"))

        intelligence_engine = CompanionIntelligenceEngine(
            session_manager=session_manager,
            ontology=ontology,
            bus=bus,
            push_event=_push_companion_event,
        )

        app.state.session_manager = session_manager
        app.state.intelligence_engine = intelligence_engine
        logger.info("Intelligence engine initialized")
    except Exception:
        logger.exception("Failed to create intelligence engine")
        app.state.session_manager = None
        app.state.intelligence_engine = None

    # Wire companion stream to receive voice events from bus.
    # Must happen AFTER intelligence engine so transcripts feed into AI processing.
    if bus:
        try:
            from qareen.api.companion import wire_companion_to_bus

            wire_companion_to_bus(bus, intelligence_engine=intelligence_engine)
        except Exception:
            logger.exception(
                "Failed to wire companion to bus — companion SSE may be degraded"
            )

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
            logger.info("Core work actions registered (%d)", len(action_registry.list_actions()))
        except Exception:
            logger.exception("Failed to register core actions")

        try:
            from qareen.actions.messaging import send_message

            action_registry.register(send_message)
            logger.info("Messaging action registered")
        except Exception:
            logger.exception("Failed to register messaging action")

    # -- Pipeline engine ----------------------------------------------------

    pipeline_engine = None
    if ontology and bus:
        try:
            from qareen.pipelines.engine import PipelineEngine

            pipeline_engine = PipelineEngine(ontology=ontology, event_bus=bus)

            # -- Real pipeline action handlers ---------------------------------

            async def _update_project_stats(
                event=None, params=None, outputs=None,
            ) -> dict[str, Any]:
                """Query task counts for the project from the completed task event."""
                try:
                    project = getattr(event, "project", None) or (
                        event.payload.get("project") if hasattr(event, "payload") else None
                    )
                    if project and ontology:
                        from qareen.ontology.types import ObjectType

                        tasks = ontology.list(
                            ObjectType.TASK,
                            filters={"_type": "task", "project": project},
                            limit=200,
                        )
                        total = len(tasks)
                        done = sum(
                            1 for t in tasks
                            if getattr(t, "status", "") == "done"
                        )
                        active = sum(
                            1 for t in tasks
                            if getattr(t, "status", "") == "active"
                        )
                        logger.info(
                            "Project stats updated: %s — %d total, %d done, %d active",
                            project, total, done, active,
                        )
                        return {
                            "action": "update_project_stats",
                            "status": "completed",
                            "project": project,
                            "total": total,
                            "done": done,
                            "active": active,
                        }
                except Exception as e:
                    logger.debug("update_project_stats failed: %s", e)

                logger.info("Updating project stats for completed task")
                return {"action": "update_project_stats", "status": "completed"}

            async def _check_goal_progress(
                event=None, params=None, outputs=None,
            ) -> dict[str, Any]:
                """Check if any goals have progressed based on task completion."""
                try:
                    if ontology:
                        from qareen.ontology.types import ObjectType

                        goals = ontology.list(
                            ObjectType.GOAL,
                            filters={"_type": "goal"},
                            limit=20,
                        )
                        active_goals = [
                            g for g in goals
                            if getattr(g, "status", "") in ("active", "in_progress")
                        ]
                        logger.info(
                            "Goal progress check: %d active goals",
                            len(active_goals),
                        )
                        return {
                            "action": "check_goal_progress",
                            "status": "completed",
                            "active_goals": len(active_goals),
                        }
                except Exception as e:
                    logger.debug("check_goal_progress failed: %s", e)

                return {"action": "check_goal_progress", "status": "completed"}

            async def _notify(
                event=None, params=None, outputs=None,
            ) -> dict[str, Any]:
                """Push a notification event to the bus for the companion stream."""
                try:
                    channel = (params or {}).get("channel", "operator")
                    event_type = getattr(event, "event_type", "unknown")
                    title = ""
                    if hasattr(event, "title"):
                        title = event.title
                    elif hasattr(event, "payload"):
                        title = event.payload.get("title", "")

                    notification = {
                        "id": str(id(event))[:8],
                        "source": "pipeline",
                        "message": f"Pipeline completed: {title or event_type}",
                        "channel": channel,
                        "timestamp": datetime.now().isoformat(),
                    }

                    if bus:
                        from qareen.events.types import Event as BusEvent

                        await bus.emit(BusEvent(
                            event_type="notification",
                            source="pipeline",
                            payload=notification,
                        ))

                    # Also push to companion SSE stream directly
                    try:
                        from qareen.api.companion import _push_companion_event

                        await _push_companion_event("activity", notification)
                    except Exception:
                        pass

                    logger.info("Notification sent: %s", notification["message"])
                except Exception as e:
                    logger.debug("notify action failed: %s", e)

                return {"action": "notify", "status": "completed"}

            async def _classify_intent(
                event=None, params=None, outputs=None,
            ) -> dict[str, Any]:
                """Classify the intent of an inbound message."""
                try:
                    from qareen.intelligence.classifier import classify

                    text = ""
                    if hasattr(event, "content_preview"):
                        text = event.content_preview
                    elif hasattr(event, "payload"):
                        text = event.payload.get(
                            "text", event.payload.get("content_preview", ""),
                        )

                    if text:
                        result = classify(text)
                        logger.info(
                            "Pipeline classify: intent=%s confidence=%.1f",
                            result.intent.value, result.confidence,
                        )
                        return {
                            "action": "classify_intent",
                            "status": "completed",
                            "intent": result.intent.value,
                            "confidence": result.confidence,
                        }
                except Exception as e:
                    logger.debug("classify_intent failed: %s", e)

                return {"action": "classify_intent", "status": "completed"}

            async def _extract_entities(
                event=None, params=None, outputs=None,
            ) -> dict[str, Any]:
                """Extract entities from the classified message."""
                classify_output = (outputs or {}).get("classify")
                logger.info(
                    "Entity extraction (classify output: %s)",
                    classify_output.get("intent") if classify_output else "none",
                )
                return {"action": "extract_entities", "status": "completed"}

            async def _route_message(
                event=None, params=None, outputs=None,
            ) -> dict[str, Any]:
                """Route a classified message to the appropriate handler."""
                classify_output = (outputs or {}).get("classify")
                intent = (
                    classify_output.get("intent", "unknown")
                    if classify_output
                    else "unknown"
                )
                logger.info("Message routing: intent=%s", intent)
                return {
                    "action": "route_message",
                    "status": "completed",
                    "routed_to": intent,
                }

            pipeline_engine.register_action("update_project_stats", _update_project_stats)
            pipeline_engine.register_action("check_goal_progress", _check_goal_progress)
            pipeline_engine.register_action("notify", _notify)
            pipeline_engine.register_action("classify_intent", _classify_intent)
            pipeline_engine.register_action("extract_entities", _extract_entities)
            pipeline_engine.register_action("route_message", _route_message)

            # Load definitions
            definitions_dir = str(
                Path(__file__).parent / "pipelines" / "definitions"
            )
            await pipeline_engine.load_definitions(definitions_dir)
            logger.info("Pipeline engine initialized")
        except Exception:
            logger.exception("Failed to initialize pipeline engine")
            pipeline_engine = None

    # -- Bridge listener (inbound message capture) ---------------------------

    bridge_listener_task = None
    if bus:
        try:
            from qareen.channels.bridge_listener import start_bridge_listener

            bridge_listener_task = await start_bridge_listener(bus)
        except Exception:
            logger.exception("Failed to start bridge listener")

    # -- Store in app.state for route access ------------------------------

    app.state.ontology = ontology
    app.state.bus = bus
    app.state.audit_log = audit_log
    app.state.action_registry = action_registry
    app.state.pipeline_engine = pipeline_engine
    app.state.version = _read_version()

    logger.info("Qareen startup complete")

    yield

    # -- Shutdown ---------------------------------------------------------

    logger.info("Qareen shutting down")

    if bridge_listener_task and not bridge_listener_task.done():
        bridge_listener_task.cancel()
        try:
            await bridge_listener_task
        except asyncio.CancelledError:
            pass
        logger.info("Bridge listener stopped")

    # Close adapter connections
    if ontology:
        try:
            ontology.close()
        except Exception:
            logger.debug("Adapter close failed", exc_info=True)
        logger.info("Adapter connections closed")

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

    Returns component status for each runtime subsystem plus system metrics.
    """
    import shutil

    voice_mgr = getattr(app.state, "voice_manager", None)
    pipe_engine = getattr(app.state, "pipeline_engine", None)

    # Disk metrics (root volume)
    try:
        disk = shutil.disk_usage("/")
        disk_pct = round((disk.used / disk.total) * 100, 1)
        disk_free_gb = round(disk.free / (1024**3), 1)
    except OSError:
        disk_pct = 0
        disk_free_gb = 0

    # RAM metrics (macOS-native, no psutil dependency)
    try:
        import re as _re
        import subprocess as _sp
        total_mem = int(_sp.check_output(["sysctl", "-n", "hw.memsize"]).strip())
        vm_out = _sp.check_output(["vm_stat"]).decode()
        # Extract page size from "page size of NNNN bytes"
        ps_match = _re.search(r"page size of (\d+)", vm_out)
        page_size = int(ps_match.group(1)) if ps_match else 16384
        def _parse_vm(label: str) -> int:
            for line in vm_out.splitlines():
                if line.startswith(label):
                    return int(line.split(":")[1].strip().rstrip(".")) * page_size
            return 0
        free = _parse_vm("Pages free")
        inactive = _parse_vm("Pages inactive")
        speculative = _parse_vm("Pages speculative")
        available = free + inactive + speculative
        used = total_mem - available
        ram_pct = round((used / total_mem) * 100, 1)
        ram_used_gb = round(used / (1024**3), 1)
    except Exception:
        ram_pct = 0
        ram_used_gb = 0

    return {
        "status": "ok",
        "version": _read_version(),
        "disk_pct": disk_pct,
        "disk_free_gb": disk_free_gb,
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used_gb,
        "components": {
            "ontology": getattr(app.state, "ontology", None) is not None,
            "bus": getattr(app.state, "bus", None) is not None,
            "audit_log": getattr(app.state, "audit_log", None) is not None,
            "action_registry": getattr(app.state, "action_registry", None) is not None,
            "voice_manager": voice_mgr is not None,
            "voice_stt": voice_mgr._stt_engine if voice_mgr else None,
            "voice_chunks": voice_mgr._chunk_count if voice_mgr else 0,
            "voice_speaking": voice_mgr._is_speaking if voice_mgr else False,
            "pipeline_engine": pipe_engine is not None,
            "pipeline_definitions": len(pipe_engine._definitions) if pipe_engine else 0,
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
    from qareen.voice.websocket import router as voice_ws_router

    app.include_router(voice_ws_router)
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
    ("qareen.api.meetings", "meetings"),
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
