"""Qareen Intelligence — Context Assembly.

When the operator speaks or types, the context assembler queries the
ontology for related entities and pushes context cards to the companion
stream. This is what makes the left column come alive.

Also provides a briefing builder for the initial page load.
"""

from __future__ import annotations

import json as _json
import logging
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# People DB — instance data, read-only from here
# ---------------------------------------------------------------------------
_PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"


def _query_person_context(person_id: str) -> dict[str, Any]:
    """Query people.db directly for rich person context.

    Returns a dict with channels, last_contact info, relationship trend,
    recent interaction summary, and open items count. Designed to be fast
    (<50ms) and graceful on missing data.
    """
    result: dict[str, Any] = {}

    if not _PEOPLE_DB.exists():
        return result

    try:
        conn = sqlite3.connect(str(_PEOPLE_DB), timeout=2)
        conn.row_factory = sqlite3.Row
    except Exception:
        return result

    try:
        # --- Channels (from person_identifiers) ---
        rows = conn.execute(
            """SELECT type, value, label, source
               FROM person_identifiers
               WHERE person_id = ?
               ORDER BY is_primary DESC, rowid ASC""",
            (person_id,),
        ).fetchall()

        channels: list[str] = []
        seen: set[str] = set()
        for row in rows:
            # Map identifier types to human-readable channel names
            itype = row["type"]
            if itype == "wa_jid":
                ch = "whatsapp"
            elif itype == "phone":
                ch = "phone"
            elif itype == "email":
                ch = "email"
            elif itype == "telegram":
                ch = "telegram"
            elif itype == "imessage":
                ch = "imessage"
            else:
                ch = itype
            if ch not in seen:
                seen.add(ch)
                channels.append(ch)
        if channels:
            result["channels"] = channels

        # --- Relationship state (trajectory, last contact) ---
        rstate = conn.execute(
            """SELECT last_interaction_at, last_interaction_channel,
                      days_since_contact, trajectory,
                      interaction_count_30d, msg_count_30d
               FROM relationship_state
               WHERE person_id = ?""",
            (person_id,),
        ).fetchone()

        if rstate:
            # Last contact — human-readable
            last_ts = rstate["last_interaction_at"]
            if last_ts:
                try:
                    last_dt = datetime.fromtimestamp(int(last_ts))
                    days = rstate["days_since_contact"]
                    channel = rstate["last_interaction_channel"] or ""
                    if days is not None and days == 0:
                        result["last_contact"] = f"Today via {channel}" if channel else "Today"
                    elif days is not None and days == 1:
                        result["last_contact"] = f"Yesterday via {channel}" if channel else "Yesterday"
                    elif days is not None and days < 7:
                        result["last_contact"] = f"{days}d ago via {channel}" if channel else f"{days}d ago"
                    elif days is not None and days < 30:
                        weeks = days // 7
                        result["last_contact"] = f"{weeks}w ago via {channel}" if channel else f"{weeks}w ago"
                    else:
                        result["last_contact"] = last_dt.strftime("%b %d") + (f" via {channel}" if channel else "")
                except (ValueError, TypeError, OSError):
                    pass

            # Trend
            trajectory = rstate["trajectory"]
            if trajectory:
                result["trend"] = trajectory

        # --- Recent interaction summary ---
        recent = conn.execute(
            """SELECT occurred_at, channel, direction, msg_count, subject, summary
               FROM interactions
               WHERE person_id = ?
               ORDER BY occurred_at DESC
               LIMIT 1""",
            (person_id,),
        ).fetchone()

        if recent:
            # Build a compact recent message string
            summary = recent["summary"] or recent["subject"]
            if summary:
                result["recent_message"] = summary[:80]
            else:
                # No summary — describe the interaction
                direction = recent["direction"] or "unknown"
                channel = recent["channel"] or "unknown"
                msg_count = recent["msg_count"] or 0
                if msg_count > 0:
                    result["recent_message"] = (
                        f"{msg_count} {direction} msg{'s' if msg_count != 1 else ''} on {channel}"
                    )

        # --- Contact metadata for richer subtitle ---
        meta = conn.execute(
            """SELECT organization, job_title, city, preferred_channel
               FROM contact_metadata
               WHERE person_id = ?""",
            (person_id,),
        ).fetchone()

        if meta:
            parts: list[str] = []
            if meta["job_title"]:
                parts.append(meta["job_title"])
            if meta["organization"]:
                parts.append(meta["organization"])
            if parts:
                result["subtitle"] = " @ ".join(parts)
            elif meta["city"]:
                result["subtitle"] = meta["city"]

            # Preferred channel — used by the reply card to pre-select
            if meta["preferred_channel"]:
                result["preferred_channel"] = meta["preferred_channel"]

    except Exception as e:
        logger.debug("Person DB query failed for %s: %s", person_id, e)
    finally:
        conn.close()

    return result


