"""Qareen API — People CRM routes.

List, search, inspect, and update people. Includes intelligence surfaces.
"""

from __future__ import annotations

import logging
from pathlib import Path as FilePath
from typing import Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..ontology.types import ObjectType
from .schemas import (
    ChannelPresenceSchema,
    InteractionSchema,
    PersonDetailResponse,
    PersonListResponse,
    PersonResponse,
    PersonSurfaceItem,
    PersonSurfaceResponse,
    RelationshipSchema,
    UpdatePersonRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/people", tags=["people"])


def _get_people_adapter(request: Request):
    """Get the PeopleAdapter from the ontology if available."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return None
    return ontology._adapters.get(ObjectType.PERSON)


def _person_to_response(person) -> PersonResponse:
    """Convert a Person ontology object to a PersonResponse schema."""
    return PersonResponse(
        id=person.id,
        name=person.name,
        importance=person.importance,
        privacy_level=person.privacy_level,
        tags=person.tags or [],
        aliases=person.aliases if hasattr(person, "aliases") else [],
        channels=person.channels if hasattr(person, "channels") else {},
        organization=person.organization,
        role=person.role,
        city=person.city,
        notes=getattr(person, "notes", None),
        birthday=person.birthday,
        how_met=person.how_met,
        last_contact=person.last_contact,
        days_since_contact=person.days_since_contact,
        relationship_trend=person.relationship_trend,
        projects=person.projects or [],
    )


def _person_to_detail(person, interactions=None, relationships=None, presence=None) -> PersonDetailResponse:
    """Convert a Person ontology object to a PersonDetailResponse schema."""
    return PersonDetailResponse(
        id=person.id,
        name=person.name,
        importance=person.importance,
        privacy_level=person.privacy_level,
        tags=person.tags or [],
        aliases=person.aliases if hasattr(person, "aliases") else [],
        channels=person.channels if hasattr(person, "channels") else {},
        organization=person.organization,
        role=person.role,
        city=person.city,
        notes=getattr(person, "notes", None),
        birthday=person.birthday,
        how_met=person.how_met,
        last_contact=person.last_contact,
        days_since_contact=person.days_since_contact,
        relationship_trend=person.relationship_trend,
        projects=person.projects or [],
        email=person.email,
        phone=person.phone,
        comms_trust_level=person.comms_trust_level,
        interactions=interactions or [],
        relationships=relationships or [],
        presence=presence or [],
    )


@router.get("", response_model=PersonListResponse)
async def list_people(
    request: Request,
    page: int = Query(1, description="Page number", ge=1),
    per_page: int = Query(50, description="Items per page", ge=1, le=100),
    q: str | None = Query(None, description="Search query"),
    tag: str | None = Query(None, description="Filter by tag"),
    project: str | None = Query(None, description="Filter by project"),
    importance_max: int | None = Query(None, description="Max importance (1=most important)"),
) -> PersonListResponse:
    """List people with optional search and filters. Paginated."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return PersonListResponse()

    # If a search query is provided, use the search method
    if q:
        results = ontology.search(q, types=[ObjectType.PERSON], limit=per_page)
        people = [_person_to_response(r.obj if r.obj else r) for r in results]
        return PersonListResponse(
            people=people,
            total=len(people),
            page=page,
            per_page=per_page,
            has_more=False,
        )

    # Build filters
    filters: dict[str, Any] = {}
    if tag:
        filters["tags"] = tag
    if project:
        filters["project"] = project
    if importance_max is not None:
        filters["importance"] = {"max": importance_max}

    offset = (page - 1) * per_page
    people_objs = ontology.list(ObjectType.PERSON, filters=filters, limit=per_page, offset=offset)
    total = ontology.count(ObjectType.PERSON, filters=filters)

    people = [_person_to_response(p) for p in people_objs]
    has_more = (offset + per_page) < total

    return PersonListResponse(
        people=people,
        total=total,
        page=page,
        per_page=per_page,
        has_more=has_more,
    )


# ---------------------------------------------------------------------------
# Contact Sources — discovery and import for onboarding
# ---------------------------------------------------------------------------


class ContactSourceInfo(BaseModel):
    """A contact import source."""

    id: str = Field(..., description="Source identifier")
    name: str = Field(..., description="Human-readable name")
    type: str = Field(..., description="Source type: apple, google, whatsapp, telegram")
    available: bool = Field(False, description="Whether this source is accessible")
    estimated_count: int = Field(0, description="Estimated number of contacts")
    status: str = Field("unknown", description="Health status: ready, unavailable, error")
    description: str = Field("", description="User-facing description")


class ContactSourcesResponse(BaseModel):
    """Available contact import sources."""

    sources: list[ContactSourceInfo] = Field(default_factory=list)
    total_available: int = Field(0, description="Number of available sources")
    people_count: int = Field(0, description="Current people in DB")


class ImportRequest(BaseModel):
    """Request to import from a source."""

    source_id: str = Field(..., description="Source to import from")


class ImportResponse(BaseModel):
    """Result of an import operation."""

    source_id: str
    imported: int = Field(0, description="New contacts imported")
    updated: int = Field(0, description="Existing contacts updated")
    skipped: int = Field(0, description="Contacts skipped (duplicates)")
    message: str = Field("", description="Status message")


def _check_apple_contacts() -> ContactSourceInfo:
    """Check Apple Contacts availability and count."""
    import glob
    import sqlite3
    from pathlib import Path

    source = ContactSourceInfo(
        id="apple",
        name="Apple Contacts",
        type="apple",
        description="Import contacts from macOS Contacts app",
    )
    try:
        dbs = glob.glob(str(Path.home() / "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"))
        if not dbs:
            source.status = "unavailable"
            return source

        total = 0
        for db_path in dbs:
            try:
                conn = sqlite3.connect(db_path)
                cnt = conn.execute("SELECT COUNT(*) FROM ZABCDRECORD").fetchone()[0]
                total += cnt
                conn.close()
            except Exception:
                pass

        source.available = total > 0
        source.estimated_count = total
        source.status = "ready" if total > 0 else "empty"
    except Exception as e:
        source.status = f"error: {e}"
    return source


def _check_whatsapp() -> ContactSourceInfo:
    """Check WhatsApp bridge availability."""
    import urllib.request

    source = ContactSourceInfo(
        id="whatsapp",
        name="WhatsApp",
        type="whatsapp",
        description="Import contacts from WhatsApp conversations",
    )
    try:
        req = urllib.request.urlopen("http://127.0.0.1:7601/health", timeout=3)
        data = __import__("json").loads(req.read())
        source.available = data.get("connected", False)
        source.estimated_count = data.get("messages", 0)
        source.status = "ready" if source.available else "disconnected"
    except Exception:
        source.status = "unavailable"
    return source


def _check_telegram() -> ContactSourceInfo:
    """Check Telegram bridge availability."""
    import urllib.request

    source = ContactSourceInfo(
        id="telegram",
        name="Telegram",
        type="telegram",
        description="Import contacts from Telegram chats",
    )
    try:
        req = urllib.request.urlopen("http://127.0.0.1:4098/health", timeout=3)
        data = __import__("json").loads(req.read())
        source.available = data.get("ok", False)
        source.status = "ready" if source.available else "disconnected"
    except Exception:
        source.status = "unavailable"
    return source


def _check_google() -> ContactSourceInfo:
    """Check Google Contacts availability via workspace MCP."""
    from pathlib import Path
    import yaml

    source = ContactSourceInfo(
        id="google",
        name="Google Contacts",
        type="google",
        description="Import contacts from your Google account",
    )
    try:
        accounts_path = Path.home() / ".aos" / "config" / "accounts.yaml"
        if accounts_path.exists():
            with open(accounts_path) as f:
                accounts = yaml.safe_load(f) or {}
            google = accounts.get("accounts", {}).get("google", {})
            entries = google.get("entries", {})
            if entries:
                # Google workspace is configured
                for _email, info in entries.items():
                    services = info.get("services", [])
                    if "contacts" in services:
                        source.available = True
                        source.status = "ready"
                        source.description = f"Import from {_email}"
                        break
        if not source.available:
            source.status = "not_configured"
    except Exception:
        source.status = "unavailable"
    return source


@router.get("/sources", response_model=ContactSourcesResponse)
async def get_contact_sources(request: Request) -> ContactSourcesResponse:
    """Discover available contact import sources for onboarding."""
    sources = [
        _check_apple_contacts(),
        _check_google(),
        _check_whatsapp(),
        _check_telegram(),
    ]

    # Get current people count
    people_count = 0
    ontology = getattr(request.app.state, "ontology", None)
    if ontology:
        try:
            people_count = ontology.count(ObjectType.PERSON)
        except Exception:
            pass

    available = [s for s in sources if s.available]
    return ContactSourcesResponse(
        sources=sources,
        total_available=len(available),
        people_count=people_count,
    )


@router.post("/import", response_model=ImportResponse)
async def import_contacts(body: ImportRequest, request: Request) -> ImportResponse | JSONResponse:
    """Trigger a contact import from a specified source."""
    source_id = body.source_id

    if source_id == "apple":
        # Trigger sync_contacts.py
        import subprocess
        import sys
        from pathlib import Path

        script = Path.home() / "aos" / "core" / "engine" / "comms" / "sync_contacts.py"
        if not script.exists():
            return JSONResponse({"error": "Apple contacts sync script not found"}, status_code=500)

        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, timeout=60,
            )
            # Parse output for counts
            output = result.stdout + result.stderr
            imported = output.count("INSERT") + output.count("imported") + output.count("new")
            return ImportResponse(
                source_id=source_id,
                imported=imported,
                message=f"Apple Contacts sync completed. {output[:200]}" if output else "Sync completed.",
            )
        except subprocess.TimeoutExpired:
            return ImportResponse(source_id=source_id, message="Import timed out after 60s")
        except Exception as e:
            return JSONResponse({"error": f"Import failed: {e}"}, status_code=500)

    elif source_id == "google":
        return ImportResponse(
            source_id=source_id,
            message="Google Contacts import via MCP — use the companion to run: 'import my Google contacts'",
        )

    elif source_id in ("whatsapp", "telegram"):
        return ImportResponse(
            source_id=source_id,
            message=f"{source_id.title()} contacts are auto-imported from message history. New contacts appear as you chat.",
        )

    return JSONResponse({"error": f"Unknown source: {source_id}"}, status_code=400)


