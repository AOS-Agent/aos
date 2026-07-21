"""File one external signal into ``work.db`` as a ``pipeline='bug'`` task.

The single primitive shared by every work-system intake (islah import, ASC
crash/feedback). It does three things, idempotently:

  1. ensure a project exists for the app (so bugs group on the board),
  2. create the task with ``narrate=False`` (bug pipeline, stage, fields JSON),
  3. reconstruct the ``task_activity`` narrative with ORIGINAL timestamps.

Idempotency is keyed on ``source_ref`` (e.g. ``islah:qg#1``, ``asc-crash:<id>``,
``testflight:<id>``): every activity beat is written with
``source_event_id = "<source_ref>:<marker>"`` and inserted only if that marker
is absent, so a re-run — the migration re-applied, the mirror cron firing again,
ascbuild polling the same submission — adds nothing. The presence of the
``<source_ref>:created`` beat is the "already filed" signal.

Activity is written by direct SQL (like migration 089's backfill), NOT via
``append_activity``: that path stamps ``ts`` at wall-clock time and refuses the
``created``/``status_changed`` kinds. The intake needs the original report
timestamp and the full vocabulary, and the append-only table only forbids
UPDATE/DELETE — INSERT of a historical row is exactly what a backfill does.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.engine.work.pipelines import (
    BUG_STAGE_INTAKE,
    bug_stage_category,
    bug_stage_to_status,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prefix(app_id: str) -> str:
    """A short task-id prefix / project short_id derived from an app id."""
    parts = [p for p in (app_id or "").replace("_", "-").split("-") if p]
    if len(parts) >= 2:
        return "".join(p[0] for p in parts)[:4].lower()
    return ((app_id or "")[:3] or "app").lower()


def _titleize(app_id: str) -> str:
    return " ".join(w.capitalize() for w in (app_id or "").replace("_", "-").split("-")) or app_id


def _conn(engine):
    return engine._get_adapter()._conn


def already_filed(engine, source_ref: str) -> str | None:
    """Return the task id this source_ref was filed as, or None if never filed."""
    try:
        row = _conn(engine).execute(
            "SELECT task_id FROM task_activity WHERE source_event_id = ?",
            (f"{source_ref}:created",),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row["task_id"] if hasattr(row, "keys") else row[0]


def ensure_project(engine, app_id: str, name: str | None = None) -> str:
    """Idempotently ensure a project keyed on ``app_id``; return its handle.

    The app id doubles as the canonical project id (dossier: "app = project_id").
    Matching an existing project by id or short_id makes this safe to call on
    every import.
    """
    for p in engine.get_all_projects():
        if p.get("id") == app_id or p.get("short_id") == app_id:
            return p.get("id") or app_id
    engine.add_project(
        title=name or _titleize(app_id),
        short_id=_prefix(app_id),
        project_id=app_id,
    )
    return app_id


def _insert_activity(conn, task_id, ts, actor, kind, body, data, marker) -> bool:
    """Insert one activity row if its marker is new. Returns True if inserted."""
    if conn.execute(
        "SELECT 1 FROM task_activity WHERE source_event_id = ?", (marker,)
    ).fetchone():
        return False
    conn.execute(
        "INSERT INTO task_activity "
        "(task_id, ts, actor, kind, body, data, source_event_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, ts or _now(), actor, kind, body,
         json.dumps(data, ensure_ascii=False) if data else None, marker),
    )
    return True


def file_bug(
    engine,
    *,
    title: str,
    app: str | None,
    source: str,
    source_ref: str,
    fields: dict | None = None,
    stage: str = BUG_STAGE_INTAKE,
    reported_ts: str | None = None,
    activities: list[dict] | None = None,
    created_body: str | None = None,
    created_data: dict | None = None,
    actor: str = "intake",
    project_name: str | None = None,
) -> dict:
    """Create (or no-op) one bug task with a faithful activity narrative.

    ``activities`` is an ordered list of extra beats appended after the created
    beat, each ``{kind, body, data?, ts?, actor?, marker}``. ``marker`` is the
    per-beat idempotency suffix (unique within the bug). Returns
    ``{task_id, created, activities}`` — ``created=False`` means it already
    existed and nothing was written.
    """
    existing = already_filed(engine, source_ref)
    if existing:
        return {"task_id": existing, "created": False, "activities": 0}

    project_handle = ensure_project(engine, app, name=project_name) if app else None

    task = engine.add_task(
        title,
        project=project_handle,
        pipeline="bug",
        stage=stage,
        fields=fields or {},
        source=source,
        source_ref=source_ref,
        narrate=False,
    )
    tid = task["id"]

    conn = _conn(engine)
    body = created_body or f'Filed via {source}: "{title}"'
    cdata = {"source": source, "source_ref": source_ref, "pipeline": "bug", "stage": stage}
    if app:
        cdata["app"] = app
    if created_data:
        cdata.update(created_data)

    beats = [{"kind": "created", "body": body, "data": cdata,
              "ts": reported_ts, "marker": "created"}]
    beats.extend(activities or [])

    written = 0
    for b in beats:
        if _insert_activity(
            conn, tid, b.get("ts"), b.get("actor", actor),
            b["kind"], b["body"], b.get("data"),
            f"{source_ref}:{b['marker']}",
        ):
            written += 1
    conn.commit()
    return {"task_id": tid, "created": True, "activities": written}


def status_beat(stage: str, *, ts: str | None = None, marker: str = "status") -> dict | None:
    """Build a ``status_changed`` beat for a bug's current stage, or None for a
    fresh 'new' bug (its created beat already says everything)."""
    if stage == BUG_STAGE_INTAKE:
        return None
    coarse = bug_stage_to_status(stage) or "todo"
    verbs = {
        "active": "In progress", "done": "Completed", "cancelled": "Cancelled",
        "waiting": "Waiting on input", "in_review": "Sent for review",
        "todo": "Moved to todo", "triage": "In triage",
    }
    return {
        "kind": "status_changed",
        "body": verbs.get(coarse, f"Moved to {coarse}"),
        "data": {"to": coarse, "stage": stage, "category": bug_stage_category(stage)},
        "ts": ts,
        "marker": marker,
    }
