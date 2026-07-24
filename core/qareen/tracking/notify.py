"""Milestone nudges + alerts for the tracking subsystem.

Sends notifications on the milestones that matter to a human
(``out_for_delivery``, ``delivered``, ``exception``) through two
best-effort channels:

a) **qareen.db ``notifications`` table** — schema mirrors
   ``core/qareen/api/notifications.py`` so the dashboard/notification tray
   picks the rows up directly — plus an emit on the qareen EventBus when a
   bus instance is injected (the import is guarded so the scheduler also
   works standalone, outside the qareen runtime).
b) **Telegram direct** — bot token/chat id from Keychain
   (``TELEGRAM_BOT_TOKEN``/``TELEGRAM_CHAT_ID`` via agent-secret) +
   stdlib urllib, same pattern as ``core/bin/internal/scheduler``.

Per-category preferences decide loud vs silent: loud = both channels,
silent = dashboard row only (no push). Defaults live in
``CATEGORY_DEFAULTS`` (business → loud, uncategorized → silent); the
tracking store's ``domain_rules`` table overrides them via the duck-typed
``category_preference(category)`` method.

Dedup: a given (shipment, milestone) nudges at most once — in-memory plus
persisted in the store's ``tracking_state`` when available, so restarts
don't re-nudge.

Everything is wrapped: a notification failure NEVER breaks a poll run.

Compatible with system Python 3.9.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .client import Transport, TransportResponse, agent_secret_get, urllib_transport
from .models import Milestone

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

# Milestones that push a nudge. failed_attempt/returned are deliberately
# not here — they ride along as events, not interruptions.
NUDGE_MILESTONES = (
    Milestone.OUT_FOR_DELIVERY,
    Milestone.DELIVERED,
    Milestone.EXCEPTION,
)

# Per-category loud/silent defaults (category is matched case-insensitively,
# on the full category string and on its top-level segment before "/").
# Loud = dashboard + Telegram. Silent = dashboard row only. The store's
# domain_rules table overrides these via category_preference().
CATEGORY_DEFAULTS: Dict[str, str] = {
    "business": "loud",
    "uncategorized": "silent",
}
DEFAULT_PREFERENCE = "silent"  # unknown categories stay quiet

_LOUD = "loud"
_SILENT = "silent"

_TITLES = {
    Milestone.OUT_FOR_DELIVERY: "Out for delivery",
    Milestone.DELIVERED: "Delivered",
    Milestone.EXCEPTION: "Delivery exception",
}


class Notifier:
    """Best-effort, never-raises notification fan-out.

    Parameters
    ----------
    db_path:
        qareen.db path (injectable for tests). The ``notifications`` table
        is created if missing, mirroring api/notifications.py.
    secret_getter / telegram_transport:
        Injectable for tests; default to agent-secret + urllib.
    bus:
        Optional qareen EventBus instance. When given (and the qareen
        events package imports), milestone events are emitted as
        ``shipment.milestone`` events. Absent bus = standalone mode.
    store:
        Optional duck-typed tracking store, used for:
          - ``category_preference(category) -> "loud"|"silent"|None``
            (domain_rules override; failures fall back to code defaults)
          - ``get_state(key)`` / ``set_state(key, value)`` for restart-
            persistent nudge dedup
        Any store method failure is swallowed — notifications degrade,
        they never break.
    clock:
        ``() -> datetime`` (UTC); injectable for tests.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        secret_getter: Optional[Callable[[str], Optional[str]]] = None,
        telegram_transport: Optional[Transport] = None,
        bus: Optional[Any] = None,
        store: Optional[Any] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._secret_getter = secret_getter or agent_secret_get
        self._transport = telegram_transport or urllib_transport
        self._bus = bus
        self._store = store
        self._clock = clock or datetime.utcnow
        self._sent: set = set()  # (shipment_key, milestone_value)

    # ── public API ────────────────────────────────────────────────────

    def notify_milestone(
        self,
        shipment: Any,
        milestone: Milestone,
        event: Optional[Any] = None,
    ) -> bool:
        """Nudge for one shipment milestone transition.

        *shipment* is a dict or object with fields: id, tracking_number,
        carrier, and optionally label/merchant/category. Returns True when
        a nudge was actually sent (False = deduped, not a nudge milestone,
        or silent-and-dashboard-insert-failed). Never raises.
        """
        try:
            milestone = Milestone(milestone)
        except ValueError:
            return False
        if milestone not in NUDGE_MILESTONES:
            return False
        try:
            return self._send_nudge(shipment, milestone, event)
        except Exception:
            logger.exception("milestone nudge failed")
            return False

    def alert(self, title: str, body: str) -> bool:
        """Operational alert (auth self-test failure, tracker down).
        Always loud: dashboard (urgent) + Telegram. Never raises."""
        try:
            self._insert_notification(
                notif_type="tracker.alert",
                title=title,
                body=body,
                priority="urgent",
                channels=["app", "telegram"],
            )
        except Exception:
            logger.exception("alert dashboard insert failed: %s", title)
        try:
            self._emit_bus("tracker.alert", {"title": title, "body": body})
        except Exception:
            logger.exception("alert bus emit failed: %s", title)
        return self._send_telegram("%s\n%s" % (title, body))

    # ── nudge internals ───────────────────────────────────────────────

    def _send_nudge(
        self, shipment: Any, milestone: Milestone, event: Optional[Any]
    ) -> bool:
        number = _field(shipment, "tracking_number") or "?"
        ship_key = _field(shipment, "id") or number
        if self._already_sent(ship_key, milestone):
            return False

        category = (_field(shipment, "category") or "uncategorized")
        loud = self._preference(category) == _LOUD
        carrier = _field(shipment, "carrier") or ""
        label = (
            _field(shipment, "label")
            or _field(shipment, "merchant")
            or number
        )
        title = "%s: %s" % (_TITLES[milestone], label)
        parts = ["%s %s" % (carrier, number) if carrier else number]
        description = event is not None and _field(event, "description") or None
        if description:
            parts.append(description)
        location = event is not None and _field(event, "location") or None
        if location:
            parts.append(str(location))
        body = " — ".join(str(p) for p in parts if p)

        channels = ["app", "telegram"] if loud else ["app"]
        self._insert_notification(
            notif_type="shipment.%s" % milestone.value,
            title=title,
            body=body,
            priority="normal" if milestone != Milestone.EXCEPTION else "high",
            channels=channels,
        )
        self._emit_bus(
            "shipment.milestone",
            {
                "shipment_id": ship_key,
                "tracking_number": number,
                "carrier": carrier,
                "milestone": milestone.value,
                "category": category,
                "loud": loud,
                "title": title,
                "body": body,
            },
        )
        telegram_ok: Optional[bool] = True  # silent categories skip Telegram
        if loud:
            telegram_ok = self._send_telegram("%s\n%s" % (title, body))
        self._mark_sent(ship_key, milestone)
        # True = delivered (or Telegram gracefully unconfigured); False only
        # when a loud Telegram send was attempted and failed.
        return telegram_ok is not None

    # ── category preferences ──────────────────────────────────────────

    def _preference(self, category: str) -> str:
        """Store domain_rules override first, then code defaults."""
        lowered = (category or "").strip().lower()
        top = lowered.split("/", 1)[0].strip()
        if self._store is not None:
            for candidate in (category, lowered, top):
                if not candidate:
                    continue
                try:
                    pref = self._store.category_preference(candidate)
                except Exception:
                    break  # best-effort override; fall through to defaults
                if pref in (_LOUD, _SILENT):
                    return pref
        return CATEGORY_DEFAULTS.get(
            lowered, CATEGORY_DEFAULTS.get(top, DEFAULT_PREFERENCE)
        )

    # ── dedup ─────────────────────────────────────────────────────────

    def _dedup_key(self, ship_key: str, milestone: Milestone) -> str:
        return "notified:%s:%s" % (ship_key, milestone.value)

    def _already_sent(self, ship_key: str, milestone: Milestone) -> bool:
        key = (str(ship_key), milestone.value)
        if key in self._sent:
            return True
        if self._store is not None:
            try:
                if self._store.get_state(self._dedup_key(str(ship_key), milestone)):
                    self._sent.add(key)
                    return True
            except Exception:
                pass
        return False

    def _mark_sent(self, ship_key: str, milestone: Milestone) -> None:
        self._sent.add((str(ship_key), milestone.value))
        if self._store is not None:
            try:
                self._store.set_state(
                    self._dedup_key(str(ship_key), milestone),
                    self._clock().isoformat(),
                )
            except Exception:
                pass

    # ── channel (a): qareen.db + EventBus ─────────────────────────────

    def _insert_notification(
        self,
        notif_type: str,
        title: str,
        body: str,
        priority: str,
        channels: list,
    ) -> None:
        """Insert a row into qareen.db's notifications table — same schema
        the API router maintains, so the tray/SSE pick it up."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id          TEXT PRIMARY KEY,
                    type        TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    body        TEXT,
                    priority    TEXT DEFAULT 'normal',
                    created_at  TEXT NOT NULL,
                    read        INTEGER DEFAULT 0,
                    dismissed   INTEGER DEFAULT 0,
                    action_url  TEXT,
                    channels    TEXT
                )
                """
            )
            conn.execute(
                """INSERT INTO notifications
                   (id, type, title, body, priority, created_at, action_url, channels)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "n_%s" % uuid.uuid4().hex[:12],
                    notif_type,
                    title,
                    body,
                    priority,
                    self._clock().isoformat(),
                    None,
                    json.dumps(channels),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _emit_bus(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit on the injected qareen EventBus. Import-guarded: without the
        qareen runtime (standalone scheduler), this is a no-op."""
        if self._bus is None:
            return
        try:
            from qareen.events.types import Event
        except ImportError:
            return
        event = Event(event_type=event_type, source="tracking", payload=payload)
        result = self._bus.emit(event)
        if asyncio.iscoroutine(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                loop.create_task(result)

    # ── channel (b): Telegram ─────────────────────────────────────────

    def _send_telegram(self, text: str) -> Optional[bool]:
        """Direct Telegram bot send. Tri-state, mirroring the pattern in
        core/bin/internal/scheduler: True = sent, False = Telegram not
        configured (graceful skip), None = send attempted but failed."""
        try:
            token = self._secret_getter("TELEGRAM_BOT_TOKEN")
            chat_id = self._secret_getter("TELEGRAM_CHAT_ID")
        except Exception:
            return None
        if not token or not chat_id:
            return False
        try:
            data = json.dumps({"chat_id": chat_id, "text": text}).encode()
            resp: TransportResponse = self._transport(
                "POST",
                "https://api.telegram.org/bot%s/sendMessage" % token,
                {"Content-Type": "application/json"},
                data,
            )
            if resp.status >= 400:
                logger.warning("telegram send failed: HTTP %d", resp.status)
                return None
            return True
        except Exception:
            logger.exception("telegram send failed")
            return None


def _field(obj: Any, name: str) -> Optional[Any]:
    """Read *name* from a dict or an object; None when absent."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
