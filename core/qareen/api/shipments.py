"""Qareen API — Auto Tracker shipment routes (auto-tracker#3, #9-api, #16).

Implements the fixed Wave-2 API contract:

    GET    /api/shipments?status=&milestone=&category=&q=
    GET    /api/shipments/{id}
    POST   /api/shipments                      (manual add / paste box)
    PATCH  /api/shipments/{id}                 (label / category / status)
    GET    /api/shipments/candidates
    POST   /api/shipments/candidates/{id}/confirm | /reject
    GET    /api/shipments/domain-rules
    POST   /api/shipments/domain-rules
    GET    /api/shipments/eval
    POST   /api/shipments/eval/{id}/label
    POST   /api/shipments/webhook/ups/rotate-secret
    POST   /api/shipments/webhook/ups/{secret}

Storage goes through ``qareen.tracking.store.ShipmentStore`` for every
read and mutation it owns — filtered lists, summary counts, candidate
reads, eval reads/writes all live on the store (folded in during wave-2
integration).

UPS Track Alert webhook security:
- 128-bit hex secret generated once and persisted in tracking_state under
  ``ups_webhook_secret``; a wrong secret returns 404 (existence is never
  confirmed), compared with hmac.compare_digest.
- Payloads are validated against a strict allowlist schema — unknown
  fields, wrong types, or over-long strings are rejected with 400.
- Payload strings are untrusted: never logged raw, never interpolated
  into prompts. Logs carry only the (pattern-validated) tracking number
  and event counts.
- Polling remains the source of truth: any internal webhook failure is
  logged and answered 202 so UPS retries never wedge the pipeline.

Python 3.9-compatible.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from qareen.tracking import engine, onboard
from qareen.tracking.config import TrackingConfig, action_for
from qareen.tracking.detect import detect, sender_domain
from qareen.tracking.models import Shipment, TrackingEvent
from qareen.tracking.store import ShipmentStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/shipments", tags=["shipments"])

UPS_WEBHOOK_SECRET_KEY = "ups_webhook_secret"
UPS_WEBHOOK_PATH = "/api/shipments/webhook/ups"

_SHIPMENT_STATUSES = ("active", "delivered", "expired", "archived")
_EVAL_LABELS = ("correct", "incorrect", "missed")
_LIST_LIMIT = 200

# Strict UPS Track Alert payload allowlist. The pack's Track Alert shape is
# doc-derived (canary), so the schema is deliberately tight: anything the
# docs don't promise is rejected rather than trusted.
_UPS_TOP_FIELDS = frozenset({"trackingNumber", "deliveryDate", "events"})
_UPS_EVENT_FIELDS = frozenset({
    "statusCode", "statusDescription", "date", "time",
    "gmtDate", "gmtTime", "city", "signedForByName",
})
_UPS_MAX_STRING = 500
_UPS_MAX_EVENTS = 100


# ---------------------------------------------------------------------------
# Injectable singletons (tests monkeypatch these)
# ---------------------------------------------------------------------------

_store: Optional[ShipmentStore] = None
_packs: Optional[Dict[str, Any]] = None
_config: Optional[TrackingConfig] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_store() -> ShipmentStore:
    global _store
    if _store is None:
        _store = ShipmentStore()
    return _store


def _get_packs() -> Dict[str, Any]:
    """Lifecycle-filtered packs for detection: state ``active`` only
    (canary packs stay silent; scaffolded are inert)."""
    global _packs
    if _packs is None:
        _packs = onboard.detection_packs(_get_store())
    return _packs


def _get_config() -> TrackingConfig:
    global _config
    if _config is None:
        _config = TrackingConfig.load()
    return _config


# ---------------------------------------------------------------------------
# SSE / EventBus
# ---------------------------------------------------------------------------

async def _emit(request: Request, event_type: str, payload: Dict[str, Any]) -> None:
    """Publish on the qareen EventBus; SSE clients already subscribe.

    Fire-and-forget: a missing/dead bus never breaks an API call."""
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return
    try:
        from qareen.events.types import Event

        await bus.emit(Event(event_type=event_type, source="tracking", payload=payload))
    except Exception:
        logger.debug("bus emit failed for %s", event_type)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _shipment_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """A shipments table row as the API shape (already JSON-safe strings)."""
    return dict(row)


def _event_dict(event: TrackingEvent) -> Dict[str, Any]:
    return {
        "seq": event.seq,
        "milestone": event.milestone.value if event.milestone else None,
        "description": event.description,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "fetched_at": event.fetched_at.isoformat() if event.fetched_at else None,
        "location": event.location,
        "carrier_code": event.carrier_code,
    }


def _candidate_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    payload = out.pop("candidate_json", None)
    if "candidate" not in out:
        try:
            out["candidate"] = json.loads(payload) if payload else None
        except (ValueError, TypeError):
            out["candidate"] = payload
    return out


# ---------------------------------------------------------------------------
# Shipments — list / detail / create / patch
# ---------------------------------------------------------------------------

@router.get("")
async def list_shipments(
    status: Optional[str] = None,
    milestone: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
) -> JSONResponse:
    store = _get_store()
    rows = store.list_shipments(
        status=status, milestone=milestone, category=category, q=q,
        limit=_LIST_LIMIT,
    )
    return JSONResponse({
        "shipments": [_shipment_dict(r) for r in rows],
        "summary": store.shipment_summary(),
    })


def _create_shipment_from_number(
    store: ShipmentStore,
    number: str,
    carrier: str,
    *,
    source: str = "manual",
    confidence: float = 1.0,
    merchant_domain: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a shipment for a validated (carrier, number); returns the row."""
    shipment = Shipment(
        tracking_number=number,
        carrier=carrier,
        source=source,
        confidence=confidence,
        merchant_domain=merchant_domain,
        first_seen=_utcnow(),
    )
    shipment_id, _created = store.upsert_shipment(
        shipment, next_poll_at=_utcnow()
    )
    row = store.get_shipment_row(shipment_id)
    return _shipment_dict(row) if row else {"id": shipment_id}


