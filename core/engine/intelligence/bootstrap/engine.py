"""Bootstrap engine — dry-run preview + background execution worker.

Dry-run: walks vault_inventory, identifies which docs need compilation
(missing summary/concepts/topic or has contract violations), returns a
per-doc plan with estimated token cost. Zero LLM calls.

Execute: takes a git snapshot, then loops through the plan running the
compile engine on each doc. For each doc:
    1. Read its body from disk
    2. Build a synthetic ExtractionResult (platform='vault', backend='bootstrap')
    3. Call compile_capture() — writes a proposal via shadow-mode
    4. If auto-accepted: merge the new frontmatter fields into the file
       (preserving operator-set fields), update topic index, create links
    5. If pending: leave the file alone, the proposal waits for review
    6. Update bootstrap_runs row with progress
    7. Check pause/cancel flag between docs
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".aos" / "data" / "qareen.db"
VAULT_DIR = Path.home() / "vault"

# Approximate Haiku pricing (input + output rough avg) per compile call.
# Used for dry-run cost estimate. Real cost is tracked via execution_log
# in Part 9.
ESTIMATED_COST_PER_COMPILE_USD = 0.002

# Throttle — process at most N compiles per minute to stay under rate limits
# and give the operator a chance to pause/cancel mid-run.
COMPILES_PER_MINUTE = 20

# Inter-compile sleep (seconds) — derived from rate above
INTER_COMPILE_SLEEP = 60.0 / COMPILES_PER_MINUTE


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BootstrapPreview:
    total_docs: int
    eligible_docs: int
    by_reason: dict[str, int] = field(default_factory=dict)
    by_stage: dict[int, int] = field(default_factory=dict)
    sample_docs: list[dict[str, Any]] = field(default_factory=list)
    estimated_cost_usd: float = 0.0
    estimated_duration_seconds: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_docs": self.total_docs,
            "eligible_docs": self.eligible_docs,
            "by_reason": self.by_reason,
            "by_stage": self.by_stage,
            "sample_docs": self.sample_docs,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "notes": self.notes,
        }


@dataclass
class BootstrapRun:
    id: str
    started_at: str
    ended_at: str | None
    status: str
    total_docs: int
    processed_docs: int
    skipped_docs: int
    auto_accepted: int
    pending_review: int
    errors: int
    current_path: str | None
    git_ref: str | None
    git_branch: str | None
    model: str | None
    provider: str | None
    estimated_cost_usd: float | None
    actual_cost_usd: float | None
    error_log: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        return d


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_run(row: sqlite3.Row) -> BootstrapRun:
    error_log = []
    if row["error_log"]:
        try:
            error_log = json.loads(row["error_log"])
        except Exception:
            error_log = []
    return BootstrapRun(
        id=row["id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        status=row["status"],
        total_docs=row["total_docs"],
        processed_docs=row["processed_docs"],
        skipped_docs=row["skipped_docs"],
        auto_accepted=row["auto_accepted"],
        pending_review=row["pending_review"],
        errors=row["errors"],
        current_path=row["current_path"],
        git_ref=row["git_ref"],
        git_branch=row["git_branch"],
        model=row["model"],
        provider=row["provider"],
        estimated_cost_usd=row["estimated_cost_usd"],
        actual_cost_usd=row["actual_cost_usd"],
        error_log=error_log,
    )


# ---------------------------------------------------------------------------
# Dry-run preview (no LLM, no mutation)
# ---------------------------------------------------------------------------

def build_preview() -> BootstrapPreview:
    """Walk vault_inventory, identify eligible docs, estimate cost."""
    if not DB_PATH.exists():
        return BootstrapPreview(0, 0, notes=["qareen.db not found"])

    conn = _db()
    try:
        # Make sure the inventory table exists
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_inventory'"
        ).fetchone()
        if not row:
            return BootstrapPreview(
                0, 0, notes=["vault_inventory table not found — run scan_vault() first"]
            )

        total_row = conn.execute("SELECT COUNT(*) as c FROM vault_inventory").fetchone()
        total = total_row["c"] if total_row else 0

        # A doc is ELIGIBLE for bootstrap if:
        #   - it's in a stage where we actually compile (1, 3, 4, 5, 6)
        #   - AND it's missing summary OR concepts OR topic
        #   - OR it has contract violations
        #   - AND it's not an index file (those are LLM-managed differently)
        eligible_rows = conn.execute(
            """
            SELECT path, stage, type, title, topic, has_summary, has_concepts,
                   has_topic, backlink_count, is_orphan, issues, word_count
            FROM vault_inventory
            WHERE type != 'index'
              AND (
                  (has_summary = 0 OR has_concepts = 0 OR has_topic = 0)
                  OR (issues IS NOT NULL AND issues != '[]')
              )
            ORDER BY last_modified DESC
            """
        ).fetchall()

        eligible = len(eligible_rows)

        # Classify by reason
        by_reason: dict[str, int] = {}
        by_stage: dict[int, int] = {}
        sample_docs: list[dict[str, Any]] = []

        for r in eligible_rows:
            reasons: list[str] = []
            if not r["has_summary"]:
                reasons.append("no_summary")
            if not r["has_concepts"]:
                reasons.append("no_concepts")
            if not r["has_topic"]:
                reasons.append("no_topic")
            if r["issues"]:
                try:
                    issue_list = json.loads(r["issues"])
                    if issue_list:
                        reasons.append("contract_violations")
                except Exception:
                    pass
            reason_key = "|".join(reasons) if reasons else "unknown"
            by_reason[reason_key] = by_reason.get(reason_key, 0) + 1
            by_stage[r["stage"] or 0] = by_stage.get(r["stage"] or 0, 0) + 1

            if len(sample_docs) < 10:
                sample_docs.append({
                    "path": r["path"],
                    "title": r["title"],
                    "stage": r["stage"],
                    "type": r["type"],
                    "reasons": reasons,
                    "word_count": r["word_count"],
                })

        cost = eligible * ESTIMATED_COST_PER_COMPILE_USD
        duration = int(eligible * INTER_COMPILE_SLEEP)

        notes = []
        if eligible == 0:
            notes.append("Nothing to bootstrap — every doc already has frontmatter")
        else:
            notes.append(
                f"{eligible} docs will be compiled at ~{COMPILES_PER_MINUTE}/min "
                f"(est. {duration//60}m {duration%60}s)"
            )
            notes.append(
                "Confidence >= 0.85 will auto-apply; lower stays pending for review"
            )
            notes.append("Doc bodies are never modified — only frontmatter is augmented")

        return BootstrapPreview(
            total_docs=total,
            eligible_docs=eligible,
            by_reason=by_reason,
            by_stage=by_stage,
            sample_docs=sample_docs,
            estimated_cost_usd=round(cost, 4),
            estimated_duration_seconds=duration,
            notes=notes,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Run lifecycle (start, pause, resume, cancel, get, list)
# ---------------------------------------------------------------------------

# In-memory handle to running worker tasks (keyed by run_id)
_WORKERS: dict[str, asyncio.Task] = {}


def start_run(
    *, model: str = "haiku", limit: int | None = None,
) -> BootstrapRun:
    """Create a new bootstrap_runs row, take git snapshot, schedule worker.

    Returns the run record. The worker runs in the background via asyncio
    and updates the row as it progresses.
    """
    from .git_snapshot import take_snapshot

    # Check if there's already an active run
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, status FROM bootstrap_runs WHERE status IN ('pending','running','paused') LIMIT 1"
        ).fetchone()
        if row:
            raise RuntimeError(
                f"Bootstrap already in progress: {row['id']} ({row['status']})"
            )
    finally:
        conn.close()

    # Preview for estimates + total
    preview = build_preview()
    total = preview.eligible_docs
    if limit is not None:
        total = min(total, limit)

    run_id = uuid.uuid4().hex[:12]
    now_iso = datetime.now(timezone.utc).isoformat()

    snapshot = take_snapshot(run_id)

    conn = _db()
    try:
        conn.execute(
            """
            INSERT INTO bootstrap_runs
                (id, started_at, status, git_ref, git_branch,
                 total_docs, processed_docs, skipped_docs, auto_accepted,
                 pending_review, errors, model, estimated_cost_usd)
            VALUES (?, ?, 'pending', ?, ?, ?, 0, 0, 0, 0, 0, ?, ?)
            """,
            (
                run_id, now_iso,
                snapshot.get("git_ref"),
                snapshot.get("git_branch"),
                total, model,
                total * ESTIMATED_COST_PER_COMPILE_USD,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Schedule the background worker
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_worker_run(run_id, limit=limit, model=model))
        _WORKERS[run_id] = task
    except RuntimeError:
        # No running loop — caller is responsible for awaiting _worker_run directly
        logger.warning("start_run called outside event loop; worker NOT scheduled")

    return get_run(run_id)  # type: ignore[return-value]


def get_run(run_id: str) -> BootstrapRun | None:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM bootstrap_runs WHERE id = ?", (run_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_run(row)
    finally:
        conn.close()


def list_runs(limit: int = 20) -> list[BootstrapRun]:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM bootstrap_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_run(r) for r in rows]
    finally:
        conn.close()


def pause_run(run_id: str) -> BootstrapRun | None:
    return _set_status(run_id, "paused", allowed_from={"running"})


def resume_run(run_id: str) -> BootstrapRun | None:
    run = get_run(run_id)
    if run is None:
        return None
    if run.status != "paused":
        return run

    # Flip back to running and re-schedule the worker
    _set_status(run_id, "running", allowed_from={"paused"})
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_worker_run(run_id, limit=None, model=run.model or "haiku", resume=True))
        _WORKERS[run_id] = task
    except RuntimeError:
        pass
    return get_run(run_id)


def cancel_run(run_id: str) -> BootstrapRun | None:
    run = _set_status(run_id, "cancelled", allowed_from={"pending", "running", "paused"})
    task = _WORKERS.pop(run_id, None)
    if task and not task.done():
        task.cancel()
    return run


def _set_status(run_id: str, new_status: str, *, allowed_from: set[str]) -> BootstrapRun | None:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT status FROM bootstrap_runs WHERE id = ?", (run_id,),
        ).fetchone()
        if not row:
            return None
        if row["status"] not in allowed_from:
            # Invalid transition, return the current state
            return get_run(run_id)

        fields = ["status = ?"]
        params: list[Any] = [new_status]
        if new_status in ("done", "failed", "cancelled"):
            fields.append("ended_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())

        params.append(run_id)
        conn.execute(
            f"UPDATE bootstrap_runs SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return get_run(run_id)


# ---------------------------------------------------------------------------
# Worker — the actual loop that compiles docs one by one
# ---------------------------------------------------------------------------

async def _worker_run(
    run_id: str,
    *,
    limit: int | None = None,
    model: str = "haiku",
    resume: bool = False,
) -> None:
    """Background worker — loops eligible docs, compiles each, updates row.

    Checks the run's status between docs so pause/cancel are honored.
    """
    # Flip to running
    _set_status(run_id, "running", allowed_from={"pending", "paused"})

    # Load eligible doc paths (order matches the preview)
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT path, stage, type, title FROM vault_inventory
            WHERE type != 'index'
              AND (
                  (has_summary = 0 OR has_concepts = 0 OR has_topic = 0)
                  OR (issues IS NOT NULL AND issues != '[]')
              )
            ORDER BY last_modified DESC
            """
        ).fetchall()
    finally:
        conn.close()

    if limit is not None:
        rows = rows[:limit]

    # If resuming, skip docs we already processed
    if resume:
        run = get_run(run_id)
        if run is not None:
            rows = rows[run.processed_docs:]

    # Lazy imports — avoid dragging compile stack into callers that don't use it
    from ..compile import compile_capture, CompilationError
    from ..content.result import ExtractionResult

    errors: list[dict[str, Any]] = []
    auto_accepted = 0
    pending_review = 0
    processed = 0
    skipped = 0

    for row in rows:
        # Honor pause/cancel
        cur = get_run(run_id)
        if cur is None or cur.status in ("cancelled", "paused", "failed"):
            logger.info("bootstrap worker %s exiting: status=%s", run_id, cur.status if cur else "missing")
            return

        path = row["path"]
        _update_current(run_id, path)

        try:
            result = await _compile_vault_doc(path, model=model)
            if result is None:
                skipped += 1
            elif result["status"] == "auto_accepted":
                auto_accepted += 1
            elif result["status"] == "pending":
                pending_review += 1
            processed += 1
        except CompilationError as e:
            logger.warning("bootstrap compile error for %s: %s", path, e)
            errors.append({"path": path, "error": str(e)})
            processed += 1
        except Exception as e:
            logger.exception("bootstrap unexpected error for %s", path)
            errors.append({"path": path, "error": f"{type(e).__name__}: {e}"})
            processed += 1

        _update_progress(
            run_id,
            processed=processed,
            auto_accepted=auto_accepted,
            pending_review=pending_review,
            errors=len(errors),
            error_log=errors,
            skipped=skipped,
        )

        # Throttle between compiles
        await asyncio.sleep(INTER_COMPILE_SLEEP)

    # Completed normally
    _set_status(run_id, "done", allowed_from={"running"})
    _WORKERS.pop(run_id, None)


