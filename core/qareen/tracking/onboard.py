"""New-carrier onboarding pipeline (initiative §8, auto-tracker#19).

The programmatic core of ``aos track`` — the CLI (core/bin/cli/aos-track)
is a thin shell over these functions.

Honest framing (from the initiative's adversarial audit): the agent
scaffolds, the human clears credential gates, and **no pack goes live
without validation against a real captured API response**. Realistic cost
is 0.5–3 days per carrier — credential wrangling dominates — not hours.
Nothing in here shortcuts that: ``canary`` and ``graduate`` both refuse a
pack whose fixtures don't parse green.

Lifecycle (stored in the store's tracking_state table, key
``carrier.<slug>.state``, JSON value)::

    scaffolded → canary → active
                     ↑ graduate()

- **scaffolded** — ``aos track add`` copied the template; the pack exists
  but must not join detection or polling.
- **canary**    — fixtures validate green; the carrier tracks silently and
  accuracy is logged, but its patterns stay out of detection.
- **active**    — graduated; patterns + URL templates join detection.

Packs with NO state row (every Wave 1 pack) default to ``active`` so
existing behavior is unchanged until a state is explicitly set.
``lifecycle()`` is the read API the scheduler/detection layers should use
to filter packs (they own the filtering itself).

Fixture validation is the "never validate against fabricated samples"
gate: it runs each captured fixture in the pack's ``fixtures/`` dir
through the pack's response_map + ``engine.normalize_event`` and diffs
what comes out. Files named ``*error*`` are error envelopes — they are
expected to yield NO events (and an XML mapper is expected to raise).

Python 3.9-compatible: no ``X | Y`` runtime unions, no match statements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import detect, engine, jsonpath, packs
from .config import TrackingConfig, action_for
from .models import Shipment
from .packs import CARRIERS_DIR, CarrierPack

# ── Lifecycle states ─────────────────────────────────────────────────────

STATE_SCAFFOLDED = "scaffolded"
STATE_CANARY = "canary"
STATE_ACTIVE = "active"
LIFECYCLE_STATES = (STATE_SCAFFOLDED, STATE_CANARY, STATE_ACTIVE)

# Packs that predate the lifecycle (no tracking_state row) are treated as
# active — Wave 1 behavior must not change until a state is set explicitly.
DEFAULT_STATE = STATE_ACTIVE

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_TEMPLATE_DIR = CARRIERS_DIR / "_template"


def _state_key(slug: str) -> str:
    return "carrier.%s.state" % slug


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Scaffold ─────────────────────────────────────────────────────────────


class OnboardError(ValueError):
    """Raised for user-facing onboarding failures (bad slug, existing pack,
    validation gate refused). The CLI prints the message and exits 1."""


def scaffold(
    slug: str,
    carriers_dir: Optional[Path] = None,
    store: Any = None,
) -> Path:
    """Create ``carriers/<slug>/`` from the ``_template`` pack.

    Copies the template manifest, prefills ``carrier:`` and
    ``display_name:``, and (when *store* is given) records the pack as
    ``scaffolded`` so detection/polling filters can skip it until it
    graduates. Refuses to overwrite an existing pack.
    """
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise OnboardError(
            "carrier slug %r must be lowercase letters/digits/hyphens "
            "(e.g. 'ups', 'canadapost')" % slug
        )
    root = Path(carriers_dir) if carriers_dir else CARRIERS_DIR
    template = root / "_template"
    if not template.is_dir():
        template = _TEMPLATE_DIR  # running from a different tree
    if not (template / packs.MANIFEST_NAME).is_file():
        raise OnboardError("template pack not found at %s" % template)
    target = root / slug
    if target.exists():
        raise OnboardError("pack already exists: %s" % target)

    target.mkdir(parents=True)
    (target / "fixtures").mkdir()
    manifest = (template / packs.MANIFEST_NAME).read_text()
    manifest = manifest.replace("carrier: _template", "carrier: %s" % slug)
    manifest = manifest.replace(
        "display_name: Template Carrier", "display_name: %s" % slug.title()
    )
    (target / packs.MANIFEST_NAME).write_text(manifest)

    if store is not None:
        set_carrier_state(store, slug, STATE_SCAFFOLDED, note="scaffolded by aos track add")
    return target


def checklist(slug: str) -> str:
    """The honest onboarding checklist printed after ``aos track add``."""
    return """\
