"""People Adapter — maps people.db to the Person ontology type.

Reads from people.db (read-only except for operator-initiated updates).
Uses qareen.db for cross-store links and context cards.

Tables consumed from people.db:
  people, person_identifiers, contact_metadata, relationship_state,
  interactions, relationships, group_members, aliases

Tables consumed from qareen.db:
  links, context_cards
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from ..model import SearchResult
from ..types import (
    ContextCard,
    Link,
    LinkType,
    ObjectType,
    Person,
)
from .base import Adapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _epoch_to_dt(epoch: int | None) -> datetime | None:
    """Convert a Unix epoch (seconds) to datetime, or None."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(int(epoch))
    except (ValueError, TypeError, OSError):
        return None


def _row_dict(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    """Row factory that returns dicts."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


# ---------------------------------------------------------------------------
# PeopleAdapter
# ---------------------------------------------------------------------------

class PeopleAdapter(Adapter):
    """Concrete adapter for Person objects backed by people.db."""

    def __init__(self, people_db_path: str, qareen_db_path: str) -> None:
        self._people_db_path = people_db_path
        self._qareen_db_path = qareen_db_path
        self._people_conn = self._open(people_db_path)
        self._qareen_conn = self._open(qareen_db_path)

    # -- connection helpers --------------------------------------------------

    @staticmethod
    def _open(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = _row_dict
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def close(self) -> None:
        self._people_conn.close()
        self._qareen_conn.close()

    # -- Adapter property ----------------------------------------------------

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.PERSON

    # -- Core CRUD -----------------------------------------------------------

    def get(self, object_id: str) -> Person | None:
        """Build a full Person from joined tables. Respects privacy."""
        row = self._people_conn.execute(
            "SELECT * FROM people WHERE id = ?", (object_id,)
        ).fetchone()
        if row is None:
            return None
        return self._build_person(row)

    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Person]:
        query, params = self._build_list_query(filters, count_only=False)
        query += " ORDER BY p.canonical_name ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._people_conn.execute(query, params).fetchall()
        return [self._build_person(r) for r in rows]

    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        query, params = self._build_list_query(filters, count_only=True)
        row = self._people_conn.execute(query, params).fetchone()
        return row["cnt"] if row else 0

    def create(self, obj: Any) -> Any:
        """Not supported — people.db has its own write pipeline via comms."""
        raise NotImplementedError(
            "PeopleAdapter is read-only for creation. "
            "Use the comms system to add people."
        )

    def update(self, object_id: str, fields: dict[str, Any]) -> Person | None:
        """Operator-initiated edits to people.db."""
        person = self.get(object_id)
        if person is None:
            return None

        # Map ontology field names to people.db columns
        people_fields = {}
        metadata_fields = {}

        people_col_map = {
            "name": "canonical_name",
            "importance": "importance",
            "privacy_level": "privacy_level",
        }
        metadata_col_map = {
            "organization": "organization",
            "role": "job_title",
            "city": "city",
            "how_met": "how_met",
            "birthday": "birthday",
        }

        for key, val in fields.items():
            if key in people_col_map:
                people_fields[people_col_map[key]] = val
            elif key in metadata_col_map:
                metadata_fields[metadata_col_map[key]] = val

        now_epoch = int(datetime.now().timestamp())

        if people_fields:
            people_fields["updated_at"] = now_epoch
            set_clause = ", ".join(f"{k} = ?" for k in people_fields)
            vals = list(people_fields.values()) + [object_id]
            self._people_conn.execute(
                f"UPDATE people SET {set_clause} WHERE id = ?", vals
            )
            self._people_conn.commit()

        if metadata_fields:
            metadata_fields["last_manual_update"] = now_epoch
            # Upsert into contact_metadata
            existing = self._people_conn.execute(
                "SELECT person_id FROM contact_metadata WHERE person_id = ?",
                (object_id,),
            ).fetchone()
            if existing:
                set_clause = ", ".join(f"{k} = ?" for k in metadata_fields)
                vals = list(metadata_fields.values()) + [object_id]
                self._people_conn.execute(
                    f"UPDATE contact_metadata SET {set_clause} WHERE person_id = ?",
                    vals,
                )
            else:
                metadata_fields["person_id"] = object_id
                cols = ", ".join(metadata_fields.keys())
                placeholders = ", ".join("?" for _ in metadata_fields)
                self._people_conn.execute(
                    f"INSERT INTO contact_metadata ({cols}) VALUES ({placeholders})",
                    list(metadata_fields.values()),
                )
            self._people_conn.commit()

        return self.get(object_id)

    def delete(self, object_id: str) -> bool:
        """Not supported — destructive ops require explicit approval."""
        raise NotImplementedError(
            "PeopleAdapter does not support deletion. "
            "Archive people via the comms system instead."
        )

    # -- Channel resolution --------------------------------------------------

    def resolve_channel(self, person_name: str) -> dict | None:
        """Look up a person by name and return their best channel for messaging.

        Searches people.db for the person, then queries person_identifiers
        for available channels. Returns the best channel by priority:
        whatsapp > email.

        Returns:
            Dict with person_id, person_name, channel, channel_id.
            None if person not found or no messaging channel available.
        """
        results = self.search(person_name, limit=5)
        if not results:
            return None

        person = results[0].obj

        # Query all identifiers for this person
        idents = self._people_conn.execute(
            """SELECT type, value, is_primary
               FROM person_identifiers
               WHERE person_id = ?
               ORDER BY is_primary DESC, rowid ASC""",
            (person.id,),
        ).fetchall()

        # Priority: wa_jid > email (telegram not yet in identifier store)
        channel_priority = [
            ("wa_jid", "whatsapp"),
            ("email", "email"),
        ]

        for ident_type, channel_name in channel_priority:
            for ident in idents:
                if ident["type"] == ident_type:
                    return {
                        "person_id": person.id,
                        "person_name": person.name,
                        "channel": channel_name,
                        "channel_id": ident["value"],
                    }

        # Fall back to phone number (can be used for WhatsApp)
        for ident in idents:
            if ident["type"] == "phone":
                # Normalize phone to WhatsApp JID format
                normalized = ident.get("normalized") or ident["value"]
                # Strip leading + and non-digit chars
                digits = "".join(c for c in normalized if c.isdigit())
                if digits:
                    return {
                        "person_id": person.id,
                        "person_name": person.name,
                        "channel": "whatsapp",
                        "channel_id": f"{digits}@s.whatsapp.net",
                    }

        return None

    # -- Search --------------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        """Search people by name, organization, job title, and aliases."""
        q = f"%{query}%"
        sql = """
            SELECT DISTINCT p.*
            FROM people p
            LEFT JOIN contact_metadata cm ON cm.person_id = p.id
            LEFT JOIN aliases a ON a.person_id = p.id
            WHERE p.canonical_name LIKE ?
               OR p.display_name LIKE ?
               OR p.first_name LIKE ?
               OR p.last_name LIKE ?
               OR p.nickname LIKE ?
               OR cm.organization LIKE ?
               OR cm.job_title LIKE ?
               OR a.alias LIKE ?
            ORDER BY p.importance ASC, p.canonical_name ASC
            LIMIT ?
        """
        rows = self._people_conn.execute(
            sql, (q, q, q, q, q, q, q, q, limit)
        ).fetchall()

        results: list[SearchResult] = []
        query_lower = query.lower()
        for row in rows:
            person = self._build_person(row)
            # Simple scoring: exact name match > starts with > contains
            name_lower = (person.name or "").lower()
            if name_lower == query_lower:
                score = 1.0
            elif name_lower.startswith(query_lower):
                score = 0.8
            elif query_lower in name_lower:
                score = 0.6
            else:
                # Matched on org/role/alias
                score = 0.4
            results.append(SearchResult(
                object_type=ObjectType.PERSON,
                object_id=person.id,
                title=person.name,
                snippet=person.name,
                score=score,
                obj=person,
            ))
        return results

    # -- Interactions & Relationships ----------------------------------------

    def get_interactions(
        self,
        person_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Get recent interactions for a person from the interactions table."""
        rows = self._people_conn.execute(
            """SELECT id, person_id, occurred_at, channel, direction,
                      msg_count, subject, summary
               FROM interactions
               WHERE person_id = ?
               ORDER BY occurred_at DESC
               LIMIT ?""",
            (person_id, limit),
        ).fetchall()

        result = []
        for r in rows:
            ts = _epoch_to_dt(r.get("occurred_at"))
            result.append({
                "id": r["id"],
                "channel": r.get("channel", "unknown"),
                "direction": r.get("direction", "inbound"),
                "summary": r.get("summary") or r.get("subject"),
                "timestamp": ts.isoformat() if ts else None,
                "message_count": r.get("msg_count", 1) or 1,
            })
        return result

    def get_relationships(
        self,
        person_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Get relationships for a person."""
        rows = self._people_conn.execute(
            """SELECT person_a_id, person_b_id, type, subtype, strength,
                      context, notes
               FROM relationships
               WHERE person_a_id = ? OR person_b_id = ?
               LIMIT ?""",
            (person_id, person_id, limit),
        ).fetchall()

        result = []
        for r in rows:
            # Determine which side is the "other" person
            other_id = r["person_b_id"] if r["person_a_id"] == person_id else r["person_a_id"]
            # Look up name
            other = self._people_conn.execute(
                "SELECT canonical_name FROM people WHERE id = ?", (other_id,)
            ).fetchone()
            other_name = other["canonical_name"] if other else None

            result.append({
                "link_type": r.get("subtype") or r.get("type", "knows"),
                "target_type": "person",
                "target_id": other_id,
                "target_name": other_name,
                "strength": r.get("strength"),
                "context": r.get("context"),
            })
        return result

    # -- Links ---------------------------------------------------------------

    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Get IDs of linked objects from qareen.db links + people.db relationships."""
        ids: list[str] = []

        # 1. Explicit links in qareen.db
        sql = """
            SELECT to_id FROM links
            WHERE from_type = 'person' AND from_id = ?
              AND to_type = ?
        """
        params: list[Any] = [obj_id, target_type.value]
        if link_type is not None:
            sql += " AND link_type = ?"
            params.append(link_type.value)
        sql += " LIMIT ?"
        params.append(limit)

        for row in self._qareen_conn.execute(sql, params).fetchall():
            ids.append(row["to_id"])

        # Also check reverse direction
        sql_rev = """
            SELECT from_id FROM links
            WHERE to_type = 'person' AND to_id = ?
              AND from_type = ?
        """
        params_rev: list[Any] = [obj_id, target_type.value]
        if link_type is not None:
            sql_rev += " AND link_type = ?"
            params_rev.append(link_type.value)
        sql_rev += " LIMIT ?"
        params_rev.append(limit)

        for row in self._qareen_conn.execute(sql_rev, params_rev).fetchall():
            if row["from_id"] not in ids:
                ids.append(row["from_id"])

        # 2. Implicit links from people.db's own tables
        if target_type == ObjectType.PERSON:
            # Relationships table
            sql_rel = """
                SELECT person_b_id AS linked_id FROM relationships WHERE person_a_id = ?
                UNION
                SELECT person_a_id AS linked_id FROM relationships WHERE person_b_id = ?
            """
            rel_params: list[Any] = [obj_id, obj_id]
            if link_type is not None:
                # Map link types to relationship types where possible
                rel_type_map = {
                    LinkType.KNOWS: None,  # all relationships imply 'knows'
                    LinkType.MEMBER_OF: "community",
                }
                mapped = rel_type_map.get(link_type)
                if mapped:
                    sql_rel = """
                        SELECT person_b_id AS linked_id FROM relationships
                        WHERE person_a_id = ? AND type = ?
                        UNION
                        SELECT person_a_id AS linked_id FROM relationships
                        WHERE person_b_id = ? AND type = ?
                    """
                    rel_params = [obj_id, mapped, obj_id, mapped]

            for row in self._people_conn.execute(
                sql_rel + " LIMIT ?", rel_params + [limit]
            ).fetchall():
                lid = row["linked_id"]
                if lid not in ids:
                    ids.append(lid)

        return ids[:limit]

    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        """Insert a link into qareen.db's links table."""
        import uuid

        now = datetime.now().isoformat()
        link_id = f"lnk_{uuid.uuid4().hex[:12]}"
        props = json.dumps(metadata) if metadata else None

        self._qareen_conn.execute(
            """INSERT INTO links
               (id, link_type, from_type, from_id, to_type, to_id,
                direction, properties, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, 'directed', ?, ?, 'people_adapter')""",
            (
                link_id,
                link_type.value,
                ObjectType.PERSON.value,
                source_id,
                target_type.value,
                target_id,
                props,
                now,
            ),
        )
        self._qareen_conn.commit()

        return Link(
            link_type=link_type,
            source_type=ObjectType.PERSON,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.fromisoformat(now),
        )

    # -- Context cards -------------------------------------------------------

    def get_context_card(self, object_id: str) -> ContextCard | None:
        row = self._qareen_conn.execute(
            """SELECT * FROM context_cards
               WHERE entity_type = 'person' AND entity_id = ?""",
            (object_id,),
        ).fetchone()
        if row is None:
            return None
        return ContextCard(
            entity_type=ObjectType.PERSON,
            entity_id=object_id,
            summary=row["summary"],
            key_facts=json.loads(row["key_facts"]) if row.get("key_facts") else [],
            recent_activity=json.loads(row["recent_activity"])
            if row.get("recent_activity")
            else [],
            open_items=json.loads(row["open_items"]) if row.get("open_items") else [],
            built_at=datetime.fromisoformat(row["built_at"])
            if row.get("built_at")
            else datetime.now(),
            stale_after=datetime.fromisoformat(row["stale_after"])
            if row.get("stale_after")
            else None,
        )

    def set_context_card(self, object_id: str, card: ContextCard) -> None:
        now = card.built_at.isoformat() if card.built_at else datetime.now().isoformat()
        stale = card.stale_after.isoformat() if card.stale_after else None

        self._qareen_conn.execute(
            """INSERT INTO context_cards
               (entity_type, entity_id, summary, key_facts, recent_activity,
                open_items, built_at, stale_after)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                 summary = excluded.summary,
                 key_facts = excluded.key_facts,
                 recent_activity = excluded.recent_activity,
                 open_items = excluded.open_items,
                 built_at = excluded.built_at,
                 stale_after = excluded.stale_after""",
            (
                ObjectType.PERSON.value,
                object_id,
                card.summary,
                json.dumps(card.key_facts),
                json.dumps(card.recent_activity),
                json.dumps(card.open_items),
                now,
                stale,
            ),
        )
        self._qareen_conn.commit()

    # -----------------------------------------------------------------------
    # Internal: build Person from a people row
    # -----------------------------------------------------------------------

    def _build_person(self, row: dict) -> Person:
        """Assemble a full Person from a people table row + joined tables."""
        pid = row["id"]
        privacy = row.get("privacy_level", 0) or 0

        person = Person(
            id=pid,
            name=row.get("canonical_name") or row.get("display_name") or "",
            importance=row.get("importance", 3) or 3,
            privacy_level=privacy,
        )

        # Privacy gate: level >= 3 means name only, no details
        if privacy >= 3:
            return person

        # -- Aliases (from people table: nickname, display_name if different) --
        name_aliases: list[str] = []
        nickname = row.get("nickname")
        display = row.get("display_name")
        canonical = row.get("canonical_name") or ""
        if nickname and nickname.lower() != canonical.lower():
            name_aliases.append(nickname)
        if display and display.lower() != canonical.lower() and display not in name_aliases:
            name_aliases.append(display)
        person.aliases = name_aliases

        # -- Identifiers (pick primary, else first of each type) -------------
        idents = self._people_conn.execute(
            """SELECT type, value, is_primary FROM person_identifiers
               WHERE person_id = ?
               ORDER BY is_primary DESC, rowid ASC""",
            (pid,),
        ).fetchall()

        channels: dict[str, str] = {}
        wa_jids: list[str] = []
        for ident in idents:
            itype = ident["type"]
            val = ident["value"]
            if itype == "email" and "email" not in channels:
                person.email = val
                channels["email"] = val
            elif itype == "phone" and "phone" not in channels:
                person.phone = val
                channels["phone"] = val
            elif itype == "wa_jid":
                wa_jids.append(val)
            elif itype == "telegram_id" and "telegram" not in channels:
                person.telegram_id = val
                channels["telegram"] = val
        # Prefer @s.whatsapp.net (messaging) over @status/@lid
        if wa_jids:
            best = next((j for j in wa_jids if "@s.whatsapp.net" in j), wa_jids[0])
            person.whatsapp_jid = best
            channels["whatsapp"] = best
        person.channels = channels

        # -- Contact metadata ------------------------------------------------
        meta = self._people_conn.execute(
            "SELECT * FROM contact_metadata WHERE person_id = ?", (pid,)
        ).fetchone()
        if meta:
            person.organization = meta.get("organization") or None
            person.role = meta.get("job_title") or None
            person.city = meta.get("city") or None
            person.how_met = meta.get("how_met") or None
            person.birthday = meta.get("birthday") or None
            person.notes = meta.get("notes") or None

        # -- Relationship state ----------------------------------------------
        rstate = self._people_conn.execute(
            "SELECT * FROM relationship_state WHERE person_id = ?", (pid,)
        ).fetchone()
        if rstate:
            person.last_contact = _epoch_to_dt(rstate.get("last_interaction_at"))
            person.days_since_contact = rstate.get("days_since_contact")
            person.relationship_trend = rstate.get("trajectory") or None

        # -- Tags from aliases -----------------------------------------------
        aliases = self._people_conn.execute(
            "SELECT alias, type FROM aliases WHERE person_id = ?", (pid,)
        ).fetchall()
        if aliases:
            person.tags = [a["alias"] for a in aliases]

        # -- Projects from qareen links --------------------------------------
        project_links = self._qareen_conn.execute(
            """SELECT to_id FROM links
               WHERE from_type = 'person' AND from_id = ?
                 AND to_type = 'project'""",
            (pid,),
        ).fetchall()
        if project_links:
            person.projects = [r["to_id"] for r in project_links]

        return person

    # -----------------------------------------------------------------------
    # Internal: build list/count query with filters
    # -----------------------------------------------------------------------

    def _build_list_query(
        self,
        filters: dict[str, Any] | None,
        count_only: bool,
    ) -> tuple[str, list[Any]]:
        """Build a SELECT query with WHERE clauses from filters.

        Returns (sql_string, params_list).
        """
        if count_only:
            select = "SELECT COUNT(DISTINCT p.id) AS cnt"
        else:
            select = "SELECT DISTINCT p.*"

        frm = "FROM people p"
        joins: list[str] = []
        wheres: list[str] = ["p.is_archived = 0"]
        params: list[Any] = []

        if filters:
            # name — fuzzy LIKE
            if "name" in filters:
                wheres.append("p.canonical_name LIKE ?")
                params.append(f"%{filters['name']}%")

            # importance — exact or range
            if "importance" in filters:
                val = filters["importance"]
                if isinstance(val, dict):
                    if "min" in val:
                        wheres.append("p.importance >= ?")
                        params.append(val["min"])
                    if "max" in val:
                        wheres.append("p.importance <= ?")
                        params.append(val["max"])
                else:
                    wheres.append("p.importance = ?")
                    params.append(val)

            # privacy_level
            if "privacy_level" in filters:
                wheres.append("p.privacy_level = ?")
                params.append(filters["privacy_level"])

            # organization
            if "organization" in filters:
                joins.append(
                    "LEFT JOIN contact_metadata cm ON cm.person_id = p.id"
                )
                wheres.append("cm.organization LIKE ?")
                params.append(f"%{filters['organization']}%")

            # tags (search aliases)
            if "tags" in filters:
                joins.append("LEFT JOIN aliases al ON al.person_id = p.id")
                tag_val = filters["tags"]
                if isinstance(tag_val, list):
                    placeholders = ", ".join("?" for _ in tag_val)
                    wheres.append(f"al.alias IN ({placeholders})")
                    params.extend(tag_val)
                else:
                    wheres.append("al.alias = ?")
                    params.append(tag_val)

            # project — via qareen links
            if "project" in filters:
                # Use a subquery against qareen.db; but since we can't
                # cross-database join easily, fetch person IDs first.
                project_id = filters["project"]
                linked = self._qareen_conn.execute(
                    """SELECT from_id FROM links
                       WHERE from_type = 'person' AND to_type = 'project'
                         AND to_id = ?""",
                    (project_id,),
                ).fetchall()
                if linked:
                    pids = [r["from_id"] for r in linked]
                    placeholders = ", ".join("?" for _ in pids)
                    wheres.append(f"p.id IN ({placeholders})")
                    params.extend(pids)
                else:
                    # No people linked to this project
                    wheres.append("1 = 0")

        join_clause = " ".join(joins)
        where_clause = " AND ".join(wheres)
        sql = f"{select} {frm} {join_clause} WHERE {where_clause}"
        return sql, params
