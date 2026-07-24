"""Order extraction — order-confirmation emails → orders + line items.

Initiative §5: ``orders`` + ``order_items`` (merchant, order number, order
date, total; line items name/qty/price/sku) are extracted from
order-confirmation emails already sitting in comms.db, and shipments link
to orders N:M. That powers "what's in the box arriving Tuesday" and
reconciliation ("ordered 3 things, 2 arrived").

Extraction uses the existing Haiku subscription pattern (see
``core/comms/enrich/extract.py``): ``claude --print --model haiku
--output-format json`` via subprocess, prompt on stdin, spawned in its own
process group (``start_new_session=True``) and torn down with ``killpg`` on
timeout so a killed run never orphans quota-burning children. The output is
parsed defensively — garbage JSON, wrong shapes, and missing fields degrade
to "no order", never an exception.

Privacy: senders whose people.db ``privacy_level`` >= ``privacy_min_level``
(default 2) are excluded, best-effort and wrapped — a missing people.db
never blocks the sweep (same rule as detect.py).

Batch CLI (dry-run report by default; ``--write`` persists)::

    python3 -m qareen.tracking.orders --backfill [--write] [--limit N]

Persistence goes through a duck-typed store (``upsert_order``,
``link_shipment_order``) so tests use a fake and NEVER spawn the real
``claude`` CLI — the extractor is an injectable callable.

Python 3.9-compatible: no ``X | Y`` runtime unions, no match statements.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .detect import sender_privacy_level

log = logging.getLogger(__name__)

DEFAULT_COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
DEFAULT_QAREEN_DB = Path.home() / ".aos" / "data" / "qareen.db"

DEFAULT_MODEL = "haiku"
DEFAULT_TIMEOUT_S = 90
DEFAULT_LIMIT = 200
DEFAULT_PRIVACY_MIN_LEVEL = 2

# Long newsletters/receipts get truncated before hitting the model.
MAX_EMAIL_CHARS = 12000

# How many characters of an email address make a plausible merchant domain
# fallback (sender "orders@shop.example.com" → "shop.example.com").
_DOMAIN_RE = re.compile(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# Cheap pre-filter so the model is only consulted on plausible
# order-confirmation emails (SQL LIKE widens, this regex tightens).
_ORDER_HINT_RE = re.compile(
    r"order\s*(confirm|number|#|summary|has been placed|update)"
    r"|your order"
    r"|thanks for your (order|purchase)"
    r"|e?-?receipt"
    r"|purchase summary",
    re.IGNORECASE,
)

# SQL LIKE widening net (any hit is re-checked by _ORDER_HINT_RE).
_SQL_LIKES = (
    "%order confirm%",
    "%order number%",
    "%order #%",
    "%your order%",
    "%receipt%",
    "%purchase%",
)

# Frozen extraction prompt: strict JSON schema, precision over recall, no
# fabrication. The model answers with the envelope only.
SYSTEM_PROMPT = """You are an order-extraction engine for a personal shipment tracker.
You are given ONE email. Decide whether it is a merchant order confirmation
(or receipt). If it is NOT (shipping notification, marketing, newsletter,
personal mail), return {"order": null}.

If it IS an order confirmation, extract EXACTLY what is explicitly present —
never infer, guess, or invent values. Return this exact JSON shape and
nothing else (no preamble, no markdown fences):
{"order": {
  "merchant": "Shop Name",            // merchant display name, or null
  "merchant_domain": "shop.com",      // merchant domain, or null
  "order_number": "123-4567890",      // REQUIRED; if none is visible, return {"order": null}
  "order_date": "2024-01-05",         // ISO date, or null
  "total": 42.50,                     // number, grand total charged, or null
  "currency": "USD",                  // ISO 4217, or null
  "items": [
    {"name": "Widget", "qty": 2, "price": 19.99, "sku": "WID-1"}
  ]
}}

Rules:
- items: one entry per purchased line item. qty is an integer >= 1
  (default 1). price is the line price as a number, or null. sku only when
  explicitly shown, else null. No items visible → empty array.
- Numbers must be JSON numbers, not strings. Unknown → null.
- Never fabricate names, amounts, SKUs, or dates. Precision over recall."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class OrderItem:
    name: str
    qty: int = 1
    price: Optional[float] = None
    sku: Optional[str] = None

    def to_store_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "qty": self.qty, "price": self.price, "sku": self.sku}


