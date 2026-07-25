"""Chit Chats outbound sync (auto-tracker#7).

Pulls recent outbound shipments/labels from the Chit Chats API and folds
them into the tracking store. Chit Chats is a Canadian shipping aggregator:
labels are created on chitchats.com, then *inducted* into a last-mile
carrier (usually USPS for US-bound, Canada Post domestic). The API response
carries that final carrier + tracking number (``carrier`` /
``carrier_tracking_code``), so each sync also records the handoff as a
``shipment_numbers`` entry (role ``handoff``) plus a one-time handoff note
event on the shipment.

API shape learned from the chitchats-mcp server
(~/project/chitchats-mcp/src/client.ts, tools/shipments.ts):

- Base: ``https://chitchats.com/api/v1/clients/{client_id}``
- Auth: ``Authorization: <access_token>`` header (raw token, no Bearer
  prefix), ``Accept: application/json``
- ``GET /shipments?limit=&page=&status=&from_date=&to_date=&search=``
  returns a bare JSON array of shipment objects.
- Shipment fields used here: ``id``, ``status``, ``order_id``,
  ``order_store``, ``to_name``/``to_city``/``to_province_code``/
  ``to_country_code``, ``postage_type``, ``carrier``,
  ``carrier_tracking_code``, ``tracking_url``, ``ship_date``,
  ``created_at``.

Credentials live in the macOS Keychain, fetched via the ``agent-secret``
CLI (subprocess, 5s timeout, values never logged). A missing key degrades
the sync to a logged no-op rather than an exception.

Python 3.9-compatible; stdlib ``urllib`` only. HTTP is injected as a
transport callable so tests never touch the network.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .engine import canonicalize
from .models import Milestone, Shipment, TrackingEvent

log = logging.getLogger("qareen.tracking.chitchats")

# --- Configuration constants -------------------------------------------------

#: Keychain entry names (via agent-secret). The API key is the Chit Chats
#: access token; the client ID scopes the API path.
API_KEY_NAME = "CHITCHATS_API_KEY"
CLIENT_ID_KEY_NAME = "CHITCHATS_CLIENT_ID"

#: agent-secret CLI (runtime install; read/execute only, never edited).
AGENT_SECRET_CLI = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

#: How long to wait for a Keychain read before giving up.
SECRET_TIMEOUT_S = 5

BASE_URL = "https://chitchats.com"
REQUEST_TIMEOUT_S = 30
DEFAULT_PAGE_LIMIT = 200  # API max is 1000; we page if a page comes back full

#: tracking_state key for the sync checkpoint (ISO timestamp of last success).
STATE_LAST_SYNC = "chitchats.last_sync_at"
#: Per-shipment state keys (prefixed with the Chit Chats shipment id) that
#: make event appends idempotent across re-syncs.
STATE_MILESTONE_FMT = "chitchats.milestone.{sid}"
STATE_HANDOFF_FMT = "chitchats.handoff.{sid}"

CARRIER = "chitchats"

#: Chit Chats shipment status → canonical Milestone. Statuses from the API
#: docs / MCP schema: pending, ready, inducted, in_transit,
#: out_for_delivery, delivered, exception, cancelled, refunded.
#: ``pending``/``ready`` are pre-induction (label exists, not yet with the
#: carrier) → LABEL_CREATED. ``inducted`` means Chit Chats has handed the
#: parcel into the carrier network → PICKED_UP. Cancelled/refunded labels
#: will never move → EXPIRED (terminal, stops polling).
STATUS_TO_MILESTONE: Dict[str, Milestone] = {
    "pending": Milestone.LABEL_CREATED,
    "ready": Milestone.LABEL_CREATED,
    "inducted": Milestone.PICKED_UP,
    "in_transit": Milestone.IN_TRANSIT,
    "out_for_delivery": Milestone.OUT_FOR_DELIVERY,
    "delivered": Milestone.DELIVERED,
    "exception": Milestone.EXCEPTION,
    "cancelled": Milestone.EXPIRED,
    "refunded": Milestone.EXPIRED,
}


class ChitChatsError(Exception):
    """API-level failure (non-2xx, bad JSON, network). Never carries secrets."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class ChitChatsRateLimited(ChitChatsError):
    """HTTP 429. ``retry_after`` is the server-provided delay, if any."""

    def __init__(self, retry_after: Optional[str] = None) -> None:
        super().__init__(
            "rate limited by Chit Chats (retry after %s)"
            % (retry_after or "unknown"),
            status=429,
        )
        self.retry_after = retry_after


