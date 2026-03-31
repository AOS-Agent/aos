"""Work Adapter — Tasks, Projects, Goals, Inbox, Threads.

Maps between the ontology's typed objects (Task, Project, Goal) and
the qareen.db SQLite storage. Handles all CRUD, link management,
and full-text search via the tasks_fts FTS5 table.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from ..types import (
    Goal,
    KeyResult,
    Link,
    LinkType,
    ObjectType,
    Project,
    Task,
    TaskHandoff,
    TaskPriority,
    TaskStatus,
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

    # ── Adapter interface ────────────────────────────────

    @property
    def object_type(self) -> ObjectType:
        return ObjectType.TASK

    def get(self, object_id: str) -> Task | Project | Goal | None:
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
        else:
            return self._count_table("tasks", filters)

    def create(self, obj: Any) -> Any:
        """Create a new object in storage. Returns the created object."""
        if isinstance(obj, Task):
            return self._create_task(obj)
        elif isinstance(obj, Project):
            return self._create_project(obj)
        elif isinstance(obj, Goal):
            return self._create_goal(obj)
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

        # Load subtask IDs
        subtask_rows = self._conn.execute(
            "SELECT id FROM tasks WHERE parent_id = ?", (task_id,)
        ).fetchall()
        subtask_ids = [r["id"] for r in subtask_rows]

        return Task(
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
        sets = []
        params: list[Any] = []
        for key, val in fields.items():
            if key == "status":
                sets.append("status = ?")
                params.append(val if isinstance(val, str) else val.value)
            elif key == "priority":
                sets.append("priority = ?")
                params.append(int(val) if not isinstance(val, int) else val)
            elif key == "title":
                sets.append("title = ?")
                params.append(val)
            elif key == "description":
                sets.append("description = ?")
                params.append(val)
            elif key == "tags":
                sets.append("tags = ?")
                params.append(_json_dumps(val))
            elif key == "assigned_to":
                sets.append("assigned_to = ?")
                params.append(val)
            elif key == "started":
                sets.append("started_at = ?")
                params.append(_to_iso(val) if isinstance(val, datetime) else val)
            elif key == "completed":
                sets.append("completed_at = ?")
                params.append(_to_iso(val) if isinstance(val, datetime) else val)
            elif key == "due":
                sets.append("due_at = ?")
                params.append(_to_iso(val) if isinstance(val, datetime) else val)

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
        return [dict(r) for r in rows]

    # ── Internal: Thread operations ──────────────────────

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
        return [dict(r) for r in rows]

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
        return ObjectType.TASK  # default