@router.post("", status_code=201)
async def add_shipment(request: Request) -> JSONResponse:
    """Manual add / paste box.

    ``{tracking_number, carrier?}`` — carrier omitted → infer from pack
    validation; ambiguous → approval-queue candidate.
    ``{text}`` — runs the full detection pipeline over the pasted text.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON object expected"}, status_code=400)

    store = _get_store()
    packs = _get_packs()
    config = _get_config()

    number = body.get("tracking_number")
    carrier = body.get("carrier")
    text = body.get("text")

    if number:
        try:
            canonical = engine.canonicalize(str(number))
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid tracking_number"}, status_code=400)
        if carrier:
            pack = packs.get(str(carrier))
            if pack is None:
                return JSONResponse(
                    {"error": "unknown carrier: %s" % carrier}, status_code=400
                )
            if not engine.validate_number(pack, canonical):
                return JSONResponse(
                    {"error": "tracking_number failed validation for carrier"},
                    status_code=400,
                )
            row = _create_shipment_from_number(store, canonical, pack.slug)
            await _emit(request, "shipment.updated", {"shipment_id": row.get("id")})
            return JSONResponse({"shipment": row}, status_code=201)

        matches = [
            slug for slug, pack in packs.items()
            if engine.validate_number(pack, canonical)
        ]
        if len(matches) == 1:
            row = _create_shipment_from_number(store, canonical, matches[0])
            await _emit(request, "shipment.updated", {"shipment_id": row.get("id")})
            return JSONResponse({"shipment": row}, status_code=201)
        if len(matches) > 1:
            # Ambiguous number — human picks the carrier.
            candidate = {
                "tracking_number": canonical,
                "carrier": None,
                "candidate_carriers": matches,
                "confidence": 0.0,
                "layer": "manual",
                "source": {"channel": "manual"},
                "sources": [{"channel": "manual"}],
                "context": {"ambiguous": True},
            }
            candidate_id = store.enqueue_candidate(candidate, layer="manual", confidence=0.0)
            await _emit(request, "shipment.candidate", {"candidate_id": candidate_id})
            return JSONResponse(
                {"candidate": {"id": candidate_id, **candidate}}, status_code=202
            )
        return JSONResponse(
            {"error": "tracking_number matches no carrier pack"}, status_code=400
        )

    if text:
        message = {
            "text": str(text),
            "sender": "",
            "channel": "manual",
            "from_me": False,
        }
        result = detect(message, packs, store=store, config=config)
        first_shipment = None
        first_candidate = None
        for cand in result.candidates:
            action = action_for(cand.confidence, config)
            if action == "auto_add":
                row = _create_shipment_from_number(
                    store,
                    cand.tracking_number,
                    cand.carrier,
                    source="manual",
                    confidence=cand.confidence,
                    merchant_domain=cand.context.get("sender_domain"),
                )
                await _emit(request, "shipment.updated", {"shipment_id": row.get("id")})
                if first_shipment is None:
                    first_shipment = row
            elif action == "queue":
                candidate_id = store.enqueue_candidate(
                    cand.to_dict(), layer=cand.layer, confidence=cand.confidence
                )
                await _emit(request, "shipment.candidate", {"candidate_id": candidate_id})
                if first_candidate is None:
                    first_candidate = {"id": candidate_id, **cand.to_dict()}
        if first_shipment is not None:
            return JSONResponse({"shipment": first_shipment}, status_code=201)
        if first_candidate is not None:
            return JSONResponse({"candidate": first_candidate}, status_code=202)
        return JSONResponse(
            {"error": "no tracking number found in text"}, status_code=404
        )

    return JSONResponse(
        {"error": "provide 'tracking_number' or 'text'"}, status_code=400
    )


@router.patch("/{shipment_id}")
async def patch_shipment(shipment_id: str, request: Request) -> JSONResponse:
    """User edits: label, category, status (archive via status='archived')."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON object expected"}, status_code=400)

    allowed = ("label", "category", "status")
    fields = {k: body[k] for k in allowed if k in body}
    if not fields:
        return JSONResponse(
            {"error": "nothing to update (allowed: %s)" % ", ".join(allowed)},
            status_code=400,
        )
    if "status" in fields and fields["status"] not in _SHIPMENT_STATUSES:
        return JSONResponse(
            {"error": "status must be one of %s" % ", ".join(_SHIPMENT_STATUSES)},
            status_code=400,
        )

    store = _get_store()
    try:
        ok = store.update_shipment(shipment_id, **fields)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not ok:
        return JSONResponse({"error": "shipment not found"}, status_code=404)
    row = store.get_shipment_row(shipment_id)
    await _emit(
        request,
        "shipment.updated",
        {"shipment_id": shipment_id, "fields": sorted(fields)},
    )
    return JSONResponse({"shipment": _shipment_dict(row) if row else {"id": shipment_id}})