# Transport signature: (method, url, headers) -> (status, parsed_json|None).
# headers values must be treated as sensitive by implementations (the
# Authorization header carries the access token).
Transport = Callable[[str, str, Dict[str, str]], Tuple[int, Any]]


def _urllib_transport(method: str, url: str, headers: Dict[str, str]) -> Tuple[int, Any]:
    """Default transport: stdlib urllib, JSON in/out."""
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            body = resp.read()
            if resp.status == 204 or not body:
                return resp.status, None
            return resp.status, json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ChitChatsRateLimited(exc.headers.get("Retry-After"))
        raise ChitChatsError("HTTP %d from Chit Chats" % exc.code, status=exc.code)
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise ChitChatsError("request failed: %s" % exc)


class ChitChatsClient:
    """Thin read-only client for the Chit Chats v1 API.

    Only what the outbound sync needs: list shipments, get one shipment.
    ``transport`` is injectable for tests; production uses urllib.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        transport: Optional[Transport] = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._client_id = client_id
        self._access_token = access_token
        self._transport = transport or _urllib_transport
        self._base = "%s/api/v1/clients/%s" % (base_url.rstrip("/"), client_id)

    def _get(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Any:
        url = self._base + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Authorization": self._access_token,
            "Accept": "application/json",
        }
        status, data = self._transport("GET", url, headers)
        if status == 429:
            raise ChitChatsRateLimited()
        if status < 200 or status >= 300:
            message = "HTTP %d from Chit Chats" % status
            if isinstance(data, dict):
                message = str(data.get("error") or data.get("message") or message)
            raise ChitChatsError(message, status=status)
        return data

    def list_shipments(
        self,
        limit: int = DEFAULT_PAGE_LIMIT,
        page: int = 1,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """One page of shipments. The API returns a bare JSON array."""
        params: Dict[str, str] = {"limit": str(limit), "page": str(page)}
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date
        if status:
            params["status"] = status
        data = self._get("/shipments", params)
        if not isinstance(data, list):
            raise ChitChatsError("unexpected /shipments payload (not a list)")
        return data

    def get_shipment(self, shipment_id: str) -> Dict[str, Any]:
        """Single shipment detail (``{shipment: {...}}`` envelope)."""
        data = self._get("/shipments/%s" % urllib.parse.quote(shipment_id, safe=""))
        if not isinstance(data, dict) or not isinstance(data.get("shipment"), dict):
            raise ChitChatsError("shipment %s not found" % shipment_id)
        return data["shipment"]


def _read_secret(name: str) -> Optional[str]:
    """Read one Keychain secret via agent-secret. Never logs the value."""
    try:
        proc = subprocess.run(
            [str(AGENT_SECRET_CLI), "get", name],
            capture_output=True,
            timeout=SECRET_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("agent-secret get %s failed: %s", name, exc)
        return None
    if proc.returncode != 0:
        log.warning("agent-secret has no value for %s", name)
        return None
    value = proc.stdout.decode("utf-8", "replace").strip()
    return value or None


def get_credentials() -> Optional[Tuple[str, str]]:
    """(client_id, access_token) from the Keychain, or None if either is absent."""
    client_id = _read_secret(CLIENT_ID_KEY_NAME)
    access_token = _read_secret(API_KEY_NAME)
    if not client_id or not access_token:
        return None
    return client_id, access_token


def map_status(status: Any) -> Milestone:
    """Chit Chats status string → Milestone. Unknown → LABEL_CREATED (the
    label exists in their system; we just don't know its finer state)."""
    if isinstance(status, str):
        milestone = STATUS_TO_MILESTONE.get(status.strip().lower())
        if milestone is not None:
            return milestone
    return Milestone.LABEL_CREATED


def _as_float(value: Any) -> Optional[float]:
    """Coerce a Chit Chats money field to float. None on anything unusable.

    The API returns money as decimal *strings* ("30.00") and omits or nulls
    fields freely, so this must tolerate None, "", and junk.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # drop NaN


def _as_int(value: Any, default: int = 1) -> int:
    """Coerce a quantity to a positive int, falling back to `default`."""
    if value is None or isinstance(value, bool):
        return default
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return default
    return out if out >= 1 else default


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string; naive values assumed UTC. None on junk."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def map_shipment(raw: Dict[str, Any], now: Optional[datetime] = None) -> Shipment:
    """Map one Chit Chats shipment dict to a canonical Shipment.

    The primary tracking number is the Chit Chats shipment id (canonicalized)
    — pre-induction there is often no carrier tracking code yet, and the id
    is stable for the label's whole life. The last-mile number rides as a
    separate ``shipment_numbers`` handoff entry (see ChitChatsSync).
    """
    now = now or datetime.now(timezone.utc)
    cc_id = str(raw.get("id") or "").strip()
    if not cc_id:
        raise ValueError("Chit Chats shipment has no id: %r" % (raw,))

    recipient_bits = [
        raw.get("to_name"),
        raw.get("to_city"),
        raw.get("to_province_code"),
        raw.get("to_country_code"),
    ]
    recipient = ", ".join(str(b) for b in recipient_bits if b)
    order_id = raw.get("order_id")
    if order_id and recipient:
        label = "%s → %s" % (order_id, recipient)
    else:
        label = str(order_id or recipient) or None

    status = raw.get("status")
    milestone = map_status(status)
    ship_date = _parse_dt(raw.get("ship_date")) or _parse_dt(raw.get("created_at"))

    return Shipment(
        tracking_number=canonicalize(cc_id),
        carrier=CARRIER,
        milestone=milestone,
        direction="outbound",
        status="delivered" if milestone is Milestone.DELIVERED else (
            "expired" if milestone is Milestone.EXPIRED else "active"
        ),
        source="api",
        merchant=raw.get("order_store") or None,
        merchant_domain=None,
        label=label,
        confidence=1.0,
        first_seen=ship_date or now,
    )


class ChitChatsSync:
    """Sync outbound Chit Chats shipments into the tracking store.

    Store protocol (duck-typed; the real implementation is
    ``qareen.tracking.store``, built concurrently):

    - ``upsert_shipment(shipment: Shipment) -> (str, bool)``
      Insert or update keyed on ``(carrier, tracking_number)``; returns the
      ``(shipment_id, created)`` tuple. Must be idempotent — a re-sync of
      the same label updates milestone/status in place rather than
      duplicating the row.
    - ``add_number(shipment_id: str, number: str, carrier: str = None, role: str = "handoff") -> None``
      Link an extra tracking number (``role`` is ``"handoff"`` here for the
      last-mile carrier). Must be idempotent on ``(shipment_id, carrier, number)``.
    - ``append_event(shipment_id: str, event: TrackingEvent) -> None``
      Append to the shipment's event timeline.
    - ``get_state(key: str) -> Optional[str]`` / ``set_state(key, value)``
      Key-value checkpoint storage (``tracking_state`` table).

    Idempotency strategy: shipment rows and handoff numbers are idempotent
    by store contract; *events* are gated by per-shipment state keys so a
    re-sync only appends when the milestone actually advanced or a handoff
    is seen for the first time.
    """

    def __init__(
        self,
        store: Any,
        client: Optional[ChitChatsClient] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.store = store
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _get_client(self) -> Optional[ChitChatsClient]:
        if self._client is not None:
            return self._client
        creds = get_credentials()
        if creds is None:
            log.warning(
                "Chit Chats credentials missing from Keychain (%s/%s); "
                "outbound sync skipped",
                CLIENT_ID_KEY_NAME,
                API_KEY_NAME,
            )
            return None
        self._client = ChitChatsClient(client_id=creds[0], access_token=creds[1])
        return self._client

    def sync(
        self,
        limit: int = DEFAULT_PAGE_LIMIT,
        from_date: Optional[str] = None,
        max_pages: int = 5,
    ) -> Dict[str, Any]:
        """Run one sync pass. Returns a summary dict (always, even on failure).

        ``from_date`` (YYYY-MM-DD) overrides the incremental window; by
        default the date of the last successful sync checkpoint is used so
        repeat runs only re-read recent labels.
        """
        client = self._get_client()
        if client is None:
            return {"ok": False, "reason": "missing_credentials", "synced": 0}

        if from_date is None:
            last = self.store.get_state(STATE_LAST_SYNC)
            if last:
                from_date = last[:10]  # ISO timestamp → YYYY-MM-DD

        raw_shipments: List[Dict[str, Any]] = []
        try:
            for page in range(1, max_pages + 1):
                batch = client.list_shipments(
                    limit=limit, page=page, from_date=from_date
                )
                raw_shipments.extend(batch)
                if len(batch) < limit:
                    break
        except ChitChatsError as exc:
            log.warning("Chit Chats sync failed: %s", exc)
            return {
                "ok": False,
                "reason": "api_error",
                "error": str(exc),
                "synced": 0,
            }

        now = self._now()
        summary = {
            "ok": True,
            "synced": 0,
            "handoffs": 0,
            "events": 0,
            "orders": 0,
            "order_items": 0,
            "errors": 0,
        }
        for raw in raw_shipments:
            try:
                self._sync_one(raw, now, summary)
            except Exception:  # one bad row must not sink the batch
                log.exception("failed to sync Chit Chats shipment %r", raw.get("id"))
                summary["errors"] += 1

        checkpoint = now.isoformat()
        self.store.set_state(STATE_LAST_SYNC, checkpoint)
        summary["synced_at"] = checkpoint
        log.info(
            "Chit Chats sync: %d shipment(s), %d handoff(s), %d event(s), "
            "%d order(s)/%d item(s), %d error(s)",
            summary["synced"],
            summary["handoffs"],
            summary["events"],
            summary["orders"],
            summary["order_items"],
            summary["errors"],
        )
        return summary

    def _sync_order(
        self, raw: Dict[str, Any], shipment_id: str, summary: Dict[str, Any]
    ) -> None:
        """Persist order + line items straight from the Chit Chats payload.

        Chit Chats already returns fully structured order data — ``order_id``,
        ``order_store``, and a ``line_items`` array with quantity/description/
        value/SKU. v0.7.0 read ``order_id`` only to build a display label and
        discarded the rest, while a separate LLM-over-email extractor
        (``orders.py``) was written to recover the same information. This is
        the free path: no model call, no email parsing, no API key.

        Best-effort by design — order enrichment must never fail a shipment
        sync. The store is a duck-typed seam, so a store without the order
        API (older instances, test fakes) simply skips.
        """
        upsert = getattr(self.store, "upsert_order", None)
        if not callable(upsert):
            return

        order_number = raw.get("order_id")
        if order_number in (None, ""):
            return
        order_number = str(order_number).strip()
        if not order_number:
            return

        items = []
        for li in raw.get("line_items") or []:
            if not isinstance(li, dict):
                continue
            name = (li.get("description") or "").strip()
            if not name:
                continue
            items.append(
                {
                    "name": name,
                    "qty": _as_int(li.get("quantity"), default=1),
                    "price": _as_float(li.get("value_amount")),
                    "sku": (li.get("sku_code") or None),
                }
            )

        try:
            order_id = upsert(
                order_number=order_number,
                merchant=raw.get("order_store") or None,
                # Left NULL deliberately: order_store is a platform name
                # ("shopify"), not a domain, and upsert_order dedups on
                # (merchant_domain, order_number). Inventing a domain here
                # would fragment against real email-extracted orders later.
                merchant_domain=None,
                order_date=raw.get("ship_date") or raw.get("created_at"),
                total=_as_float(raw.get("value")),
                currency=raw.get("value_currency") or None,
                items=items or None,
            )
        except Exception as exc:  # never fail the shipment sync
            log.warning(
                "chitchats: order %s enrichment failed (%s)", order_number, exc
            )
            summary["errors"] = summary.get("errors", 0) + 1
            return

        link = getattr(self.store, "link_shipment_order", None)
        if callable(link):
            try:
                link(shipment_id, order_id)
            except Exception as exc:
                log.warning("chitchats: order link failed (%s)", exc)

        summary["orders"] = summary.get("orders", 0) + 1
        summary["order_items"] = summary.get("order_items", 0) + len(items)

    def _sync_one(self, raw: Dict[str, Any], now: datetime, summary: Dict[str, Any]) -> None:
        shipment = map_shipment(raw, now=now)
        # upsert_shipment returns (shipment_id, created) — the id is what
        # every downstream call keys on.
        shipment_id, _created = self.store.upsert_shipment(shipment)
        summary["synced"] += 1
        sid = shipment.tracking_number  # canonical Chit Chats id; state-key suffix

        # Milestone event — only when the milestone actually changed.
        milestone_key = STATE_MILESTONE_FMT.format(sid=sid)
        previous = self.store.get_state(milestone_key)
        if previous != shipment.milestone.value:
            recipient = raw.get("to_name") or "recipient"
            self.store.append_event(
                shipment_id,
                TrackingEvent(
                    milestone=shipment.milestone,
                    description="Chit Chats status: %s (to %s)"
                    % (raw.get("status") or "unknown", recipient),
                    timestamp=_parse_dt(raw.get("ship_date"))
                    or _parse_dt(raw.get("created_at"))
                    or now,
                    fetched_at=now,
                    carrier_code=str(raw.get("status") or ""),
                    raw=raw,
                ),
            )
            self.store.set_state(milestone_key, shipment.milestone.value)
            summary["events"] += 1

        # Order contents — free, no LLM, no email parsing.
        self._sync_order(raw, shipment_id, summary)

        # Last-mile handoff: Chit Chats inducts into USPS/Canada Post/etc.;
        # the response then carries the final carrier + tracking number.
        handoff_carrier = raw.get("carrier")
        handoff_code = raw.get("carrier_tracking_code")
        if handoff_carrier and handoff_code:
            number = canonicalize(str(handoff_code))
            carrier_slug = str(handoff_carrier).strip().lower()
            self.store.add_number(shipment_id, number, carrier=carrier_slug, role="handoff")
            summary["handoffs"] += 1

            handoff_key = STATE_HANDOFF_FMT.format(sid=sid)
            if self.store.get_state(handoff_key) != number:
                self.store.append_event(
                    shipment_id,
                    TrackingEvent(
                        milestone=None,  # a note, not a milestone change
                        description="Handed off to %s: %s"
                        % (handoff_carrier, number),
                        timestamp=now,
                        fetched_at=now,
                        carrier_code="handoff",
                        raw={
                            "carrier": handoff_carrier,
                            "carrier_tracking_code": handoff_code,
                            "tracking_url": raw.get("tracking_url"),
                        },
                    ),
                )
                self.store.set_state(handoff_key, number)
                summary["events"] += 1
