"""Qareen API — Remote Access routes.

Thin HTTP layer over the TunnelManager (``app.state.tunnel_manager``). Lets the
Companion UI validate a scoped Cloudflare token, provision the secure
remote-access tunnel, poll status, and tear everything down.

Provisioning is long-running, so ``/connect`` kicks ``tunnel_manager.connect``
off as a fire-and-forget asyncio task and returns ``202`` immediately. Progress
streams to the frontend over the existing SSE channel as ``remote_access.progress``
events; the final state is read back via ``GET /status``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/remote-access", tags=["remote_access"])

# Strong references to in-flight background tasks. asyncio only keeps a weak
# reference to bare ``create_task`` results, so a long provisioning/teardown
# coroutine can be garbage-collected mid-flight. Retaining the Task here (and
# discarding it via a done-callback) guarantees it runs to completion.
_bg_tasks: set[asyncio.Task] = set()


def _retain(coro) -> asyncio.Task:
    """Schedule ``coro`` as a background task that cannot be GC'd mid-flight."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


def _tm(request: Request):
    """Return the TunnelManager from app.state, or raise 503 if unavailable."""
    tm = getattr(request.app.state, "tunnel_manager", None)
    if tm is None:
        raise HTTPException(status_code=503, detail="Remote access service not available")
    return tm


@router.post("/validate-token")
async def validate_token(request: Request) -> JSONResponse:
    """Validate a scoped Cloudflare token: verify it, list zones, infer scopes.

    Synchronous — returns ``{"ok": ..., "account_id", "zones", "missing_scopes"}``.
    """
    tm = _tm(request)
    body = await request.json()
    token = (body or {}).get("token")
    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)

    try:
        result = await tm.validate_token(token)
    except Exception as e:
        logger.exception("Remote access token validation failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    return JSONResponse(result)


@router.post("/connect")
async def connect(request: Request) -> JSONResponse:
    """Kick off tunnel provisioning as a background task.

    Returns ``202 {"started": true}`` immediately; progress flows over SSE
    (``remote_access.progress``) and the final state via ``GET /status``. A
    second connect while one is already ``provisioning`` or ``connected`` is
    rejected with ``409`` so a double-submit can't race two provisioning flows.
    """
    tm = _tm(request)
    body = await request.json() or {}

    token = body.get("token")
    hostname = body.get("hostname")
    if not token or not hostname:
        return JSONResponse({"error": "token and hostname are required"}, status_code=400)

    # Reject re-entry before scheduling a second task. The status check is read
    # synchronously here (not inside the background task) so a concurrent POST
    # is rejected even before the first task has run its provisioning upsert.
    current_status = tm.state.get().get("status")
    if current_status in {"provisioning", "connected"}:
        return JSONResponse(
            {"error": f"remote access is already {current_status}", "status": current_status},
            status_code=409,
        )

    domain = body.get("domain")
    zone_id = body.get("zone_id")
    account_id = body.get("account_id")
    emails = body.get("allowed_emails") or []

    async def _provision_bg() -> None:
        try:
            await tm.connect(
                token=token,
                domain=domain,
                hostname=hostname,
                zone_id=zone_id,
                account_id=account_id,
                emails=emails,
            )
        except Exception:
            # TunnelManager.connect emits its own error progress + state; this
            # guard just keeps the fire-and-forget task from going unhandled.
            logger.exception("Remote access provisioning failed")

    _retain(_provision_bg())
    return JSONResponse({"started": True}, status_code=202)


@router.get("/status")
async def status(request: Request) -> JSONResponse:
    """Return the current remote-access state plus connector health."""
    tm = _tm(request)
    try:
        result = await tm.status()
    except Exception as e:
        logger.exception("Remote access status check failed")
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse(result)


@router.post("/disconnect")
async def disconnect(request: Request) -> JSONResponse:
    """Kick off tunnel teardown as a background task.

    Teardown rebinds Qareen and ``launchctl kickstart -k``'s this very process,
    so awaiting it inline would SIGKILL the server mid-request and truncate the
    HTTP response. Instead this is fire-and-forget: it schedules
    ``tunnel_manager.disconnect`` as a retained background task and returns
    ``202 {"status": "disconnecting"}`` immediately. The UI re-polls
    ``GET /status`` to confirm the final ``disconnected`` state.
    """
    tm = _tm(request)

    async def _disconnect_bg() -> None:
        try:
            await tm.disconnect()
        except Exception:
            # TunnelManager.disconnect emits its own error progress + state;
            # this guard keeps the fire-and-forget task from going unhandled.
            logger.exception("Remote access disconnect failed")

    _retain(_disconnect_bg())
    return JSONResponse({"status": "disconnecting"}, status_code=202)