# ---------------------------------------------------------------------------
# Candidate approval queue
# ---------------------------------------------------------------------------

def _candidate_row(store: ShipmentStore, candidate_id: str) -> Optional[Dict[str, Any]]:
    return store.get_candidate(candidate_id)


def _record_priors(store: ShipmentStore, candidate: Dict[str, Any], hit: bool) -> None:
    """The detection flywheel: sender domain → carrier, on confirm AND reject."""
    carrier = candidate.get("carrier")
    if not carrier:
        return
    source = candidate.get("source") or {}
    domain = sender_domain(str(source.get("sender") or "")) or str(
        (candidate.get("context") or {}).get("sender_domain") or ""
    )
    if domain:
        try:
            store.record_prior("domain", domain, str(carrier), hit)
        except Exception:
            logger.debug("record_prior failed")


@router.get("/candidates")
async def list_candidates() -> JSONResponse:
    store = _get_store()
    rows = store.peek_candidates(status="pending")
    return JSONResponse({"candidates": [_candidate_dict(r) for r in rows]})


@router.post("/candidates/{candidate_id}/confirm")
async def confirm_candidate(candidate_id: str, request: Request) -> JSONResponse:
    store = _get_store()
    row = _candidate_row(store, candidate_id)
    if row is None or row["status"] != "pending":
        return JSONResponse({"error": "candidate not found"}, status_code=404)
    if not store.resolve_candidate(candidate_id, "confirmed"):
        return JSONResponse({"error": "candidate already resolved"}, status_code=409)

    candidate = _candidate_dict(row).get("candidate") or {}
    _record_priors(store, candidate, hit=True)

    shipment_row = None
    number = candidate.get("tracking_number")
    carrier = candidate.get("carrier")
    if number and carrier:
        shipment_row = _create_shipment_from_number(
            store,
            str(number),
            str(carrier),
            source=str(candidate.get("layer") or "api"),
            confidence=float(candidate.get("confidence") or 0.0),
            merchant_domain=(candidate.get("context") or {}).get("sender_domain"),
        )
    # The UI re-fetches on shipment.updated — publish for every confirm,
    # even when the candidate carried no concrete (number, carrier) to add.
    await _emit(
        request,
        "shipment.updated",
        {
            "shipment_id": shipment_row.get("id") if shipment_row else None,
            "candidate_id": candidate_id,
            "source": "candidate_confirm",
        },
    )
    return JSONResponse({"status": "confirmed", "shipment": shipment_row})


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(candidate_id: str) -> JSONResponse:
    store = _get_store()
    row = _candidate_row(store, candidate_id)
    if row is None or row["status"] != "pending":
        return JSONResponse({"error": "candidate not found"}, status_code=404)
    if not store.resolve_candidate(candidate_id, "rejected"):
        return JSONResponse({"error": "candidate already resolved"}, status_code=409)
    candidate = _candidate_dict(row).get("candidate") or {}
    _record_priors(store, candidate, hit=False)
    return JSONResponse({"status": "rejected"})


