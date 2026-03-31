"""Qareen API — People CRM routes.

List, search, inspect, and update people. Includes intelligence surfaces.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

from ..ontology.types import ObjectType
from .schemas import (
    PersonDetailResponse,
    PersonListResponse,
    PersonResponse,
    PersonSurfaceItem,
    PersonSurfaceResponse,
    UpdatePersonRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/people", tags=["people"])


def _person_to_response(person) -> PersonResponse:
    """Convert a Person ontology object to a PersonResponse schema."""
    return PersonResponse(
        id=person.id,
        name=person.name,
        importance=person.importance,
        privacy_level=person.privacy_level,
        tags=person.tags or [],
        organization=person.organization,
        role=person.role,
        city=person.city,
        last_contact=person.last_contact,
        days_since_contact=person.days_since_contact,
        relationship_trend=person.relationship_trend,
        projects=person.projects or [],
    )


def _person_to_detail(person) -> PersonDetailResponse:
    """Convert a Person ontology object to a PersonDetailResponse schema."""
    return PersonDetailResponse(
        id=person.id,
        name=person.name,
        importance=person.importance,
        privacy_level=person.privacy_level,
        tags=person.tags or [],
        organization=person.organization,
        role=person.role,
        city=person.city,
        last_contact=person.last_contact,
        days_since_contact=person.days_since_contact,
        relationship_trend=person.relationship_trend,
        projects=person.projects or [],
        email=person.email,
        phone=person.phone,
        how_met=person.how_met,
        birthday=person.birthday,
        comms_trust_level=person.comms_trust_level,
        interactions=[],
        relationships=[],
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

    adapter = ontology._adapters.get(ObjectType.PERSON)
    if not adapter:
        return PersonListResponse()

    # If a search query is provided, use the search method
    if q:
        results = adapter.search(q, limit=per_page)
        people = [_person_to_response(r.person if hasattr(r, "person") else r) for r in results]
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
    people_objs = adapter.list(filters=filters, limit=per_page, offset=offset)
    total = adapter.count(filters=filters)

    people = [_person_to_response(p) for p in people_objs]
    has_more = (offset + per_page) < total

    return PersonListResponse(
        people=people,
        total=total,
        page=page,
        per_page=per_page,
        has_more=has_more,
    )


@router.get("/surfaces", response_model=PersonSurfaceResponse)
async def get_surfaces(request: Request) -> PersonSurfaceResponse:
    """Get the intelligence queue — people needing attention."""
    ontology = getattr(request.app.state, "ontology", None)
    if not ontology:
        return PersonSurfaceResponse()

    adapter = ontology._adapters.get(ObjectType.PERSON)
    if not adapter:
        return PersonSurfaceResponse()

    # Find people with high importance who haven't been contacted recently
    surfaces: list[PersonSurfaceItem] = []

    # Get important people (importance 1-2) with drifting contact
    try:
        important = adapter.list(
            filters={"importance": {"max": 2}},
            limit=20,
        )
        for person in important:
            if person.days_since_contact and person.days_since_contact > 14:
                surfaces.append(PersonSurfaceItem(
                    person=_person_to_response(person),
                    reason=f"No contact in {person.days_since_contact} days",
                    urgency=min(5, 1 + person.days_since_contact // 14),
                    suggested_action="Reach out to maintain the relationship",
                ))
            elif person.relationship_trend == "drifting":
                surfaces.append(PersonSurfaceItem(
                    person=_person_to_response(person),
                    reason="Relationship trend is drifting",
                    urgency=3,
                    suggested_action="Schedule a catch-up",
                ))
    except Exception:
        logger.exception("Failed to compute people surfaces")

    surfaces.sort(key=lambda s: s.urgency)
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

    return _person_to_detail(person)


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

    adapter = ontology._adapters.get(ObjectType.PERSON)
    if not adapter:
        return JSONResponse({"error": "People adapter not available"}, status_code=503)

    fields = body.model_dump(exclude_none=True)
    updated = adapter.update(person_id, fields)
    if not updated:
        return JSONResponse({"error": f"Person not found: {person_id}"}, status_code=404)

    return _person_to_detail(updated)
