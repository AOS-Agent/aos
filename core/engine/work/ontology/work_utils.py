"""Work Utilities -- user-facing helpers for the work CLI.

These are functions the CLI needs that don't belong in the data adapter.
They handle task resolution, project detection, live context management,
and handoff formatting. Pure utilities: no side effects, no logging,
no event emission.
"""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Protocol for the adapter dependency (avoids circular imports)
# ---------------------------------------------------------------------------

class WorkAdapterProtocol(Protocol):
    """Minimal interface expected from the WorkAdapter."""

    def list(self, *, filters: dict[str, Any] | None = None,
             limit: int = 100, offset: int = 0) -> list[Any]: ...

    def get(self, object_id: str) -> Any | None: ...


# ---------------------------------------------------------------------------
# 1. TaskResolver
# ---------------------------------------------------------------------------

class TaskResolver:
    """Resolve a task reference (ID, partial ID, or fuzzy title) to a task dict."""

    # Fuzzy scoring floor: below this a title is not considered a match at all.
    THRESHOLD = 0.3

    # Two candidates this close are indistinguishable. Used only by
    # resolve_for_mutation, which refuses rather than pick between them.
    TIE_MARGIN = 0.1

    def __init__(self, adapter: WorkAdapterProtocol):
        self.adapter = adapter

    def resolve(self, query_str: str, project_id: str | None = None) -> dict | None:
        """Resolve a task by exact ID, partial ID, or fuzzy title match.

        Priority:
        1. Exact ID match
        2. Legacy ID match (_legacy_id field)
        3. Project-scoped shorthand: bare digit "3" + project "aos" -> "aos#3"
        4. Substring match
        5. Fuzzy scoring: word hits (60%) + SequenceMatcher (40%), threshold 0.3

        Args:
            query_str: The user's query -- could be an ID, partial title, etc.
            project_id: Optional project context for scoped shorthand.

        Returns:
            The best matching task dict, or None.
        """
        tasks = self._get_all_tasks()

        if not query_str or not tasks:
            return None

        query_str = query_str.strip()

        # 1. Exact ID match
        for t in tasks:
            if t["id"] == query_str:
                return t

        # 2. Legacy ID match
        for t in tasks:
            if t.get("_legacy_id") == query_str:
                return t

        # 3. Project-scoped shorthand: bare digit + project context
        if query_str.isdigit() and project_id:
            prefix = _project_prefix(project_id)
            scoped_id = f"{prefix}#{query_str}"
            for t in tasks:
                if t["id"] == scoped_id:
                    return t

        # 4. Substring match
        query_lower = query_str.lower()
        substring_matches = [
            t for t in tasks
            if query_lower in t.get("title", "").lower()
        ]

        if len(substring_matches) == 1:
            return substring_matches[0]

        if substring_matches:
            # Multiple substring matches -- pick best by similarity
            return max(
                substring_matches,
                key=lambda t: SequenceMatcher(
                    None, query_lower, t["title"].lower()
                ).ratio(),
            )

        # 5. Full fuzzy match across all tasks
        scored = self._scored(tasks, query_lower)
        if scored:
            return scored[0][0]

        return None

    # -----------------------------------------------------------------------
    # Scoring, shared by the read path and the mutation path
    # -----------------------------------------------------------------------

    @classmethod
    def score(cls, query_lower: str, title: str) -> float:
        """Word hits (60%) + sequence similarity (40%). 0.0 - 1.0."""
        title_lower = (title or "").lower()
        query_words = query_lower.split()
        word_hits = sum(1 for w in query_words if w in title_lower)
        seq_ratio = SequenceMatcher(None, query_lower, title_lower).ratio()
        return (word_hits / max(len(query_words), 1)) * 0.6 + seq_ratio * 0.4

    def _scored(self, tasks: list[dict], query_lower: str) -> list[tuple[dict, float]]:
        """Every task scoring above THRESHOLD, best first."""
        scored = [
            (t, self.score(query_lower, t.get("title", "")))
            for t in tasks
        ]
        scored = [(t, s) for t, s in scored if s > self.THRESHOLD]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # -----------------------------------------------------------------------
    # The mutation path: resolve, or name the candidates and refuse
    # -----------------------------------------------------------------------

    def resolve_for_mutation(
        self, query_str: str, project_id: str | None = None
    ) -> tuple[dict | None, list[dict]]:
        """Resolve for a command that is about to CHANGE the task.

        Returns ``(task, [])`` when there is exactly one answer, and
        ``(None, candidates)`` when there is more than one — the caller prints
        the candidates and refuses. ``(None, [])`` means nothing matched at all,
        which is a different answer and a different exit code.

        ``resolve`` ends by returning the single best fuzzy hit. With tied
        scores, which task that is depends on the order ``adapter.list()``
        happened to return, and the live database held 64 rows titled "Fix the
        login bug" — so `work done "fix the login bug"` flipped one of
        sixty-four and the operator could not know which. That is fine for
        `show`; for `done` it silently rewrites the wrong history.

        An identity always wins: exact ID, legacy ID, project-scoped shorthand.
        A title is allowed — mutating commands stay fuzzy-friendly — but a
        title that cannot single one task out is refused, never guessed.

        **"Candidate" means "within TIE_MARGIN of the best score."** Defining it
        as "everything above THRESHOLD" was measured against the live 2,571-row
        table and would have refused nearly every fuzzy mutation: "sse push"
        clears 0.3 on 49 titles, "telegram" on 17, even though the intended
        match scores 0.85 and the runner-up 0.69. A clear winner is not an
        ambiguity; two titles a hair apart are. That distinction is the whole
        feature.
        """
        tasks = self._get_all_tasks()
        if not query_str or not tasks:
            return None, []

        query_str = query_str.strip()

        # Identities first — these are lookups, not searches.
        for t in tasks:
            if t["id"] == query_str:
                return t, []
        for t in tasks:
            if t.get("_legacy_id") == query_str:
                return t, []
        if query_str.isdigit() and project_id:
            scoped_id = f"{_project_prefix(project_id)}#{query_str}"
            for t in tasks:
                if t["id"] == scoped_id:
                    return t, []

        query_lower = query_str.lower()

        # A literal substring is a stronger signal than a score, so substring
        # matches form their own pool when there are any. One is an answer;
        # several are ranked and then held to the same tie rule.
        pool = [t for t in tasks if query_lower in t.get("title", "").lower()]
        if len(pool) == 1:
            return pool[0], []
        if pool:
            ranked = [(t, self.score(query_lower, t.get("title", ""))) for t in pool]
            ranked.sort(key=lambda x: x[1], reverse=True)
        else:
            ranked = self._scored(tasks, query_lower)

        if not ranked:
            return None, []

        best = ranked[0][1]
        candidates = [t for t, s in ranked if best - s <= self.TIE_MARGIN]
        if len(candidates) == 1:
            return candidates[0], []
        return None, candidates

    def _get_all_tasks(self) -> list[dict]:
        """Retrieve all tasks from the adapter.

        The adapter returns typed Task objects. The resolver works with
        dicts for backward compatibility with the CLI, so we convert
        if necessary.
        """
        results = self.adapter.list(limit=10000)
        tasks: list[dict] = []
        for item in results:
            if isinstance(item, dict):
                tasks.append(item)
            else:
                # Convert a Task dataclass to a dict with the keys
                # the resolver expects: id, title, _legacy_id, project
                tasks.append(_task_to_dict(item))
        return tasks