def _count_person_tasks(ontology, person_name: str) -> int:
    """Count active/todo tasks that mention a person by name.

    Scans task titles for the person's name. Fast enough for small
    task lists (<200). Returns 0 on any error.
    """
    try:
        from ..ontology.types import ObjectType

        tasks = ontology.list(
            ObjectType.TASK,
            filters={"_type": "task"},
            limit=200,
        )
        name_lower = person_name.lower()
        return sum(
            1
            for t in tasks
            if getattr(t, "status", "") in ("active", "todo")
            and name_lower in getattr(t, "title", "").lower()
        )
    except Exception:
        return 0


async def assemble_context(
    ontology,
    intent_result,
    push_event,
) -> None:
    """Query the ontology for context related to the classified intent.

    Extracts entities (project, person, topic) from the intent result,
    looks them up in the ontology, and pushes context cards via push_event.

    Args:
        ontology: The Qareen Ontology instance.
        intent_result: The IntentResult from the classifier.
        push_event: async callable(event_type, data) to push SSE events.
    """
    if not ontology:
        return

    from ..ontology.types import ObjectType

    entities = intent_result.entities if intent_result.entities else []

    # Track what we've already surfaced to avoid duplicates
    surfaced: set[str] = set()

    # --- Surface project context ---
    for entity in entities:
        if entity.entity_type == "project" and entity.value:
            project_name = entity.value.lower()
            key = f"project:{project_name}"
            if key in surfaced:
                continue
            surfaced.add(key)

            try:
                projects = ontology.list(
                    ObjectType.PROJECT,
                    filters={"_type": "project"},
                    limit=50,
                )
                # Find by name match
                project = None
                for p in projects:
                    if hasattr(p, "title") and p.title and p.title.lower() == project_name:
                        project = p
                        break
                    if hasattr(p, "name") and p.name and p.name.lower() == project_name:
                        project = p
                        break
                    if hasattr(p, "id") and p.id and p.id.lower() == project_name:
                        project = p
                        break

                if project:
                    # Get task counts for this project
                    tasks = ontology.list(
                        ObjectType.TASK,
                        filters={"_type": "task", "project": project_name},
                        limit=200,
                    )
                    total = len(tasks)
                    active = sum(1 for t in tasks if getattr(t, "status", "") == "active")
                    done = sum(1 for t in tasks if getattr(t, "status", "") == "done")
                    progress = round((done / total * 100) if total > 0 else 0)

                    await push_event("context", {
                        "id": f"project-{project_name}",
                        "context_type": "project",
                        "title": getattr(project, "title", project_name).title(),
                        "subtitle": getattr(project, "description", "") or "",
                        "progress": progress,
                        "active_tasks": active,
                        "total_tasks": total,
                        "done_tasks": done,
                        "timestamp": datetime.now().isoformat(),
                    })
            except Exception as e:
                logger.debug("Project context failed for %s: %s", project_name, e)

    # --- Surface topic context (vault search via QMD) ---
    for entity in entities:
        if entity.entity_type == "topic" and entity.value:
            topic = entity.value
            key = f"topic:{topic[:30]}"
            if key in surfaced:
                continue
            surfaced.add(key)

            # Search vault via QMD for related notes
            vault_results = []
            try:
                result = subprocess.run(
                    ["qmd", "query", topic, "-n", "3", "--json"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    data = _json.loads(result.stdout)
                    if isinstance(data, list):
                        vault_results = data[:3]
                    elif isinstance(data, dict) and "results" in data:
                        vault_results = data["results"][:3]
            except Exception as e:
                logger.debug("QMD search failed for topic '%s': %s", topic, e)

            # Build context card with vault results
            related_notes = []
            for r in vault_results:
                title = r.get("title", r.get("path", ""))
                snippet = r.get("snippet", r.get("content", ""))[:150]
                score = r.get("score", 0)
                if score > 0.3:  # Only include relevant results
                    related_notes.append({
                        "title": title,
                        "snippet": snippet,
                        "path": r.get("path", ""),
                        "score": round(score, 2),
                    })

            await push_event("context", {
                "id": f"topic-{hash(topic) % 100000}",
                "context_type": "topic",
                "title": topic[:50],
                "related_notes": related_notes,
                "has_vault_context": len(related_notes) > 0,
                "timestamp": datetime.now().isoformat(),
            })

    # --- Surface person context ---
    for entity in entities:
        if entity.entity_type == "person" and entity.value:
            person_name = entity.value
            key = f"person:{person_name.lower()}"
            if key in surfaced:
                continue
            surfaced.add(key)

            try:
                # Use ontology search for fuzzy name/alias matching
                person = None
                results = ontology.search(person_name, types=[ObjectType.PERSON], limit=1)
                if results:
                    person = results[0].obj

                if not person:
                    continue

                # Build rich context card from Person object + direct DB query
                person_id = person.id
                db_context = _query_person_context(person_id)

                # Count tasks mentioning this person
                open_items = _count_person_tasks(ontology, person.name)

                # Assemble the card payload — fields match frontend PersonDetails
                card: dict[str, Any] = {
                    "id": f"person-{person_id}",
                    "context_type": "person",
                    "title": person.name or person_name,
                    "subtitle": db_context.get("subtitle")
                    or getattr(person, "organization", "")
                    or "",
                    "timestamp": datetime.now().isoformat(),
                }

                # last_contact — prefer the human-readable version from DB
                card["last_contact"] = db_context.get("last_contact") or (
                    str(person.last_contact.strftime("%b %d"))
                    if getattr(person, "last_contact", None)
                    else None
                )

                # channels — list of platforms this person is reachable on
                if db_context.get("channels"):
                    card["channels"] = db_context["channels"]

                # open_items — tasks mentioning this person
                card["open_items"] = open_items

                # trend — relationship trajectory (growing/stable/drifting)
                card["trend"] = db_context.get("trend") or getattr(
                    person, "relationship_trend", None
                )

                # recent_message — last interaction summary
                if db_context.get("recent_message"):
                    card["recent_message"] = db_context["recent_message"]

                # preferred_channel — for pre-selecting channel on reply cards
                if db_context.get("preferred_channel"):
                    card["preferred_channel"] = db_context["preferred_channel"]

                # Extra fields for deeper context (frontend can use these)
                if getattr(person, "city", None):
                    card["city"] = person.city
                if getattr(person, "importance", 3) <= 2:
                    card["importance"] = person.importance
                if getattr(person, "days_since_contact", None) is not None:
                    card["days_since_contact"] = person.days_since_contact

                await push_event("context", card)

            except Exception as e:
                logger.debug("Person context failed for %s: %s", person_name, e)

    # --- Always surface active tasks summary if nothing else was surfaced ---
    if not surfaced:
        try:
            active_tasks = ontology.list(
                ObjectType.TASK,
                filters={"_type": "task", "status": "active"},
                limit=5,
            )
            if active_tasks:
                task_titles = [getattr(t, "title", "untitled") for t in active_tasks[:5]]
                await push_event("context", {
                    "id": "active-tasks",
                    "context_type": "schedule",
                    "title": "Active Tasks",
                    "subtitle": f"{len(active_tasks)} in progress",
                    "items": task_titles,
                    "timestamp": datetime.now().isoformat(),
                })
        except Exception as e:
            logger.debug("Active tasks context failed: %s", e)


async def build_briefing(ontology) -> dict[str, Any] | None:
    """Build an initial briefing for the companion page.

    Returns a briefing dict with summary, active tasks, schedule hints,
    and attention items. Returns None if ontology is unavailable.
    """
    if not ontology:
        return None

    import uuid

    from ..ontology.types import ObjectType

    try:
        # Active tasks
        active_tasks = ontology.list(
            ObjectType.TASK,
            filters={"_type": "task", "status": "active"},
            limit=10,
        )

        # Todo tasks (high priority)
        todo_tasks = ontology.list(
            ObjectType.TASK,
            filters={"_type": "task", "status": "todo"},
            limit=10,
        )
        urgent = [t for t in todo_tasks if getattr(t, "priority", 3) <= 2]

        # Projects
        projects = ontology.list(
            ObjectType.PROJECT,
            filters={"_type": "project"},
            limit=10,
        )

        # Operator name
        operator = ontology.operator()
        name = operator.name if operator else "Operator"

        # Build summary
        active_count = len(active_tasks)
        todo_count = len(todo_tasks)
        urgent_count = len(urgent)

        # Time-based greeting
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"

        summary = f"{greeting}, {name}."
        if active_count > 0:
            summary += f" You have {active_count} active task{'s' if active_count != 1 else ''}."
        if urgent_count > 0:
            summary += f" {urgent_count} urgent item{'s' if urgent_count != 1 else ''} need attention."
        if active_count == 0 and todo_count > 0:
            summary += f" {todo_count} task{'s' if todo_count != 1 else ''} in your queue."

        # Overdue tasks (have due_at in the past)
        overdue_tasks = []
        now_str = datetime.now().isoformat()
        for t in todo_tasks + active_tasks:
            due = getattr(t, "due_at", None)
            if due and str(due) < now_str and getattr(t, "status", "") != "done":
                overdue_tasks.append(t)

        if overdue_tasks:
            summary += f" {len(overdue_tasks)} overdue."

        # Attention items — priority: overdue > urgent > active
        attention = []
        for t in overdue_tasks[:3]:
            due = getattr(t, "due_at", "")
            attention.append({
                "type": "overdue",
                "text": f"Overdue: {getattr(t, 'title', 'untitled')}",
                "detail": f"Due: {str(due)[:10]}" if due else "",
            })
        for t in urgent:
            pri = getattr(t, "priority", 3)
            pri_val = pri.value if hasattr(pri, "value") else pri
            attention.append({
                "type": "urgent",
                "text": f"P{pri_val}: {getattr(t, 'title', 'untitled')}",
            })
        for t in active_tasks[:3]:
            attention.append({
                "type": "active",
                "text": f"Active: {getattr(t, 'title', 'untitled')}",
            })

        # Relationship drift alerts — people drifting or dormant
        drift_alerts = []
        try:
            people_db = Path.home() / ".aos" / "data" / "people.db"
            if people_db.exists():
                conn = sqlite3.connect(str(people_db))
                conn.row_factory = sqlite3.Row
                # Find important people (importance <= 2) who haven't been contacted in 14+ days
                rows = conn.execute("""
                    SELECT name, importance, days_since_contact, trajectory
                    FROM people
                    WHERE importance <= 2
                      AND days_since_contact > 14
                      AND trajectory IN ('drifting', 'dormant')
                    ORDER BY importance, days_since_contact DESC
                    LIMIT 3
                """).fetchall()
                conn.close()
                for row in rows:
                    drift_alerts.append({
                        "type": "drift",
                        "text": f"{row['name']} — {row['trajectory']} ({row['days_since_contact']}d)",
                    })
                    attention.append({
                        "type": "drift",
                        "text": f"Drifting: {row['name']} ({row['days_since_contact']}d no contact)",
                    })
        except Exception as e:
            logger.debug("Drift alert query failed: %s", e)

        # Metrics
        metrics = {
            "active_tasks": active_count,
            "todo_tasks": todo_count,
            "urgent_tasks": urgent_count,
            "overdue_tasks": len(overdue_tasks),
            "projects": len(projects),
            "drift_alerts": len(drift_alerts),
        }

        knowledge_section = _build_knowledge_briefing_section()

        return {
            "id": str(uuid.uuid4())[:8],
            "summary": summary,
            "greeting": f"{greeting}, {name}.",
            "schedule": [],  # Calendar integration future
            "attention": attention[:8],
            "drift_alerts": drift_alerts,
            "metrics": metrics,
            "knowledge": knowledge_section,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.warning("Briefing build failed: %s", e)
        return None


def _build_knowledge_briefing_section() -> dict[str, Any]:
    """Pull intelligence signals for the morning briefing.

    Reads (no LLM, no side effects):
        - Top 5 unread high-relevance briefs from last 24h
        - Count of pending compilation proposals (shadow-mode review queue)
        - Latest vault maintenance report summary if present

    Returns a minimal dict the companion UI can drop straight into its
    briefing card.
    """
    import sqlite3
    from datetime import timedelta, timezone as _tz

    out: dict[str, Any] = {
        "top_captures": [],
        "pending_review": 0,
        "latest_maintenance": None,
    }

    db_path = Path.home() / ".aos" / "data" / "qareen.db"
    if not db_path.exists():
        return out

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yesterday = (datetime.now(_tz.utc) - timedelta(days=1)).isoformat()

        # Top 5 unread high-relevance briefs from last 24h
        try:
            rows = conn.execute(
                """
                SELECT id, title, platform, relevance_score, url
                FROM intelligence_briefs
                WHERE status = 'unread'
                  AND created_at >= ?
                  AND relevance_score >= 0.3
                ORDER BY relevance_score DESC, published_at DESC
                LIMIT 5
                """,
                (yesterday,),
            ).fetchall()
            out["top_captures"] = [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "platform": r["platform"],
                    "relevance": r["relevance_score"],
                    "url": r["url"],
                }
                for r in rows
            ]
        except sqlite3.OperationalError:
            pass

        # Pending review count
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM compilation_proposals WHERE status = 'pending'"
            ).fetchone()
            out["pending_review"] = row["cnt"] if row else 0
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()

    # Latest maintenance report summary (read from vault/log/)
    try:
        from engine.intelligence.lint.report import list_reports
    except ImportError:
        try:
            from core.engine.intelligence.lint.report import list_reports
        except Exception:
            list_reports = None
    if list_reports:
        try:
            reports = list_reports(limit=1)
            if reports:
                out["latest_maintenance"] = reports[0]
        except Exception:
            pass

    return out