@dataclass
class OrderExtraction:
    """One validated order extraction, ready for store.upsert_order."""

    order_number: str
    merchant: Optional[str] = None
    merchant_domain: Optional[str] = None
    order_date: Optional[str] = None  # ISO date string, passthrough
    total: Optional[float] = None
    currency: Optional[str] = None
    items: List[OrderItem] = field(default_factory=list)

    def item_dicts(self) -> List[Dict[str, Any]]:
        return [i.to_store_dict() for i in self.items]


# Extractor callable shape: email text → parsed payload dict (or None).
Extractor = Callable[[str], Optional[Dict[str, Any]]]

# Link lookup callable shape: merchant_domain → shipment ids to link.
LinkLookup = Callable[[Optional[str]], List[str]]


# ---------------------------------------------------------------------------
# Defensive payload validation
# ---------------------------------------------------------------------------

def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _as_qty(value: Any) -> int:
    try:
        qty = int(value)
    except (ValueError, TypeError):
        return 1
    return qty if qty >= 1 else 1


def parse_order_payload(
    payload: Any, *, default_domain: Optional[str] = None
) -> Optional[OrderExtraction]:
    """Validate an extractor payload into an OrderExtraction.

    Returns None for anything unusable: non-dict, ``order: null``, missing
    or non-string order_number. Bad line items are dropped individually;
    scalar fields fall back to None rather than failing the whole order.
    """
    if not isinstance(payload, dict):
        return None
    order = payload.get("order")
    if not isinstance(order, dict):
        return None
    order_number = _as_str(order.get("order_number"))
    if not order_number:
        return None

    items: List[OrderItem] = []
    raw_items = order.get("items")
    if isinstance(raw_items, list):
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = _as_str(raw.get("name"))
            if not name:
                continue
            items.append(
                OrderItem(
                    name=name,
                    qty=_as_qty(raw.get("qty")),
                    price=_as_float(raw.get("price")),
                    sku=_as_str(raw.get("sku")),
                )
            )

    return OrderExtraction(
        order_number=order_number,
        merchant=_as_str(order.get("merchant")),
        merchant_domain=_as_str(order.get("merchant_domain")) or default_domain,
        order_date=_as_str(order.get("order_date")),
        total=_as_float(order.get("total")),
        currency=_as_str(order.get("currency")),
        items=items,
    )


def sender_domain(sender: Optional[str]) -> Optional[str]:
    """Best-effort merchant domain from a sender address/header."""
    if not sender:
        return None
    matches = _DOMAIN_RE.findall(sender.strip())
    return matches[-1].lower() if matches else None


def is_order_confirmation(text: str) -> bool:
    """Cheap regex pre-filter before spending a model call."""
    return bool(text) and bool(_ORDER_HINT_RE.search(text))


# ---------------------------------------------------------------------------
# Haiku extractor (claude --print; pattern from comms/enrich/extract.py)
# ---------------------------------------------------------------------------

def _claude_bin() -> str:
    """Resolve the `claude` binary by absolute path (cron-safe)."""
    override = os.environ.get("AOS_CLAUDE_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("claude")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude",
                 str(Path.home() / ".local/bin/claude")):
        if Path(cand).exists():
            return cand
    return "claude"


def _terminate_group(pid: int, sig: int = signal.SIGTERM) -> None:
    """Signal the whole process group led by pid (claude + node children)."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _parse_envelope_result(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse the model's `result` string, tolerating stray fences/prose."""
    if not text:
        return None
    s = text.strip()
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        try:
            parsed = json.loads(m.group(1))
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            pass
    return None