# ---------------------------------------------------------------------------
# 2. ProjectContext
# ---------------------------------------------------------------------------

class ProjectContext:
    """Detect the active project from the current working directory."""

    def __init__(self, adapter: WorkAdapterProtocol):
        self.adapter = adapter

    def detect_from_cwd(self, cwd: str | None = None) -> str | None:
        """Detect project from current working directory.

        Resolution order:
        1. Match against project ``path`` fields from the database.
           A project matches if ``cwd`` is equal to or inside that path, and
           the **longest** matching path wins.
        2. Fall back to directory name matching against project IDs.

        Longest-match is load-bearing, not a nicety. Projects nest — a dataset
        or an app inside a monorepo — and this used to return whichever
        candidate the adapter listed first, so the same directory could resolve
        to the parent on one machine and the child on another purely by row
        order. Thread dedup (backend.get_or_create_thread_for_cwd) keys on the
        answer, so a non-deterministic answer means duplicate threads.

        Args:
            cwd: Override for the working directory. Defaults to os.getcwd().

        Returns:
            The project ID, or None if no match.
        """
        if cwd is None:
            cwd = os.getcwd()

        cwd_path = Path(cwd).resolve()

        # 1. Query projects from the adapter and match by path, longest first
        projects = self._get_all_projects()
        best_id: str | None = None
        best_len = -1
        for proj in projects:
            proj_path_str = proj.get("path") if isinstance(proj, dict) else getattr(proj, "path", None)
            if not proj_path_str or not str(proj_path_str).strip():
                continue
            proj_path = Path(str(proj_path_str).strip()).expanduser().resolve()
            try:
                cwd_path.relative_to(proj_path)
            except ValueError:
                continue
            depth = len(proj_path.parts)
            if depth > best_len:
                best_len = depth
                best_id = proj["id"] if isinstance(proj, dict) else proj.id
        if best_id is not None:
            return best_id

        # 2. Fall back to directory name matching against project IDs
        dir_name = cwd_path.name
        for proj in projects:
            proj_id = proj["id"] if isinstance(proj, dict) else proj.id
            if dir_name == proj_id:
                return proj_id

        # 3. Check if cwd is inside ~/project/<dir_name> or ~/<dir_name>
        #    and <dir_name> matches a known project ID
        for proj in projects:
            proj_id = proj["id"] if isinstance(proj, dict) else proj.id
            for base in (Path.home() / "project", Path.home()):
                candidate = base / proj_id
                try:
                    cwd_path.relative_to(candidate)
                    return proj_id
                except ValueError:
                    continue

        return None

    def _get_all_projects(self) -> list[Any]:
        """Retrieve all projects from the adapter."""
        return self.adapter.list(filters={"_type": "project"}, limit=10000)


