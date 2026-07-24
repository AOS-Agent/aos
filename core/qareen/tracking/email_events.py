"""Email-event channel — lifecycle emails as a shipment status source.

Some carriers have no API but DO send rich lifecycle emails. Amazon is the
big one: order confirmation → shipped → out-for-delivery → delivered emails
(already in comms.db) become milestone events on a pseudo-shipment keyed by
Amazon order ID. TBA tracking numbers detected in text are linked to their
order's pseudo-shipment; a TBA seen without a known order creates a bare
pseudo-shipment keyed by the number itself. Photo-on-delivery emails surface
in the timeline via the event description.

Honest limits (per the initiative): no live GPS, depends on emails arriving,
and every event carries ``source: email`` (``raw["source"]`` and the store
call) so the UI never confuses it with API tracking.

Structure:

- Pure parsing — ``parse_amazon_email(message) -> ParsedEmailEvent | None``.
  No I/O, no store access; a message that isn't a recognizable Amazon
  lifecycle email yields None, never an exception.
- Merchant-parser registry — ``register_parser(domain_pattern, parser)``.
  New merchants plug in without touching this core (the generic seam).
- ``EmailEventChannel`` — dispatches messages through the registry and
  persists results via a duck-typed store.

Store protocol (duck-typed; the real store is ``qareen.tracking.store``):

    upsert_shipment_key(*, key: str, carrier: str, merchant: str | None = None,
                        merchant_domain: str | None = None, source: str = "email",
                        label: str | None = None) -> str
        Idempotent on (carrier, key): returns the shipment id, creating the
        row on first sight. THIS is what makes two emails for the same order
        one shipment with two events. The real store provides this method;
        older fakes may instead expose the duck-typed
        ``upsert_shipment(key=..., carrier=..., ...)`` keyword form — the
        channel prefers upsert_shipment_key and falls back.
    append_event(shipment_id: str, event: TrackingEvent) -> None
        Append-only. The store dedups repeated deliveries of the same email
        (event.raw carries message_id for exactly this).
    add_number(shipment_id: str, number: str, carrier: str) -> None
        Links a canonical tracking number to the shipment. When a bare
        TBA-keyed pseudo-shipment later gets linked to an order-keyed one,
        the store may merge them; this channel only declares the link.

Compatible with system Python 3.9: no ``X | Y`` unions, no match statements.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Pattern, Tuple

from .models import Milestone, TrackingEvent

log = logging.getLogger(__name__)

# Pack slug for email-sourced pseudo-shipments — matches the pack directory
# carriers/amazon-email/ so shipments resolve to a real, loadable pack.
AMAZON_EMAIL_CARRIER = "amazon-email"


# ── Parsed result ─────────────────────────────────────────────────────────────

@dataclass
class ParsedEmailEvent:
    """One merchant lifecycle email, parsed. Output of the pure parsers."""

    kind: str  # confirmed | shipped | out_for_delivery | delivered |
    #            delivery_attempt | delayed | tracking_only
    milestone: Optional[Milestone]  # None only for kind=tracking_only
    merchant: str  # e.g. "Amazon"
    merchant_domain: str  # sender domain, e.g. "amazon.ca"
    description: str
    timestamp: Optional[datetime] = None  # message timestamp (best-effort)
    order_id: Optional[str] = None  # pseudo-shipment key when present
    tracking_numbers: List[str] = field(default_factory=list)  # canonical
    item_summary: Optional[str] = None  # short "what's in the box" hint
    photo_on_delivery: bool = False
    subject: str = ""
    source: str = "email"  # never confuse with API tracking
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchResult:
    """Outcome of processing a batch of messages through the channel."""

    consumed: int = 0  # emails recognized and persisted
    skipped: int = 0  # messages no parser claimed
    errors: int = 0  # messages that raised (wrapped, logged, never fatal)


# ── Amazon parsing (pure) ─────────────────────────────────────────────────────

# Sender-domain matcher: amazon.com / amazon.ca (subdomains included).
_AMAZON_DOMAIN_RE = re.compile(r"(?:^|[.@])amazon\.(?:com|ca)$", re.IGNORECASE)

# Amazon order IDs: 3-7-7 digits, dashes included (both .com and .ca).
_ORDER_ID_RES = [
    re.compile(r"(?i)\border\s*(?:number|id|#)?\s*[:.]?\s*#?\s*(\d{3}-\d{7}-\d{7})\b"),
    re.compile(r"\b(\d{3}-\d{7}-\d{7})\b"),  # bare fallback
]

# Amazon Logistics tracking IDs: "TBA" + digits. Bounded + flat (the linter's
# ReDoS idiom applies here too — these run over arbitrary message text).
_TBA_RE = re.compile(r"\b(TBA[0-9]{10,15})\b", re.IGNORECASE)

# Subject/body classifiers, ordered SPECIFIC → GENERAL. The first matching
# rule wins; "delivered" must precede "shipped" because shipped emails often
# promise future delivery ("will be delivered Thursday").
_KIND_RULES: List[Tuple[str, List[Pattern[str]], Optional[Milestone]]] = [
    ("delivered", [
        re.compile(r"(?i)^delivered[:!]"),
        re.compile(r"(?i)\byour package (?:was|has been) delivered\b"),
        re.compile(r"(?i)\byour order (?:was|has been) delivered\b"),
    ], Milestone.DELIVERED),
    ("delivery_attempt", [
        re.compile(r"(?i)\bwe (?:tried|attempted) to deliver\b"),
        re.compile(r"(?i)\bdelivery attempt(?:ed)?\b"),
        re.compile(r"(?i)\bunable to deliver\b"),
    ], Milestone.FAILED_ATTEMPT),
    ("out_for_delivery", [
        re.compile(r"(?i)\bout for delivery\b"),
        re.compile(r"(?i)\barriving today\b"),
    ], Milestone.OUT_FOR_DELIVERY),
    ("delayed", [
        re.compile(r"(?i)\b(?:has been|is|was) delayed\b"),
        re.compile(r"(?i)\bdelivery (?:date )?(?:change[ds]?|rescheduled)\b"),
    ], Milestone.EXCEPTION),
    ("shipped", [
        re.compile(r"(?i)^shipped[:!]"),
        re.compile(r"(?i)\bhas shipped\b"),
        re.compile(r"(?i)\bon (?:its|the) way\b"),
    ], Milestone.IN_TRANSIT),
    ("confirmed", [
        re.compile(r"(?i)^ordered[:!]"),
        re.compile(r"(?i)\border confirmation\b"),
        re.compile(r"(?i)\bthanks for your order\b"),
        re.compile(r"(?i)\byour order (?:is |has been )?(?:confirmed|placed)\b"),
    ], Milestone.LABEL_CREATED),
]

# \s+ (not ' ') between words: email bodies hard-wrap mid-phrase
# ("View your delivery\nphoto").
_PHOTO_RE = re.compile(
    r"(?i)\b(?:delivery\s+photo|photo\s+of\s+your\s+(?:delivery|package)|"
    r"photo[-\s]on[-\s]delivery|see\s+your\s+photo|view\s+your\s+photo)\b"
)

# Best-effort "what's in the box" hint from subjects like
#   Ordered: "Instant Pot Duo..." / Your Amazon.ca order of "Nespresso pods..."
_ITEM_RES = [
    re.compile(r"(?i)^(?:ordered|shipped|delivered):\s*\"([^\"]{3,120})\""),
    re.compile(r"(?i)\border of\s+\"([^\"]{3,120})\""),
]


def _extract_email_address(sender: str) -> str:
    """Pull the bare address out of 'Name <addr@host>' or return as-is."""
    match = re.search(r"<([^<>\s]+@[^<>\s]+)>", sender)
    return match.group(1) if match else sender.strip()


def _sender_domain(sender: str) -> str:
    address = _extract_email_address(sender)
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def _extract_subject(message: Mapping[str, Any]) -> str:
    """Subject from the explicit field, else a 'Subject:' line in content."""
    subject = message.get("subject")
    if isinstance(subject, str) and subject.strip():
        return subject.strip()
    content = message.get("content")
    if isinstance(content, str):
        match = re.search(r"(?im)^subject:\s*(.+?)\s*$", content)
        if match:
            return match.group(1)
    return ""


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Tolerant timestamp parse: datetime, epoch, or ISO string; else None."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):  # fromisoformat predates Z support on 3.9
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _classify(subject: str, body: str) -> Tuple[Optional[str], Optional[Milestone]]:
    """First matching rule wins; subject checked before body."""
    for text in (subject, body):
        if not text:
            continue
        for kind, patterns, milestone in _KIND_RULES:
            if any(p.search(text) for p in patterns):
                return kind, milestone
    return None, None


def _extract_order_id(text: str) -> Optional[str]:
    for pattern in _ORDER_ID_RES:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def _extract_item_summary(subject: str) -> Optional[str]:
    for pattern in _ITEM_RES:
        match = pattern.search(subject)
        if match:
            return match.group(1).strip()
    return None


def parse_amazon_email(message: Mapping[str, Any]) -> Optional[ParsedEmailEvent]:
    """Parse one Amazon lifecycle email into a ParsedEmailEvent, or None.

    Returns None (never raises) for: non-Amazon senders, non-Amazon content,
    malformed message dicts, and Amazon emails that are not lifecycle events
    (marketing, receipts, Alexa notifications) UNLESS they carry a usable TBA
    tracking number — a TBA with no recognizable lifecycle type still yields
    a ``tracking_only`` event so the bare-number path can key a pseudo-shipment
    on it.
    """
    try:
        if not isinstance(message, Mapping):
            return None
        sender = message.get("sender_id")
        if not isinstance(sender, str) or not sender.strip():
            return None
        domain = _sender_domain(sender)
        if not _AMAZON_DOMAIN_RE.search(domain):
            return None

        content = message.get("content")
        body = content if isinstance(content, str) else ""
        subject = _extract_subject(message)
        text = subject + "\n" + body

        order_id = _extract_order_id(text)
        tbas = sorted({m.group(1).upper() for m in _TBA_RE.finditer(text)})
        kind, milestone = _classify(subject, body)

        if kind is None:
            if not tbas:
                return None  # Amazon email, but not a lifecycle event
            kind = "tracking_only"
        if order_id is None and not tbas:
            return None  # nothing to key a pseudo-shipment on

        photo = bool(_PHOTO_RE.search(text))
        item_summary = _extract_item_summary(subject)
        description = _describe(kind, item_summary, order_id, tbas, photo)

        return ParsedEmailEvent(
            kind=kind,
            milestone=milestone,
            merchant="Amazon",
            merchant_domain=domain,
            description=description,
            timestamp=_parse_timestamp(message.get("timestamp")),
            order_id=order_id,
            tracking_numbers=tbas,
            item_summary=item_summary,
            photo_on_delivery=photo,
            subject=subject,
            raw={
                "message_id": message.get("id"),
                "thread_id": message.get("thread_id"),
                "conversation_id": message.get("conversation_id"),
                "sender": sender,
            },
        )
    except Exception:  # pragma: no cover - defensive: parsing never crashes
        log.exception("parse_amazon_email failed; message skipped")
        return None


def _describe(
    kind: str,
    item_summary: Optional[str],
    order_id: Optional[str],
    tbas: List[str],
    photo: bool,
) -> str:
    """Human timeline line for the event; original wording stays in raw."""
    item = ' "%s"' % item_summary if item_summary else ""
    order = " (order %s)" % order_id if order_id else ""
    if kind == "confirmed":
        return "Amazon order confirmed%s%s" % (item, order)
    if kind == "shipped":
        track = " — tracking %s" % ", ".join(tbas) if tbas else ""
        return "Amazon order shipped%s%s%s" % (item, order, track)
    if kind == "out_for_delivery":
        return "Amazon package out for delivery%s" % order
    if kind == "delivered":
        note = " — delivery photo available" if photo else ""
        return "Amazon package delivered%s%s" % (order, note)
    if kind == "delivery_attempt":
        return "Amazon delivery attempted%s" % order
    if kind == "delayed":
        return "Amazon delivery delayed%s" % order
    # tracking_only
    return "Amazon tracking number detected: %s" % ", ".join(tbas)


# ── Merchant-parser registry (the generic-merchant seam) ─────────────────────

ParserFn = Callable[[Mapping[str, Any]], Optional[ParsedEmailEvent]]

# (sender-domain regex, parser) — first match wins. Parsers are pure
# functions; adding a merchant never touches EmailEventChannel.
_PARSERS: List[Tuple[Pattern[str], ParserFn]] = []


def register_parser(domain_pattern: str, parser: ParserFn) -> None:
    """Register *parser* for senders whose domain matches *domain_pattern*.

    Later registrations are appended, so register specific domains before
    general ones. The parser contract is ``message dict -> ParsedEmailEvent
    | None``; it must be pure (no I/O) and must return None on input it does
    not recognize.
    """
    _PARSERS.append((re.compile(domain_pattern, re.IGNORECASE), parser))


def default_registry() -> List[Tuple[Pattern[str], ParserFn]]:
    """Built-in merchants (Amazon) plus any module-level registrations.

    A fresh list each call, so channels can append custom parsers without
    mutating shared state.
    """
    registry: List[Tuple[Pattern[str], ParserFn]] = []
    registry.append((re.compile(r"(?:^|[.@])amazon\.(?:com|ca)$", re.IGNORECASE),
                     parse_amazon_email))
    registry.extend(_PARSERS)
    return registry


# ── Channel ───────────────────────────────────────────────────────────────────

class EmailEventChannel:
    """Processes message batches through merchant parsers into the store.

    ``store`` implements the duck-typed protocol documented in the module
    docstring. ``registry`` defaults to the built-in merchants; pass a custom
    list of (compiled domain regex, parser) to extend or replace it — this is
    how later merchants plug in without touching core.

    Consumer-safety rule: no message may crash the channel. Per-message
    exceptions are caught, logged, and counted in the BatchResult.
    """

    def __init__(
        self,
        store: Any,
        registry: Optional[List[Tuple[Pattern[str], ParserFn]]] = None,
    ) -> None:
        self.store = store
        self._registry = registry if registry is not None else default_registry()

    def _dispatch(self, message: Mapping[str, Any]) -> Optional[ParsedEmailEvent]:
        if not isinstance(message, Mapping):
            return None
        sender = message.get("sender_id")
        if not isinstance(sender, str):
            return None
        domain = _sender_domain(sender)
        for pattern, parser in self._registry:
            if pattern.search(domain):
                return parser(message)
        return None

    def process_message(self, message: Mapping[str, Any]) -> bool:
        """Parse and persist one message. True iff a parser consumed it."""
        parsed = self._dispatch(message)
        if parsed is None:
            return False

        # Pseudo-shipment key: the merchant order ID when known, else the
        # first tracking number (the bare-TBA path).
        key = parsed.order_id or (parsed.tracking_numbers[0] if parsed.tracking_numbers else None)
        if not key:
            return False

        carrier = AMAZON_EMAIL_CARRIER if parsed.merchant == "Amazon" else parsed.merchant_domain
        upsert_key = getattr(self.store, "upsert_shipment_key", None)
        if callable(upsert_key):
            # The real store's key-based idempotent path.
            shipment_id = upsert_key(
                key=key,
                carrier=carrier,
                merchant=parsed.merchant,
                merchant_domain=parsed.merchant_domain,
                source="email",
                label=parsed.item_summary,
            )
        else:
            # Duck-typed fallback for older fakes (keyword form).
            shipment_id = self.store.upsert_shipment(
                key=key,
                carrier=carrier,
                merchant=parsed.merchant,
                merchant_domain=parsed.merchant_domain,
                source="email",
                label=parsed.item_summary,
            )
        event = TrackingEvent(
            milestone=parsed.milestone,
            description=parsed.description,
            timestamp=parsed.timestamp,
            raw={
                "source": "email",  # UI must never confuse this with API tracking
                "kind": parsed.kind,
                "subject": parsed.subject,
                "photo_on_delivery": parsed.photo_on_delivery,
                "order_id": parsed.order_id,
                **parsed.raw,
            },
        )
        self.store.append_event(shipment_id, event)
        for number in parsed.tracking_numbers:
            self.store.add_number(shipment_id, number, carrier)
        return True

    def process(self, messages: Any) -> BatchResult:
        """Process a batch of messages; never raises on bad input."""
        result = BatchResult()
        for message in messages:
            try:
                if self.process_message(message):
                    result.consumed += 1
                else:
                    result.skipped += 1
            except Exception:
                result.errors += 1
                log.exception("email-event channel failed on a message; skipped")
        return result
