"""ShipmentStore — qareen.db storage layer for the Auto Tracker.

Owns every Auto Tracker table in qareen.db: shipments, shipment_events
(append-only), shipment_numbers (international handoffs), orders +
order_items + order_shipments (N:M), detection_priors, domain_rules,
shipment_candidates (approval queue), detection_eval, tracking_state
(key-value: watermarks, singleton lock, quota-exhausted-until, buckets).

Design rules baked in here (initiative adversarial audit):

- **Fork-don't-merge on recycled numbers.** FedEx/USPS reissue tracking
  numbers within months. ``upsert_shipment`` keys on
  (carrier, canonical number, first_seen window) and merges ONLY into a
  non-terminal row inside the window; a terminal/stale row means the
  number was recycled → a NEW shipment row is created. ``ingest_events``
  applies the same guard at poll time: if a terminal/archived shipment
  gets events inconsistent with stored history (new origin scan after
  delivery, or any event timestamped well after the terminal scan), the
  poll forks a new shipment instead of merging a stranger's package.
- **Events are append-only.** The carrier API is a source, never the
  store (carriers purge server-side history). ``shipment_events`` rows
  are never updated or deleted; ``seq`` is assigned here (max+1 per
  shipment) so ordering survives carrier clock skew.
- **Self-initializing.** ``_ensure_tables()`` mirrors migration 093's
  DDL (both share ``SCHEMA_SQL`` below), so the feature works even on a
  machine where migrations haven't run yet (house pattern).
- **SQLite contention.** WAL + busy_timeout=5000 on every connection;
  all writes are short single transactions.

Python 3.9-compatible: no ``X | Y`` runtime unions, no match statements.

Other Auto Tracker components should depend on a duck-typed subset of
this class (the methods they use), not on the concrete type, so tests
can substitute fakes.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from qareen.tracking.engine import canonicalize
from qareen.tracking.models import Milestone, Shipment, TrackingEvent

DEFAULT_DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"

# Shipment lifecycle: statuses that mean "this number is done with" — a
# poll or upsert against one of these implies a recycled number.
TERMINAL_STATUSES = ("delivered", "expired", "archived")

# Milestone values that stop polling even while status stays 'active'
# (returned has no matching status value, so due_shipments filters on
# milestone too).
TERMINAL_MILESTONE_VALUES = (
    Milestone.DELIVERED.value,
    Milestone.RETURNED.value,
    Milestone.EXPIRED.value,
)

# Early-journey scans. One of these arriving for a terminal shipment is
# the classic recycled-number signature: a new origin scan after delivery.
ORIGIN_MILESTONES = frozenset({Milestone.LABEL_CREATED, Milestone.PICKED_UP})

# An active (non-terminal) shipment older than this is treated as stale:
# a new upsert for the same number starts a fresh row instead of merging.
FIRST_SEEN_WINDOW = timedelta(days=180)

# Any poll event timestamped this far after the shipment's last stored
# scan (on a terminal shipment) is treated as a recycled number even when
# the milestone alone wouldn't prove it.
RECYCLE_TIMESTAMP_GAP = timedelta(days=7)

AUTO_TRACKER_TABLES = (
    "shipments",
    "shipment_events",
    "shipment_numbers",
    "orders",
    "order_items",
    "order_shipments",
    "detection_priors",
    "domain_rules",
    "shipment_candidates",
    "detection_eval",
    "tracking_state",
)

# Single source of truth for the Auto Tracker DDL. Migration 093 imports
# this verbatim and ShipmentStore._ensure_tables() executes it — the two
# can never drift.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shipments (
    id              TEXT PRIMARY KEY,
    tracking_number TEXT NOT NULL,          -- canonical (engine.canonicalize)
    carrier         TEXT NOT NULL,          -- pack slug, e.g. 'ups'
    direction       TEXT NOT NULL DEFAULT 'inbound',  -- inbound|outbound|return
    milestone       TEXT NOT NULL DEFAULT 'label_created',
    eta             TEXT,                   -- ISO 8601
    merchant        TEXT,
    merchant_domain TEXT,
    category        TEXT,                   -- from domain_rules / LLM
    label           TEXT,                   -- user-facing free-text label
    person_id       TEXT,                   -- ontology link (about → person)
    privacy_level   INTEGER NOT NULL DEFAULT 0,  -- propagated from people.db
    source          TEXT NOT NULL DEFAULT 'manual',  -- api|email|manual|digest
    confidence      REAL NOT NULL DEFAULT 1.0,
    status          TEXT NOT NULL DEFAULT 'active',  -- active|delivered|expired|archived
    first_seen      TEXT NOT NULL,          -- ISO 8601; part of the dedup key
    next_poll_at    TEXT,                   -- scheduler due-queue column
    created         TEXT NOT NULL,
    updated         TEXT NOT NULL
);
-- Hot paths: scheduler due-queue, number lookup/dedup.
CREATE INDEX IF NOT EXISTS idx_shipments_poll
    ON shipments(status, next_poll_at);
CREATE INDEX IF NOT EXISTS idx_shipments_number
    ON shipments(tracking_number);
CREATE INDEX IF NOT EXISTS idx_shipments_carrier_number
    ON shipments(carrier, tracking_number);

-- Append-only event store. The carrier API is a source, never the store.
CREATE TABLE IF NOT EXISTS shipment_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    seq         INTEGER NOT NULL,           -- assigned by the store (max+1)
    timestamp   TEXT,                       -- carrier-reported event time
    fetched_at  TEXT NOT NULL,              -- when we pulled it from the API
    milestone   TEXT,                       -- NULL when unmapped; raw kept
    description TEXT,
    location    TEXT,
    raw_json    TEXT,                       -- unmodified carrier event JSON
    UNIQUE(shipment_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_shipment_seq
    ON shipment_events(shipment_id, seq);

-- Many numbers per shipment: international handoffs (DHL eCommerce → USPS
-- last mile, UPS Mail Innovations → USPS, Chit Chats → USPS, S10 UPU
-- numbers shared across national posts) and multi-package orders.
CREATE TABLE IF NOT EXISTS shipment_numbers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    carrier     TEXT NOT NULL,              -- carrier this number belongs to
    number      TEXT NOT NULL,              -- canonical
    role        TEXT NOT NULL DEFAULT 'handoff',  -- primary|handoff
    created     TEXT NOT NULL,
    UNIQUE(shipment_id, carrier, number)
);
CREATE INDEX IF NOT EXISTS idx_numbers_lookup
    ON shipment_numbers(carrier, number);

CREATE TABLE IF NOT EXISTS orders (
    id              TEXT PRIMARY KEY,
    merchant        TEXT,
    merchant_domain TEXT,
    order_number    TEXT NOT NULL,
    order_date      TEXT,
    total           REAL,
    currency        TEXT,
    created         TEXT NOT NULL,
    updated         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_number
    ON orders(merchant_domain, order_number);

CREATE TABLE IF NOT EXISTS order_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  TEXT NOT NULL REFERENCES orders(id),
    name      TEXT NOT NULL,
    qty       INTEGER NOT NULL DEFAULT 1,
    price     REAL,
    sku       TEXT,
    image_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);

-- Shipments link to orders N:M (multi-package orders; combined shipments).
CREATE TABLE IF NOT EXISTS order_shipments (
    order_id    TEXT NOT NULL REFERENCES orders(id),
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    PRIMARY KEY (order_id, shipment_id)
);
CREATE INDEX IF NOT EXISTS idx_order_shipments_shipment
    ON order_shipments(shipment_id);

-- The detection flywheel: sender/domain → carrier hit rates.
CREATE TABLE IF NOT EXISTS detection_priors (
    kind    TEXT NOT NULL,                  -- sender|domain
    key     TEXT NOT NULL,                  -- sender address or domain
    carrier TEXT NOT NULL,
    hits    INTEGER NOT NULL DEFAULT 0,     -- candidate confirmed
    misses  INTEGER NOT NULL DEFAULT 0,     -- candidate rejected
    updated TEXT NOT NULL,
    PRIMARY KEY (kind, key, carrier)
);

-- User-editable domain → category + display name. One click assigns an
-- unknown domain and the rule sticks.
CREATE TABLE IF NOT EXISTS domain_rules (
    domain       TEXT PRIMARY KEY,
    category     TEXT,
    display_name TEXT,
    created      TEXT NOT NULL,
    updated      TEXT NOT NULL
);

-- Approval queue for low-confidence detections.
CREATE TABLE IF NOT EXISTS shipment_candidates (
    id             TEXT PRIMARY KEY,
    candidate_json TEXT NOT NULL,           -- full detection payload
    layer          TEXT NOT NULL,           -- detection layer that produced it
    confidence     REAL,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|rejected
    created        TEXT NOT NULL,
    resolved_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_status
    ON shipment_candidates(status, created);

-- Hand-labeled eval set for confidence-threshold tuning.
CREATE TABLE IF NOT EXISTS detection_eval (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_json TEXT NOT NULL,
    layer          TEXT NOT NULL,
    predicted      TEXT,                    -- what the pipeline decided
    label          TEXT,                    -- ground truth (human)
    labeled_at     TEXT
);

-- Key-value state: watermarks, singleton lock, per-carrier
-- quota-exhausted-until, token buckets.
CREATE TABLE IF NOT EXISTS tracking_state (
    key     TEXT PRIMARY KEY,
    value   TEXT,
    updated TEXT NOT NULL
);
"""