def claude_extractor(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Optional[Dict[str, Any]]:
    """Extract one email via `claude --print --model haiku`.

    Spawned in its own process group; on timeout the whole group is killed
    so no quota-burning children survive. ANY failure (spawn, timeout,
    non-zero rc, unparseable output) degrades to None — one bad email never
    crashes the sweep.
    """
    cmd = [_claude_bin(), "--print", "--model", model,
           "--system-prompt", SYSTEM_PROMPT, "--output-format", "json"]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            start_new_session=True,  # own process group → killpg-able
        )
    except (FileNotFoundError, OSError) as e:
        log.warning("orders: claude spawn failed: %s", e)
        return None
    try:
        out, _err = proc.communicate(input=text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_group(proc.pid, signal.SIGKILL)
        proc.wait()
        log.warning("orders: claude timed out after %ds", timeout_s)
        return None
    if proc.returncode != 0:
        log.warning("orders: claude rc=%s", proc.returncode)
        return None
    try:
        envelope = json.loads(out)
    except ValueError:
        return None
    if not isinstance(envelope, dict):
        return None
    return _parse_envelope_result(envelope.get("result", ""))


# ---------------------------------------------------------------------------
# comms.db candidate selection
# ---------------------------------------------------------------------------

def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate_rows(conn: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
    """Inbound messages whose content widens past the LIKE net, newest first."""
    where = " OR ".join("content LIKE ?" for _ in _SQL_LIKES)
    rows = conn.execute(
        "SELECT id, channel, direction, sender_id, content, timestamp,"
        "       conversation_id"
        " FROM messages"
        " WHERE lower(COALESCE(direction, '')) NOT IN ('out', 'sent', 'outbound')"
        "   AND (%s)"
        " ORDER BY id DESC LIMIT ?" % where,
        list(_SQL_LIKES) + [limit],
    ).fetchall()
    return rows


def make_domain_link_lookup(qareen_db: Path) -> LinkLookup:
    """Link lookup over qareen.db: active shipments for a merchant domain.

    Used by the CLI so extracted orders attach to existing shipments of the
    same merchant. Read-only; a missing db yields a lookup that links
    nothing.
    """
    db_path = Path(qareen_db)

    def lookup(domain: Optional[str]) -> List[str]:
        if not domain or not db_path.is_file():
            return []
        try:
            conn = _connect_readonly(db_path)
            try:
                rows = conn.execute(
                    "SELECT id FROM shipments"
                    " WHERE merchant_domain = ? AND status = 'active'",
                    (domain,),
                ).fetchall()
                return [r["id"] for r in rows]
            finally:
                conn.close()
        except sqlite3.Error as e:
            log.warning("orders: link lookup failed for %s: %s", domain, e)
            return []

    return lookup


# ---------------------------------------------------------------------------
# Backfill sweep
# ---------------------------------------------------------------------------

def run_backfill(
    comms_db: Path,
    *,
    store: Any = None,
    extractor: Optional[Extractor] = None,
    link_lookup: Optional[LinkLookup] = None,
    write: bool = False,
    limit: int = DEFAULT_LIMIT,
    people_db_path: Optional[Path] = None,
    privacy_min_level: int = DEFAULT_PRIVACY_MIN_LEVEL,
) -> Dict[str, Any]:
    """Sweep comms.db order-confirmation candidates through extraction.

    Dry-run by default: extraction runs and the report says what WOULD be
    written; nothing is persisted. With ``write=True`` (and a store) each
    extraction is persisted via ``store.upsert_order`` and linked to
    same-merchant active shipments via ``store.link_shipment_order``.

    Per-message failures (extractor error, bad payload, store error) are
    counted and logged, never raised.
    """
    if extractor is None:
        extractor = claude_extractor

    report: Dict[str, Any] = {
        "write": write,
        "limit": limit,
        "scanned": 0,          # LIKE candidates considered
        "prefilter_dropped": 0,  # LIKE hit, regex said no
        "skipped_privacy": 0,
        "extracted": 0,        # valid order payloads
        "no_order": 0,         # extractor ran, no usable order
        "errors": 0,
        "orders_written": 0,
        "links_written": 0,
        "samples": [],
    }

    conn = _connect_readonly(Path(comms_db))
    try:
        rows = _candidate_rows(conn, limit)
    finally:
        conn.close()

    for row in rows:
        report["scanned"] += 1
        content = row["content"] or ""
        if not is_order_confirmation(content):
            report["prefilter_dropped"] += 1
            continue

        sender = row["sender_id"] or ""
        try:
            if sender_privacy_level(sender, people_db_path) >= privacy_min_level:
                report["skipped_privacy"] += 1
                continue
        except Exception:
            # privacy lookup is best-effort; on error, proceed (0 assumed)
            pass

        try:
            payload = extractor(content[:MAX_EMAIL_CHARS])
            extraction = parse_order_payload(
                payload, default_domain=sender_domain(sender)
            )
        except Exception as e:
            report["errors"] += 1
            log.warning("orders: extractor failed on message %s: %s", row["id"], e)
            continue

        if extraction is None:
            report["no_order"] += 1
            continue
        report["extracted"] += 1

        sample = {
            "message_id": row["id"],
            "merchant": extraction.merchant,
            "merchant_domain": extraction.merchant_domain,
            "order_number": extraction.order_number,
            "total": extraction.total,
            "currency": extraction.currency,
            "items": len(extraction.items),
        }
        if len(report["samples"]) < 20:
            report["samples"].append(sample)

        if not write or store is None:
            continue

        try:
            order_id = store.upsert_order(
                order_number=extraction.order_number,
                merchant=extraction.merchant,
                merchant_domain=extraction.merchant_domain,
                order_date=extraction.order_date,
                total=extraction.total,
                currency=extraction.currency,
                items=extraction.item_dicts(),
            )
            report["orders_written"] += 1
        except Exception as e:
            report["errors"] += 1
            log.warning("orders: upsert failed for %s: %s",
                        extraction.order_number, e)
            continue

        if link_lookup is not None:
            try:
                shipment_ids = link_lookup(extraction.merchant_domain)
            except Exception as e:
                shipment_ids = []
                log.warning("orders: link lookup failed: %s", e)
            for shipment_id in shipment_ids:
                try:
                    store.link_shipment_order(shipment_id, order_id)
                    report["links_written"] += 1
                except Exception as e:
                    log.warning("orders: link %s→%s failed: %s",
                                shipment_id, order_id, e)
    return report


def render_report(report: Dict[str, Any]) -> str:
    """Human-readable one-screen summary of a backfill run."""
    lines = [
        "orders backfill %s — limit %d"
        % ("WRITE" if report["write"] else "DRY-RUN", report["limit"]),
        "  scanned: %d  (pre-filter dropped: %d, privacy: %d)"
        % (report["scanned"], report["prefilter_dropped"], report["skipped_privacy"]),
        "  extracted: %d  (no order: %d, errors: %d)"
        % (report["extracted"], report["no_order"], report["errors"]),
    ]
    if report["write"]:
        lines.append(
            "  written: %d orders, %d shipment links"
            % (report["orders_written"], report["links_written"])
        )
    for sample in report.get("samples", []):
        lines.append(
            "    [msg %s] %s (%s) order %s — %s %s, %d item(s)"
            % (
                sample["message_id"],
                sample["merchant"] or "?",
                sample["merchant_domain"] or "?",
                sample["order_number"],
                sample["total"],
                sample["currency"] or "",
                sample["items"],
            )
        )
    if not report["write"]:
        lines.append("  (dry run — re-run with --write to persist orders)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qareen.tracking.orders",
        description="Extract orders + line items from order-confirmation emails in comms.db.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="sweep comms.db order-confirmation candidates (dry-run report by default)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="persist extracted orders + shipment links (default: dry-run report only)",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="max candidate messages to consider (default: %(default)s)")
    parser.add_argument("--comms-db", type=Path, default=DEFAULT_COMMS_DB,
                        help="path to comms.db (default: ~/.aos/data/comms.db)")
    parser.add_argument("--qareen-db", type=Path, default=DEFAULT_QAREEN_DB,
                        help="path to qareen.db for --write/linking (default: ~/.aos/data/qareen.db)")
    parser.add_argument("--privacy-min-level", type=int, default=DEFAULT_PRIVACY_MIN_LEVEL,
                        help="skip senders with privacy_level >= N (default: %(default)s)")
    args = parser.parse_args(argv)

    if not args.backfill:
        parser.print_help()
        return 1
    if not args.comms_db.is_file():
        print("orders: no comms.db at %s — nothing to sweep" % args.comms_db, file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    store = None
    link_lookup = None
    if args.write:
        from .store import ShipmentStore

        store = ShipmentStore(args.qareen_db)
        link_lookup = make_domain_link_lookup(args.qareen_db)

    report = run_backfill(
        args.comms_db,
        store=store,
        link_lookup=link_lookup,
        write=args.write,
        limit=args.limit,
        privacy_min_level=args.privacy_min_level,
    )
    print(render_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