def _update_current(run_id: str, path: str) -> None:
    conn = _db()
    try:
        conn.execute(
            "UPDATE bootstrap_runs SET current_path = ?, current_started = ? WHERE id = ?",
            (path, datetime.now(timezone.utc).isoformat(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _update_progress(
    run_id: str,
    *,
    processed: int,
    auto_accepted: int,
    pending_review: int,
    errors: int,
    error_log: list[dict[str, Any]],
    skipped: int,
) -> None:
    conn = _db()
    try:
        conn.execute(
            """
            UPDATE bootstrap_runs
            SET processed_docs = ?,
                auto_accepted = ?,
                pending_review = ?,
                errors = ?,
                skipped_docs = ?,
                error_log = ?
            WHERE id = ?
            """,
            (
                processed, auto_accepted, pending_review, errors, skipped,
                json.dumps(error_log) if error_log else None, run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Per-doc compilation (the actual work)
# ---------------------------------------------------------------------------

async def _compile_vault_doc(path: str, *, model: str = "haiku") -> dict[str, Any] | None:
    """Compile one existing vault doc, merge frontmatter, write proposal.

    Returns:
        {"status": "auto_accepted" | "pending" | "skipped", "vault_path": ...}
        None if the doc can't be processed (file missing, empty, etc.)
    """
    from ..compile import compile_capture
    from ..content.result import ExtractionResult

    full = VAULT_DIR / path
    if not full.is_file():
        return None

    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("bootstrap: cannot read %s: %s", path, e)
        return None

    frontmatter, body = _split_frontmatter(content)
    if not body.strip():
        return {"status": "skipped", "vault_path": path}

    # Build a synthetic extraction result
    title = frontmatter.get("title") or full.stem
    author = frontmatter.get("author") or ""
    source_url = frontmatter.get("source_url") or frontmatter.get("source") or ""
    platform = frontmatter.get("platform") or _infer_platform_from_type(frontmatter.get("type"))

    extraction = ExtractionResult(
        url=source_url or f"vault://{path}",
        platform=platform,
        title=str(title),
        author=str(author),
        content=body[:12000],  # cap content for LLM context
        published_at=frontmatter.get("date") or None,
        media=[],
        links=[],
        metadata={"vault_path": path},
        backend="bootstrap",
    )

    # Compile via the same pass the live save uses
    compilation = await compile_capture(extraction, model=model)

    # Write a proposal row (source='bootstrap' so we can distinguish later)
    proposal_id = _write_bootstrap_proposal(path, extraction, compilation)

    # Decide: auto-accept or leave pending
    threshold = _get_shadow_threshold()
    if compilation.topic_confidence >= threshold:
        _merge_frontmatter_into_file(full, frontmatter, compilation, path)
        _mark_bootstrap_proposal(proposal_id, "auto_accepted", vault_path=path)
        return {"status": "auto_accepted", "vault_path": path, "proposal_id": proposal_id}

    return {"status": "pending", "vault_path": path, "proposal_id": proposal_id}


def _get_shadow_threshold() -> float:
    import os
    try:
        return float(os.environ.get("AOS_SHADOW_THRESHOLD", "0.85"))
    except (TypeError, ValueError):
        return 0.85


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    raw = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}, content
    if not isinstance(fm, dict):
        return {}, content
    return fm, body


def _infer_platform_from_type(type_value: str | None) -> str:
    if not type_value:
        return "vault"
    t = str(type_value).lower()
    if "capture" in t:
        return "blog"
    if "research" in t:
        return "blog"
    return "vault"


def _merge_frontmatter_into_file(
    full: Path,
    existing_frontmatter: dict[str, Any],
    compilation: Any,
    path: str,
) -> None:
    """Merge LLM-produced frontmatter fields into an existing vault file.

    Merge rules (operator-safe):
        - Existing fields are NEVER overwritten — they win
        - Missing fields are added
        - tags list is unioned
        - concepts list is added only if missing entirely
    """
    merged = dict(existing_frontmatter)

    # Fill missing summary
    if not merged.get("summary") and compilation.summary:
        merged["summary"] = compilation.summary

    # Fill missing concepts
    if not merged.get("concepts"):
        if compilation.concepts:
            merged["concepts"] = list(compilation.concepts)

    # Fill missing topic
    if not merged.get("topic") and compilation.topic:
        merged["topic"] = compilation.topic

    # Union tags (list-preserving)
    existing_tags = merged.get("tags") or []
    if not isinstance(existing_tags, list):
        existing_tags = [str(existing_tags)]
    new_tags = list(compilation.concepts or [])
    union = list(existing_tags)
    for t in new_tags:
        if t not in union:
            union.append(t)
    if union:
        merged["tags"] = union

    # Add a provenance marker so we know this doc was touched by bootstrap
    merged["bootstrap_compiled_at"] = datetime.now(timezone.utc).isoformat()

    # Ensure type and stage are set (inferred from folder if missing)
    if not merged.get("type"):
        try:
            from ..inventory.contract import infer_type
            merged["type"] = infer_type(Path(path))
        except Exception:
            pass
    if merged.get("stage") is None:
        try:
            from ..inventory.contract import infer_stage
            s = infer_stage(Path(path))
            if s > 0:
                merged["stage"] = s
        except Exception:
            pass

    # Re-read current body (don't trust the captured body — in case of
    # concurrent edits, we want the latest)
    try:
        current = full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return

    _fm, body = _split_frontmatter(current)

    new_content = (
        "---\n"
        + yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + body.lstrip("\n")
    )

    try:
        full.write_text(new_content, encoding="utf-8")
    except Exception as e:
        logger.warning("bootstrap: failed to write merged frontmatter to %s: %s", full, e)

    # Also update the topic index
    if compilation.topic:
        try:
            from ..topics import TopicEntry, update_index, slugify
            from datetime import date
            entry = TopicEntry(
                path=path,
                title=str(merged.get("title", full.stem)),
                type=str(merged.get("type", "capture")),
                stage=int(merged.get("stage") or 1),
                date=str(merged.get("date") or date.today().isoformat()),
                summary=(compilation.summary or "")[:200],
            )
            update_index(
                slug=slugify(compilation.topic),
                title=compilation.topic.replace("-", " ").title(),
                entry=entry,
            )
        except Exception as e:
            logger.warning("bootstrap: topic index update failed for %s: %s", path, e)


# ---------------------------------------------------------------------------
# Proposal persistence (uses the same compilation_proposals table as live save)
# ---------------------------------------------------------------------------

def _write_bootstrap_proposal(
    path: str, extraction: Any, compilation: Any,
) -> str:
    proposal_id = uuid.uuid4().hex[:12]
    now_iso = datetime.now(timezone.utc).isoformat()
    extraction_json = json.dumps(extraction.to_dict(), default=str)
    compilation_json = json.dumps(compilation.to_dict(), default=str)

    conn = _db()
    try:
        conn.execute(
            """
            INSERT INTO compilation_proposals
                (id, created_at, source, source_id, status, auto_accepted,
                 topic_confidence, extraction_json, compilation_json)
            VALUES (?, ?, 'bootstrap', ?, 'pending', 0, ?, ?, ?)
            """,
            (
                proposal_id, now_iso, path,
                float(compilation.topic_confidence or 0.0),
                extraction_json, compilation_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return proposal_id


def _mark_bootstrap_proposal(
    proposal_id: str, status: str, *, vault_path: str | None = None,
) -> None:
    conn = _db()
    try:
        conn.execute(
            """
            UPDATE compilation_proposals
            SET status = ?,
                auto_accepted = ?,
                vault_path = COALESCE(?, vault_path),
                reviewed_at = ?,
                reviewed_by = 'bootstrap'
            WHERE id = ?
            """,
            (
                status,
                1 if status == "auto_accepted" else 0,
                vault_path,
                datetime.now(timezone.utc).isoformat(),
                proposal_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