# Columns update_shipment() is allowed to touch. `id`, `tracking_number`,
# `carrier`, and `created` are identity — never mutable through this path.
_UPDATABLE_SHIPMENT_FIELDS = frozenset({
    "direction", "milestone", "eta", "merchant", "merchant_domain",
    "category", "label", "person_id", "privacy_level", "source",
    "confidence", "status", "first_seen", "next_poll_at",
})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Union[datetime, str, None]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_dt(value: Union[str, None]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class ShipmentStore:
    """Storage layer for the Auto Tracker, over qareen.db.

    ``db_path`` is constructor-injected so tests use tmp DBs; production
    callers pass nothing and get ``~/.aos/data/qareen.db``.
    """

    def __init__(self, db_path: Union[str, Path, None] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    # -- connection plumbing -------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """One short transaction per operation; commit on success."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_tables(self) -> None:
        """Idempotent self-init, mirroring migration 093 (shared SCHEMA_SQL)."""
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)

    # -- row mapping ----------------------------------------------------

    @staticmethod
    def _row_to_shipment(row: sqlite3.Row) -> Shipment:
        return Shipment(
            id=row["id"],
            tracking_number=row["tracking_number"],
            carrier=row["carrier"],
            direction=row["direction"],
            milestone=Milestone(row["milestone"]),
            status=row["status"],
            source=row["source"],
            eta=_parse_dt(row["eta"]),
            merchant=row["merchant"],
            merchant_domain=row["merchant_domain"],
            label=row["label"],
            confidence=row["confidence"],
            first_seen=_parse_dt(row["first_seen"]),
            created_at=_parse_dt(row["created"]),
            updated_at=_parse_dt(row["updated"]),
        )

    # -- shipments ------------------------------------------------------

    def _insert_shipment(
        self,
        conn: sqlite3.Connection,
        shipment: Shipment,
        *,
        category: Optional[str] = None,
        person_id: Optional[str] = None,
        privacy_level: int = 0,
        next_poll_at: Union[datetime, str, None] = None,
    ) -> str:
        now = _utcnow().isoformat()
        shipment_id = shipment.id or "shp_" + uuid.uuid4().hex[:12]
        number = canonicalize(shipment.tracking_number)
        first_seen = _iso(shipment.first_seen) or now
        conn.execute(
            """
            INSERT INTO shipments (
                id, tracking_number, carrier, direction, milestone, eta,
                merchant, merchant_domain, category, label, person_id,
                privacy_level, source, confidence, status, first_seen,
                next_poll_at, created, updated
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                shipment_id,
                number,
                shipment.carrier,
                shipment.direction,
                shipment.milestone.value,
                _iso(shipment.eta),
                shipment.merchant,
                shipment.merchant_domain,
                category,
                shipment.label,
                person_id,
                privacy_level,
                shipment.source,
                shipment.confidence,
                shipment.status,
                first_seen,
                _iso(next_poll_at),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO shipment_numbers
                (shipment_id, carrier, number, role, created)
            VALUES (?,?,?,?,?)
            """,
            (shipment_id, shipment.carrier, number, "primary", now),
        )
        return shipment_id

    def upsert_shipment(
        self,
        shipment: Shipment,
        *,
        category: Optional[str] = None,
        person_id: Optional[str] = None,
        privacy_level: Optional[int] = None,
        next_poll_at: Union[datetime, str, None] = None,
    ) -> Tuple[str, bool]:
        """Insert or merge a shipment. Returns (shipment_id, created).

        Canonical dedup key: (carrier, canonical number) + first_seen
        window. Merges into the newest row that is BOTH non-terminal and
        inside FIRST_SEEN_WINDOW; otherwise — no row, terminal row
        (delivered/expired/archived), or stale row — a NEW shipment is
        created. This is the upsert half of the number-recycling guard:
        carriers reissue numbers, so a finished number never absorbs a
        new package.
        """
        number = canonicalize(shipment.tracking_number)
        now = _utcnow()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM shipments
                WHERE carrier = ? AND tracking_number = ?
                ORDER BY created DESC
                """,
                (shipment.carrier, number),
            ).fetchall()

            target = None
            for row in rows:
                if row["status"] in TERMINAL_STATUSES:
                    continue
                first_seen = _parse_dt(row["first_seen"])
                if first_seen is not None and now - first_seen > FIRST_SEEN_WINDOW:
                    continue  # stale active row — treat as recycled
                target = row
                break

            if target is None:
                new_id = self._insert_shipment(
                    conn,
                    shipment,
                    category=category,
                    person_id=person_id,
                    privacy_level=privacy_level or 0,
                    next_poll_at=next_poll_at,
                )
                return new_id, True

            # Merge: refresh the fields a re-detection legitimately
            # improves. milestone/status are owned by append_event /
            # update_shipment, not by upsert, so a duplicate detection
            # never regresses shipment progress.
            conn.execute(
                """
                UPDATE shipments SET
                    direction       = ?,
                    eta             = COALESCE(?, eta),
                    merchant        = COALESCE(?, merchant),
                    merchant_domain = COALESCE(?, merchant_domain),
                    category        = COALESCE(?, category),
                    label           = COALESCE(?, label),
                    person_id       = COALESCE(?, person_id),
                    privacy_level   = COALESCE(?, privacy_level),
                    source          = ?,
                    confidence      = ?,
                    next_poll_at    = COALESCE(?, next_poll_at),
                    updated         = ?
                WHERE id = ?
                """,
                (
                    shipment.direction,
                    _iso(shipment.eta),
                    shipment.merchant,
                    shipment.merchant_domain,
                    category,
                    shipment.label,
                    person_id,
                    privacy_level,
                    shipment.source,
                    shipment.confidence,
                    _iso(next_poll_at),
                    now.isoformat(),
                    target["id"],
                ),
            )
            return target["id"], False

    def get_shipment(self, shipment_id: str) -> Optional[Shipment]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM shipments WHERE id = ?", (shipment_id,)
            ).fetchone()
        return self._row_to_shipment(row) if row else None

    def get_shipment_row(self, shipment_id: str) -> Optional[Dict[str, Any]]:
        """Full row as a dict — includes columns the Shipment dataclass
        doesn't model (category, person_id, privacy_level, next_poll_at)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM shipments WHERE id = ?", (shipment_id,)
            ).fetchone()
        return dict(row) if row else None

    def shipments_for_number(self, carrier: str, number: str) -> List[Shipment]:
        """All shipment rows for a (carrier, number) — >1 means the number
        was recycled and forked, newest first."""
        canonical = canonicalize(number)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM shipments
                WHERE carrier = ? AND tracking_number = ?
                ORDER BY created DESC
                """,
                (carrier, canonical),
            ).fetchall()
        return [self._row_to_shipment(r) for r in rows]

    def update_shipment(self, shipment_id: str, **fields: Any) -> bool:
        """Update allowlisted columns on a shipment; bumps `updated`.

        Datetime values are stored as ISO strings. Returns False when no
        valid fields were passed or the shipment doesn't exist.
        """
        unknown = set(fields) - _UPDATABLE_SHIPMENT_FIELDS
        if unknown:
            raise ValueError(
                "update_shipment: not updatable: %s" % ", ".join(sorted(unknown))
            )
        if not fields:
            return False
        assignments = []
        values: List[Any] = []
        for name, value in fields.items():
            assignments.append("%s = ?" % name)
            values.append(_iso(value) if isinstance(value, datetime) else value)
        assignments.append("updated = ?")
        values.append(_utcnow().isoformat())
        values.append(shipment_id)
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE shipments SET %s WHERE id = ?" % ", ".join(assignments),
                values,
            )
        return cur.rowcount > 0

    def due_shipments(
        self,
        now: Union[datetime, str, None] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Shipments the scheduler should poll right now.

        Active, scheduled (next_poll_at set and due), and not at a
        terminal milestone — `returned` has no matching status value, so
        the milestone filter is what stops polling those. ``limit`` caps
        how many due rows one run picks up (oldest-due first).
        """
        now_iso = _iso(now) if now else _utcnow().isoformat()
        placeholders = ",".join("?" for _ in TERMINAL_MILESTONE_VALUES)
        sql = """
            SELECT * FROM shipments
            WHERE status = 'active'
              AND next_poll_at IS NOT NULL
              AND next_poll_at <= ?
              AND milestone NOT IN (%s)
            ORDER BY next_poll_at
            """ % placeholders
        params: List[Any] = [now_iso] + list(TERMINAL_MILESTONE_VALUES)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # -- listing / summary (API + CLI reads) -------------------------------

    def list_shipments(
        self,
        status: Optional[str] = None,
        milestone: Optional[str] = None,
        category: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Filtered shipment rows (newest-updated first), as dicts.

        ``q`` is a case-insensitive substring match over tracking number,
        label, and merchant. This is the read behind GET /api/shipments and
        the CLI ``aos track list``.
        """
        where = []
        params: List[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if milestone:
            where.append("milestone = ?")
            params.append(milestone)
        if category:
            where.append("category = ?")
            params.append(category)
        if q:
            where.append(
                "(tracking_number LIKE ? OR label LIKE ? OR merchant LIKE ?)"
            )
            like = "%" + q + "%"
            params.extend([like, like, like])
        sql = "SELECT * FROM shipments"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def shipment_summary(self) -> Dict[str, int]:
        """Dashboard counts: active, arriving today, in exception."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                  SUM(CASE WHEN status = 'active' AND eta IS NOT NULL
                            AND date(eta) = date('now') THEN 1 ELSE 0 END
                      ) AS arriving_today,
                  SUM(CASE WHEN status = 'active'
                            AND milestone IN ('exception', 'failed_attempt')
                           THEN 1 ELSE 0 END) AS exceptions
                FROM shipments
                """
            ).fetchone()
        return {
            "active": (row["active"] or 0) if row else 0,
            "arriving_today": (row["arriving_today"] or 0) if row else 0,
            "exceptions": (row["exceptions"] or 0) if row else 0,
        }

    # -- detection-layer convenience ---------------------------------------

    def add_shipment(
        self,
        tracking_number: str,
        carrier: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        confidence: float = 1.0,
        layer: Optional[str] = None,
        merchant_domain: Optional[str] = None,
    ) -> Tuple[str, bool]:
        """Persist an auto-add detection; returns (shipment_id, created).

        Thin convenience over ``upsert_shipment`` for the detection layer:
        builds the Shipment, schedules the first poll immediately, and —
        on creation only — appends a note event carrying the source links
        (``raw["message_id"]`` feeds the ontology's received_via links).
        """
        shipment = Shipment(
            tracking_number=tracking_number,
            carrier=carrier,
            source=layer or "api",
            confidence=confidence,
            merchant_domain=merchant_domain,
            first_seen=_utcnow(),
        )
        shipment_id, created = self.upsert_shipment(
            shipment, next_poll_at=_utcnow()
        )
        if created and sources:
            raw: Dict[str, Any] = {"source": "detection", "sources": sources}
            first = sources[0] if sources else {}
            if isinstance(first, dict) and first.get("message_id") is not None:
                raw["message_id"] = first.get("message_id")
            self.append_event(
                shipment_id,
                TrackingEvent(
                    milestone=None,
                    description="Auto-detected via %s" % (layer or "detection"),
                    timestamp=_utcnow(),
                    fetched_at=_utcnow(),
                    raw=raw,
                ),
            )
        return shipment_id, created

    def upsert_shipment_key(
        self,
        *,
        key: str,
        carrier: str,
        merchant: Optional[str] = None,
        merchant_domain: Optional[str] = None,
        source: str = "email",
        label: Optional[str] = None,
    ) -> str:
        """Idempotent upsert keyed on an external identity (merchant order
        id, TBA number) rather than a full Shipment — the email-event
        channel's seam. Returns the shipment id."""
        shipment = Shipment(
            tracking_number=key,
            carrier=carrier,
            merchant=merchant,
            merchant_domain=merchant_domain,
            source=source,
            label=label,
            confidence=1.0,
            first_seen=_utcnow(),
        )
        shipment_id, _created = self.upsert_shipment(shipment)
        return shipment_id

    def get_priors(self, domain: str) -> Dict[str, float]:
        """Sender-domain → {carrier: hit rate} for detection context scoring.

        Carriers with no data yet (rate None) are omitted — a missing entry
        and a 0.0 entry mean different things to the scorer.
        """
        out: Dict[str, float] = {}
        for prior in self.priors_for("domain", (domain or "").strip().lower()):
            if prior["rate"] is not None:
                out[prior["carrier"]] = prior["rate"]
        return out

    # -- events (append-only) -------------------------------------------

    def _append_event(
        self, conn: sqlite3.Connection, shipment_id: str, event: TrackingEvent
    ) -> int:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM shipment_events WHERE shipment_id = ?",
            (shipment_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO shipment_events
                (shipment_id, seq, timestamp, fetched_at, milestone,
                 description, location, raw_json)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                shipment_id,
                seq,
                _iso(event.timestamp),
                _iso(event.fetched_at) or _utcnow().isoformat(),
                event.milestone.value if event.milestone else None,
                event.description,
                event.location,
                json.dumps(event.raw) if event.raw else None,
            ),
        )
        now = _utcnow().isoformat()
        if event.milestone is not None:
            # Terminal milestones flip status; everything else just
            # advances milestone. `returned` keeps status active — the
            # due_shipments milestone filter stops its polling.
            new_status = None
            if event.milestone == Milestone.DELIVERED:
                new_status = "delivered"
            elif event.milestone == Milestone.EXPIRED:
                new_status = "expired"
            conn.execute(
                """
                UPDATE shipments
                SET milestone = ?, status = COALESCE(?, status), updated = ?
                WHERE id = ?
                """,
                (event.milestone.value, new_status, now, shipment_id),
            )
        else:
            conn.execute(
                "UPDATE shipments SET updated = ? WHERE id = ?",
                (now, shipment_id),
            )
        return seq

    def append_event(self, shipment_id: str, event: TrackingEvent) -> int:
        """Append one event; assigns and returns its seq. Never updates or
        deletes existing events (carrier retention deletes history; we don't).
        """
        with self._conn() as conn:
            return self._append_event(conn, shipment_id, event)

    def ingest_events(
        self, shipment_id: str, events: List[TrackingEvent]
    ) -> Tuple[str, bool]:
        """Append a poll's events, with the number-recycling guard.

        Returns (shipment_id, forked). When the target shipment is
        terminal/archived and the incoming events are inconsistent with
        stored history — a new origin scan (label_created/picked_up)
        after delivery, or any event timestamped more than
        RECYCLE_TIMESTAMP_GAP after the last stored scan — the carrier
        has reissued the number: a NEW shipment row is forked (carrier,
        number, and merchant/person context copied; lifecycle reset) and
        the events land there. Otherwise they append to the existing
        shipment.
        """
        events = list(events)
        if not events:
            return shipment_id, False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM shipments WHERE id = ?", (shipment_id,)
            ).fetchone()
            if row is None:
                raise KeyError("unknown shipment: %s" % shipment_id)

            forked = False
            target_id = shipment_id
            if self._poll_is_inconsistent(conn, row, events):
                fork = Shipment(
                    tracking_number=row["tracking_number"],
                    carrier=row["carrier"],
                    direction=row["direction"],
                    status="active",
                    source=row["source"],
                    merchant=row["merchant"],
                    merchant_domain=row["merchant_domain"],
                    label=row["label"],
                    confidence=row["confidence"],
                    first_seen=_utcnow(),
                )
                target_id = self._insert_shipment(
                    conn,
                    fork,
                    category=row["category"],
                    person_id=row["person_id"],
                    privacy_level=row["privacy_level"],
                    next_poll_at=row["next_poll_at"],
                )
                forked = True

            for event in events:
                self._append_event(conn, target_id, event)
        return target_id, forked

    def _poll_is_inconsistent(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        events: List[TrackingEvent],
    ) -> bool:
        """True when events polled for a terminal/archived shipment cannot
        belong to it — the recycled-number signature."""
        if row["status"] not in TERMINAL_STATUSES:
            return False
        # New origin scan after delivery: unambiguous.
        if any(e.milestone in ORIGIN_MILESTONES for e in events):
            return True
        # Otherwise fall back to timing: anything scanned well after the
        # last stored scan of a finished shipment belongs to a new journey.
        last_ts = conn.execute(
            "SELECT MAX(timestamp) FROM shipment_events WHERE shipment_id = ?",
            (row["id"],),
        ).fetchone()[0]
        last = _parse_dt(last_ts)
        if last is None:
            return False
        for event in events:
            ts = (
                event.timestamp
                if isinstance(event.timestamp, datetime)
                else _parse_dt(event.timestamp)
            )
            if ts is not None and ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            if ts is not None and ts - last > RECYCLE_TIMESTAMP_GAP:
                return True
        return False

    def events_for(self, shipment_id: str) -> List[TrackingEvent]:
        """Stored events in append order (seq)."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM shipment_events
                WHERE shipment_id = ? ORDER BY seq
                """,
                (shipment_id,),
            ).fetchall()
        events = []
        for r in rows:
            events.append(
                TrackingEvent(
                    milestone=Milestone(r["milestone"]) if r["milestone"] else None,
                    description=r["description"] or "",
                    timestamp=_parse_dt(r["timestamp"]),
                    fetched_at=_parse_dt(r["fetched_at"]),
                    location=r["location"],
                    seq=r["seq"],
                    raw=json.loads(r["raw_json"]) if r["raw_json"] else {},
                )
            )
        return events

    # -- shipment numbers (handoffs) -------------------------------------

    def add_number(
        self,
        shipment_id: str,
        number: str,
        carrier: Optional[str] = None,
        role: str = "handoff",
    ) -> None:
        """Register an additional tracking number on a shipment (e.g. the
        USPS last-mile number parsed from a DHL handoff event). Carrier
        defaults to the shipment's own carrier; numbers are canonicalized.
        Idempotent per (shipment, carrier, number)."""
        canonical = canonicalize(number)
        with self._conn() as conn:
            if carrier is None:
                row = conn.execute(
                    "SELECT carrier FROM shipments WHERE id = ?", (shipment_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("unknown shipment: %s" % shipment_id)
                carrier = row["carrier"]
            conn.execute(
                """
                INSERT OR IGNORE INTO shipment_numbers
                    (shipment_id, carrier, number, role, created)
                VALUES (?,?,?,?,?)
                """,
                (shipment_id, carrier, canonical, role, _utcnow().isoformat()),
            )

    def link_number(self, carrier: str, number: str) -> Optional[str]:
        """Resolve a (carrier, number) to a shipment id via the numbers
        table (primary + handoff numbers). None when unknown."""
        canonical = canonicalize(number)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT shipment_id FROM shipment_numbers
                WHERE carrier = ? AND number = ?
                ORDER BY id DESC LIMIT 1
                """,
                (carrier, canonical),
            ).fetchone()
        return row["shipment_id"] if row else None

    def numbers_for(self, shipment_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT carrier, number, role, created FROM shipment_numbers
                WHERE shipment_id = ? ORDER BY id
                """,
                (shipment_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- orders ----------------------------------------------------------

    def upsert_order(
        self,
        *,
        order_number: str,
        merchant: Optional[str] = None,
        merchant_domain: Optional[str] = None,
        order_date: Union[datetime, str, None] = None,
        total: Optional[float] = None,
        currency: Optional[str] = None,
        items: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Insert or refresh an order; returns the order id.

        Dedup key: (merchant_domain, order_number), NULL-domain-tolerant.
        When `items` is given, the stored line items are REPLACED (an
        order-confirmation re-parse is authoritative for contents).
        """
        now = _utcnow().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM orders
                WHERE order_number = ?
                  AND COALESCE(merchant_domain, '') = COALESCE(?, '')
                ORDER BY created DESC LIMIT 1
                """,
                (order_number, merchant_domain),
            ).fetchone()
            if row is None:
                order_id = "ord_" + uuid.uuid4().hex[:12]
                conn.execute(
                    """
                    INSERT INTO orders
                        (id, merchant, merchant_domain, order_number,
                         order_date, total, currency, created, updated)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        order_id,
                        merchant,
                        merchant_domain,
                        order_number,
                        _iso(order_date),
                        total,
                        currency,
                        now,
                        now,
                    ),
                )
            else:
                order_id = row["id"]
                conn.execute(
                    """
                    UPDATE orders SET
                        merchant = COALESCE(?, merchant),
                        order_date = COALESCE(?, order_date),
                        total = COALESCE(?, total),
                        currency = COALESCE(?, currency),
                        updated = ?
                    WHERE id = ?
                    """,
                    (merchant, _iso(order_date), total, currency, now, order_id),
                )
            if items is not None:
                conn.execute(
                    "DELETE FROM order_items WHERE order_id = ?", (order_id,)
                )
                for item in items:
                    conn.execute(
                        """
                        INSERT INTO order_items
                            (order_id, name, qty, price, sku, image_url)
                        VALUES (?,?,?,?,?,?)
                        """,
                        (
                            order_id,
                            item["name"],
                            int(item.get("qty", 1)),
                            item.get("price"),
                            item.get("sku"),
                            item.get("image_url"),
                        ),
                    )
        return order_id

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Order row + its line items."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if row is None:
                return None
            items = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
                (order_id,),
            ).fetchall()
        order = dict(row)
        order["items"] = [dict(i) for i in items]
        return order

    def link_shipment_order(self, shipment_id: str, order_id: str) -> None:
        """N:M link — a shipment can carry items from several orders, and
        one order can span several shipments."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO order_shipments (order_id, shipment_id) VALUES (?,?)",
                (order_id, shipment_id),
            )

    def orders_for_shipment(self, shipment_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT o.* FROM orders o
                JOIN order_shipments os ON os.order_id = o.id
                WHERE os.shipment_id = ? ORDER BY o.created
                """,
                (shipment_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def shipments_for_order(self, order_id: str) -> List[Shipment]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT s.* FROM shipments s
                JOIN order_shipments os ON os.shipment_id = s.id
                WHERE os.order_id = ? ORDER BY s.created
                """,
                (order_id,),
            ).fetchall()
        return [self._row_to_shipment(r) for r in rows]

    # -- candidate approval queue ----------------------------------------

    def enqueue_candidate(
        self,
        candidate: Union[Dict[str, Any], str],
        layer: str,
        confidence: Optional[float] = None,
    ) -> str:
        """Queue a low-confidence detection for human approval."""
        candidate_id = "cand_" + uuid.uuid4().hex[:12]
        payload = candidate if isinstance(candidate, str) else json.dumps(candidate)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO shipment_candidates
                    (id, candidate_json, layer, confidence, status, created)
                VALUES (?,?,?,?, 'pending', ?)
                """,
                (candidate_id, payload, layer, confidence, _utcnow().isoformat()),
            )
        return candidate_id

    def peek_candidates(
        self, status: str = "pending", limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Queue rows, oldest first. `candidate` is the parsed JSON payload."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM shipment_candidates
                WHERE status = ? ORDER BY created LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["candidate"] = json.loads(d["candidate_json"])
            except (ValueError, TypeError):
                d["candidate"] = d["candidate_json"]
            out.append(d)
        return out

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """One approval-queue row by id (any status), or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM shipment_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        return dict(row) if row else None

    def resolve_candidate(self, candidate_id: str, status: str) -> bool:
        """Resolve a pending candidate as 'confirmed' or 'rejected'.
        Returns False when the id is unknown or already resolved."""
        if status not in ("confirmed", "rejected"):
            raise ValueError("status must be 'confirmed' or 'rejected'")
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE shipment_candidates
                SET status = ?, resolved_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, _utcnow().isoformat(), candidate_id),
            )
        return cur.rowcount > 0

    # -- detection eval labels -------------------------------------------

    def record_eval_label(
        self,
        candidate: Union[Dict[str, Any], str],
        layer: str,
        predicted: Optional[str] = None,
        label: Optional[str] = None,
    ) -> int:
        """Append one hand-labeled eval row (threshold tuning set)."""
        payload = candidate if isinstance(candidate, str) else json.dumps(candidate)
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO detection_eval
                    (candidate_json, layer, predicted, label, labeled_at)
                VALUES (?,?,?,?,?)
                """,
                (payload, layer, predicted, label, _utcnow().isoformat()),
            )
        return int(cur.lastrowid)

    def eval_labels(self, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM detection_eval ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def add_eval_candidate(self, row: Dict[str, Any]) -> int:
        """Persist one detection_eval row from the evalset exporter.

        The exporter's row carries the candidate fields plus ``layer`` and
        an optional ``label``; the whole row is stored as candidate_json so
        nothing the labeler needs is lost.
        """
        return self.record_eval_label(
            row,
            layer=str(row.get("layer") or "unknown"),
            predicted=row.get("predicted"),
            label=row.get("label"),
        )

    def iter_eval_rows(self, limit: int = 10000) -> Iterator[Dict[str, Any]]:
        """Yield detection_eval rows (labeling queue + report input)."""
        return iter(self.eval_labels(limit=limit))

    def unlabeled_eval(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Unlabeled detection_eval rows — the hand-labeling queue."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM detection_eval
                WHERE label IS NULL ORDER BY id LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def label_eval(self, eval_id: int, label: str) -> bool:
        """Set the ground-truth label on one eval row. False = unknown id."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE detection_eval SET label = ?, labeled_at = ? WHERE id = ?",
                (label, _utcnow().isoformat(), int(eval_id)),
            )
        return cur.rowcount > 0

    # -- detection priors (the flywheel) ----------------------------------

    def get_prior(self, kind: str, key: str, carrier: str) -> Dict[str, Any]:
        """Hit-rate record for one (kind, key, carrier). `rate` is
        hits/(hits+misses), or None when there's no data."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT hits, misses FROM detection_priors
                WHERE kind = ? AND key = ? AND carrier = ?
                """,
                (kind, key, carrier),
            ).fetchone()
        hits = row["hits"] if row else 0
        misses = row["misses"] if row else 0
        total = hits + misses
        return {
            "kind": kind,
            "key": key,
            "carrier": carrier,
            "hits": hits,
            "misses": misses,
            "rate": (hits / total) if total else None,
        }

    def record_prior(self, kind: str, key: str, carrier: str, hit: bool) -> None:
        """Record one confirmation (hit=True) or rejection (hit=False) of a
        candidate from this sender/domain for this carrier."""
        if kind not in ("sender", "domain"):
            raise ValueError("kind must be 'sender' or 'domain'")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO detection_priors (kind, key, carrier, hits, misses, updated)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(kind, key, carrier) DO UPDATE SET
                    hits = hits + excluded.hits,
                    misses = misses + excluded.misses,
                    updated = excluded.updated
                """,
                (
                    kind,
                    key,
                    carrier,
                    1 if hit else 0,
                    0 if hit else 1,
                    _utcnow().isoformat(),
                ),
            )

    def priors_for(self, kind: str, key: str) -> List[Dict[str, Any]]:
        """All carrier priors for a sender/domain, best hit-rate first
        (carriers with no data yet aren't stored, so none appear)."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT carrier, hits, misses FROM detection_priors
                WHERE kind = ? AND key = ?
                """,
                (kind, key),
            ).fetchall()
        out = []
        for r in rows:
            total = r["hits"] + r["misses"]
            out.append(
                {
                    "carrier": r["carrier"],
                    "hits": r["hits"],
                    "misses": r["misses"],
                    "rate": (r["hits"] / total) if total else None,
                }
            )
        out.sort(key=lambda p: (p["rate"] is not None, p["rate"]), reverse=True)
        return out

    # -- domain rules ------------------------------------------------------

    def set_domain_rule(
        self,
        domain: str,
        category: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> None:
        """Create or update the rule for a domain (user-editable)."""
        domain = domain.strip().lower()
        now = _utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO domain_rules (domain, category, display_name, created, updated)
                VALUES (?,?,?,?,?)
                ON CONFLICT(domain) DO UPDATE SET
                    category = excluded.category,
                    display_name = excluded.display_name,
                    updated = excluded.updated
                """,
                (domain, category, display_name, now, now),
            )

    def get_domain_rule(self, domain: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM domain_rules WHERE domain = ?",
                (domain.strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def delete_domain_rule(self, domain: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM domain_rules WHERE domain = ?",
                (domain.strip().lower(),),
            )
        return cur.rowcount > 0

    def list_domain_rules(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM domain_rules ORDER BY domain"
            ).fetchall()
        return [dict(r) for r in rows]

    # -- tracking_state (key-value) -----------------------------------------

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Read a tracking_state value (watermarks, singleton lock,
        quota-exhausted-until, token buckets). Values are opaque strings;
        callers serialize structured data as JSON themselves."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM tracking_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: Optional[str]) -> None:
        """Upsert a tracking_state value. None deletes the key."""
        with self._conn() as conn:
            if value is None:
                conn.execute("DELETE FROM tracking_state WHERE key = ?", (key,))
            else:
                conn.execute(
                    """
                    INSERT INTO tracking_state (key, value, updated) VALUES (?,?,?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value, updated = excluded.updated
                    """,
                    (key, value, _utcnow().isoformat()),
                )


def open_default() -> ShipmentStore:
    """Open the real store over ``~/.aos/data/qareen.db``.

    The conventional entry point for CLI consumers (backfill, evalset) that
    look for a module-level opener named ``open_default`` / ``open`` /
    ``connect``.
    """
    return ShipmentStore()


# Alias for the same convention ("open" is deliberately not aliased — it
# would shadow the builtin inside this module).
connect = open_default
