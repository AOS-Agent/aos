"""Work Adapter — Tasks, Projects, Goals, Inbox, Threads.

Maps between the ontology's typed objects (Task, Project, Goal) and
the qareen.db SQLite storage. Handles all CRUD, link management,
and full-text search via the tasks_fts FTS5 table.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, date
from typing import Any

from ..types import (
    Area,
    Goal,
    KeyResult,
    Link,
    LinkType,
    ObjectType,
    PipelineEntry,
    Procedure,
    Project,
    Reminder,
    Session,
    SessionStatus,
    Task,
    TaskHandoff,
    TaskPriority,
    TaskStatus,
    Transaction,
    Workflow,
    WorkflowRun,
)
from .base import Adapter


def _parse_dt(val: str | None) -> datetime | None:
    """Parse an ISO8601 string into a datetime, or return None."""
    if not val:
        return None
    # Handle date-only strings
    if len(val) == 10:
        try:
            return datetime.fromisoformat(val + "T00:00:00")
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        return None


def _to_iso(dt: datetime | None) -> str | None:
    """Convert a datetime to ISO8601 string, or return None."""
    if dt is None:
        return None
    return dt.isoformat()


def _json_loads(val: str | None, default=None):
    """Safely parse a JSON string."""
    if not val:
        return default if default is not None else []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _json_dumps(val: list | dict | None) -> str | None:
    """Serialize to JSON string or return None."""
    if val is None:
        return None
    if isinstance(val, list) and len(val) == 0:
        return None
    return json.dumps(val)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _to_priority(val: int | None) -> TaskPriority:
    """Convert an integer to TaskPriority enum."""
    if val is None:
        return TaskPriority.NORMAL
    try:
        return TaskPriority(val)
    except ValueError:
        return TaskPriority.NORMAL


def _to_status(val: str | None) -> TaskStatus:
    """Convert a string to TaskStatus enum."""
    if not val:
        return TaskStatus.TODO
    try:
        return TaskStatus(val)
    except ValueError:
        return TaskStatus.TODO


class WorkAdapter(Adapter):
    """Handles Task, Project, Goal, Inbox, Thread objects in qareen.db.

    One adapter for multiple types. The ``_type`` filter key or explicit
    type parameters control which table is queried.  Default operations
    target the tasks table.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row

    def close(self):
        self._conn.close()

    # ── ID Generation (private) ──────────────────────────

    def _project_prefix(self, project_id: str | None) -> str:
        """Derive short prefix from project ID.

        aos -> aos, aos-v2 -> aos, None -> t (unaffiliated).
        """
        if not project_id:
            return "t"
        clean = re.sub(r'[-_]v\d+$', '', project_id)
        return clean

    def _next_scoped_id(self, prefix: str) -> str:
        """Generate next project-scoped ID: aos#1, aos#2, chief#1, t#1, etc."""
        pattern = f"{prefix}#%"
        rows = self._conn.execute(
            "SELECT id FROM tasks WHERE id LIKE ?", (pattern,)
        ).fetchall()

        max_num = 0
        pat = f"{prefix}#"
        for row in rows:
            task_id = row["id"]
            if task_id.startswith(pat):
                rest = task_id[len(pat):]
                base_num = rest.split(".")[0]
                try:
                    num = int(base_num)
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"{prefix}#{max_num + 1}"

    def _next_subtask_id(self, parent_id: str) -> str:
        """Generate next subtask ID: aos#3.1, aos#3.2, etc."""
        pattern = f"{parent_id}.%"
        rows = self._conn.execute(
            "SELECT id FROM tasks WHERE id LIKE ?", (pattern,)
        ).fetchall()

        max_num = 0
        pat = f"{parent_id}."
        for row in rows:
            task_id = row["id"]
            if task_id.startswith(pat):
                try:
                    num = int(task_id[len(pat):])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"{parent_id}.{max_num + 1}"

    def _next_id(self, prefix: str) -> str:
        """ID generation for non-task entities (p1, g1, th1, i1)."""
        table_map = {
            "p": "projects",
            "g": "goals",
            "th": "threads",
            "i": "inbox",
        }
        table = table_map.get(prefix, "tasks")
        rows = self._conn.execute(f"SELECT id FROM {table}").fetchall()

        max_num = 0
        for row in rows:
            item_id = row["id"]
            if item_id.startswith(prefix):
                try:
                    num = int(item_id[len(prefix):])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"{prefix}{max_num + 1}"

    # ── Adapter interface ────────────────────────────────

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.TASK

    def get(self, object_id: str) -> Task | Project | Goal | dict | None:
        """Get a single object by id. Auto-detects type from tables."""
        # Try tasks first (most common)
        task = self._get_task(object_id)
        if task is not None:
            return task

        # Try projects
        project = self._get_project(object_id)
        if project is not None:
            return project

        # Try goals
        goal = self._get_goal(object_id)
        if goal is not None:
            return goal

        # Try threads
        thread = self._get_thread(object_id)
        if thread is not None:
            return thread

        # Try new entity tables
        for table, converter in (
            ("sessions", self._row_to_session),
            ("areas", self._row_to_area),
            ("workflows", self._row_to_workflow),
            ("workflow_runs", self._row_to_workflow_run),
            ("pipeline_entries", self._row_to_pipeline_entry),
            ("reminders", self._row_to_reminder),
            ("transactions", self._row_to_transaction),
            ("procedures", self._row_to_procedure),
        ):
            row = self._conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (object_id,)
            ).fetchone()
            if row is not None:
                return converter(row)

        return None

    def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """List objects with optional filters.

        Special filter keys:
          _type: 'task' | 'project' | 'goal' | 'inbox' | 'thread'
          status: filter by status
          priority: filter by priority (int)
          project_id: filter by project
          assigned_to: filter by assignee
          tags: filter by tag (JSON contains check)
        """
        filters = filters or {}
        obj_type = filters.pop("_type", "task")

        if obj_type == "project":
            return self._list_projects(filters, limit, offset)
        elif obj_type == "goal":
            return self._list_goals(filters, limit, offset)
        elif obj_type == "inbox":
            return self._list_inbox(filters, limit, offset)
        elif obj_type == "thread":
            return self._list_threads(filters, limit, offset)
        elif obj_type == "session":
            return self._list_sessions(filters, limit, offset)
        elif obj_type == "area":
            return self._list_areas(filters, limit, offset)
        elif obj_type == "workflow":
            return self._list_workflows(filters, limit, offset)
        elif obj_type == "workflow_run":
            return self._list_workflow_runs(filters, limit, offset)
        elif obj_type == "pipeline_entry":
            return self._list_pipeline_entries(filters, limit, offset)
        elif obj_type == "reminder":
            return self._list_reminders(filters, limit, offset)
        elif obj_type == "transaction":
            return self._list_transactions(filters, limit, offset)
        elif obj_type == "procedure":
            return self._list_procedures(filters, limit, offset)
        else:
            return self._list_tasks(filters, limit, offset)

    def count(self, *, filters: dict[str, Any] | None = None) -> int:
        """Count objects matching filters."""
        filters = filters or {}
        obj_type = filters.pop("_type", "task")

        if obj_type == "project":
            return self._count_table("projects", filters)
        elif obj_type == "goal":
            return self._count_table("goals", filters)
        elif obj_type == "inbox":
            return self._count_table("inbox", filters)
        elif obj_type == "thread":
            return self._count_table("threads", filters)
        elif obj_type == "session":
            return self._count_table("sessions", filters)
        elif obj_type == "area":
            return self._count_table("areas", filters)
        elif obj_type == "workflow":
            return self._count_table("workflows", filters)
        elif obj_type == "workflow_run":
            return self._count_table("workflow_runs", filters)
        elif obj_type == "pipeline_entry":
            return self._count_table("pipeline_entries", filters)
        elif obj_type == "reminder":
            return self._count_table("reminders", filters)
        elif obj_type == "transaction":
            return self._count_table("transactions", filters)
        elif obj_type == "procedure":
            return self._count_table("procedures", filters)
        else:
            return self._count_table("tasks", filters)

    def create(self, obj: Any) -> Any:
        """Create a new object in storage. Returns the created object.

        Supports Task, Project, Goal, and dict objects for inbox/thread:
          - dict with "text" key -> inbox item
          - dict with "title" key and "_type"=="thread" -> thread
        """
        if isinstance(obj, Task):
            # Auto-generate ID if empty
            if not obj.id:
                if obj.parent_id:
                    # Inherit project from parent if not set
                    parent_row = self._conn.execute(
                        "SELECT project_id FROM tasks WHERE id = ?",
                        (obj.parent_id,),
                    ).fetchone()
                    if parent_row and not obj.project:
                        obj.project = parent_row["project_id"]
                    obj.id = self._next_subtask_id(obj.parent_id)
                else:
                    prefix = self._project_prefix(obj.project)
                    obj.id = self._next_scoped_id(prefix)

            # Encode energy/context/source_ref as <!-- meta: ... --> in description
            meta_parts = []
            if hasattr(obj, "energy") and obj.energy:
                meta_parts.append(f"energy:{obj.energy}")
            if hasattr(obj, "context") and obj.context:
                meta_parts.append(f"context:{obj.context}")
            if hasattr(obj, "source_ref") and obj.source_ref:
                meta_parts.append(f"source_ref:{obj.source_ref}")
            if meta_parts:
                meta_comment = "<!-- meta: " + ", ".join(meta_parts) + " -->"
                if obj.description:
                    obj.description = obj.description + "\n\n" + meta_comment
                else:
                    obj.description = meta_comment

            return self._create_task(obj)
        elif isinstance(obj, Project):
            if not obj.id:
                obj.id = self._next_id("p")
            return self._create_project(obj)
        elif isinstance(obj, Goal):
            if not obj.id:
                obj.id = self._next_id("g")
            return self._create_goal(obj)
        elif isinstance(obj, Session):
            return self._create_session(obj)
        elif isinstance(obj, Area):
            return self._create_area(obj)
        elif isinstance(obj, Workflow):
            return self._create_workflow(obj)
        elif isinstance(obj, WorkflowRun):
            return self._create_workflow_run(obj)
        elif isinstance(obj, PipelineEntry):
            return self._create_pipeline_entry(obj)
        elif isinstance(obj, Reminder):
            return self._create_reminder(obj)
        elif isinstance(obj, Transaction):
            return self._create_transaction(obj)
        elif isinstance(obj, Procedure):
            return self._create_procedure(obj)
        elif isinstance(obj, dict):
            obj_type = obj.get("_type", "")
            if obj_type == "thread" or ("title" in obj and obj_type == "thread"):
                return self._create_thread(
                    title=obj["title"],
                    session_id=obj.get("session_id"),
                )
            elif "text" in obj:
                return self._create_inbox(
                    text=obj["text"],
                    source=obj.get("source", "manual"),
                    confidence=obj.get("confidence"),
                )
            else:
                raise TypeError(f"Dict must have 'text' (inbox) or '_type'='thread' with 'title'")
        else:
            raise TypeError(f"Unsupported type: {type(obj)}")

    def update(self, object_id: str, fields: dict[str, Any]) -> Any | None:
        """Update fields on an existing object. Returns updated object."""
        # Determine which table this ID belongs to
        row = self._conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (object_id,)
        ).fetchone()
        if row:
            return self._update_task(object_id, fields)

        row = self._conn.execute(
            "SELECT id FROM projects WHERE id = ?", (object_id,)
        ).fetchone()
        if row:
            return self._update_project(object_id, fields)

        row = self._conn.execute(
            "SELECT id FROM goals WHERE id = ?", (object_id,)
        ).fetchone()
        if row:
            return self._update_goal(object_id, fields)

        return None

    def delete(self, object_id: str) -> bool:
        """Delete an object. Returns True if deleted."""
        # Try each table
        for table in ("tasks", "projects", "goals", "inbox", "threads"):
            cur = self._conn.execute(
                f"DELETE FROM {table} WHERE id = ?", (object_id,)
            )
            if cur.rowcount > 0:
                # Clean up related data
                if table == "tasks":
                    self._conn.execute(
                        "DELETE FROM task_handoffs WHERE task_id = ?",
                        (object_id,),
                    )
                    # Remove from FTS
                    self._conn.execute(
                        "DELETE FROM tasks_fts WHERE rowid IN "
                        "(SELECT rowid FROM tasks WHERE id = ?)",
                        (object_id,),
                    )
                self._conn.commit()
                return True
        return False

    # ── Relationship methods ────────────────────────────

    def get_links(
        self,
        obj_id: str,
        target_type: ObjectType,
        link_type: LinkType | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Get ids of linked objects."""
        if link_type:
            rows = self._conn.execute(
                "SELECT to_id FROM links "
                "WHERE from_id = ? AND to_type = ? AND link_type = ? "
                "LIMIT ?",
                (obj_id, target_type.value, link_type.value, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT to_id FROM links "
                "WHERE from_id = ? AND to_type = ? "
                "LIMIT ?",
                (obj_id, target_type.value, limit),
            ).fetchall()
        return [r["to_id"] for r in rows]

    def create_link(
        self,
        source_id: str,
        target_type: ObjectType,
        target_id: str,
        link_type: LinkType,
        metadata: dict[str, Any] | None = None,
    ) -> Link:
        """Create a link between this object and another."""
        link_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        source_type = self._detect_type(source_id)

        self._conn.execute(
            "INSERT OR REPLACE INTO links "
            "(id, link_type, from_type, from_id, to_type, to_id, "
            " direction, properties, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 'directed', ?, ?, 'work_adapter')",
            (
                link_id,
                link_type.value,
                source_type.value,
                source_id,
                target_type.value,
                target_id,
                _json_dumps(metadata),
                now,
            ),
        )
        self._conn.commit()

        return Link(
            link_type=link_type,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.fromisoformat(now),
        )

    # ── Search ──────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> list[Task]:
        """Full-text search across tasks using FTS5."""
        # First try FTS search
        try:
            rows = self._conn.execute(
                "SELECT t.* FROM tasks t "
                "JOIN tasks_fts fts ON t.rowid = fts.rowid "
                "WHERE tasks_fts MATCH ? "
                "LIMIT ?",
                (query, limit),
            ).fetchall()
            if rows:
                return [self._row_to_task(r) for r in rows]
        except sqlite3.OperationalError:
            pass

        # Fallback: LIKE search on title and description
        rows = self._conn.execute(
            "SELECT * FROM tasks "
            "WHERE title LIKE ? OR description LIKE ? "
            "LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    # ── Internal: Task operations ────────────────────────

    def _get_task(self, task_id: str) -> Task | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert a tasks table row to a Task dataclass."""
        task_id = row["id"]
        tags = _json_loads(row["tags"])

        # Load handoff if exists
        handoff = None
        ho_row = self._conn.execute(
            "SELECT * FROM task_handoffs WHERE task_id = ?", (task_id,)
        ).fetchone()
        if ho_row:
            handoff = TaskHandoff(
                state=ho_row["state"],
                next_step=ho_row["next_step"],
                files=_json_loads(ho_row["files"]),
                decisions=_json_loads(ho_row["decisions"]),
                blockers=_json_loads(ho_row["blockers"]),
                session_id=ho_row["session_id"],
                timestamp=_parse_dt(ho_row["timestamp"]),
            )

        # Load session links
        sessions = []
        try:
            sess_rows = self._conn.execute(
                "SELECT st.session_id, s.started_at, s.outcome "
                "FROM session_tasks st "
                "LEFT JOIN sessions s ON st.session_id = s.id "
                "WHERE st.task_id = ?",
                (task_id,),
            ).fetchall()
            for sr in sess_rows:
                entry = {"id": sr["session_id"]}
                if sr["started_at"]:
                    entry["date"] = sr["started_at"][:10]
                if sr["outcome"]:
                    entry["outcome"] = sr["outcome"]
                sessions.append(entry)
        except sqlite3.OperationalError:
            pass  # session_tasks table may not exist yet

        # Load subtask IDs
        subtask_rows = self._conn.execute(
            "SELECT id FROM tasks WHERE parent_id = ?", (task_id,)
        ).fetchall()
        subtask_ids = [r["id"] for r in subtask_rows]

        task = Task(
            id=task_id,
            title=row["title"],
            status=_to_status(row["status"]),
            priority=_to_priority(row["priority"]),
            project=row["project_id"],
            tags=tags,
            description=row["description"],
            assigned_to=row["assigned_to"],
            created_by=row["created_by"],
            created=_parse_dt(row["created_at"]),
            started=_parse_dt(row["started_at"]),
            completed=_parse_dt(row["completed_at"]),
            due=_parse_dt(row["due_at"]),
            parent_id=row["parent_id"],
            subtask_ids=subtask_ids,
            handoff=handoff,
            pipeline=row["pipeline"],
            pipeline_stage=None,  # TODO: convert pipeline_stage string
            recurrence=row["recurrence"],
        )
        # Attach session links (not part of Task dataclass, added dynamically)
        if sessions:
            task.sessions = sessions  # type: ignore[attr-defined]
        return task

    def _list_tasks(
        self, filters: dict, limit: int, offset: int
    ) -> list[Task]:
        clauses = []
        params: list[Any] = []

        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        if "priority" in filters:
            clauses.append("priority = ?")
            params.append(int(filters["priority"]))
        if "project_id" in filters:
            clauses.append("project_id = ?")
            params.append(filters["project_id"])
        if "assigned_to" in filters:
            clauses.append("assigned_to = ?")
            params.append(filters["assigned_to"])
        if "tags" in filters:
            # JSON contains check
            clauses.append("tags LIKE ?")
            params.append(f'%"{filters["tags"]}"%')
        if "parent_id" in filters:
            clauses.append("parent_id = ?")
            params.append(filters["parent_id"])

        where = " AND ".join(clauses) if clauses else "1=1"
        query = (
            f"SELECT * FROM tasks WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_task(r) for r in rows]

    def _count_table(self, table: str, filters: dict) -> int:
        clauses = []
        params: list[Any] = []

        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        if "priority" in filters:
            clauses.append("priority = ?")
            params.append(int(filters["priority"]))
        if table == "tasks":
            if "project_id" in filters:
                clauses.append("project_id = ?")
                params.append(filters["project_id"])
            if "assigned_to" in filters:
                clauses.append("assigned_to = ?")
                params.append(filters["assigned_to"])

        where = " AND ".join(clauses) if clauses else "1=1"
        row = self._conn.execute(
            f"SELECT count(*) as cnt FROM {table} WHERE {where}", params
        ).fetchone()
        return row["cnt"] if row else 0

    def _create_task(self, task: Task) -> Task:
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks "
            "(id, title, status, priority, project_id, description, "
            " assigned_to, created_by, created_at, started_at, "
            " completed_at, due_at, parent_id, pipeline, pipeline_stage, "
            " recurrence, tags, version, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                task.id,
                task.title,
                task.status.value,
                task.priority.value,
                task.project,
                task.description,
                task.assigned_to,
                task.created_by,
                _to_iso(task.created) or now,
                _to_iso(task.started),
                _to_iso(task.completed),
                _to_iso(task.due),
                task.parent_id,
                task.pipeline,
                task.pipeline_stage.value if task.pipeline_stage else None,
                task.recurrence,
                _json_dumps(task.tags),
                now,
            ),
        )
        # FTS sync
        self._sync_fts(task.id, task.title, task.description)

        # Handoff
        if task.handoff:
            self._upsert_handoff(task.id, task.handoff)

        self._conn.commit()
        return task

    def _update_task(self, task_id: str, fields: dict) -> Task | None:
        # Map YAML-style and direct field names to DB column names
        field_map = {
            "title": "title",
            "status": "status",
            "priority": "priority",
            "description": "description",
            "notes": "description",
            "tags": "tags",
            "assigned_to": "assigned_to",
            "started": "started_at",
            "started_at": "started_at",
            "completed": "completed_at",
            "completed_at": "completed_at",
            "due": "due_at",
            "due_at": "due_at",
            "pipeline": "pipeline",
            "recurrence": "recurrence",
            "project_id": "project_id",
            "project": "project_id",
            "parent_id": "parent_id",
            "parent": "parent_id",
            "created_by": "created_by",
            "source": "created_by",
        }

        sets = []
        params: list[Any] = []
        for key, val in fields.items():
            col = field_map.get(key)
            if not col:
                continue
            if col == "status":
                sets.append("status = ?")
                params.append(val if isinstance(val, str) else val.value)
            elif col == "priority":
                sets.append("priority = ?")
                params.append(int(val) if not isinstance(val, int) else val)
            elif col == "tags":
                sets.append("tags = ?")
                params.append(_json_dumps(val) if val else None)
            elif col in ("started_at", "completed_at", "due_at"):
                sets.append(f"{col} = ?")
                params.append(_to_iso(val) if isinstance(val, datetime) else val)
            else:
                sets.append(f"{col} = ?")
                params.append(val)

        if not sets:
            return self._get_task(task_id)

        sets.append("modified_at = ?")
        params.append(datetime.now().isoformat())
        params.append(task_id)

        self._conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params
        )
        self._conn.commit()
        return self._get_task(task_id)

    # ── Internal: Project operations ─────────────────────

    def _get_project(self, project_id: str) -> Project | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_project(row)

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        # Compute task counts
        counts = self._conn.execute(
            "SELECT "
            "  count(*) as total, "
            "  sum(CASE WHEN status = 'done' THEN 1 ELSE 0 END) as done, "
            "  sum(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active "
            "FROM tasks WHERE project_id = ?",
            (row["id"],),
        ).fetchone()

        return Project(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            status=row["status"] or "active",
            path=row["path"],
            goal=row["goal"],
            done_when=row["done_when"],
            telegram_bot_key=row["telegram_bot_key"],
            telegram_chat_key=row["telegram_chat_key"],
            telegram_forum_topic=row["telegram_forum_topic"],
            stages=_json_loads(row["stages"]),
            current_stage=row["current_stage"],
            task_count=counts["total"] if counts else 0,
            done_count=counts["done"] if counts else 0,
            active_count=counts["active"] if counts else 0,
        )

    def _list_projects(
        self, filters: dict, limit: int, offset: int
    ) -> list[Project]:
        clauses = []
        params: list[Any] = []
        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM projects WHERE {where} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_project(r) for r in rows]

    def _create_project(self, project: Project) -> Project:
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO projects "
            "(id, title, description, status, path, goal, done_when, "
            " telegram_bot_key, telegram_chat_key, telegram_forum_topic, "
            " stages, current_stage, version, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                project.id,
                project.title,
                project.description,
                project.status,
                project.path,
                project.goal,
                project.done_when,
                project.telegram_bot_key,
                project.telegram_chat_key,
                project.telegram_forum_topic,
                _json_dumps(project.stages),
                project.current_stage,
                now,
            ),
        )
        self._conn.commit()
        return project

    def _update_project(self, project_id: str, fields: dict) -> Project | None:
        sets = []
        params: list[Any] = []
        for key, val in fields.items():
            if key in ("title", "description", "status", "path", "goal",
                       "done_when", "current_stage"):
                sets.append(f"{key} = ?")
                params.append(val)
            elif key == "stages":
                sets.append("stages = ?")
                params.append(_json_dumps(val))

        if not sets:
            return self._get_project(project_id)

        sets.append("modified_at = ?")
        params.append(datetime.now().isoformat())
        params.append(project_id)

        self._conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params
        )
        self._conn.commit()
        return self._get_project(project_id)

    # ── Internal: Goal operations ────────────────────────

    def _get_goal(self, goal_id: str) -> Goal | None:
        row = self._conn.execute(
            "SELECT * FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_goal(row)

    def _row_to_goal(self, row: sqlite3.Row) -> Goal:
        kr_rows = self._conn.execute(
            "SELECT * FROM key_results WHERE goal_id = ?", (row["id"],)
        ).fetchall()
        key_results = [
            KeyResult(
                title=kr["title"],
                progress=kr["progress"] or 0,
                target=kr["target"],
            )
            for kr in kr_rows
        ]
        return Goal(
            id=row["id"],
            title=row["title"],
            weight=row["weight"] or 0,
            description=row["description"],
            key_results=key_results,
            project=row["project_id"],
        )

    def _list_goals(
        self, filters: dict, limit: int, offset: int
    ) -> list[Goal]:
        rows = self._conn.execute(
            "SELECT * FROM goals LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    def _create_goal(self, goal: Goal) -> Goal:
        self._conn.execute(
            "INSERT OR REPLACE INTO goals "
            "(id, title, weight, description, project_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (goal.id, goal.title, goal.weight, goal.description, goal.project),
        )
        # Insert key results
        for kr in goal.key_results:
            self._conn.execute(
                "INSERT INTO key_results (goal_id, title, progress, target) "
                "VALUES (?, ?, ?, ?)",
                (goal.id, kr.title, kr.progress, kr.target),
            )
        self._conn.commit()
        return goal

    def _update_goal(self, goal_id: str, fields: dict) -> Goal | None:
        sets = []
        params: list[Any] = []
        for key, val in fields.items():
            if key in ("title", "description", "weight"):
                sets.append(f"{key} = ?")
                params.append(val)
        if not sets:
            return self._get_goal(goal_id)
        params.append(goal_id)
        self._conn.execute(
            f"UPDATE goals SET {', '.join(sets)} WHERE id = ?", params
        )
        self._conn.commit()
        return self._get_goal(goal_id)

    # ── Internal: Inbox operations ───────────────────────

    def _list_inbox(
        self, filters: dict, limit: int, offset: int
    ) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM inbox ORDER BY captured_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_inbox(r) for r in rows]

    @staticmethod
    def _row_to_inbox(row: sqlite3.Row) -> dict:
        """Convert an inbox table row to a normalized dict."""
        return {
            "id": row["id"],
            "text": row["text"],
            "captured": row["captured_at"] or "",
            "source": "manual",
        }

    def _create_inbox(
        self, text: str, source: str = "manual", confidence: float | None = None
    ) -> dict:
        """Create an inbox item. Returns normalized dict."""
        iid = self._next_id("i")
        now = _now()
        self._conn.execute(
            "INSERT INTO inbox (id, text, captured_at) VALUES (?, ?, ?)",
            (iid, text, now),
        )
        self._conn.commit()
        item = {
            "id": iid,
            "text": text,
            "captured": now,
            "source": source,
        }
        if confidence is not None:
            item["confidence"] = confidence
        return item

    # ── Internal: Thread operations ──────────────────────

    def _get_thread(self, thread_id: str) -> dict | None:
        """Get a single thread by ID. Returns normalized dict."""
        row = self._conn.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_thread(row)

    @staticmethod
    def _row_to_thread(row: sqlite3.Row) -> dict:
        """Convert a threads table row to a normalized dict."""
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"] or "exploring",
            "started": row["created_at"] or "",
            "project": row["project_id"] if row["project_id"] else None,
        }

    def _list_threads(
        self, filters: dict, limit: int, offset: int
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM threads WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_thread(r) for r in rows]

    def _create_thread(
        self, title: str, session_id: str | None = None
    ) -> dict:
        """Create a thread. Returns normalized dict."""
        tid = self._next_id("th")
        now = _today()
        self._conn.execute(
            "INSERT INTO threads (id, title, status, created_at) "
            "VALUES (?, ?, 'exploring', ?)",
            (tid, title, now),
        )
        self._conn.commit()
        thread = {
            "id": tid,
            "title": title,
            "status": "exploring",
            "started": now,
            "project": None,
        }
        if session_id:
            thread["sessions"] = [session_id]
        return thread

    # ── Internal: Session operations ───────────────────────

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Convert a sessions table row to a Session dataclass."""
        return Session(
            id=row["id"],
            agent_id=row["agent_id"],
            operator_id=row["operator_id"],
            status=SessionStatus(row["status"]) if row["status"] else SessionStatus.ACTIVE,
            started=_parse_dt(row["started_at"]) or datetime.now(),
            ended=_parse_dt(row["ended_at"]),
            outcome=row["outcome"],
            transcript_summary=row["transcript_summary"],
            utterance_count=row["utterance_count"] or 0,
            project=row["project_id"],
            thread_id=row["thread_id"],
        )

    def _list_sessions(
        self, filters: dict, limit: int, offset: int
    ) -> list[Session]:
        clauses: list[str] = []
        params: list[Any] = []
        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        if "project_id" in filters:
            clauses.append("project_id = ?")
            params.append(filters["project_id"])
        if "agent_id" in filters:
            clauses.append("agent_id = ?")
            params.append(filters["agent_id"])
        if "started_after" in filters:
            clauses.append("started_at >= ?")
            params.append(filters["started_after"])
        if "started_before" in filters:
            clauses.append("started_at <= ?")
            params.append(filters["started_before"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM sessions WHERE {where} "
            f"ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def _create_session(self, obj: Session) -> Session:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(id, agent_id, operator_id, status, started_at, ended_at, "
            " project_id, task_id, thread_id, outcome, transcript_summary, "
            " utterance_count, tokens_in, tokens_out, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.agent_id,
                obj.operator_id,
                obj.status.value if isinstance(obj.status, SessionStatus) else (obj.status or "active"),
                _to_iso(obj.started) or _now(),
                _to_iso(obj.ended),
                obj.project,
                None,  # task_id — not on the Session dataclass directly
                obj.thread_id,
                obj.outcome,
                obj.transcript_summary,
                obj.utterance_count,
                0,  # tokens_in
                0,  # tokens_out
                0,  # cost_usd
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: Area operations ────────────────────────

    def _row_to_area(self, row: sqlite3.Row) -> Area:
        """Convert an areas table row to an Area dataclass."""
        return Area(
            id=row["id"],
            name=row["name"],
            standard=row["standard"] or "",
            review_cadence=row["review_cadence"] or "weekly",
            parent_id=row["parent_id"],
            is_active=bool(row["is_active"]) if row["is_active"] is not None else True,
            metrics=_json_loads(row["metrics"], default=[]),
        )

    def _list_areas(
        self, filters: dict, limit: int, offset: int
    ) -> list[Area]:
        clauses: list[str] = []
        params: list[Any] = []
        if "is_active" in filters:
            clauses.append("is_active = ?")
            params.append(1 if filters["is_active"] else 0)
        if "parent_id" in filters:
            clauses.append("parent_id = ?")
            params.append(filters["parent_id"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM areas WHERE {where} "
            f"ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_area(r) for r in rows]

    def _create_area(self, obj: Area) -> Area:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO areas "
            "(id, name, standard, review_cadence, parent_id, is_active, metrics) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.name,
                obj.standard,
                obj.review_cadence,
                obj.parent_id,
                1 if obj.is_active else 0,
                _json_dumps(obj.metrics),
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: Workflow operations ────────────────────

    def _row_to_workflow(self, row: sqlite3.Row) -> Workflow:
        """Convert a workflows table row to a Workflow dataclass."""
        return Workflow(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            trigger_type=row["trigger_type"] or "manual",
            trigger_config=_json_loads(row["trigger_config"], default={}),
            task_templates=_json_loads(row["task_templates"], default=[]),
            project_template=_json_loads(row["project_template"], default=None),
            assignee_defaults=_json_loads(row["assignee_defaults"], default={}),
            is_active=bool(row["is_active"]) if row["is_active"] is not None else True,
            run_count=row["run_count"] or 0,
            last_run=_parse_dt(row["last_run_at"]),
        )

    def _list_workflows(
        self, filters: dict, limit: int, offset: int
    ) -> list[Workflow]:
        clauses: list[str] = []
        params: list[Any] = []
        if "is_active" in filters:
            clauses.append("is_active = ?")
            params.append(1 if filters["is_active"] else 0)
        if "trigger_type" in filters:
            clauses.append("trigger_type = ?")
            params.append(filters["trigger_type"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM workflows WHERE {where} "
            f"ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_workflow(r) for r in rows]

    def _create_workflow(self, obj: Workflow) -> Workflow:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO workflows "
            "(id, name, description, trigger_type, trigger_config, "
            " task_templates, project_template, assignee_defaults, "
            " is_active, run_count, last_run_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.name,
                obj.description,
                obj.trigger_type,
                _json_dumps(obj.trigger_config),
                _json_dumps(obj.task_templates),
                _json_dumps(obj.project_template) if obj.project_template else None,
                _json_dumps(obj.assignee_defaults),
                1 if obj.is_active else 0,
                obj.run_count,
                _to_iso(obj.last_run),
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: WorkflowRun operations ─────────────────

    def _row_to_workflow_run(self, row: sqlite3.Row) -> WorkflowRun:
        """Convert a workflow_runs table row to a WorkflowRun dataclass."""
        return WorkflowRun(
            id=row["id"],
            workflow_id=row["workflow_id"],
            status=row["status"] or "running",
            started_at=_parse_dt(row["started_at"]) or datetime.now(),
            completed_at=_parse_dt(row["completed_at"]),
            project_id=row["project_id"],
            task_ids=_json_loads(row["task_ids"], default=[]),
            triggered_by=row["triggered_by"] or "operator",
            trigger_event=_json_loads(row["trigger_event"], default={}),
        )

    def _list_workflow_runs(
        self, filters: dict, limit: int, offset: int
    ) -> list[WorkflowRun]:
        clauses: list[str] = []
        params: list[Any] = []
        if "workflow_id" in filters:
            clauses.append("workflow_id = ?")
            params.append(filters["workflow_id"])
        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        if "project_id" in filters:
            clauses.append("project_id = ?")
            params.append(filters["project_id"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM workflow_runs WHERE {where} "
            f"ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_workflow_run(r) for r in rows]

    def _create_workflow_run(self, obj: WorkflowRun) -> WorkflowRun:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO workflow_runs "
            "(id, workflow_id, status, started_at, completed_at, "
            " project_id, task_ids, triggered_by, trigger_event) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.workflow_id,
                obj.status,
                _to_iso(obj.started_at) or _now(),
                _to_iso(obj.completed_at),
                obj.project_id,
                _json_dumps(obj.task_ids),
                obj.triggered_by,
                _json_dumps(obj.trigger_event) if obj.trigger_event else None,
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: PipelineEntry operations ───────────────

    def _row_to_pipeline_entry(self, row: sqlite3.Row) -> PipelineEntry:
        """Convert a pipeline_entries table row to a PipelineEntry dataclass."""
        return PipelineEntry(
            id=row["id"],
            person_id=row["person_id"],
            pipeline_name=row["pipeline_name"],
            stage=row["stage"],
            value=row["value"] or 0.0,
            currency=row["currency"] or "CAD",
            entered_at=_parse_dt(row["entered_at"]) or datetime.now(),
            last_moved_at=_parse_dt(row["last_moved_at"]),
            expected_close=_parse_dt(row["expected_close"]),
            owner=row["owner"],
            project_id=row["project_id"],
            notes=row["notes"] or "",
        )

    def _list_pipeline_entries(
        self, filters: dict, limit: int, offset: int
    ) -> list[PipelineEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if "pipeline_name" in filters:
            clauses.append("pipeline_name = ?")
            params.append(filters["pipeline_name"])
        if "stage" in filters:
            clauses.append("stage = ?")
            params.append(filters["stage"])
        if "person_id" in filters:
            clauses.append("person_id = ?")
            params.append(filters["person_id"])
        if "project_id" in filters:
            clauses.append("project_id = ?")
            params.append(filters["project_id"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM pipeline_entries WHERE {where} "
            f"ORDER BY entered_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_pipeline_entry(r) for r in rows]

    def _create_pipeline_entry(self, obj: PipelineEntry) -> PipelineEntry:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO pipeline_entries "
            "(id, person_id, pipeline_name, stage, value, currency, "
            " entered_at, last_moved_at, expected_close, owner, project_id, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.person_id,
                obj.pipeline_name,
                obj.stage,
                obj.value,
                obj.currency,
                _to_iso(obj.entered_at) or _now(),
                _to_iso(obj.last_moved_at),
                _to_iso(obj.expected_close),
                obj.owner,
                obj.project_id,
                obj.notes,
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: Reminder operations ────────────────────

    def _row_to_reminder(self, row: sqlite3.Row) -> Reminder:
        """Convert a reminders table row to a Reminder dataclass."""
        return Reminder(
            id=row["id"],
            person_id=row["person_id"],
            due_date=_parse_dt(row["due_date"]) or datetime.now(),
            note=row["note"] or "",
            recurrence=row["recurrence"],
            status=row["status"] or "pending",
            snoozed_until=_parse_dt(row["snoozed_until"]),
            created_by=row["created_by"] or "operator",
            task_id=row["task_id"],
        )

    def _list_reminders(
        self, filters: dict, limit: int, offset: int
    ) -> list[Reminder]:
        clauses: list[str] = []
        params: list[Any] = []
        if "person_id" in filters:
            clauses.append("person_id = ?")
            params.append(filters["person_id"])
        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        if "due_before" in filters:
            clauses.append("due_date <= ?")
            params.append(filters["due_before"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM reminders WHERE {where} "
            f"ORDER BY due_date ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def _create_reminder(self, obj: Reminder) -> Reminder:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO reminders "
            "(id, person_id, due_date, note, recurrence, status, "
            " snoozed_until, created_by, task_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.person_id,
                _to_iso(obj.due_date) if isinstance(obj.due_date, datetime) else str(obj.due_date),
                obj.note,
                obj.recurrence,
                obj.status,
                _to_iso(obj.snoozed_until),
                obj.created_by,
                obj.task_id,
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: Transaction operations ─────────────────

    def _row_to_transaction(self, row: sqlite3.Row) -> Transaction:
        """Convert a transactions table row to a Transaction dataclass."""
        return Transaction(
            id=row["id"],
            person_id=row["person_id"],
            amount=row["amount"] or 0.0,
            currency=row["currency"] or "CAD",
            transaction_type=row["transaction_type"] or "payment",
            date=_parse_dt(row["date"]) or datetime.now(),
            status=row["status"] or "completed",
            description=row["description"] or "",
            project_id=row["project_id"],
            external_ref=row["external_ref"],
        )

    def _list_transactions(
        self, filters: dict, limit: int, offset: int
    ) -> list[Transaction]:
        clauses: list[str] = []
        params: list[Any] = []
        if "person_id" in filters:
            clauses.append("person_id = ?")
            params.append(filters["person_id"])
        if "transaction_type" in filters:
            clauses.append("transaction_type = ?")
            params.append(filters["transaction_type"])
        if "status" in filters:
            clauses.append("status = ?")
            params.append(filters["status"])
        if "project_id" in filters:
            clauses.append("project_id = ?")
            params.append(filters["project_id"])
        if "date_after" in filters:
            clauses.append("date >= ?")
            params.append(filters["date_after"])
        if "date_before" in filters:
            clauses.append("date <= ?")
            params.append(filters["date_before"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM transactions WHERE {where} "
            f"ORDER BY date DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_transaction(r) for r in rows]

    def _create_transaction(self, obj: Transaction) -> Transaction:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO transactions "
            "(id, person_id, amount, currency, transaction_type, date, "
            " status, description, project_id, external_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.person_id,
                obj.amount,
                obj.currency,
                obj.transaction_type,
                _to_iso(obj.date) if isinstance(obj.date, datetime) else str(obj.date),
                obj.status,
                obj.description,
                obj.project_id,
                obj.external_ref,
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: Procedure operations ───────────────────

    def _row_to_procedure(self, row: sqlite3.Row) -> Procedure:
        """Convert a procedures table row to a Procedure dataclass."""
        return Procedure(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            steps=_json_loads(row["steps"], default=[]),
            owner=row["owner"],
            review_interval_days=row["review_interval_days"] or 90,
            last_reviewed=_parse_dt(row["last_reviewed"]),
            next_review=_parse_dt(row["next_review"]),
            linked_workflow=row["linked_workflow"],
            project=row["project_id"],
            tags=_json_loads(row["tags"], default=[]),
            version=row["version"] or 1,
        )

    def _list_procedures(
        self, filters: dict, limit: int, offset: int
    ) -> list[Procedure]:
        clauses: list[str] = []
        params: list[Any] = []
        if "owner" in filters:
            clauses.append("owner = ?")
            params.append(filters["owner"])
        if "project_id" in filters:
            clauses.append("project_id = ?")
            params.append(filters["project_id"])
        if "linked_workflow" in filters:
            clauses.append("linked_workflow = ?")
            params.append(filters["linked_workflow"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM procedures WHERE {where} "
            f"ORDER BY title LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_procedure(r) for r in rows]

    def _create_procedure(self, obj: Procedure) -> Procedure:
        if not obj.id:
            obj.id = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT OR REPLACE INTO procedures "
            "(id, title, description, steps, owner, review_interval_days, "
            " last_reviewed, next_review, linked_workflow, project_id, "
            " tags, version, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.title,
                obj.description,
                _json_dumps(obj.steps),
                obj.owner,
                obj.review_interval_days,
                _to_iso(obj.last_reviewed),
                _to_iso(obj.next_review),
                obj.linked_workflow,
                obj.project,
                _json_dumps(obj.tags),
                obj.version,
                _now(),
            ),
        )
        self._conn.commit()
        return obj

    # ── Internal: Helpers ────────────────────────────────

    def _upsert_handoff(self, task_id: str, handoff: TaskHandoff) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO task_handoffs "
            "(task_id, state, next_step, files, decisions, blockers, "
            " session_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                handoff.state,
                handoff.next_step,
                _json_dumps(handoff.files),
                _json_dumps(handoff.decisions),
                _json_dumps(handoff.blockers),
                handoff.session_id,
                _to_iso(handoff.timestamp),
            ),
        )

    def _sync_fts(
        self, task_id: str, title: str, description: str | None
    ) -> None:
        """Keep FTS index in sync. Uses content-external approach."""
        # Get the rowid for the task
        row = self._conn.execute(
            "SELECT rowid FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row:
            rowid = row[0]
            # Delete old entry if it exists
            try:
                self._conn.execute(
                    "INSERT INTO tasks_fts(tasks_fts, rowid, title, description) "
                    "VALUES('delete', ?, ?, ?)",
                    (rowid, title, description or ""),
                )
            except sqlite3.OperationalError:
                pass
            # Insert new
            try:
                self._conn.execute(
                    "INSERT INTO tasks_fts(rowid, title, description) "
                    "VALUES(?, ?, ?)",
                    (rowid, title, description or ""),
                )
            except sqlite3.OperationalError:
                pass

    def _detect_type(self, object_id: str) -> ObjectType:
        """Detect what type an object ID belongs to."""
        if self._conn.execute(
            "SELECT 1 FROM tasks WHERE id = ?", (object_id,)
        ).fetchone():
            return ObjectType.TASK
        if self._conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (object_id,)
        ).fetchone():
            return ObjectType.PROJECT
        if self._conn.execute(
            "SELECT 1 FROM goals WHERE id = ?", (object_id,)
        ).fetchone():
            return ObjectType.GOAL
        # New entity tables
        _table_type_map = (
            ("sessions", ObjectType.SESSION),
            ("areas", ObjectType.AREA),
            ("workflows", ObjectType.WORKFLOW),
            ("workflow_runs", ObjectType.WORKFLOW_RUN),
            ("pipeline_entries", ObjectType.PIPELINE_ENTRY),
            ("reminders", ObjectType.REMINDER),
            ("transactions", ObjectType.TRANSACTION),
            ("procedures", ObjectType.PROCEDURE),
        )
        for table, obj_type in _table_type_map:
            if self._conn.execute(
                f"SELECT 1 FROM {table} WHERE id = ?", (object_id,)
            ).fetchone():
                return obj_type
        return ObjectType.TASK  # default

    # ── Task lifecycle (public) ─────────────────────────

    def add_subtask(
        self,
        parent_id: str,
        title: str,
        priority: int | None = None,
        status: str = "todo",
    ) -> Task | None:
        """Add a subtask to an existing task.

        Inherits project and priority from parent. Returns the new Task
        or None if parent not found.
        """
        parent_row = self._conn.execute(
            "SELECT id, project_id, priority FROM tasks WHERE id = ?",
            (parent_id,),
        ).fetchone()
        if not parent_row:
            return None

        if priority is None:
            priority = parent_row["priority"] if parent_row["priority"] is not None else 3

        task = Task(
            id="",  # auto-generated by create()
            title=title,
            status=_to_status(status),
            priority=_to_priority(priority),
            project=parent_row["project_id"],
            parent_id=parent_id,
            created_by="subtask",
        )
        return self.create(task)

    def complete_task(self, task_id: str) -> Task | None:
        """Mark a task as done with completed_at timestamp.

        Cascades parent completion if all siblings are done.
        Returns the updated task with ``auto_completed`` flag set if
        cascade fired on the parent. No side effects (no activity log,
        no GitHub sync, no SSE push).
        """
        now = _now()
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None

        self._conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ?, modified_at = ? "
            "WHERE id = ?",
            (now, now, task_id),
        )
        self._conn.commit()

        task = self._row_to_task(
            self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        )

        # Cascade: check if parent should auto-complete
        parent_id = row["parent_id"]
        if parent_id:
            parent_was_done_before = False
            parent_check = self._conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (parent_id,)
            ).fetchone()
            if parent_check:
                parent_was_done_before = parent_check["status"] == "done"

            self._cascade_parent(parent_id)

            # Check if cascade actually completed the parent
            if not parent_was_done_before:
                parent_after = self._conn.execute(
                    "SELECT status FROM tasks WHERE id = ?", (parent_id,)
                ).fetchone()
                if parent_after and parent_after["status"] == "done":
                    task.auto_completed = True  # type: ignore[attr-defined]

        return task

    def _cascade_parent(self, parent_id: str) -> None:
        """If all subtasks of parent are done, mark parent as done too.

        Recurses up the tree. This is a data integrity operation.
        """
        subtasks = self._conn.execute(
            "SELECT id, status FROM tasks WHERE parent_id = ?", (parent_id,)
        ).fetchall()
        if not subtasks:
            return

        all_done = all(r["status"] == "done" for r in subtasks)
        if all_done:
            parent_row = self._conn.execute(
                "SELECT id, status, parent_id FROM tasks WHERE id = ?",
                (parent_id,),
            ).fetchone()
            if parent_row and parent_row["status"] != "done":
                now = _now()
                self._conn.execute(
                    "UPDATE tasks SET status = 'done', completed_at = ?, "
                    "modified_at = ? WHERE id = ?",
                    (now, now, parent_id),
                )
                self._conn.commit()
                # Cascade up further if this parent also has a parent
                if parent_row["parent_id"]:
                    self._cascade_parent(parent_row["parent_id"])

    def cancel_task(self, task_id: str) -> Task | None:
        """Cancel a task. No side effects."""
        return self._update_task(task_id, {"status": "cancelled"})

    def start_task(self, task_id: str) -> Task | None:
        """Move a task to active status. No side effects (no live context)."""
        now = _now()
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None

        self._conn.execute(
            "UPDATE tasks SET status = 'active', started_at = ?, modified_at = ? "
            "WHERE id = ?",
            (now, now, task_id),
        )
        self._conn.commit()
        return self._row_to_task(
            self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        )

    # ── Handoff methods (public) ────────────────────────

    def write_handoff(
        self,
        task_id: str,
        state: str,
        next_step: str | None = None,
        files_touched: list | None = None,
        decisions: list | None = None,
        blockers: list | None = None,
    ) -> Task | None:
        """Upsert handoff context for a task. Returns full task dict or None."""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None

        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO task_handoffs "
            "(task_id, state, next_step, files, decisions, blockers, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                state,
                next_step or "",
                _json_dumps(files_touched),
                _json_dumps(decisions),
                _json_dumps(blockers),
                now,
            ),
        )
        self._conn.commit()

        return self._row_to_task(
            self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        )

    def get_handoff(self, task_id: str) -> dict | None:
        """Read handoff context for a task from task_handoffs table."""
        ho_row = self._conn.execute(
            "SELECT * FROM task_handoffs WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not ho_row:
            return None
        handoff = {
            "updated": ho_row["timestamp"] or "",
            "state": ho_row["state"] or "",
        }
        if ho_row["next_step"]:
            handoff["next_step"] = ho_row["next_step"]
        files = _json_loads(ho_row["files"])
        if files:
            handoff["files_touched"] = files
        decisions = _json_loads(ho_row["decisions"])
        if decisions:
            handoff["decisions"] = decisions
        blockers = _json_loads(ho_row["blockers"])
        if blockers:
            handoff["blockers"] = blockers
        return handoff

    # ── Task tree (public) ──────────────────────────────

    def get_task_tree(self, task_id: str) -> dict | None:
        """Fetch task + all children, return nested structure.

        Returns a dict (not Task) with ``task["subtasks"]`` containing
        full subtask dicts, matching engine.py's format.
        """
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        task = self._row_to_task(row)
        sub_rows = self._conn.execute(
            "SELECT * FROM tasks WHERE parent_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        # Attach full subtask objects
        task.subtasks = [self._row_to_task(r) for r in sub_rows]  # type: ignore[attr-defined]
        return task

    # ── Thread promotion (public) ───────────────────────

    def promote_thread(
        self, thread_id: str, project_title: str | None = None, goal: str | None = None
    ) -> Project | None:
        """Promote a thread to a project.

        Updates thread status to 'promoted', creates a new Project.
        Returns the created Project or None if thread not found.
        """
        row = self._conn.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if not row:
            return None

        title = project_title or row["title"]
        self._conn.execute(
            "UPDATE threads SET status = 'promoted' WHERE id = ?",
            (thread_id,),
        )
        self._conn.commit()

        project = Project(
            id="",  # auto-generated by create()
            title=title,
            goal=goal,
        )
        return self.create(project)

    # ── Session linking (public) ────────────────────────

    def link_session_to_task(
        self, task_id: str, session_id: str, outcome: str | None = None
    ) -> Task | None:
        """Link a session to a task.

        Inserts into session_tasks and ensures a sessions row exists.
        Returns the updated task or None if task not found.
        """
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None

        now = _now()

        # Ensure sessions row exists FIRST (session_tasks has FK to sessions)
        sess_exists = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not sess_exists:
            self._conn.execute(
                "INSERT INTO sessions (id, status, started_at, task_id, outcome) "
                "VALUES (?, 'active', ?, ?, ?)",
                (session_id, now, task_id, outcome),
            )
        elif outcome:
            self._conn.execute(
                "UPDATE sessions SET outcome = ? WHERE id = ?",
                (outcome, session_id),
            )

        # Insert session_tasks link if not present
        existing = self._conn.execute(
            "SELECT 1 FROM session_tasks WHERE session_id = ? AND task_id = ?",
            (session_id, task_id),
        ).fetchone()
        if not existing:
            self._conn.execute(
                "INSERT INTO session_tasks (session_id, task_id, relation) "
                "VALUES (?, ?, 'worked_on')",
                (session_id, task_id),
            )

        self._conn.commit()
        return self._row_to_task(
            self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        )

    def link_session_to_thread(
        self,
        thread_id: str,
        session_id: str,
        cwd: str | None = None,
        project: str | None = None,
    ) -> dict | None:
        """Link a session to a thread via the sessions table.

        Returns the thread dict or None if thread not found.
        """
        row = self._conn.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if not row:
            return None

        now = _now()
        sess_exists = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not sess_exists:
            self._conn.execute(
                "INSERT INTO sessions (id, status, started_at, thread_id) "
                "VALUES (?, 'active', ?, ?)",
                (session_id, now, thread_id),
            )
        else:
            self._conn.execute(
                "UPDATE sessions SET thread_id = ? WHERE id = ?",
                (thread_id, session_id),
            )

        self._conn.commit()

        thread = self._row_to_thread(row)
        # Add session count for display
        sess_count = self._conn.execute(
            "SELECT count(*) as cnt FROM sessions WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if sess_count:
            thread["session_count"] = sess_count["cnt"]
        return thread

    # ── Summary (public) ────────────────────────────────

    def summary(self) -> dict:
        """Return summary stats across all work entities.

        Returns dict with: total_tasks, by_status, by_priority,
        projects, goals, threads, inbox.
        """
        # Count top-level tasks by status
        rows = self._conn.execute(
            "SELECT status, count(*) as cnt FROM tasks "
            "WHERE parent_id IS NULL GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["cnt"] for r in rows}
        total = sum(by_status.values())

        # Count by priority
        prows = self._conn.execute(
            "SELECT priority, count(*) as cnt FROM tasks "
            "WHERE parent_id IS NULL GROUP BY priority"
        ).fetchall()
        by_priority = {str(r["priority"]): r["cnt"] for r in prows}

        project_count = self._conn.execute(
            "SELECT count(*) as cnt FROM projects"
        ).fetchone()["cnt"]
        goal_count = self._conn.execute(
            "SELECT count(*) as cnt FROM goals"
        ).fetchone()["cnt"]
        thread_count = self._conn.execute(
            "SELECT count(*) as cnt FROM threads"
        ).fetchone()["cnt"]
        inbox_count = self._conn.execute(
            "SELECT count(*) as cnt FROM inbox"
        ).fetchone()["cnt"]

        return {
            "total_tasks": total,
            "by_status": by_status,
            "by_priority": by_priority,
            "projects": project_count,
            "goals": goal_count,
            "threads": thread_count,
            "inbox": inbox_count,
        }