Onboarding {slug} — realistic cost is 0.5–3 days (credential wrangling
dominates). No pack goes live without validation against a REAL captured
API response.

  1. RESEARCH    Read the carrier's API docs (Chrome extension when the
                 portal needs login) → fill in the manifest: auth model +
                 Keychain key NAMES, endpoints, tracking patterns + check
                 digit, URL templates.
  2. CREDENTIALS Agent-driven signup (agent pipes client ID/secret
                 straight into `agent-secret set <NAME>` — never a file),
                 or exact printed steps when the signup is human-only.
  3. FIXTURE     Probe the live API with a real tracking number; capture
                 the response (anonymized) into
                 carriers/{slug}/fixtures/track_*.json.
  4. VALIDATE    `aos track validate {slug}` — diff the fixture against
                 response_map; iterate mappings until green. Never
                 validate against fabricated samples.
  5. CANARY      `aos track canary {slug}` — tracks silently for ~1 week,
                 accuracy logged vs. the carrier website.
  6. GRADUATE    `aos track graduate {slug}` — promotes to active;
                 patterns + URL templates join detection.
""".format(slug=slug)


# ── Fixture validation (the "no fabricated samples" gate) ────────────────


@dataclass
class FixtureResult:
    """One fixture's validation outcome."""

    name: str
    ok: bool
    kind: str = "track"  # track | error
    events: int = 0
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Whole-pack validation outcome; ``ok`` gates canary/graduate."""

    slug: str
    ok: bool
    fixtures: List[FixtureResult] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)  # pack-level

    def render(self) -> str:
        lines = []
        for problem in self.problems:
            lines.append("✗ %s" % problem)
        for fix in self.fixtures:
            if fix.ok:
                lines.append(
                    "✓ %s — %d event(s), all checks green" % (fix.name, fix.events)
                )
            else:
                lines.append("✗ %s" % fix.name)
            for problem in fix.problems:
                lines.append("    ✗ %s" % problem)
            for note in fix.notes:
                lines.append("    ~ %s" % note)
        if not self.fixtures and not self.problems:
            lines.append("✗ no fixtures found — capture a real API response first")
        verdict = "GREEN" if self.ok else "RED"
        lines.append(
            "%s: %s (%d fixture(s))" % (self.slug, verdict, len(self.fixtures))
        )
        return "\n".join(lines)


def _load_mapper(pack: CarrierPack):
    """Load the pack's optional ``mapper.py`` (XML → dict, e.g. Canada Post).

    Returns the ``track_xml_to_dict`` callable or None. The engine client
    runs the same mapper before applying response_map; validation must too.
    """
    return packs.load_mapper(pack)


def _validate_track_fixture(pack: CarrierPack, name: str, data: Any) -> FixtureResult:
    """Run one track-response fixture through response_map + normalize_event."""
    result = FixtureResult(name=name, ok=True, kind="track")
    response_map = pack.response_map or {}

    events_path = response_map.get("events")
    events: List[Any] = []
    if events_path:
        extracted = jsonpath.extract(data, events_path)
        if isinstance(extracted, list):
            events = extracted
        elif extracted is not None:
            events = [extracted]
    if not events:
        result.ok = False
        result.problems.append(
            "response_map.events (%s) matched nothing — the path doesn't "
            "reach the scan-event array in this captured response" % events_path
        )
    result.events = len(events)

    for i, raw_event in enumerate(events):
        if not isinstance(raw_event, dict):
            result.ok = False
            result.problems.append("event[%d] is not an object: %r" % (i, raw_event))
            continue
        event = engine.normalize_event(pack, raw_event)
        if event.milestone is None:
            result.ok = False
            result.problems.append(
                "event[%d]: carrier code %r is not in status_map — extend "
                "status_map from this captured response, never from docs alone"
                % (i, event.carrier_code)
            )
        if not event.description:
            result.notes.append(
                "event[%d]: no description at the mapped path (event_fields.description)"
                % i
            )

    eta_path = response_map.get("eta")
    if eta_path:
        eta = jsonpath.extract(data, eta_path)
        if eta is None:
            result.notes.append("no eta at %s (may be absent pre-delivery)" % eta_path)
        else:
            result.notes.append("eta: %s" % eta)
    return result


def validate_pack(
    slug: str,
    carriers_dir: Optional[Path] = None,
) -> ValidationReport:
    """Validate a pack against every fixture in its ``fixtures/`` dir.

    This is the onboarding gate: mappings are proven against captured API
    responses (docs-derived or live), never against fabricated samples.
    A pack with no fixtures is RED — capture one first.
    """
    root = Path(carriers_dir) if carriers_dir else CARRIERS_DIR
    pack_dir = root / slug
    try:
        pack = packs.load_pack(pack_dir)
    except packs.PackError as exc:
        return ValidationReport(slug=slug, ok=False, problems=[str(exc)])

    fixtures_dir = pack_dir / "fixtures"
    report = ValidationReport(slug=slug, ok=True)
    if not fixtures_dir.is_dir():
        report.ok = False
        report.problems.append("no fixtures/ dir in %s" % pack_dir)
        return report

    mapper = None
    fixture_files = sorted(
        p for p in fixtures_dir.iterdir()
        if p.is_file() and p.suffix in (".json", ".xml")
    )
    for fixture in fixture_files:
        is_error = "error" in fixture.stem.lower()
        try:
            if fixture.suffix == ".xml":
                if mapper is None:
                    mapper = _load_mapper(pack)
                if mapper is None:
                    raise OnboardError("XML fixture but no mapper.py in pack")
                data = mapper(fixture.read_text())
            else:
                data = json.loads(fixture.read_text())
        except Exception as exc:
            # Error envelopes are EXPECTED to fail parsing/mapping.
            if is_error:
                report.fixtures.append(
                    FixtureResult(
                        name=fixture.name,
                        ok=True,
                        kind="error",
                        notes=["error envelope handled: %s" % exc],
                    )
                )
            else:
                report.ok = False
                report.fixtures.append(
                    FixtureResult(
                        name=fixture.name,
                        ok=False,
                        kind="error" if is_error else "track",
                        problems=["fixture failed to parse: %s" % exc],
                    )
                )
            continue

        if is_error:
            # An error envelope must NOT yield normalized events.
            events_path = (pack.response_map or {}).get("events")
            extracted = jsonpath.extract(data, events_path) if events_path else None
            n_events = len(extracted) if isinstance(extracted, list) else (1 if extracted else 0)
            ok = n_events == 0
            if not ok:
                report.ok = False
            report.fixtures.append(
                FixtureResult(
                    name=fixture.name,
                    ok=ok,
                    kind="error",
                    events=n_events,
                    problems=(
                        ["error fixture produced %d event(s) — response_map "
                         "matches the error envelope" % n_events]
                        if not ok else []
                    ),
                    notes=["no events extracted, as expected"] if ok else [],
                )
            )
            continue

        result = _validate_track_fixture(pack, fixture.name, data)
        if not result.ok:
            report.ok = False
        report.fixtures.append(result)

    if not report.fixtures:
        report.ok = False
    return report


# ── Lifecycle (tracking_state) ───────────────────────────────────────────


def carrier_state(store: Any, slug: str) -> Dict[str, Any]:
    """Lifecycle record for a pack: {state, since, note}.

    Packs with no recorded state default to ``active`` (Wave 1 packs keep
    their current behavior until an explicit state is set).
    """
    raw = store.get_state(_state_key(slug)) if store is not None else None
    if raw:
        try:
            record = json.loads(raw)
            if isinstance(record, dict) and record.get("state") in LIFECYCLE_STATES:
                return record
        except ValueError:
            pass
    return {"state": DEFAULT_STATE, "since": None, "note": "default (no state recorded)"}


def set_carrier_state(store: Any, slug: str, state: str, note: str = "") -> Dict[str, Any]:
    if state not in LIFECYCLE_STATES:
        raise OnboardError("state must be one of %s" % (LIFECYCLE_STATES,))
    record = {"state": state, "since": _utcnow_iso(), "note": note}
    store.set_state(_state_key(slug), json.dumps(record))
    return record


def lifecycle(store: Any, carriers_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Lifecycle overview for every discovered pack (scaffolds excluded).

    This is the read API the scheduler/detection layers should consult:
    only packs whose state is ``active`` should join detection and normal
    polling; ``canary`` packs track silently; ``scaffolded`` packs are inert.
    """
    out = []
    for pack_dir in packs.discover_packs(carriers_dir):
        if pack_dir.name.startswith("_"):
            continue
        record = carrier_state(store, pack_dir.name)
        out.append(
            {
                "slug": pack_dir.name,
                "state": record["state"],
                "since": record.get("since"),
                "note": record.get("note", ""),
            }
        )
    return out


