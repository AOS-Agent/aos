"""Due-queue tracking scheduler.

Not cron: every shipment carries a persisted ``next_poll_at``; the poller
processes what is due. Restarts/reboots catch up instead of skipping or
double-firing. A singleton lock (persisted in the store's ``tracking_state``
with owner + timestamp, stale after a timeout) keeps launchd KeepAlive
from running two pollers at once.

Cadence is state-driven: ``label_created`` ~3x/day, ``picked_up`` 4x/day,
``in_transit`` ~5x/day, ``out_for_delivery`` hourly, and terminal states
(``delivered``/``returned``/``expired``) stop polling and auto-archive.

Budgets come from each pack manifest's ``rate_limits`` and are enforced as
a per-carrier daily token bucket plus a minimum-interval pacer, both
persisted in ``tracking_state`` so a restart doesn't reset spend. HTTP 429
trips an exponential backoff with a persisted "quota exhausted until"
marker; while the marker is set, that carrier's shipments are skipped and
rescheduled past it.

Probes (the detection layer's ambiguous-candidate checks) draw from a
SEPARATE daily budget with a circuit breaker — probes can never starve
the poll budget. ``probe_allow``/``record_probe`` are the detection
layer's entry points.

Milestone transitions to out_for_delivery / delivered / exception call the
injected notifier (see notify.py).

Store protocol (duck-typed; the real implementation is
``qareen.tracking.store``):

    due_shipments(now_iso: str, limit: int) -> iterable of shipments
        Each shipment is a dict or object with at least:
        id, tracking_number, carrier, milestone, status, next_poll_at;
        optionally label, merchant, category (used for notify routing).
    append_event(shipment_id, event: TrackingEvent) -> None
    update_shipment(shipment_id, **fields) -> bool
        The real store's field updater (allowlisted columns). Fields
        written here: milestone, status, next_poll_at, eta (ISO strings
        or None). Duck-typed fakes may instead expose the legacy
        ``upsert_shipment(shipment_id, fields: dict)`` — the scheduler
        prefers update_shipment and falls back.
    add_number(shipment_id, number, carrier=..., role=...) -> None
        Optional; handoff numbers parsed from poll responses are
        registered through it when present.
    get_state(key: str) -> Optional[str]
    set_state(key: str, value: str) -> None

Compatible with system Python 3.9.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import socket
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from . import engine, handoff, jsonpath
from .client import (
    CarrierAuthError,
    CarrierClient,
    RateLimited,
    Transport,
)
from .models import HAPPY_PATH, Milestone
from .notify import NUDGE_MILESTONES, Notifier, _field
from .packs import CarrierPack

logger = logging.getLogger(__name__)

# ── cadence (seconds between polls per milestone) ─────────────────────────
# label_created 2-4x/day, in_transit 4-6x/day, out_for_delivery hourly.
CADENCE_SECONDS = {
    Milestone.LABEL_CREATED: 8 * 3600,  # 3x/day
    Milestone.PICKED_UP: 6 * 3600,  # 4x/day
    Milestone.IN_TRANSIT: 5 * 3600,  # ~5x/day
    Milestone.OUT_FOR_DELIVERY: 1 * 3600,  # hourly
    Milestone.EXCEPTION: 4 * 3600,  # keep watching an exception
    Milestone.FAILED_ATTEMPT: 4 * 3600,
}
DEFAULT_CADENCE_SECONDS = 6 * 3600
ERROR_RETRY_SECONDS = 3600  # generic poll failure: try again in an hour
AUTH_ERROR_RETRY_SECONDS = 6 * 3600  # bad credentials: back off harder
NO_PACK_RETRY_SECONDS = 24 * 3600  # shipment whose carrier has no pack

# ── backoff / budgets ─────────────────────────────────────────────────────
BACKOFF_BASE_SECONDS = 300  # 429 with no Retry-After: 5m, 10m, 20m, …
BACKOFF_MAX_SECONDS = 6 * 3600
PROBE_BUDGET_FRACTION = 0.1  # probes get ~10% of the daily budget, separately
PROBE_MIN_BUDGET = 2
PROBE_FAILURE_THRESHOLD = 5  # consecutive probe failures trip the breaker
PROBE_CIRCUIT_OPEN_SECONDS = 3600

DEFAULT_LOCK_TTL_SECONDS = 300  # singleton lock goes stale after 5 minutes
DEFAULT_MAX_PER_RUN = 50

LOCK_KEY = "scheduler:lock"
_RANK_OFF_PATH = len(HAPPY_PATH)  # exception/failed_attempt/… outrank happy path


def _epoch(dt: datetime) -> float:
    """Naive-UTC datetime → epoch seconds (timezone-free, stable across
    processes regardless of host TZ)."""
    return float(calendar.timegm(dt.utctimetuple()))


def _milestone_rank(milestone: Milestone) -> int:
    try:
        return HAPPY_PATH.index(milestone)
    except ValueError:
        return _RANK_OFF_PATH


class TrackingScheduler:
    """The tracking poller. One instance per process; ``run_once(now)`` is
    called by the outer service loop (launchd KeepAlive wrapper).

    Parameters
    ----------
    store:
        Duck-typed tracking store (see module docstring).
    packs:
        ``{slug: CarrierPack}`` from ``packs.load_packs()``.
    client_factory:
        ``(pack) -> CarrierClient``; injectable for tests. Clients are
        cached per carrier so OAuth token caches persist across polls.
    notifier:
        ``notify.Notifier`` (or compatible); None = no nudges/alerts.
    owner:
        Identity written into the singleton lock; defaults to host:pid.
    """

    def __init__(
        self,
        store: Any,
        packs: Dict[str, CarrierPack],
        client_factory: Optional[Callable[[CarrierPack], CarrierClient]] = None,
        notifier: Optional[Notifier] = None,
        owner: Optional[str] = None,
        lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
        max_per_run: int = DEFAULT_MAX_PER_RUN,
        secret_getter: Optional[Callable[[str], Optional[str]]] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        self.store = store
        self.packs = dict(packs)
        self.notifier = notifier
        self.owner = owner or "%s:%d" % (socket.gethostname(), os.getpid())
        self.lock_ttl = lock_ttl_seconds
        self.max_per_run = max_per_run
        self._secret_getter = secret_getter
        self._transport = transport
        self._client_factory = client_factory
        self._clients: Dict[str, CarrierClient] = {}

    # ── main entry ────────────────────────────────────────────────────

    def run_once(self, now: datetime) -> Dict[str, Any]:
        """Process all due shipments. Returns a run report dict.

        When another live poller holds the singleton lock, returns
        immediately with ``{"locked": True, ...}``.
        """
        report: Dict[str, Any] = {
            "locked": False,
            "polled": 0,
            "transitions": 0,
            "archived": 0,
            "skipped_quota": [],
            "rate_limited": [],
            "auth_errors": [],
            "errors": [],
            "notifications": 0,
        }
        if not self._acquire_lock(now):
            report["locked"] = True
            logger.info("tracking poller: another instance holds the lock; skipping")
            return report
        try:
            exhausted: Dict[str, datetime] = {}  # carrier → quota marker
            inert = self._inert_carriers()  # scaffolded packs: lifecycle-gated
            due = list(
                self.store.due_shipments(now.isoformat(), self.max_per_run) or []
            )
            for shipment in due:
                carrier = str(_field(shipment, "carrier") or "")
                if carrier in exhausted:
                    continue  # 429 earlier this run; leave for the marker
                if carrier in inert:
                    continue  # scaffolded pack — inert until canary/active
                try:
                    outcome = self._poll_one(shipment, now, report)
                except RateLimited as exc:
                    until = self._trip_backoff(carrier, now, exc.retry_after)
                    exhausted[carrier] = until
                    report["rate_limited"].append(carrier)
                    self._reschedule(shipment, until, now)
                except CarrierAuthError as exc:
                    report["auth_errors"].append("%s: %s" % (carrier, exc))
                    self._reschedule_after(shipment, AUTH_ERROR_RETRY_SECONDS, now)
                except Exception as exc:  # never let one shipment kill the run
                    logger.exception("poll failed for %s", _field(shipment, "id"))
                    report["errors"].append(
                        "%s/%s: %s: %s"
                        % (carrier, _field(shipment, "tracking_number"),
                           type(exc).__name__, exc)
                    )
                    self._reschedule_after(shipment, ERROR_RETRY_SECONDS, now)
                else:
                    if outcome == "quota":
                        until = self._quota_marker(carrier, now)
                        if until is not None:
                            exhausted[carrier] = until
                        report["skipped_quota"].append(carrier)
            if report["auth_errors"] and self.notifier is not None:
                self.notifier.alert(
                    "Tracker auth rejected",
                    "Carrier credentials were rejected during polling: %s"
                    % "; ".join(report["auth_errors"]),
                )
        finally:
            self._release_lock()
        return report

    # ── per-shipment poll ─────────────────────────────────────────────

    def _poll_one(
        self, shipment: Any, now: datetime, report: Dict[str, Any]
    ) -> Optional[str]:
        """Poll one due shipment. Returns "quota" when the carrier budget
        said no (shipment left for later), None otherwise."""
        ship_id = _field(shipment, "id")
        number = str(_field(shipment, "tracking_number") or "")
        carrier = str(_field(shipment, "carrier") or "")
        pack = self.packs.get(carrier)
        if pack is None:
            logger.warning("no pack for carrier %r; rescheduling", carrier)
            self._reschedule_after(shipment, NO_PACK_RETRY_SECONDS, now)
            report["errors"].append("%s: no carrier pack" % carrier)
            return None
        if not (pack.endpoints or {}).get("base"):
            # Pseudo-carrier (e.g. amazon-email): no HTTP surface — its
            # events arrive via the email-event channel, never via polling.
            return None

        # Persisted quota marker (set by a prior 429 or daily exhaustion).
        marker = self._quota_marker(carrier, now)
        if marker is not None:
            self._reschedule(shipment, marker, now)
            return "quota"

        # Daily token bucket + min-interval pacer (persisted).
        bucket_status = self._bucket_acquire(carrier, now)
        if bucket_status == "exhausted":
            until = self._next_midnight(now)
            self._set_quota_marker(carrier, until)
            self._reschedule(shipment, until, now)
            return "quota"
        if bucket_status == "paced":
            return None  # too soon after the last call; still due next run

        client = self._client(pack)
        data = client.track(number)  # typed errors propagate to run_once
        self._clear_backoff(carrier)
        report["polled"] += 1
        self._register_handoffs(ship_id, number, carrier, data)

        raw_events = jsonpath.extract(data, pack.response_map.get("events"))
        if raw_events is None:
            raw_events = []
        if not isinstance(raw_events, list):
            raw_events = [raw_events]

        current = _current_milestone(shipment)
        best_event = None
        best = current
        for idx, raw in enumerate(raw_events):
            if not isinstance(raw, dict):
                continue
            event = engine.normalize_event(pack, raw)
            event.fetched_at = now
            event.seq = idx
            self.store.append_event(ship_id, event)
            if event.milestone is not None and _milestone_rank(
                event.milestone
            ) >= _milestone_rank(best):
                best, best_event = event.milestone, event

        fields: Dict[str, Any] = {"updated_at": now.isoformat()}
        eta = _parse_eta(jsonpath.extract(data, pack.response_map.get("eta")))
        if eta is not None:
            fields["eta"] = eta

        transitioned = best != current
        if transitioned:
            fields["milestone"] = best.value
            report["transitions"] += 1
        if Milestone.is_terminal(best):
            # delivered/returned/expired → stop + auto-archive
            fields["status"] = "archived"
            fields["next_poll_at"] = None
            report["archived"] += 1
        else:
            interval = CADENCE_SECONDS.get(best, DEFAULT_CADENCE_SECONDS)
            fields["next_poll_at"] = (now + timedelta(seconds=interval)).isoformat()
        self._update_fields(ship_id, fields)

        if transitioned and best in NUDGE_MILESTONES and self.notifier is not None:
            sent = self.notifier.notify_milestone(shipment, best, event=best_event)
            if sent:
                report["notifications"] += 1
        return None

    # ── clients ───────────────────────────────────────────────────────

    def _client(self, pack: CarrierPack) -> CarrierClient:
        if pack.slug not in self._clients:
            if self._client_factory is not None:
                self._clients[pack.slug] = self._client_factory(pack)
            else:
                self._clients[pack.slug] = CarrierClient(
                    pack,
                    secret_getter=self._secret_getter,
                    transport=self._transport,
                )
        return self._clients[pack.slug]

    # ── singleton lock (tracking_state) ───────────────────────────────

    def _acquire_lock(self, now: datetime) -> bool:
        """Take the poller singleton lock unless a live peer holds it.
        A lock older than lock_ttl is stale (crashed/rebooted holder) and
        is taken over."""
        try:
            raw = self.store.get_state(LOCK_KEY)
        except Exception:
            raw = None  # unreadable state must not stop polling
        if raw:
            try:
                data = json.loads(raw)
                at = float(data.get("at", 0))
                owner = data.get("owner", "")
            except (ValueError, TypeError, AttributeError):
                at, owner = 0.0, ""
            if owner != self.owner and (_epoch(now) - at) < self.lock_ttl:
                return False
        try:
            self.store.set_state(
                LOCK_KEY,
                json.dumps({"owner": self.owner, "at": _epoch(now)}),
            )
        except Exception:
            logger.exception("failed to write poller lock")
            return False
        return True

    def _release_lock(self) -> None:
        try:
            self.store.set_state(LOCK_KEY, "")
        except Exception:
            logger.exception("failed to release poller lock")

    # ── daily token bucket + pacing (tracking_state) ──────────────────

    def _bucket_key(self, carrier: str) -> str:
        return "bucket:%s" % carrier

    def _bucket_limits(self, carrier: str) -> tuple:
        pack = self.packs.get(carrier)
        limits = (pack.rate_limits if pack else {}) or {}
        per_day = int(limits.get("requests_per_day") or 100)
        min_interval = float(limits.get("min_interval_seconds") or 0)
        return per_day, min_interval

    def _bucket_acquire(self, carrier: str, now: datetime) -> str:
        """Consume one daily token. Returns "ok" | "exhausted" | "paced"."""
        per_day, min_interval = self._bucket_limits(carrier)
        day = now.date().isoformat()
        epoch = _epoch(now)
        state = self._read_json(self._bucket_key(carrier)) or {}
        if state.get("day") != day:
            state = {"day": day, "tokens": per_day, "last_at": 0.0}
        last_at = float(state.get("last_at") or 0.0)
        if min_interval and last_at and epoch - last_at < min_interval:
            return "paced"
        tokens = int(state.get("tokens") or 0)
        if tokens <= 0:
            return "exhausted"
        state["tokens"] = tokens - 1
        state["last_at"] = epoch
        self._write_json(self._bucket_key(carrier), state)
        return "ok"

    # ── quota-exhausted marker (429 backoff / daily exhaustion) ───────

    def _quota_key(self, carrier: str) -> str:
        return "quota:%s:exhausted_until" % carrier

    def _quota_marker(self, carrier: str, now: datetime) -> Optional[datetime]:
        raw = None
        try:
            raw = self.store.get_state(self._quota_key(carrier))
        except Exception:
            pass
        if not raw:
            return None
        try:
            until_epoch = float(raw)
        except (TypeError, ValueError):
            return None
        if _epoch(now) >= until_epoch:
            return None  # marker expired
        return datetime.utcfromtimestamp(until_epoch)

    def _set_quota_marker(self, carrier: str, until: datetime) -> None:
        try:
            self.store.set_state(self._quota_key(carrier), repr(_epoch(until)))
        except Exception:
            logger.exception("failed to persist quota marker for %s", carrier)

    def _trip_backoff(
        self, carrier: str, now: datetime, retry_after: Optional[float]
    ) -> datetime:
        """Exponential backoff on 429: persists the attempt count and the
        quota-exhausted-until marker; returns the marker time."""
        key = "backoff:%s" % carrier
        state = self._read_json(key) or {}
        count = int(state.get("count") or 0)
        if retry_after is not None and retry_after > 0:
            delay = retry_after
        else:
            delay = min(BACKOFF_BASE_SECONDS * (2 ** count), BACKOFF_MAX_SECONDS)
        until = now + timedelta(seconds=delay)
        self._write_json(key, {"count": count + 1})
        self._set_quota_marker(carrier, until)
        logger.warning(
            "carrier %s rate-limited; backing off until %s", carrier, until
        )
        return until

    def _clear_backoff(self, carrier: str) -> None:
        try:
            self.store.set_state("backoff:%s" % carrier, "")
        except Exception:
            pass

    # ── probe budget + circuit breaker (separate from poll budget) ────

    def _probe_key(self, carrier: str) -> str:
        return "probe:%s" % carrier

    def probe_budget(self, carrier: str) -> int:
        """Daily probe allowance for a carrier (~10% of its poll budget)."""
        per_day, _ = self._bucket_limits(carrier)
        return max(PROBE_MIN_BUDGET, int(per_day * PROBE_BUDGET_FRACTION))

    def probe_allow(self, carrier: str, now: datetime) -> bool:
        """True iff a detection probe may fire for this carrier now:
        within the separate daily probe budget and the circuit breaker
        closed."""
        state = self._read_json(self._probe_key(carrier)) or {}
        day = now.date().isoformat()
        if state.get("day") != day:
            state = {"day": day, "used": 0, "failures": 0, "open_until": 0.0}
        open_until = float(state.get("open_until") or 0.0)
        if open_until and _epoch(now) < open_until:
            return False  # circuit breaker open
        return int(state.get("used") or 0) < self.probe_budget(carrier)

    def record_probe(self, carrier: str, ok: bool, now: datetime) -> None:
        """Record one probe attempt. ``PROBE_FAILURE_THRESHOLD`` consecutive
        failures open the circuit for ``PROBE_CIRCUIT_OPEN_SECONDS``."""
        key = self._probe_key(carrier)
        state = self._read_json(key) or {}
        day = now.date().isoformat()
        if state.get("day") != day:
            state = {"day": day, "used": 0, "failures": 0, "open_until": 0.0}
        state["used"] = int(state.get("used") or 0) + 1
        if ok:
            state["failures"] = 0
        else:
            state["failures"] = int(state.get("failures") or 0) + 1
            if state["failures"] >= PROBE_FAILURE_THRESHOLD:
                state["open_until"] = _epoch(now) + PROBE_CIRCUIT_OPEN_SECONDS
                state["failures"] = 0
                logger.warning("probe circuit breaker OPEN for %s", carrier)
        self._write_json(key, state)

    # ── small helpers ─────────────────────────────────────────────────

    def _update_fields(self, shipment_id: Any, fields: Dict[str, Any]) -> None:
        """Write scheduler-owned fields through the store's field updater.

        The real store exposes ``update_shipment(id, **fields)`` with an
        allowlist — ``updated_at`` is not a real column (the store bumps
        ``updated`` itself), so it is stripped here. Older duck-typed fakes
        expose ``upsert_shipment(id, fields_dict)``; that path is kept as
        the fallback.
        """
        update = getattr(self.store, "update_shipment", None)
        if callable(update):
            payload = {k: v for k, v in fields.items() if k != "updated_at"}
            if payload:
                update(shipment_id, **payload)
            return
        upsert = getattr(self.store, "upsert_shipment", None)
        if callable(upsert):
            upsert(shipment_id, fields)

    def _register_handoffs(
        self, ship_id: Any, number: str, carrier: str, data: Any
    ) -> None:
        """Register linked last-mile numbers parsed from the poll response
        (DHL eCommerce → USPS, UPS Mail Innovations → USPS, S10 cross-post).
        Best-effort: stores without add_number, or unparseable payloads,
        just skip."""
        add_number = getattr(self.store, "add_number", None)
        if not callable(add_number):
            return
        try:
            links = handoff.extract_from_response(data, exclude=[number])
        except Exception:
            logger.exception("handoff extraction failed for %s", ship_id)
            return
        for link in links:
            try:
                add_number(
                    ship_id,
                    link.number,
                    carrier=link.carrier or carrier,
                    role=link.role,
                )
            except Exception:
                logger.exception("handoff persist failed for %s", ship_id)

    def _inert_carriers(self) -> set:
        """Carriers whose pack lifecycle state is ``scaffolded`` — inert:
        no polling until the pack is canaried/graduated (onboarding §8).
        ``canary`` packs DO poll (they track silently); packs with no
        recorded state default to active. Any state-read failure means
        "not inert" — polling must never stop on a lifecycle lookup bug.
        """
        inert = set()
        try:
            from . import onboard
        except Exception:
            return inert
        for slug in self.packs:
            try:
                state = onboard.carrier_state(self.store, slug).get("state")
            except Exception:
                continue
            if state == onboard.STATE_SCAFFOLDED:
                inert.add(slug)
        return inert

    def _reschedule(self, shipment: Any, when: datetime, now: datetime) -> None:
        try:
            self._update_fields(
                _field(shipment, "id"),
                {"next_poll_at": when.isoformat(), "updated_at": now.isoformat()},
            )
        except Exception:
            logger.exception("reschedule failed for %s", _field(shipment, "id"))

    def _reschedule_after(self, shipment: Any, seconds: float, now: datetime) -> None:
        self._reschedule(shipment, now + timedelta(seconds=seconds), now)

    @staticmethod
    def _next_midnight(now: datetime) -> datetime:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                 microsecond=0)

    def _read_json(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self.store.get_state(key)
        except Exception:
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_json(self, key: str, value: Dict[str, Any]) -> None:
        try:
            self.store.set_state(key, json.dumps(value))
        except Exception:
            logger.exception("state write failed: %s", key)


def _current_milestone(shipment: Any) -> Milestone:
    raw = _field(shipment, "milestone")
    try:
        return Milestone(raw) if raw else Milestone.LABEL_CREATED
    except ValueError:
        return Milestone.LABEL_CREATED


def _parse_eta(value: Any) -> Optional[str]:
    """Carrier ETA → ISO string. Returns None when absent/unparseable —
    we never guess an ETA."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return None


# ── CLI ─────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    """``python3 -m qareen.tracking.scheduler --once [--db PATH]``.

    One due-queue pass against the real store: lifecycle-filtered packs
    (scaffolded inert, pseudo-carriers skipped), notifier wired to the same
    qareen.db, no event bus (cron context).
    """
    import argparse
    from datetime import timezone

    parser = argparse.ArgumentParser(
        prog="qareen.tracking.scheduler",
        description="Auto Tracker due-queue poller.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one poll pass and exit (the only mode today)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="qareen.db path (default ~/.aos/data/qareen.db)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    from . import onboard
    from .store import ShipmentStore

    store = ShipmentStore(args.db)
    packs = onboard.polling_packs(store)  # active+canary, HTTP-surfaced only
    notifier = Notifier(db_path=store.db_path, bus=None)
    scheduler = TrackingScheduler(store, packs, notifier=notifier)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    report = scheduler.run_once(now)
    print(
        "track-poll: polled=%(polled)d transitions=%(transitions)d "
        "archived=%(archived)d locked=%(locked)s errors=%(n_errors)d"
        % dict(report, n_errors=len(report.get("errors", [])))
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