# ---------------------------------------------------------------------------
# Pipeline triggers — run enrichment on demand
# ---------------------------------------------------------------------------


class PipelineRunRequest(BaseModel):
    """Request to trigger a data pipeline."""

    pipeline: str = Field(..., description="Pipeline ID: extraction, patterns, sync")


class PipelineRunResponse(BaseModel):
    """Result of a pipeline trigger."""

    pipeline: str
    started: bool = False
    message: str = ""
    output: str = ""


# Track running pipelines to prevent double-starts
_running_pipelines: set[str] = set()


PIPELINE_SCRIPTS = {
    "extraction": {
        "command": ["python3", str(FilePath.home() / "aos" / "core" / "engine" / "comms" / "extract" / "lifecycle.py")],
        "description": "Extract interaction history from WhatsApp, iMessage, Telegram",
        "timeout": 300,
    },
    "patterns": {
        "command": ["python3", str(FilePath.home() / "aos" / "core" / "engine" / "comms" / "patterns" / "compute.py")],
        "description": "Compute communication patterns and auto-classify importance",
        "timeout": 180,
    },
    "sync": {
        "command": ["python3", str(FilePath.home() / "aos" / "core" / "engine" / "comms" / "sync_contacts.py")],
        "description": "Sync macOS Contacts into People DB",
        "timeout": 120,
    },
}


@router.post("/pipelines/run", response_model=PipelineRunResponse)
async def run_pipeline(body: PipelineRunRequest) -> PipelineRunResponse | JSONResponse:
    """Trigger a data pipeline to run on demand.

    Available pipelines: extraction, patterns, sync.
    Runs as a subprocess with a timeout. Returns output.
    """
    import subprocess
    import sys
    from pathlib import Path

    pipeline_id = body.pipeline.lower()
    if pipeline_id not in PIPELINE_SCRIPTS:
        return JSONResponse(
            {"error": f"Unknown pipeline: {pipeline_id}. Available: {list(PIPELINE_SCRIPTS.keys())}"},
            status_code=400,
        )

    if pipeline_id in _running_pipelines:
        return PipelineRunResponse(
            pipeline=pipeline_id,
            started=False,
            message=f"Pipeline '{pipeline_id}' is already running",
        )

    config = PIPELINE_SCRIPTS[pipeline_id]
    _running_pipelines.add(pipeline_id)

    try:
        result = subprocess.run(
            config["command"],
            capture_output=True,
            text=True,
            timeout=config["timeout"],
            cwd=str(Path.home() / "aos"),
        )
        output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0

        return PipelineRunResponse(
            pipeline=pipeline_id,
            started=True,
            message=f"Pipeline '{pipeline_id}' {'completed' if success else 'failed'} (exit {result.returncode})",
            output=output[:2000],  # Cap output length
        )
    except subprocess.TimeoutExpired:
        return PipelineRunResponse(
            pipeline=pipeline_id,
            started=True,
            message=f"Pipeline '{pipeline_id}' timed out after {config['timeout']}s",
        )
    except Exception as e:
        logger.exception("Pipeline %s failed", pipeline_id)
        return PipelineRunResponse(
            pipeline=pipeline_id,
            started=False,
            message=f"Failed to start: {e}",
        )
    finally:
        _running_pipelines.discard(pipeline_id)


# ---------------------------------------------------------------------------
# Health — data quality, pipeline status, channel connections
# ---------------------------------------------------------------------------


class PipelineStatus(BaseModel):
    """Status of a data pipeline."""

    name: str = Field(..., description="Pipeline name")
    last_run: str | None = Field(None, description="ISO timestamp of last run")
    stale: bool = Field(False, description="Whether the pipeline is overdue")
    stale_days: int = Field(0, description="Days since last run")
    description: str = Field("", description="What this pipeline does")
    can_trigger: bool = Field(True, description="Whether this can be triggered manually")


class ChannelHealth(BaseModel):
    """Health status of a communication channel."""

    channel: str = Field(..., description="Channel name")
    connected: bool = Field(False, description="Whether the channel is reachable")
    configured: bool = Field(False, description="Whether the channel is configured")
    contact_count: int = Field(0, description="Contacts with this channel identifier")
    detail: str = Field("", description="Status detail")


class DataQuality(BaseModel):
    """Data quality metrics for the people system."""

    total_contacts: int = 0
    with_interactions: int = 0
    with_metadata: int = 0
    with_identifiers: int = 0
    enrichment_pct: int = 0
    importance_dist: dict[str, int] = Field(default_factory=dict)
    needs_enrichment: int = 0


class HealthIssue(BaseModel):
    """An actionable health issue."""

    severity: str = Field("warning", description="info, warning, error")
    message: str = Field(..., description="Human-readable issue")
    action: str | None = Field(None, description="Suggested action or pipeline to run")
    action_id: str | None = Field(None, description="Pipeline ID for trigger endpoint")


class PeopleHealthResponse(BaseModel):
    """Complete health report for the people system."""

    healthy: bool = Field(True, description="Overall health status")
    data_quality: DataQuality = Field(default_factory=DataQuality)
    pipelines: list[PipelineStatus] = Field(default_factory=list)
    channels: list[ChannelHealth] = Field(default_factory=list)
    integrations: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[HealthIssue] = Field(default_factory=list)