def detection_packs(
    store: Any, carriers_dir: Optional[Path] = None
) -> Dict[str, CarrierPack]:
    """Packs allowed into DETECTION: lifecycle state ``active`` only.

    ``canary`` packs track silently — their patterns stay out of detection
    until graduation; ``scaffolded`` packs are inert. Packs with no recorded
    state default to active (Wave 1 behavior).
    """
    return {
        slug: pack
        for slug, pack in packs.load_packs(carriers_dir).items()
        if carrier_state(store, slug)["state"] == STATE_ACTIVE
    }


def polling_packs(
    store: Any, carriers_dir: Optional[Path] = None
) -> Dict[str, CarrierPack]:
    """Packs allowed into POLLING: not ``scaffolded``, and HTTP-surfaced.

    Canary packs poll (silent tracking is the point of canary). Packs whose
    ``endpoints.base`` is null (pseudo-carriers like amazon-email) have no
    HTTP surface — the email-event channel produces their events, so they
    are excluded here.
    """
    out: Dict[str, CarrierPack] = {}
    for slug, pack in packs.load_packs(carriers_dir).items():
        if carrier_state(store, slug)["state"] == STATE_SCAFFOLDED:
            continue
        if not (pack.endpoints or {}).get("base"):
            continue
        out[slug] = pack
    return out