# ---------------------------------------------------------------------------
# Domain rules
# ---------------------------------------------------------------------------

_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


@router.get("/domain-rules")
async def list_domain_rules() -> JSONResponse:
    store = _get_store()
    return JSONResponse({"rules": store.list_domain_rules()})


@router.post("/domain-rules", status_code=201)
async def create_domain_rule(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON object expected"}, status_code=400)
    domain = str(body.get("domain") or "").strip().lower()
    if not domain or not _DOMAIN_RE.match(domain):
        return JSONResponse({"error": "valid 'domain' is required"}, status_code=400)
    store = _get_store()
    store.set_domain_rule(
        domain,
        category=body.get("category"),
        display_name=body.get("display_name"),
    )
    return JSONResponse({"rule": store.get_domain_rule(domain)}, status_code=201)


# ---------------------------------------------------------------------------
# Eval labeling
# ---------------------------------------------------------------------------

@router.get("/eval")
async def list_eval_candidates(limit: int = 50) -> JSONResponse:
    """Unlabeled detection_eval rows — the hand-labeling queue."""
    store = _get_store()
    rows = store.unlabeled_eval(limit=max(1, min(int(limit), 500)))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["candidate"] = json.loads(d["candidate_json"])
        except (ValueError, TypeError):
            d["candidate"] = d.get("candidate_json")
        d.pop("candidate_json", None)
        out.append(d)
    return JSONResponse({"candidates": out})


@router.post("/eval/{eval_id}/label")
async def label_eval_candidate(eval_id: int, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON object expected"}, status_code=400)
    label = body.get("label")
    if label not in _EVAL_LABELS:
        return JSONResponse(
            {"error": "label must be one of %s" % ", ".join(_EVAL_LABELS)},
            status_code=400,
        )
    store = _get_store()
    if not store.label_eval(eval_id, label):
        return JSONResponse({"error": "eval row not found"}, status_code=404)
    return JSONResponse({"status": "labeled", "id": eval_id, "label": label})


# ---------------------------------------------------------------------------
# UPS Track Alert webhook
# ---------------------------------------------------------------------------

def _ups_webhook_secret(store: ShipmentStore) -> str:
    """The persisted 128-bit webhook secret; generated once on first use."""
    secret = store.get_state(UPS_WEBHOOK_SECRET_KEY)
    if not secret:
        secret = secrets.token_hex(16)  # 128 bits, 32 hex chars
        store.set_state(UPS_WEBHOOK_SECRET_KEY, secret)
    return secret


# NOTE: declared BEFORE /webhook/ups/{secret} so FastAPI doesn't route
# "rotate-secret" into the secret-capture path.
@router.post("/webhook/ups/rotate-secret")
async def rotate_ups_webhook_secret() -> JSONResponse:
    """Admin: rotate the webhook secret; returns the new callback URL path."""
    store = _get_store()
    secret = secrets.token_hex(16)
    store.set_state(UPS_WEBHOOK_SECRET_KEY, secret)
    return JSONResponse({
        "rotated": True,
        "path": "%s/%s" % (UPS_WEBHOOK_PATH, secret),
    })


def _validate_ups_payload(payload: Any) -> Optional[str]:
    """Strict allowlist validation; returns an error string or None.

    Unknown fields are rejected — the payload is untrusted carrier input,
    and silently passing extra keys through would let them reach the
    append-only event store.
    """
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    unknown = set(payload) - _UPS_TOP_FIELDS
    if unknown:
        return "unknown fields: %s" % ", ".join(sorted(unknown))
    number = payload.get("trackingNumber")
    if not isinstance(number, str) or not number.strip():
        return "trackingNumber must be a non-empty string"
    if len(number) > _UPS_MAX_STRING:
        return "trackingNumber too long"
    delivery_date = payload.get("deliveryDate")
    if delivery_date is not None and not isinstance(delivery_date, str):
        return "deliveryDate must be a string"
    events = payload.get("events")
    if events is None:
        return None
    if not isinstance(events, list):
        return "events must be a list"
    if len(events) > _UPS_MAX_EVENTS:
        return "too many events"
    for event in events:
        if not isinstance(event, dict):
            return "each event must be an object"
        unknown = set(event) - _UPS_EVENT_FIELDS
        if unknown:
            return "unknown event fields: %s" % ", ".join(sorted(unknown))
        for key, value in event.items():
            if value is None:
                continue
            if not isinstance(value, str):
                return "event field %s must be a string" % key
            if len(value) > _UPS_MAX_STRING:
                return "event field %s too long" % key
    return None


def _parse_ups_ts(date: Optional[str], time: Optional[str]) -> Optional[datetime]:
    """UPS split date/time (YYYYMMDD / HHMMSS) → naive UTC datetime."""
    if not date:
        return None
    date = date.strip()
    time = (time or "").strip() or "000000"
    if not (re.fullmatch(r"\d{8}", date) and re.fullmatch(r"\d{6}", time)):
        return None
    try:
        return datetime.strptime(date + time, "%Y%m%d%H%M%S")
    except ValueError:
        return None


@router.post("/webhook/ups/{secret}")
async def ups_track_alert_webhook(secret: str, request: Request) -> JSONResponse:
    """UPS Track Alert push receiver.

    Wrong secret → 404 (never confirm existence). Polling remains the
    source of truth: internal failures are logged and answered 202.
    """
    store = _get_store()
    expected = _ups_webhook_secret(store)
    if not hmac.compare_digest(secret, expected):
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    error = _validate_ups_payload(payload)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    packs = _get_packs()
    pack = packs.get("ups")
    canonical = engine.canonicalize(payload["trackingNumber"])
    if pack is None or not engine.matches_any_pattern(pack, canonical):
        # Pattern-validated numbers only — after this point the number is
        # safe to log (strict [0-9A-Z] charset).
        return JSONResponse({"error": "invalid tracking number"}, status_code=400)

    try:
        shipment_id = store.link_number("ups", canonical)
        if shipment_id is None:
            logger.info(
                "ups webhook: unknown number %s — ignored (polling owns it)",
                canonical,
            )
            return JSONResponse({"status": "ignored", "reason": "unknown_number"},
                                status_code=202)

        events: List[TrackingEvent] = []
        for raw_event in payload.get("events") or []:
            code = raw_event.get("statusCode")
            timestamp = _parse_ups_ts(
                raw_event.get("gmtDate"), raw_event.get("gmtTime")
            ) or _parse_ups_ts(raw_event.get("date"), raw_event.get("time"))
            events.append(TrackingEvent(
                milestone=engine.normalize_status(pack, code),
                description=raw_event.get("statusDescription") or "",
                timestamp=timestamp,
                fetched_at=_utcnow(),
                location=raw_event.get("city"),
                carrier_code=code,
                raw=raw_event,
            ))
        milestones: List[str] = []
        if events:
            shipment_id, forked = store.ingest_events(shipment_id, events)
            milestones = sorted({
                e.milestone.value for e in events if e.milestone is not None
            })
            logger.info(
                "ups webhook: %s <- %d event(s)%s",
                canonical, len(events), " (forked: recycled number)" if forked else "",
            )
        await _emit(request, "shipment.updated", {
            "shipment_id": shipment_id,
            "carrier": "ups",
            "source": "webhook",
        })
        for milestone in milestones:
            await _emit(request, "shipment.milestone", {
                "shipment_id": shipment_id,
                "milestone": milestone,
                "carrier": "ups",
            })
        return JSONResponse({"status": "ok", "events": len(events)})
    except Exception:
        # Polling is the source of truth — log and accept so UPS retries
        # never wedge the pipeline.
        logger.exception("ups webhook: processing failed for %s", canonical)
        return JSONResponse({"status": "logged"}, status_code=202)


# ---------------------------------------------------------------------------
# Shipment detail — declared LAST: "/{shipment_id}" would otherwise shadow
# the fixed GET paths (/candidates, /domain-rules, /eval) since Starlette
# matches routes in declaration order.
# ---------------------------------------------------------------------------

@router.get("/{shipment_id}")
async def get_shipment(shipment_id: str) -> JSONResponse:
    store = _get_store()
    row = store.get_shipment_row(shipment_id)
    if row is None:
        return JSONResponse({"error": "shipment not found"}, status_code=404)
    events = [_event_dict(e) for e in store.events_for(shipment_id)]
    numbers = store.numbers_for(shipment_id)
    order = None
    orders = store.orders_for_shipment(shipment_id)
    if orders:
        order = store.get_order(orders[0]["id"])
    return JSONResponse({
        "shipment": _shipment_dict(row),
        "events": events,
        "numbers": numbers,
        "order": order,
    })