@router.get("/health", response_model=PeopleHealthResponse)
async def get_people_health(request: Request) -> PeopleHealthResponse:
    """Comprehensive health check for the people system.

    Reports data quality, pipeline freshness, channel connectivity,
    and integration status. Powers the health banner and settings panel.
    """
    import sqlite3
    import urllib.request
    from datetime import datetime
    from pathlib import Path

    issues: list[HealthIssue] = []
    now = datetime.now()

    # ── Data Quality ──────────────────────────────────────
    dq = DataQuality()
    people_db = Path.home() / ".aos" / "data" / "people.db"
    if people_db.exists():
        conn = sqlite3.connect(str(people_db))
        dq.total_contacts = conn.execute(
            "SELECT COUNT(*) FROM people WHERE is_archived = 0"
        ).fetchone()[0]
        dq.with_interactions = conn.execute(
            "SELECT COUNT(DISTINCT person_id) FROM interactions"
        ).fetchone()[0]
        dq.with_metadata = conn.execute(
            "SELECT COUNT(DISTINCT person_id) FROM contact_metadata"
        ).fetchone()[0]
        dq.with_identifiers = conn.execute(
            "SELECT COUNT(DISTINCT person_id) FROM person_identifiers"
        ).fetchone()[0]
        dq.enrichment_pct = (
            (dq.with_interactions * 100 // max(1, dq.total_contacts))
        )
        dq.needs_enrichment = dq.total_contacts - dq.with_interactions

        # Importance distribution
        for row in conn.execute(
            "SELECT importance, COUNT(*) FROM people WHERE is_archived = 0 GROUP BY importance"
        ).fetchall():
            dq.importance_dist[str(row[0])] = row[1]

        # ── Pipeline Status ───────────────────────────────
        pipelines: list[PipelineStatus] = []

        # Extraction
        last_ix = conn.execute("SELECT MAX(indexed_at) FROM interactions").fetchone()[0]
        last_ix_dt = datetime.fromtimestamp(last_ix) if last_ix else None
        ix_stale_days = (now - last_ix_dt).days if last_ix_dt else 999
        pipelines.append(PipelineStatus(
            name="extraction",
            last_run=last_ix_dt.isoformat() if last_ix_dt else None,
            stale=ix_stale_days > 2,
            stale_days=ix_stale_days,
            description="Extracts interaction history from WhatsApp, iMessage, Telegram",
        ))
        if ix_stale_days > 2:
            issues.append(HealthIssue(
                severity="warning",
                message=f"Interaction extraction hasn't run in {ix_stale_days} days",
                action="Run extraction to update interaction history",
                action_id="extraction",
            ))

        # Patterns
        last_pat = conn.execute("SELECT MAX(computed_at) FROM communication_patterns").fetchone()[0]
        last_pat_dt = datetime.fromtimestamp(last_pat) if last_pat else None
        pat_stale_days = (now - last_pat_dt).days if last_pat_dt else 999
        pipelines.append(PipelineStatus(
            name="patterns",
            last_run=last_pat_dt.isoformat() if last_pat_dt else None,
            stale=pat_stale_days > 3,
            stale_days=pat_stale_days,
            description="Computes communication patterns and importance classification",
        ))
        if pat_stale_days > 3:
            issues.append(HealthIssue(
                severity="warning",
                message=f"Pattern computation hasn't run in {pat_stale_days} days",
                action="Run patterns to update importance and trends",
                action_id="patterns",
            ))

        # Classification — check if most people are at default importance
        default_imp = dq.importance_dist.get("3", 0)
        if dq.total_contacts > 50 and default_imp > dq.total_contacts * 0.8:
            issues.append(HealthIssue(
                severity="warning",
                message=f"{default_imp} of {dq.total_contacts} contacts at default importance — classification needed",
                action="Run classification to auto-tier contacts",
                action_id="patterns",
            ))

        # Enrichment
        if dq.total_contacts > 0 and dq.enrichment_pct < 30:
            issues.append(HealthIssue(
                severity="warning",
                message=f"Only {dq.enrichment_pct}% of contacts enriched ({dq.needs_enrichment} need enrichment)",
                action="Run extraction to enrich contacts from message history",
                action_id="extraction",
            ))

        conn.close()
    else:
        issues.append(HealthIssue(
            severity="error",
            message="People database not found",
            action="Run contact sync to create the database",
            action_id="sync",
        ))
        pipelines = []

    # ── Channel Health ────────────────────────────────────
    channels_health: list[ChannelHealth] = []

    # WhatsApp
    wa_health = ChannelHealth(channel="whatsapp", configured=True)
    try:
        req = urllib.request.urlopen("http://127.0.0.1:7601/health", timeout=2)
        data = __import__("json").loads(req.read())
        wa_health.connected = data.get("connected", False)
        wa_health.detail = f"Phone: {data.get('phone', '?')}, {data.get('messages', 0)} msgs"
    except Exception:
        wa_health.detail = "Bridge not reachable"
    if people_db.exists():
        conn = sqlite3.connect(str(people_db))
        wa_health.contact_count = conn.execute(
            "SELECT COUNT(DISTINCT person_id) FROM person_identifiers WHERE type = 'wa_jid'"
        ).fetchone()[0]
        conn.close()
    channels_health.append(wa_health)

    # Telegram
    tg_health = ChannelHealth(channel="telegram", configured=True)
    try:
        req = urllib.request.urlopen("http://127.0.0.1:4098/health", timeout=2)
        data = __import__("json").loads(req.read())
        tg_health.connected = data.get("ok", False)
        tg_health.detail = "Bridge active"
    except Exception:
        tg_health.detail = "Bridge not reachable"
    channels_health.append(tg_health)

    # iMessage
    imsg_health = ChannelHealth(channel="imessage", configured=True)
    chat_db = Path.home() / "Library" / "Messages" / "chat.db"
    imsg_health.connected = chat_db.exists()
    imsg_health.detail = "chat.db accessible" if chat_db.exists() else "chat.db not found"
    channels_health.append(imsg_health)

    # Google
    google_health = ChannelHealth(channel="google", configured=False)
    try:
        import yaml as _yaml
        acct_path = Path.home() / ".aos" / "config" / "accounts.yaml"
        if acct_path.exists():
            with open(acct_path) as f:
                acct_data = _yaml.safe_load(f) or {}
            contexts = acct_data.get("contexts", {})
            google_emails = []
            for _ctx, cfg in contexts.items():
                g = cfg.get("accounts", {}).get("google")
                if g:
                    google_emails.append(g)
            if google_emails:
                google_health.configured = True
                google_health.connected = True
                google_health.detail = f"Accounts: {', '.join(google_emails)}"
    except Exception:
        pass
    channels_health.append(google_health)

    if not any(c.connected for c in channels_health):
        issues.append(HealthIssue(
            severity="error",
            message="No communication channels connected",
            action="Connect WhatsApp, Telegram, or iMessage in settings",
        ))

    # ── Integrations ──────────────────────────────────────
    integrations_list: list[dict[str, Any]] = []
    try:
        import yaml as _yaml
        int_path = Path.home() / ".aos" / "config" / "integrations.yaml"
        if int_path.exists():
            with open(int_path) as f:
                int_data = _yaml.safe_load(f) or {}
            for name, cfg in int_data.get("integrations", {}).items():
                integrations_list.append({
                    "name": name,
                    "status": cfg.get("status", "unknown"),
                    "configured": cfg.get("configured"),
                })
    except Exception:
        pass

    healthy = len([i for i in issues if i.severity == "error"]) == 0

    return PeopleHealthResponse(
        healthy=healthy,
        data_quality=dq,
        pipelines=pipelines,
        channels=channels_health,
        integrations=integrations_list,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Orbit — lightweight data for visualization
# ---------------------------------------------------------------------------


class OrbitNode(BaseModel):
    """A person node for the orbit visualization."""

    id: str
    name: str
    importance: int = 3
    interaction_count: int = 0
    trend: str | None = None
    organization: str | None = None
    days_since: int | None = None


class OrbitResponse(BaseModel):
    """All contacts formatted for orbit visualization."""

    nodes: list[OrbitNode] = Field(default_factory=list)
    total: int = 0


@router.get("/orbit", response_model=OrbitResponse)
async def get_orbit_data(request: Request) -> OrbitResponse:
    """Lightweight contact data for the orbit visualization.

    Returns all active contacts with just the fields the canvas needs:
    importance (ring), interaction count (size), trend (color), org (grouping).
    """
    import sqlite3
    from pathlib import Path

    people_db = Path.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return OrbitResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    rows = conn.execute("""
        SELECT
            p.id,
            p.canonical_name AS name,
            p.importance,
            COALESCE(rs.interaction_count_90d, 0) AS interaction_count,
            rs.trajectory AS trend,
            rs.days_since_contact AS days_since,
            cm.organization
        FROM people p
        LEFT JOIN relationship_state rs ON rs.person_id = p.id
        LEFT JOIN contact_metadata cm ON cm.person_id = p.id
        WHERE p.is_archived = 0
        ORDER BY p.importance ASC, COALESCE(rs.interaction_count_90d, 0) DESC
    """).fetchall()

    conn.close()

    nodes = [
        OrbitNode(
            id=r["id"],
            name=r["name"] or "Unknown",
            importance=r["importance"] or 3,
            interaction_count=r["interaction_count"] or 0,
            trend=r["trend"],
            organization=r["organization"],
            days_since=r["days_since"],
        )
        for r in rows
    ]

    return OrbitResponse(nodes=nodes, total=len(nodes))


# ---------------------------------------------------------------------------
# Recent Activity — cross-person interaction feed
# ---------------------------------------------------------------------------

class RecentActivityItem(BaseModel):
    person_id: str
    person_name: str
    importance: int = 4
    channel: str
    direction: str
    msg_count: int
    occurred_at: str | None = None
    organization: str | None = None

class RecentActivityResponse(BaseModel):
    items: list[RecentActivityItem] = Field(default_factory=list)
    total: int = 0


@router.get("/recent", response_model=RecentActivityResponse)
async def get_recent_activity(
    request: Request,
    days: int = Query(14, ge=1, le=90),
    limit: int = Query(30, ge=1, le=100),
) -> RecentActivityResponse:
    """Cross-person interaction feed — recent communications across all channels."""
    import sqlite3
    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return RecentActivityResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    import time
    cutoff = int(time.time()) - days * 86400

    try:
        rows = conn.execute("""
            SELECT i.person_id, p.canonical_name AS person_name, p.importance,
                   i.channel, i.direction, i.msg_count, i.occurred_at,
                   cm.organization
            FROM interactions i
            JOIN people p ON p.id = i.person_id
            LEFT JOIN contact_metadata cm ON cm.person_id = p.id
            WHERE i.occurred_at >= ? AND p.is_archived = 0
            ORDER BY i.occurred_at DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()

        items = []
        for r in rows:
            from datetime import datetime
            ts = datetime.fromtimestamp(r["occurred_at"]).isoformat() if r["occurred_at"] else None
            items.append(RecentActivityItem(
                person_id=r["person_id"],
                person_name=r["person_name"],
                importance=r["importance"],
                channel=r["channel"],
                direction=r["direction"],
                msg_count=r["msg_count"],
                occurred_at=ts,
                organization=r.get("organization"),
            ))
        return RecentActivityResponse(items=items, total=len(items))
    except Exception:
        logger.exception("Failed to get recent activity")
        return RecentActivityResponse()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Relationships / Graph data
# ---------------------------------------------------------------------------

class RelationshipGraphNode(BaseModel):
    id: str
    name: str
    importance: int = 4
    organization: str | None = None

class RelationshipGraphEdge(BaseModel):
    source: str
    target: str
    type: str
    subtype: str | None = None
    context: str | None = None
    strength: float = 0.5

class RelationshipGraphResponse(BaseModel):
    nodes: list[RelationshipGraphNode] = Field(default_factory=list)
    edges: list[RelationshipGraphEdge] = Field(default_factory=list)


@router.get("/graph", response_model=RelationshipGraphResponse)
async def get_relationship_graph(request: Request) -> RelationshipGraphResponse:
    """Relationship graph — family, community, and org connections."""
    import sqlite3
    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return RelationshipGraphResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        # Get all explicit relationships
        edges = []
        node_ids: set[str] = set()
        rels = conn.execute("""
            SELECT person_a_id, person_b_id, type, subtype, context, strength
            FROM relationships
        """).fetchall()
        for r in rels:
            edges.append(RelationshipGraphEdge(
                source=r["person_a_id"], target=r["person_b_id"],
                type=r["type"], subtype=r.get("subtype"),
                context=r.get("context"), strength=r.get("strength", 0.5),
            ))
            node_ids.add(r["person_a_id"])
            node_ids.add(r["person_b_id"])

        # Also include inner circle even if no explicit relationships
        inner = conn.execute(
            "SELECT id FROM people WHERE importance <= 2 AND is_archived = 0"
        ).fetchall()
        for r in inner:
            node_ids.add(r["id"])

        # Fetch node details
        nodes = []
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            people = conn.execute(f"""
                SELECT p.id, p.canonical_name, p.importance, cm.organization
                FROM people p
                LEFT JOIN contact_metadata cm ON cm.person_id = p.id
                WHERE p.id IN ({placeholders})
            """, list(node_ids)).fetchall()
            for p in people:
                nodes.append(RelationshipGraphNode(
                    id=p["id"], name=p["canonical_name"],
                    importance=p["importance"],
                    organization=p.get("organization"),
                ))

        return RelationshipGraphResponse(nodes=nodes, edges=edges)
    except Exception:
        logger.exception("Failed to build relationship graph")
        return RelationshipGraphResponse()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Circles — detected social groups with memberships
# ---------------------------------------------------------------------------


class CircleResponse(BaseModel):
    id: str
    name: str
    category: str | None = None
    subcategory: str | None = None
    source: str | None = None
    confidence: float = 1.0
    member_count: int = 0


class CircleListResponse(BaseModel):
    circles: list[CircleResponse] = Field(default_factory=list)
    total: int = 0


class CircleMemberResponse(BaseModel):
    person_id: str
    name: str
    importance: int = 3
    role_in_circle: str | None = None
    confidence: float = 1.0


class CircleDetailResponse(BaseModel):
    circle: CircleResponse
    members: list[CircleMemberResponse] = Field(default_factory=list)


@router.get("/circles", response_model=CircleListResponse)
async def list_circles(request: Request) -> CircleListResponse:
    """All detected circles with member counts."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return CircleListResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        rows = conn.execute("""
            SELECT c.id, c.name, c.category, c.subcategory, c.source, c.confidence,
                   COUNT(cm.person_id) AS member_count
            FROM circle c
            LEFT JOIN circle_membership cm ON cm.circle_id = c.id
            GROUP BY c.id
            ORDER BY member_count DESC
        """).fetchall()

        circles = [
            CircleResponse(
                id=r["id"],
                name=r["name"],
                category=r.get("category"),
                subcategory=r.get("subcategory"),
                source=r.get("source"),
                confidence=r.get("confidence", 1.0),
                member_count=r["member_count"],
            )
            for r in rows
        ]
        return CircleListResponse(circles=circles, total=len(circles))
    except Exception:
        logger.exception("Failed to list circles")
        return CircleListResponse()
    finally:
        conn.close()


@router.get("/circles/{circle_id}", response_model=CircleDetailResponse)
async def get_circle(circle_id: str) -> CircleDetailResponse | JSONResponse:
    """Circle details with member list."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return JSONResponse({"error": "People database not found"}, status_code=404)

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        row = conn.execute("""
            SELECT c.id, c.name, c.category, c.subcategory, c.source, c.confidence,
                   COUNT(cm.person_id) AS member_count
            FROM circle c
            LEFT JOIN circle_membership cm ON cm.circle_id = c.id
            WHERE c.id = ?
            GROUP BY c.id
        """, (circle_id,)).fetchone()

        if not row:
            return JSONResponse({"error": f"Circle not found: {circle_id}"}, status_code=404)

        circle = CircleResponse(
            id=row["id"],
            name=row["name"],
            category=row.get("category"),
            subcategory=row.get("subcategory"),
            source=row.get("source"),
            confidence=row.get("confidence", 1.0),
            member_count=row["member_count"],
        )

        members_rows = conn.execute("""
            SELECT cm.person_id, p.canonical_name AS name, p.importance,
                   cm.role AS role_in_circle, cm.confidence
            FROM circle_membership cm
            JOIN people p ON p.id = cm.person_id
            WHERE cm.circle_id = ?
            ORDER BY p.importance ASC, p.canonical_name ASC
        """, (circle_id,)).fetchall()

        members = [
            CircleMemberResponse(
                person_id=m["person_id"],
                name=m["name"] or "Unknown",
                importance=m.get("importance", 3),
                role_in_circle=m.get("role_in_circle"),
                confidence=m.get("confidence", 1.0),
            )
            for m in members_rows
        ]

        return CircleDetailResponse(circle=circle, members=members)
    except Exception:
        logger.exception("Failed to get circle %s", circle_id)
        return JSONResponse({"error": "Failed to load circle"}, status_code=500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hygiene — data quality queue management
# ---------------------------------------------------------------------------


class HygieneIssueResponse(BaseModel):
    id: str
    action_type: str
    person_a_id: str | None = None
    person_a_name: str | None = None
    person_b_id: str | None = None
    person_b_name: str | None = None
    confidence: float = 0.0
    reason: str | None = None
    proposed_data: dict | None = None
    status: str = "pending"
    created_at: str | None = None


class HygieneListResponse(BaseModel):
    issues: list[HygieneIssueResponse] = Field(default_factory=list)
    total: int = 0


class HygieneStatsResponse(BaseModel):
    total_pending: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    total_resolved: int = 0


class HygieneActionRequest(BaseModel):
    notes: str = ""


@router.get("/hygiene", response_model=HygieneListResponse)
async def list_hygiene_issues(
    status: str = Query("pending"),
    action_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> HygieneListResponse:
    """Pending hygiene issues with person names joined."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return HygieneListResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        query = """
            SELECT hq.id, hq.action_type, hq.person_a_id, hq.person_b_id,
                   hq.confidence, hq.reason, hq.proposed_data, hq.status, hq.created_at,
                   pa.canonical_name AS person_a_name,
                   pb.canonical_name AS person_b_name
            FROM hygiene_queue hq
            LEFT JOIN people pa ON pa.id = hq.person_a_id
            LEFT JOIN people pb ON pb.id = hq.person_b_id
            WHERE hq.status = ?
        """
        params: list[Any] = [status]

        if action_type:
            query += " AND hq.action_type = ?"
            params.append(action_type)

        query += " ORDER BY hq.confidence DESC, hq.created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        issues = []
        for r in rows:
            proposed = None
            if r.get("proposed_data"):
                try:
                    import json
                    proposed = json.loads(r["proposed_data"])
                except (json.JSONDecodeError, TypeError):
                    proposed = None

            created_at = None
            if r.get("created_at"):
                try:
                    from datetime import datetime
                    created_at = datetime.fromtimestamp(r["created_at"]).isoformat()
                except (ValueError, TypeError, OSError):
                    created_at = str(r["created_at"])

            issues.append(HygieneIssueResponse(
                id=r["id"],
                action_type=r["action_type"],
                person_a_id=r.get("person_a_id"),
                person_a_name=r.get("person_a_name"),
                person_b_id=r.get("person_b_id"),
                person_b_name=r.get("person_b_name"),
                confidence=r.get("confidence", 0.0),
                reason=r.get("reason"),
                proposed_data=proposed,
                status=r.get("status", "pending"),
                created_at=created_at,
            ))
        return HygieneListResponse(issues=issues, total=len(issues))
    except Exception:
        logger.exception("Failed to list hygiene issues")
        return HygieneListResponse()
    finally:
        conn.close()


@router.get("/hygiene/stats", response_model=HygieneStatsResponse)
async def get_hygiene_stats() -> HygieneStatsResponse:
    """Counts by type and status."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return HygieneStatsResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        pending = conn.execute(
            "SELECT COUNT(*) AS cnt FROM hygiene_queue WHERE status = 'pending'"
        ).fetchone()["cnt"]

        by_type_rows = conn.execute(
            "SELECT action_type, COUNT(*) AS cnt FROM hygiene_queue WHERE status = 'pending' GROUP BY action_type"
        ).fetchall()
        by_type = {r["action_type"]: r["cnt"] for r in by_type_rows}

        resolved = conn.execute(
            "SELECT COUNT(*) AS cnt FROM hygiene_queue WHERE status IN ('approved', 'rejected')"
        ).fetchone()["cnt"]

        return HygieneStatsResponse(
            total_pending=pending,
            by_type=by_type,
            total_resolved=resolved,
        )
    except Exception:
        logger.exception("Failed to get hygiene stats")
        return HygieneStatsResponse()
    finally:
        conn.close()


@router.post("/hygiene/{issue_id}/approve")
async def approve_hygiene_issue(issue_id: str) -> JSONResponse:
    """Approve a hygiene issue. For merges, executes the merge."""
    import sys

    sys.path.insert(0, str(FilePath.home() / "aos" / "core" / "engine" / "people"))
    try:
        from hygiene import HygieneEngine
        engine = HygieneEngine()
        result = engine.approve_issue(issue_id)
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        logger.exception("Failed to approve hygiene issue %s", issue_id)
        return JSONResponse({"error": f"Approval failed: {e}"}, status_code=500)


@router.post("/hygiene/{issue_id}/reject")
async def reject_hygiene_issue(issue_id: str, body: HygieneActionRequest) -> JSONResponse:
    """Reject a hygiene issue with optional notes."""
    import sys

    sys.path.insert(0, str(FilePath.home() / "aos" / "core" / "engine" / "people"))
    try:
        from hygiene import HygieneEngine
        engine = HygieneEngine()
        result = engine.reject_issue(issue_id, notes=body.notes)
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        logger.exception("Failed to reject hygiene issue %s", issue_id)
        return JSONResponse({"error": f"Rejection failed: {e}"}, status_code=500)


@router.post("/hygiene/run")
async def run_hygiene_scan() -> JSONResponse:
    """Trigger a full hygiene scan and auto-fix tier-1 issues."""
    import sys

    sys.path.insert(0, str(FilePath.home() / "aos" / "core" / "engine" / "people"))
    try:
        from hygiene import HygieneEngine
        engine = HygieneEngine()
        scan_results = engine.scan_all()
        fix_results = engine.run_tier1_fixes()
        return JSONResponse({
            "ok": True,
            "scan": scan_results,
            "fixes": fix_results,
        })
    except Exception as e:
        logger.exception("Failed to run hygiene scan")
        return JSONResponse({"error": f"Scan failed: {e}"}, status_code=500)


# ---------------------------------------------------------------------------
# Organizations — inferred org memberships
# ---------------------------------------------------------------------------


class OrgResponse(BaseModel):
    id: str
    name: str
    type: str | None = None
    domain: str | None = None
    industry: str | None = None
    city: str | None = None
    member_count: int = 0


class OrgListResponse(BaseModel):
    organizations: list[OrgResponse] = Field(default_factory=list)
    total: int = 0


class OrgMemberResponse(BaseModel):
    person_id: str
    name: str
    role: str | None = None
    department: str | None = None
    importance: int = 3


class OrgDetailResponse(BaseModel):
    organization: OrgResponse
    members: list[OrgMemberResponse] = Field(default_factory=list)


@router.get("/orgs", response_model=OrgListResponse)
async def list_organizations() -> OrgListResponse:
    """All organizations with member counts."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return OrgListResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        rows = conn.execute("""
            SELECT o.id, o.name, o.type, o.domain, o.industry, o.city,
                   COUNT(om.person_id) AS member_count
            FROM organization o
            LEFT JOIN membership om ON om.org_id = o.id
            GROUP BY o.id
            ORDER BY member_count DESC
        """).fetchall()

        orgs = [
            OrgResponse(
                id=r["id"],
                name=r["name"],
                type=r.get("type"),
                domain=r.get("domain"),
                industry=r.get("industry"),
                city=r.get("city"),
                member_count=r["member_count"],
            )
            for r in rows
        ]
        return OrgListResponse(organizations=orgs, total=len(orgs))
    except Exception:
        logger.exception("Failed to list organizations")
        return OrgListResponse()
    finally:
        conn.close()


@router.get("/orgs/{org_id}", response_model=OrgDetailResponse)
async def get_organization(org_id: str) -> OrgDetailResponse | JSONResponse:
    """Organization details with members ordered by seniority."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return JSONResponse({"error": "People database not found"}, status_code=404)

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        row = conn.execute("""
            SELECT o.id, o.name, o.type, o.domain, o.industry, o.city,
                   COUNT(om.person_id) AS member_count
            FROM organization o
            LEFT JOIN membership om ON om.org_id = o.id
            WHERE o.id = ?
            GROUP BY o.id
        """, (org_id,)).fetchone()

        if not row:
            return JSONResponse({"error": f"Organization not found: {org_id}"}, status_code=404)

        org = OrgResponse(
            id=row["id"],
            name=row["name"],
            type=row.get("type"),
            domain=row.get("domain"),
            industry=row.get("industry"),
            city=row.get("city"),
            member_count=row["member_count"],
        )

        members_rows = conn.execute("""
            SELECT om.person_id, p.canonical_name AS name, om.role, om.department,
                   p.importance
            FROM membership om
            JOIN people p ON p.id = om.person_id
            WHERE om.org_id = ?
            ORDER BY p.importance ASC, p.canonical_name ASC
        """, (org_id,)).fetchall()

        members = [
            OrgMemberResponse(
                person_id=m["person_id"],
                name=m["name"] or "Unknown",
                role=m.get("role"),
                department=m.get("department"),
                importance=m.get("importance", 3),
            )
            for m in members_rows
        ]

        return OrgDetailResponse(organization=org, members=members)
    except Exception:
        logger.exception("Failed to get organization %s", org_id)
        return JSONResponse({"error": "Failed to load organization"}, status_code=500)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Family Tree — family-only relationship edges for visualization
# ---------------------------------------------------------------------------


class FamilyEdge(BaseModel):
    source_id: str
    source_name: str
    target_id: str
    target_name: str
    relationship: str  # spouse, parent, child, sibling


class FamilyTreeResponse(BaseModel):
    edges: list[FamilyEdge] = Field(default_factory=list)
    total: int = 0


@router.get("/graph/family", response_model=FamilyTreeResponse)
async def get_family_tree() -> FamilyTreeResponse:
    """Family-only relationship edges for tree visualization."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return FamilyTreeResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        rows = conn.execute("""
            SELECT r.person_a_id AS source_id, pa.canonical_name AS source_name,
                   r.person_b_id AS target_id, pb.canonical_name AS target_name,
                   r.subtype AS relationship
            FROM relationships r
            JOIN people pa ON pa.id = r.person_a_id
            JOIN people pb ON pb.id = r.person_b_id
            WHERE r.type = 'family'
            ORDER BY pa.canonical_name ASC
        """).fetchall()

        edges = [
            FamilyEdge(
                source_id=r["source_id"],
                source_name=r["source_name"] or "Unknown",
                target_id=r["target_id"],
                target_name=r["target_name"] or "Unknown",
                relationship=r.get("relationship") or "family",
            )
            for r in rows
        ]
        return FamilyTreeResponse(edges=edges, total=len(edges))
    except Exception:
        logger.exception("Failed to build family tree")
        return FamilyTreeResponse()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Person Circles — circles a specific person belongs to
# ---------------------------------------------------------------------------


@router.get("/{person_id}/circles")
async def get_person_circles(person_id: str) -> JSONResponse:
    """Circles a person belongs to."""
    import sqlite3

    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return JSONResponse({"circles": [], "total": 0})

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        rows = conn.execute("""
            SELECT c.id, c.name, c.category, c.subcategory, c.source, c.confidence,
                   cm.role AS role_in_circle, cm.confidence AS membership_confidence
            FROM circle_membership cm
            JOIN circle c ON c.id = cm.circle_id
            WHERE cm.person_id = ?
            ORDER BY c.name ASC
        """, (person_id,)).fetchall()

        circles = [
            {
                "id": r["id"],
                "name": r["name"],
                "category": r.get("category"),
                "subcategory": r.get("subcategory"),
                "source": r.get("source"),
                "confidence": r.get("confidence", 1.0),
                "role_in_circle": r.get("role_in_circle"),
                "membership_confidence": r.get("membership_confidence", 1.0),
            }
            for r in rows
        ]
        return JSONResponse({"circles": circles, "total": len(circles)})
    except Exception:
        logger.exception("Failed to get circles for person %s", person_id)
        return JSONResponse({"circles": [], "total": 0})
    finally:
        conn.close()


@router.get("/surfaces", response_model=PersonSurfaceResponse)
async def get_surfaces(request: Request) -> PersonSurfaceResponse:
    """Smarter surfacing — who needs attention and why."""
    import sqlite3
    people_db = FilePath.home() / ".aos" / "data" / "people.db"
    if not people_db.exists():
        return PersonSurfaceResponse()

    conn = sqlite3.connect(str(people_db))
    conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    surfaces: list[PersonSurfaceItem] = []
    seen: set[str] = set()

    def _row_to_person(row) -> PersonResponse:
        return PersonResponse(
            id=row["id"],
            name=row.get("name") or "Unknown",
            importance=row.get("importance", 4),
            privacy_level=row.get("privacy_level", 0),
            tags=[],
            aliases=[],
            channels={},
            last_contact=None,
            days_since_contact=row.get("days_since"),
            relationship_trend=row.get("trajectory"),
            projects=[],
            organization=row.get("organization"),
            role=row.get("job_title"),
        )

    def _add(row, reason: str, urgency: int, action: str | None = None):
        pid = row["id"]
        if pid in seen:
            return
        seen.add(pid)
        surfaces.append(PersonSurfaceItem(
            person=_row_to_person(row),
            reason=reason,
            urgency=min(5, max(1, urgency)),
            suggested_action=action,
        ))

    try:
        # 1. Inner circle drifting or dormant — highest priority
        rows = conn.execute("""
            SELECT p.id, p.canonical_name AS name, p.importance, p.privacy_level,
                   rs.trajectory, rs.days_since_contact AS days_since,
                   rs.avg_days_between, rs.inbound_30d, rs.outbound_30d,
                   rs.interaction_count_30d, rs.interaction_count_90d,
                   cm.organization, cm.job_title
            FROM people p
            JOIN relationship_state rs ON rs.person_id = p.id
            LEFT JOIN contact_metadata cm ON cm.person_id = p.id
            WHERE p.is_archived = 0
              AND p.importance <= 2
              AND rs.trajectory IN ('drifting', 'dormant')
            ORDER BY p.importance ASC, rs.days_since_contact DESC
            LIMIT 10
        """).fetchall()

        for row in rows:
            days = row.get("days_since") or 0
            if row["trajectory"] == "dormant":
                _add(row, f"No contact in {days} days — relationship going quiet", 5,
                     "Reach out before the connection fades")
            else:
                _add(row, f"Drifting — last contact {days} days ago", 4,
                     "A quick message could turn this around")

        # 2. Unanswered inbound — they reached out, you didn't reply
        rows = conn.execute("""
            SELECT p.id, p.canonical_name AS name, p.importance, p.privacy_level,
                   rs.trajectory, rs.days_since_contact AS days_since,
                   rs.avg_days_between, rs.inbound_30d, rs.outbound_30d,
                   rs.interaction_count_30d, rs.interaction_count_90d,
                   cm.organization, cm.job_title
            FROM people p
            JOIN relationship_state rs ON rs.person_id = p.id
            LEFT JOIN contact_metadata cm ON cm.person_id = p.id
            WHERE p.is_archived = 0
              AND rs.inbound_30d > 0 AND rs.outbound_30d = 0
              AND p.importance <= 3
            ORDER BY rs.inbound_30d DESC, p.importance ASC
            LIMIT 8
        """).fetchall()

        for row in rows:
            _add(row, f"Messaged you {row.get('inbound_30d', 0)} times — no reply from you", 4,
                 "Reply to keep the conversation alive")

        # 3. Growing but low-tier — someone becoming important, not yet recognized
        rows = conn.execute("""
            SELECT p.id, p.canonical_name AS name, p.importance, p.privacy_level,
                   rs.trajectory, rs.days_since_contact AS days_since,
                   rs.avg_days_between, rs.inbound_30d, rs.outbound_30d,
                   rs.interaction_count_30d, rs.interaction_count_90d,
                   cm.organization, cm.job_title
            FROM people p
            JOIN relationship_state rs ON rs.person_id = p.id
            LEFT JOIN contact_metadata cm ON cm.person_id = p.id
            WHERE p.is_archived = 0
              AND rs.trajectory = 'growing'
              AND p.importance >= 3
              AND rs.interaction_count_30d >= 5
            ORDER BY rs.interaction_count_30d DESC
            LIMIT 6
        """).fetchall()

        for row in rows:
            _add(row, f"Growing connection — {row.get('interaction_count_30d', 0)} interactions this month", 2,
                 "Consider promoting their importance tier")

        # 4. Overdue based on personal cadence (not fixed 14-day)
        rows = conn.execute("""
            SELECT p.id, p.canonical_name AS name, p.importance, p.privacy_level,
                   rs.trajectory, rs.days_since_contact AS days_since,
                   rs.avg_days_between, rs.inbound_30d, rs.outbound_30d,
                   rs.interaction_count_30d, rs.interaction_count_90d,
                   cm.organization, cm.job_title
            FROM people p
            JOIN relationship_state rs ON rs.person_id = p.id
            LEFT JOIN contact_metadata cm ON cm.person_id = p.id
            WHERE p.is_archived = 0
              AND p.importance <= 2
              AND rs.avg_days_between IS NOT NULL
              AND rs.days_since_contact > rs.avg_days_between * 2
              AND rs.trajectory NOT IN ('drifting', 'dormant')
            ORDER BY (rs.days_since_contact * 1.0 / rs.avg_days_between) DESC
            LIMIT 6
        """).fetchall()

        for row in rows:
            avg = row.get("avg_days_between")
            avg_label = f"{int(avg)}d" if avg and avg >= 1 else "frequently"
            _add(row, f"Usually in touch every {avg_label} — overdue", 3,
                 "Check in when you get a chance")

    except Exception:
        logger.exception("Failed to compute people surfaces")
    finally:
        conn.close()

    surfaces.sort(key=lambda s: -s.urgency)
    return PersonSurfaceResponse(
        surfaces=surfaces,
        total=len(surfaces),
    )


@router.get("/{person_id}", response_model=PersonDetailResponse)
async def get_person(
    request: Request,
    person_id: str = Path(..., description="Person identifier"),
) -> PersonDetailResponse | JSONResponse:
    """Get detailed info for a person, including interactions and relationships."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    person = ontology.get(ObjectType.PERSON, person_id)
    if not person:
        return JSONResponse({"error": f"Person not found: {person_id}"}, status_code=404)

    # Fetch real interactions and relationships from the adapter
    interactions = []
    relationships = []
    adapter = _get_people_adapter(request)
    if adapter:
        try:
            raw_ix = adapter.get_interactions(person_id, limit=50)
            interactions = [
                InteractionSchema(
                    id=ix["id"],
                    channel=ix.get("channel", "unknown"),
                    direction=ix.get("direction", "inbound"),
                    summary=ix.get("summary"),
                    timestamp=ix.get("timestamp"),
                    message_count=ix.get("message_count", 1),
                )
                for ix in raw_ix
            ]
        except Exception:
            logger.exception("Failed to fetch interactions for %s", person_id)

        try:
            raw_rels = adapter.get_relationships(person_id, limit=50)
            relationships = [
                RelationshipSchema(
                    link_type=rel["link_type"],
                    target_type=rel["target_type"],
                    target_id=rel["target_id"],
                    target_name=rel.get("target_name"),
                )
                for rel in raw_rels
            ]
        except Exception:
            logger.exception("Failed to fetch relationships for %s", person_id)

    # Fetch channel presence (last message timestamp per channel)
    presence_schemas: list[ChannelPresenceSchema] = []
    channel_ids = _get_person_channel_ids(person)
    if channel_ids:
        try:
            _msgs, raw_presence = _fetch_messages_from_adapters(channel_ids, limit=1, days=365)
            presence_schemas = [
                ChannelPresenceSchema(
                    channel=p.channel,
                    identifier=p.identifier,
                    last_message_at=p.last_message_at,
                    available=p.available,
                )
                for p in raw_presence
            ]
        except Exception:
            logger.exception("Failed to fetch presence for %s", person_id)

    return _person_to_detail(person, interactions=interactions, relationships=relationships, presence=presence_schemas)


@router.patch("/{person_id}", response_model=PersonDetailResponse)
async def update_person(
    body: UpdatePersonRequest,
    request: Request,
    person_id: str = Path(..., description="Person identifier"),
) -> PersonDetailResponse | JSONResponse:
    """Update fields on a person record."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    fields = body.model_dump(exclude_none=True)
    updated = ontology.update(ObjectType.PERSON, person_id, fields)
    if not updated:
        return JSONResponse({"error": f"Person not found: {person_id}"}, status_code=404)

    # Re-fetch with interactions
    adapter = _get_people_adapter(request)
    interactions = []
    relationships = []
    if adapter:
        try:
            raw_ix = adapter.get_interactions(person_id, limit=50)
            interactions = [
                InteractionSchema(
                    id=ix["id"],
                    channel=ix.get("channel", "unknown"),
                    direction=ix.get("direction", "inbound"),
                    summary=ix.get("summary"),
                    timestamp=ix.get("timestamp"),
                    message_count=ix.get("message_count", 1),
                )
                for ix in raw_ix
            ]
        except Exception:
            pass
        try:
            raw_rels = adapter.get_relationships(person_id, limit=50)
            relationships = [
                RelationshipSchema(
                    link_type=rel["link_type"],
                    target_type=rel["target_type"],
                    target_id=rel["target_id"],
                    target_name=rel.get("target_name"),
                )
                for rel in raw_rels
            ]
        except Exception:
            pass
    return _person_to_detail(updated, interactions=interactions, relationships=relationships)


# ---------------------------------------------------------------------------
# Timeline — unified chronological view of all touchpoints
# ---------------------------------------------------------------------------


class TimelineEntry(BaseModel):
    """A single entry in a person's timeline."""

    id: str
    type: str = Field(..., description="Entry type: interaction, message, task_mention, vault_mention")
    channel: str | None = None
    direction: str | None = None
    summary: str | None = None
    timestamp: str | None = None
    message_count: int = 1
    metadata: dict[str, Any] | None = None


class TimelineResponse(BaseModel):
    """Chronological timeline of all touchpoints with a person."""

    person_id: str
    entries: list[TimelineEntry] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


@router.get("/{person_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    request: Request,
    person_id: str = Path(..., description="Person identifier"),
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None, description="ISO timestamp cursor for pagination"),
) -> TimelineResponse | JSONResponse:
    """Get a unified chronological timeline for a person.

    Merges interactions from people.db with any available messages from comms.db.
    """
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    person = ontology.get(ObjectType.PERSON, person_id)
    if not person:
        return JSONResponse({"error": f"Person not found: {person_id}"}, status_code=404)

    entries: list[TimelineEntry] = []

    # 1. Real messages from channel adapters (primary source)
    channel_ids = _get_person_channel_ids(person)
    if channel_ids:
        try:
            messages, _presence = _fetch_messages_from_adapters(channel_ids, limit=limit, days=90)
            for msg in messages:
                entries.append(TimelineEntry(
                    id=msg.id,
                    type="message",
                    channel=msg.channel,
                    direction="outbound" if msg.from_me else "inbound",
                    summary=msg.text[:200] if msg.text else None,
                    timestamp=msg.timestamp,
                    message_count=1,
                    metadata={"media_type": msg.media_type, "has_media": msg.has_media, "sender": msg.sender},
                ))
        except Exception:
            logger.exception("Timeline: failed to fetch messages for %s", person_id)

    # 2. Interaction summaries from people.db (fills gaps where adapters don't reach)
    adapter = _get_people_adapter(request)
    if adapter:
        try:
            raw_ix = adapter.get_interactions(person_id, limit=limit)
            # Only include interactions that don't overlap with real messages
            # (same channel + within 60s = likely the same event)
            msg_timestamps = {(e.channel, e.timestamp[:16]) for e in entries if e.timestamp}
            for ix in raw_ix:
                ts = ix.get("timestamp")
                ch = ix.get("channel")
                key = (ch, ts[:16] if ts else "")
                if key not in msg_timestamps:
                    entries.append(TimelineEntry(
                        id=ix["id"],
                        type="interaction",
                        channel=ch,
                        direction=ix.get("direction"),
                        summary=ix.get("summary"),
                        timestamp=ts,
                        message_count=ix.get("message_count", 1),
                    ))
        except Exception:
            logger.exception("Timeline: failed to fetch interactions for %s", person_id)

    # 3. Sort by timestamp descending
    entries.sort(
        key=lambda e: e.timestamp or "",
        reverse=True,
    )

    # 4. Apply cursor-based pagination
    if before:
        entries = [e for e in entries if (e.timestamp or "") < before]

    total = len(entries)
    has_more = total > limit
    entries = entries[:limit]

    return TimelineResponse(
        person_id=person_id,
        entries=entries,
        total=total,
        has_more=has_more,
    )


# ---------------------------------------------------------------------------
# Messages — real message content from channel adapters
# ---------------------------------------------------------------------------


class ChannelMessageSchema(BaseModel):
    """A real message from a channel adapter."""

    id: str = Field(..., description="Message ID")
    channel: str = Field(..., description="Channel name")
    sender: str = Field(..., description="Sender name or 'me'")
    text: str = Field("", description="Message body text")
    timestamp: str | None = Field(None, description="ISO timestamp")
    from_me: bool = Field(False, description="Whether the operator sent this")
    media_type: str = Field("text", description="Content type: text, voice, image, etc.")
    has_media: bool = Field(False, description="Whether this has a non-text attachment")


class ChannelPresence(BaseModel):
    """Per-channel presence info for a person."""

    channel: str = Field(..., description="Channel name")
    identifier: str = Field(..., description="Person's ID on this channel")
    last_message_at: str | None = Field(None, description="Timestamp of last message")
    available: bool = Field(True, description="Whether the channel adapter is reachable")


class PersonMessagesResponse(BaseModel):
    """Real messages from channel adapters for a person."""

    person_id: str
    messages: list[ChannelMessageSchema] = Field(default_factory=list)
    presence: list[ChannelPresence] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


def _get_person_channel_ids(person) -> dict[str, str]:
    """Extract channel → identifier mapping from a Person ontology object."""
    ids: dict[str, str] = {}
    if person.whatsapp_jid:
        ids["whatsapp"] = person.whatsapp_jid
    if person.email:
        ids["email"] = person.email
    if person.phone:
        ids["phone"] = person.phone
    if person.telegram_id:
        ids["telegram"] = person.telegram_id
    # Also check the channels dict (populated in Phase 1)
    channels = getattr(person, "channels", {})
    for ch, val in channels.items():
        if ch not in ids and val:
            ids[ch] = val
    return ids


def _resolve_imessage_chat(identifier: str) -> int | None:
    """Resolve a phone/email to an iMessage chat ROWID.

    Queries the local chat.db to find the chat associated with a handle.
    """
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    chat_db = Path.home() / "Library" / "Messages" / "chat.db"
    if not chat_db.exists():
        return None

    try:
        tmp = tempfile.mktemp(suffix=".db")
        shutil.copy2(str(chat_db), tmp)
        conn = sqlite3.connect(tmp)
        # Normalize: strip non-digit for phone matching
        digits = "".join(c for c in identifier if c.isdigit())
        # Try chat_identifier match (phone or email)
        row = conn.execute(
            """SELECT c.rowid FROM chat c
               WHERE c.chat_identifier LIKE ? OR c.chat_identifier LIKE ?
               LIMIT 1""",
            (f"%{digits}%", identifier),
        ).fetchone()
        conn.close()
        import os
        os.unlink(tmp)
        return row[0] if row else None
    except Exception:
        return None


def _fetch_messages_from_adapters(
    channel_ids: dict[str, str],
    limit: int = 30,
    days: int = 90,
) -> tuple[list[ChannelMessageSchema], list[ChannelPresence]]:
    """Query channel adapters for real messages.

    Returns (messages, presence) tuple.
    """
    import sys, os
    # Add the AOS root (parent of core/) so "from core.engine..." imports work
    # Works regardless of whether cwd is ~/aos/core or ~/project/aos/core
    _this_dir = FilePath(__file__).resolve().parent  # qareen/api/
    _aos_root = str(_this_dir.parent.parent.parent)  # up from qareen/api/ → qareen/ → core/ → aos root
    if _aos_root not in sys.path:
        sys.path.insert(0, _aos_root)
    from datetime import datetime, timedelta

    since = datetime.now() - timedelta(days=days)
    all_messages: list[ChannelMessageSchema] = []
    presence: list[ChannelPresence] = []

    # WhatsApp — try local adapter first (has full history), fallback to bridge
    wa_jid = channel_ids.get("whatsapp")
    if wa_jid:
        # Normalize JID: @status → @s.whatsapp.net for messaging queries
        if "@status" in wa_jid:
            wa_jid = wa_jid.replace("@status", "@s.whatsapp.net")
        elif "@lid" in wa_jid:
            pass  # @lid JIDs work as-is in the local adapter
    if wa_jid:
        try:
            from core.engine.comms.channels.whatsapp_local import WhatsAppLocalAdapter
            wal = WhatsAppLocalAdapter()
            if wal.is_available():
                msgs = wal.get_messages(conversation_id=wa_jid, since=since, limit=limit)
                last_ts = None
                for m in msgs:
                    ts = m.timestamp.isoformat() if m.timestamp else None
                    if ts and (last_ts is None or ts > last_ts):
                        last_ts = ts
                    all_messages.append(ChannelMessageSchema(
                        id=m.id,
                        channel="whatsapp",
                        sender="me" if m.from_me else m.sender,
                        text=m.text or "",
                        timestamp=ts,
                        from_me=m.from_me,
                        media_type=m.media_type,
                        has_media=m.has_media,
                    ))
                presence.append(ChannelPresence(
                    channel="whatsapp",
                    identifier=wa_jid,
                    last_message_at=last_ts,
                    available=True,
                ))
        except Exception as e:
            logger.warning("WhatsApp local adapter failed: %s", e)
            # Try bridge fallback
            try:
                from core.engine.comms.channels.whatsapp import WhatsAppAdapter
                wab = WhatsAppAdapter()
                if wab.is_available():
                    msgs = wab.get_messages(conversation_id=wa_jid, since=since, limit=limit)
                    for m in msgs:
                        ts = m.timestamp.isoformat() if m.timestamp else None
                        all_messages.append(ChannelMessageSchema(
                            id=m.id, channel="whatsapp",
                            sender="me" if m.from_me else m.sender,
                            text=m.text or "", timestamp=ts, from_me=m.from_me,
                            media_type=m.media_type, has_media=m.has_media,
                        ))
                    presence.append(ChannelPresence(
                        channel="whatsapp", identifier=wa_jid,
                        last_message_at=msgs[0].timestamp.isoformat() if msgs else None,
                        available=True,
                    ))
            except Exception:
                logger.warning("WhatsApp bridge adapter also failed")

    # iMessage — via phone number or email
    phone = channel_ids.get("phone")
    email = channel_ids.get("email")
    imsg_identifier = phone or email
    if imsg_identifier:
        try:
            from core.engine.comms.channels.imessage import iMessageAdapter
            im = iMessageAdapter()
            if im.is_available():
                # Resolve phone/email to a chat ROWID
                normalized = "+" + "".join(c for c in imsg_identifier if c.isdigit()) if phone else imsg_identifier
                chat_rowid = _resolve_imessage_chat(normalized)
                if chat_rowid:
                    msgs = im.get_messages(conversation_id=str(chat_rowid), since=since, limit=limit)
                    last_ts = None
                    for m in msgs:
                        ts = m.timestamp.isoformat() if m.timestamp else None
                        if ts and (last_ts is None or ts > last_ts):
                            last_ts = ts
                        all_messages.append(ChannelMessageSchema(
                            id=m.id,
                            channel="imessage",
                            sender="me" if m.from_me else m.sender,
                            text=m.text or "",
                            timestamp=ts,
                            from_me=m.from_me,
                            media_type=m.media_type,
                            has_media=m.has_media,
                        ))
                    if msgs:
                        presence.append(ChannelPresence(
                            channel="imessage",
                            identifier=phone,
                            last_message_at=last_ts,
                            available=True,
                        ))
        except Exception as e:
            logger.warning("iMessage adapter failed for %s: %s", imsg_identifier, e, exc_info=True)

    # Supplement with interaction summaries from people.db (covers channels where live adapters lack FDA)
    if True:
        try:
            people_db_path = FilePath.home() / ".aos" / "data" / "people.db"
            if people_db_path.exists():
                import sqlite3 as _sql
                pconn = _sql.connect(str(people_db_path))
                pconn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
                # Find person_id from any channel identifier
                pid = None
                for ch_val in channel_ids.values():
                    digits = "".join(c for c in ch_val if c.isdigit())
                    row = pconn.execute(
                        "SELECT person_id FROM person_identifiers WHERE normalized LIKE ? LIMIT 1",
                        (f"%{digits}%",),
                    ).fetchone()
                    if row:
                        pid = row["person_id"]
                        break
                if pid:
                    # Skip channels that already have live messages
                    live_channels = {m.channel for m in all_messages}
                    cutoff = int((datetime.now() - timedelta(days=days)).timestamp())
                    interactions = pconn.execute(
                        """SELECT id, channel, direction, msg_count, occurred_at, summary
                           FROM interactions
                           WHERE person_id = ? AND occurred_at >= ?
                           ORDER BY occurred_at DESC LIMIT ?""",
                        (pid, cutoff, limit),
                    ).fetchall()
                    for ix in interactions:
                        if ix["channel"] in live_channels:
                            continue
                        ts_dt = datetime.fromtimestamp(ix["occurred_at"]) if ix["occurred_at"] else None
                        ts_str = ts_dt.isoformat() if ts_dt else None
                        all_messages.append(ChannelMessageSchema(
                            id=ix["id"],
                            channel=ix["channel"] or "unknown",
                            sender="me" if ix["direction"] == "outbound" else "them",
                            text=ix.get("summary") or f"{ix['msg_count']} messages ({ix['direction']})",
                            timestamp=ts_str,
                            from_me=ix["direction"] == "outbound",
                            media_type="summary",
                            has_media=False,
                        ))
                        ch = ix["channel"] or "unknown"
                        if not any(p.channel == ch for p in presence):
                            presence.append(ChannelPresence(
                                channel=ch, identifier=ch_val, last_message_at=ts_str, available=False,
                            ))
                pconn.close()
        except Exception:
            logger.warning("Fallback interaction lookup failed", exc_info=True)

    # Sort all messages by timestamp descending
    all_messages.sort(key=lambda m: m.timestamp or "", reverse=True)

    return all_messages[:limit], presence


@router.get("/{person_id}/messages", response_model=PersonMessagesResponse)
async def get_person_messages(
    request: Request,
    person_id: str = Path(..., description="Person identifier"),
    limit: int = Query(30, ge=1, le=100, description="Max messages to return"),
    days: int = Query(90, ge=1, le=365, description="How far back to look (days)"),
    channel: str | None = Query(None, description="Filter to specific channel"),
) -> PersonMessagesResponse | JSONResponse:
    """Get real messages from channel adapters for a person.

    Queries WhatsApp (local + bridge), iMessage, and Telegram adapters
    using the person's channel identifiers from people.db.
    """
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    person = ontology.get(ObjectType.PERSON, person_id)
    if not person:
        return JSONResponse({"error": f"Person not found: {person_id}"}, status_code=404)

    channel_ids = _get_person_channel_ids(person)
    if not channel_ids:
        return PersonMessagesResponse(person_id=person_id)

    # Filter to specific channel if requested
    if channel:
        channel_ids = {k: v for k, v in channel_ids.items() if k == channel}

    messages, presence = _fetch_messages_from_adapters(channel_ids, limit=limit, days=days)

    return PersonMessagesResponse(
        person_id=person_id,
        messages=messages,
        presence=presence,
        total=len(messages),
        has_more=len(messages) >= limit,
    )


# ---------------------------------------------------------------------------
# Send Message — route through channel adapters
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    """Request to send a message to a person via a specific channel."""

    channel: str = Field(..., description="Channel to send via: whatsapp, imessage, telegram, email")
    text: str = Field(..., description="Message text", min_length=1)


class SendMessageResponse(BaseModel):
    """Response after attempting to send a message."""

    success: bool = Field(False, description="Whether the message was sent")
    channel: str = Field(..., description="Channel used")
    recipient: str = Field("", description="Recipient identifier used")
    message: str = Field("", description="Status message")


@router.post("/{person_id}/send", response_model=SendMessageResponse)
async def send_message_to_person(
    body: SendMessageRequest,
    request: Request,
    person_id: str = Path(..., description="Person identifier"),
) -> SendMessageResponse | JSONResponse:
    """Send a message to a person via a specific channel.

    Resolves the person's identifier for the chosen channel
    and routes through the appropriate adapter.
    """
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return JSONResponse({"error": "System starting up"}, status_code=503)

    person = ontology.get(ObjectType.PERSON, person_id)
    if not person:
        return JSONResponse({"error": f"Person not found: {person_id}"}, status_code=404)

    channel_ids = _get_person_channel_ids(person)
    channel = body.channel.lower()

    # Resolve the recipient identifier for the chosen channel
    recipient = None
    if channel == "whatsapp":
        recipient = channel_ids.get("whatsapp")
        if not recipient and channel_ids.get("phone"):
            # Build JID from phone
            digits = "".join(c for c in channel_ids["phone"] if c.isdigit())
            recipient = f"{digits}@s.whatsapp.net"
    elif channel == "imessage":
        recipient = channel_ids.get("phone") or channel_ids.get("email")
        if recipient and "@" not in recipient:
            # Normalize phone for iMessage
            recipient = "+" + "".join(c for c in recipient if c.isdigit())
    elif channel == "telegram":
        recipient = channel_ids.get("telegram")
    elif channel == "email":
        recipient = channel_ids.get("email")

    if not recipient:
        return SendMessageResponse(
            success=False,
            channel=channel,
            message=f"No {channel} identifier found for {person.name}",
        )

    # Route through the appropriate adapter
    try:
        sent = False
        if channel == "whatsapp":
            from core.engine.comms.channels.whatsapp import WhatsAppAdapter
            adapter = WhatsAppAdapter()
            sent = adapter.send_message(recipient, body.text)
        elif channel == "imessage":
            from core.engine.comms.channels.imessage import iMessageAdapter
            adapter = iMessageAdapter()
            sent = adapter.send_message(recipient, body.text)
        elif channel == "telegram":
            from core.engine.comms.channels.telegram import TelegramAdapter
            adapter = TelegramAdapter()
            sent = adapter.send_message(recipient, body.text)
        else:
            return SendMessageResponse(
                success=False, channel=channel, recipient=recipient,
                message=f"Channel {channel} not supported for sending yet",
            )

        return SendMessageResponse(
            success=sent,
            channel=channel,
            recipient=recipient,
            message="Message sent" if sent else "Send failed",
        )
    except Exception as e:
        logger.exception("Failed to send message via %s to %s", channel, person_id)
        return SendMessageResponse(
            success=False, channel=channel, recipient=recipient or "",
            message=f"Error: {e}",
        )


# ─────────────────────────────────────────────────────────────────────
# People Intelligence endpoints
#
# Wrap the People Intelligence SignalExtractor subsystem
# (core/engine/people/intel/) behind HTTP. Every handler is wrapped in
# try/except so an intel subsystem bug (import error, missing DB,
# adapter crash) NEVER bricks the rest of /api/people.
#
# These endpoints are read-mostly:
#   GET  /api/people/intel/coverage   — static adapter registration info
#   POST /api/people/intel/extract    — trigger extraction
#   GET  /api/people/intel/stats      — signal store row counts
#   GET  /api/people/{id}/intel       — stored signals for one person
# ─────────────────────────────────────────────────────────────────────


class IntelExtractRequest(BaseModel):
    """POST /api/people/intel/extract body."""

    person_ids: list[str] | None = Field(
        default=None,
        description="Optional list of person IDs to extract. None = all persons.",
    )
    limit: int | None = Field(
        default=None,
        description="Optional cap on persons indexed (after filtering).",
        ge=1,
    )
    adapter_names: list[str] | None = Field(
        default=None,
        description="Optional list of adapter names to run. None = all available.",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, extract but do not persist to signal_store.",
    )


def _intel_extractor():
    """Lazy import so a missing intel subsystem never breaks the rest of /api/people."""
    from core.engine.people.intel.extractor import SignalExtractor
    return SignalExtractor()


@router.get("/intel/coverage")
async def get_intel_coverage() -> JSONResponse:
    """Static adapter registration + availability + signal type coverage."""
    try:
        ex = _intel_extractor()
        return JSONResponse(content=ex.coverage_report())
    except Exception as e:
        logger.exception("intel coverage failed")
        return JSONResponse(
            status_code=500,
            content={"error": "intel subsystem unavailable", "detail": str(e)},
        )


@router.get("/intel/stats")
async def get_intel_stats() -> JSONResponse:
    """Signal store row counts (total, distinct persons, by source)."""
    try:
        ex = _intel_extractor()
        return JSONResponse(content=ex.stats())
    except Exception as e:
        logger.exception("intel stats failed")
        return JSONResponse(
            status_code=500,
            content={"error": "intel subsystem unavailable", "detail": str(e)},
        )


@router.post("/intel/extract")
async def run_intel_extract(body: IntelExtractRequest) -> JSONResponse:
    """Run the full extraction pipeline and return a summary report.

    This is a synchronous endpoint — large runs may take several seconds.
    For the first pass we don't offload to a thread. If latency becomes
    a problem, wrap ``ex.run(...)`` in ``await asyncio.to_thread(...)``.
    """
    try:
        ex = _intel_extractor()
        report = ex.run(
            person_ids=body.person_ids,
            limit=body.limit,
            adapter_names=body.adapter_names,
            dry_run=body.dry_run,
        )
        return JSONResponse(content=report.to_dict())
    except Exception as e:
        logger.exception("intel extract failed")
        return JSONResponse(
            status_code=500,
            content={"error": "extraction failed", "detail": str(e)},
        )


@router.get("/{person_id}/intel")
async def get_person_intel(person_id: str = Path(...)) -> JSONResponse:
    """Return stored signals for one person.

    Returns 404 if no signals have been extracted for this person yet.
    Signals are serialized via dataclasses.asdict for a JSON-friendly
    shape — caller should treat the shape as advisory, not a contract.
    """
    try:
        ex = _intel_extractor()
        signals = ex.get_person_signals(person_id)
        if signals is None:
            return JSONResponse(
                status_code=404,
                content={"error": "no signals stored for this person"},
            )
        from dataclasses import asdict

        payload = asdict(signals)
        # Also include the computed aggregates that are @property on the
        # dataclass (asdict doesn't pick them up).
        payload["_aggregates"] = {
            "total_messages": signals.total_messages,
            "total_calls": signals.total_calls,
            "total_photos": signals.total_photos,
            "total_emails": signals.total_emails,
            "channels_active": signals.channels_active,
            "channel_count": signals.channel_count,
            "is_multi_channel": signals.is_multi_channel,
        }
        return JSONResponse(content=payload)
    except Exception as e:
        logger.exception("intel person signals failed for %s", person_id)
        return JSONResponse(
            status_code=500,
            content={"error": "intel lookup failed", "detail": str(e)},
        )


# ─────────────────────────────────────────────────────────────────────
# Phase 4 endpoints — profile / classify / tiers / correct
#
# Wraps ProfileBuilder + ClassifierRunner behind HTTP. Same try/except
# isolation pattern as the Phase 3 intel endpoints — a runtime error
# in the Phase 4 subsystem must NEVER brick the rest of /api/people.
# ─────────────────────────────────────────────────────────────────────


class IntelClassifyRequest(BaseModel):
    """POST /api/people/intel/classify body."""

    person_ids: list[str] | None = Field(
        default=None,
        description="Optional person IDs to classify. None = all with stored signals.",
    )
    limit: int | None = Field(
        default=None,
        description="Optional cap on persons classified.",
        ge=1,
    )
    with_llm: bool = Field(
        default=False,
        description="Enable LLM context tagging. Default: rule-only.",
    )
    max_budget_usd: float = Field(
        default=1.00,
        description="USD budget cap for LLM runs. Ignored for rule-only.",
        ge=0.0,
    )
    dry_run: bool = Field(
        default=False,
        description="Compile profiles + prompts without LLM calls or persistence.",
    )
    model: str | None = Field(
        default=None,
        description="Override LLM model. Default: operator preferred execution model.",
    )


class IntelCorrectionRequest(BaseModel):
    """POST /api/people/{id}/classification/correct body."""

    tier: str | None = Field(
        default=None,
        description="New tier value (core, active, fading, dormant, ...).",
    )
    context_tags: list[dict] | None = Field(
        default=None,
        description="New context tags as list of {tag, confidence}.",
    )
    notes: str = Field(default="", description="Operator free-text notes.")


def _intel_classifier_runner():
    """Lazy import so a missing intel subsystem never breaks /api/people."""
    from core.engine.people.intel.runner import ClassifierRunner
    return ClassifierRunner()


def _intel_profile_builder():
    from core.engine.people.intel.profiler import ProfileBuilder
    return ProfileBuilder()


@router.get("/intel/tiers")
async def get_intel_tier_distribution() -> JSONResponse:
    """Aggregate classification tier distribution (counts only, no names)."""
    try:
        runner = _intel_classifier_runner()
        return JSONResponse(content={"tiers": runner.tier_distribution()})
    except Exception as e:
        logger.exception("intel tiers failed")
        return JSONResponse(
            status_code=500,
            content={"error": "intel subsystem unavailable", "detail": str(e)},
        )


@router.post("/intel/classify")
async def run_intel_classify(body: IntelClassifyRequest) -> JSONResponse:
    """Run the classification pipeline and return a summary report.

    Synchronous; large runs may take several seconds. Wrap in
    asyncio.to_thread if latency becomes an issue.
    """
    try:
        runner = _intel_classifier_runner()
        report = await runner.run(
            person_ids=body.person_ids,
            limit=body.limit,
            with_llm=body.with_llm,
            max_budget_usd=body.max_budget_usd,
            dry_run=body.dry_run,
            llm_model=body.model,
        )
        return JSONResponse(content=report.to_dict())
    except Exception as e:
        logger.exception("intel classify failed")
        return JSONResponse(
            status_code=500,
            content={"error": "classification failed", "detail": str(e)},
        )


@router.get("/{person_id}/profile")
async def get_person_profile(person_id: str = Path(...)) -> JSONResponse:
    """Return the compiled PersonProfile for one person (explicit opt-in)."""
    try:
        builder = _intel_profile_builder()
        profile = builder.build(person_id)
        if profile is None:
            return JSONResponse(
                status_code=404,
                content={"error": "no profile — no stored signals for this person"},
            )
        from dataclasses import asdict

        return JSONResponse(content=asdict(profile))
    except Exception as e:
        logger.exception("intel profile failed for %s", person_id)
        return JSONResponse(
            status_code=500,
            content={"error": "profile lookup failed", "detail": str(e)},
        )


@router.get("/{person_id}/classification")
async def get_person_classification(person_id: str = Path(...)) -> JSONResponse:
    """Return the current classification for one person."""
    try:
        runner = _intel_classifier_runner()
        result = runner.get_classification(person_id)
        if result is None:
            return JSONResponse(
                status_code=404,
                content={"error": "no classification stored for this person"},
            )
        return JSONResponse(content=result.to_dict())
    except Exception as e:
        logger.exception("intel classification lookup failed for %s", person_id)
        return JSONResponse(
            status_code=500,
            content={"error": "classification lookup failed", "detail": str(e)},
        )


@router.post("/{person_id}/classification/correct")
async def correct_person_classification(
    person_id: str,
    body: IntelCorrectionRequest,
) -> JSONResponse:
    """Record an operator correction to a person's classification."""
    try:
        from core.engine.people.intel.taxonomy import Tier

        new_tier: Tier | None = None
        if body.tier:
            new_tier = Tier.from_str(body.tier)
            if new_tier == Tier.UNKNOWN and body.tier.strip().lower() != "unknown":
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "unknown tier",
                        "detail": f"valid tiers: {[t.value for t in Tier]}",
                    },
                )

        if new_tier is None and not body.context_tags:
            return JSONResponse(
                status_code=400,
                content={"error": "at least one of tier or context_tags must be set"},
            )

        runner = _intel_classifier_runner()
        result = runner.record_correction(
            person_id,
            new_tier=new_tier,
            new_tags=body.context_tags,
            notes=body.notes or "",
        )
        return JSONResponse(content=result.to_dict())
    except Exception as e:
        logger.exception("intel correction failed for %s", person_id)
        return JSONResponse(
            status_code=500,
            content={"error": "correction failed", "detail": str(e)},
        )