# ---------------------------------------------------------------------------
# 3. LiveContext
# ---------------------------------------------------------------------------

class LiveContext:
    """Manages the .live-context.json workbench file.

    This file tracks which task is currently being worked on, so that
    session hooks and other tools can detect active work.
    """

    CONTEXT_FILE = Path.home() / ".aos" / "work" / ".live-context.json"

    def set(self, task: dict, session_id: str | None = None) -> None:
        """Write live context for the active task.

        Args:
            task: Task dict with at least ``id``, ``title``, and optionally ``project``.
            session_id: The current Claude Code session ID, if known.
        """
        self.CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        ctx = {
            "task_id": task["id"],
            "title": task.get("title", ""),
            "project": task.get("project"),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "cwd": os.getcwd(),
        }
        self.CONTEXT_FILE.write_text(json.dumps(ctx, indent=2))

    def get(self) -> dict | None:
        """Read current live context.

        Returns:
            The context dict, or None if nothing is active.
        """
        if not self.CONTEXT_FILE.exists():
            return None
        try:
            return json.loads(self.CONTEXT_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def clear(self, task_id: str | None = None) -> dict | None:
        """Clear live context. Returns the old context if it existed.

        Args:
            task_id: If provided, only clear if the active context matches
                     this task ID. Prevents accidentally clearing a different
                     task's context.

        Returns:
            The old context dict, or None if nothing was cleared.
        """
        if not self.CONTEXT_FILE.exists():
            return None
        try:
            ctx = json.loads(self.CONTEXT_FILE.read_text())
            if task_id and ctx.get("task_id") != task_id:
                return None  # Different task is active -- don't touch
            self.CONTEXT_FILE.unlink()
            return ctx
        except (json.JSONDecodeError, OSError):
            return None


# ---------------------------------------------------------------------------
# 4. HandoffFormatter
# ---------------------------------------------------------------------------

class HandoffFormatter:
    """Formats handoff context into dispatch prompts for agent continuity."""

    def build_prompt(self, task: dict) -> str | None:
        """Build a dispatch prompt from a task's handoff data.

        Args:
            task: A task dict. Must have ``id`` and ``title``.
                  May have ``project`` and ``handoff`` keys.

        Returns:
            A formatted string ready for agent injection, or None if
            the task dict is empty/None.
        """
        if not task:
            return None

        handoff = task.get("handoff")
        lines: list[str] = []
        lines.append(f"Task: {task['id']} -- {task['title']}")

        if task.get("project"):
            lines.append(f"Project: {task['project']}")

        if handoff:
            lines.append("")
            lines.append("CONTEXT FROM PREVIOUS SESSION:")
            lines.append(handoff.get("state", "No state recorded."))

            if handoff.get("next_step"):
                lines.append("")
                lines.append("NEXT STEP:")
                lines.append(handoff["next_step"])

            files = handoff.get("files_touched") or handoff.get("files")
            if files:
                lines.append("")
                lines.append("FILES TOUCHED:")
                for f in files:
                    lines.append(f"  - {f}")

            if handoff.get("decisions"):
                lines.append("")
                lines.append("DECISIONS ALREADY MADE (don't revisit):")
                for d in handoff["decisions"]:
                    lines.append(f"  - {d}")

            if handoff.get("blockers"):
                lines.append("")
                lines.append("BLOCKERS:")
                for b in handoff["blockers"]:
                    lines.append(f"  - {b}")
        else:
            lines.append("")
            lines.append("No previous handoff context. Starting fresh.")

        lines.append("")
        lines.append(
            "When stopping, update the handoff with: "
            "work handoff <task_id> --state '...' --next '...'"
        )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Standalone convenience function (backward compatibility)
# ---------------------------------------------------------------------------

def detect_project_from_cwd(adapter: WorkAdapterProtocol,
                            cwd: str | None = None) -> str | None:
    """Detect the active project from the working directory.

    Convenience wrapper around ProjectContext for call sites that
    don't need to hold a reference to the context object.

    Args:
        adapter: A WorkAdapter instance (or anything matching the protocol).
        cwd: Override for the working directory.

    Returns:
        The project ID, or None.
    """
    return ProjectContext(adapter).detect_from_cwd(cwd)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _project_prefix(project_id: str | None) -> str:
    """Derive short prefix from project ID.

    aos   -> aos
    chief -> chief
    None  -> t  (unaffiliated)
    """
    if not project_id:
        return "t"
    clean = re.sub(r"[-_]v\d+$", "", project_id)  # aos-v2 -> aos
    return clean


def _task_to_dict(task: Any) -> dict:
    """Convert a Task dataclass (or similar) to the dict format the resolver expects."""
    d: dict[str, Any] = {
        "id": task.id,
        "title": task.title,
    }
    if hasattr(task, "project") and task.project:
        d["project"] = task.project
    if hasattr(task, "status"):
        status = task.status
        d["status"] = status.value if hasattr(status, "value") else str(status)
    if hasattr(task, "priority"):
        priority = task.priority
        d["priority"] = priority.value if hasattr(priority, "value") else int(priority)
    if hasattr(task, "handoff") and task.handoff:
        ho = task.handoff
        handoff_dict: dict[str, Any] = {
            "state": ho.state,
        }
        if ho.next_step:
            handoff_dict["next_step"] = ho.next_step
        if ho.files:
            handoff_dict["files_touched"] = ho.files
        if ho.decisions:
            handoff_dict["decisions"] = ho.decisions
        if ho.blockers:
            handoff_dict["blockers"] = ho.blockers
        d["handoff"] = handoff_dict
    return d