def _require_green_validation(slug: str, carriers_dir: Optional[Path]) -> ValidationReport:
    report = validate_pack(slug, carriers_dir)
    if not report.ok:
        raise OnboardError(
            "validation is RED — fix the mappings first:\n" + report.render()
        )
    return report


def canary(
    slug: str,
    store: Any,
    carriers_dir: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Promote a scaffolded pack to canary (silent tracking, logged).

    Gated on green fixture validation — a pack whose response_map doesn't
    parse its captured fixtures tracks garbage, not packages.
    """
    if not force:
        _require_green_validation(slug, carriers_dir)
    return set_carrier_state(
        store, slug, STATE_CANARY, note="canary: tracks silently, accuracy logged"
    )


def graduate(
    slug: str,
    store: Any,
    carriers_dir: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Promote a pack to active: patterns + URL templates join detection.

    Requires green fixture validation unless *force* — the initiative's
    rule is that no pack goes live unvalidated, so force prints its reason
    into the state record for the audit trail.
    """
    if force:
        return set_carrier_state(
            store, slug, STATE_ACTIVE, note="graduated with --force (validation bypassed)"
        )
    _require_green_validation(slug, carriers_dir)
    return set_carrier_state(
        store, slug, STATE_ACTIVE, note="graduated: patterns join detection"
    )


# ── Manual add (CLI lane of initiative §7) ───────────────────────────────


def _persist_candidate(store: Any, cand: "detect.DetectionCandidate", cfg: TrackingConfig) -> Dict[str, Any]:
    """Band one detection candidate and persist it through the store."""
    action = action_for(cand.confidence, cfg)
    if action == "auto_add":
        shipment = Shipment(
            tracking_number=cand.tracking_number,
            carrier=cand.carrier,
            source="manual",
            confidence=cand.confidence,
        )
        shipment_id, created = store.upsert_shipment(
            shipment, category=None, next_poll_at=None
        )
        return {
            "action": "added" if created else "merged",
            "shipment_id": shipment_id,
            "tracking_number": cand.tracking_number,
            "carrier": cand.carrier,
            "confidence": cand.confidence,
        }
    if action == "queue":
        candidate_id = store.enqueue_candidate(
            cand.to_dict(), layer=cand.layer, confidence=cand.confidence
        )
        return {
            "action": "queued",
            "candidate_id": candidate_id,
            "tracking_number": cand.tracking_number,
            "carrier": cand.carrier,
            "confidence": cand.confidence,
        }
    return {
        "action": "ignored",
        "tracking_number": cand.tracking_number,
        "carrier": cand.carrier,
        "confidence": cand.confidence,
        "reason": "confidence %.2f below queue threshold %.2f"
        % (cand.confidence, cfg.queue_min),
    }


def add_number(
    store: Any,
    number: Optional[str] = None,
    text: Optional[str] = None,
    carrier: Optional[str] = None,
    carriers_dir: Optional[Path] = None,
    config: Optional[TrackingConfig] = None,
) -> Dict[str, Any]:
    """Manual add: a bare tracking number or a pasted text blob.

    Runs the real detection pipeline (``qareen.tracking.detect``) — the
    same layers the comms consumer and dashboard paste box use. With
    *carrier* given, the number is validated against that pack and added
    directly at confidence 1.0 (operator attribution is authoritative);
    with *text*, the blob is scanned and candidates persist by confidence
    band (auto-add / approval queue / ignore).
    """
    cfg = config or TrackingConfig.load()
    all_packs = packs.load_packs(carriers_dir)
    if carrier:
        pack = all_packs.get(carrier)
        if pack is None:
            raise OnboardError(
                "unknown carrier %r (packs: %s)"
                % (carrier, ", ".join(sorted(all_packs)) or "none")
            )
        if number:
            canonical = engine.canonicalize(number)
            if not engine.validate_number(pack, canonical):
                raise OnboardError(
                    "%s is not a valid %s tracking number (failed pack "
                    "patterns/check digit — typo, or wrong carrier?)"
                    % (canonical, pack.display_name)
                )
            shipment = Shipment(
                tracking_number=canonical,
                carrier=carrier,
                source="manual",
                confidence=1.0,
            )
            shipment_id, created = store.upsert_shipment(shipment)
            return {
                "shipments": [
                    {
                        "action": "added" if created else "merged",
                        "shipment_id": shipment_id,
                        "tracking_number": canonical,
                        "carrier": carrier,
                        "confidence": 1.0,
                    }
                ],
                "candidates": [],
                "ignored": [],
            }
        # --carrier + --text: scan the blob with only that carrier's pack.
        all_packs = {carrier: pack}

    blob = text if text is not None else (number or "")
    if not blob.strip():
        raise OnboardError("nothing to add — pass a tracking number or --text")

    if not carrier:
        # Detection honors the lifecycle gate: only active packs scan text.
        all_packs = detection_packs(store, carriers_dir)

    message = {
        "text": blob,
        "sender": "",
        "channel": "cli",
        "conversation_id": None,
        "message_id": None,
        "timestamp": _utcnow_iso(),
        "from_me": False,
    }
    result = detect.detect(message, all_packs, store=None, config=cfg)

    shipments, candidates, ignored = [], [], []
    for cand in result.candidates:
        record = _persist_candidate(store, cand, cfg)
        if record["action"] in ("added", "merged"):
            shipments.append(record)
        elif record["action"] == "queued":
            candidates.append(record)
        else:
            ignored.append(record)
    return {"shipments": shipments, "candidates": candidates, "ignored": ignored}


# ── Listing (CLI list/show) ──────────────────────────────────────────────


def list_shipments(
    store: Any,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Shipment rows, newest first, optionally filtered by status.

    Delegates to ``ShipmentStore.list_shipments`` (folded into the store
    during wave-2 integration); falls back to a direct read for older
    duck-typed stores.
    """
    list_method = getattr(store, "list_shipments", None)
    if callable(list_method):
        return list_method(status=status, limit=limit)
    sql = "SELECT * FROM shipments"
    params: List[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated DESC LIMIT ?"
    params.append(limit)
    conn = store._connect()  # read-only listing; see docstring
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def shipment_detail(store: Any, shipment_id: str) -> Optional[Dict[str, Any]]:
    """Everything the CLI ``show`` prints: row + events + numbers."""
    row = store.get_shipment_row(shipment_id)
    if row is None:
        return None
    events = store.events_for(shipment_id)
    return {
        "shipment": row,
        "events": [
            {
                "seq": e.seq,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "milestone": e.milestone.value if e.milestone else None,
                "description": e.description,
                "location": e.location,
            }
            for e in events
        ],
        "numbers": store.numbers_for(shipment_id),
    }
