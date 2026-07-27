"""
Project Brief Compiler — a project record you never have to maintain.

A project row is something someone typed once; it goes stale the moment work
starts. A *brief* is compiled from every signal that already touched the
project — tasks, handoffs, sessions, git, vault docs, decisions — and
recompiled whenever work happens. Precedent: ``core/engine/people/profile.py``
compiles a person profile from SQL; projects get the same treatment.

Rules this module holds itself to (BRIEF-CONTRACT.md § "Hard constraints"):

1. **No LLM calls.** Every sentence here is a template over structured signal.
   The one non-deterministic field, ``narrative``, is written by an agent
   elsewhere and merely *preserved* by this module across recompiles.
2. **Every claim cites a source.** Anything emitted traces to a row or a file
   that was actually read, and that file lands in ``brief.sources``.
3. **Fast** — under 300ms warm, because it runs on every task mutation. That
   is why reads go straight to SQLite instead of through ``backend.py``'s
   object layer, which does per-task session lookups (≈3s for one project).
   The reads are strictly read-only; no engine file is touched.
4. **Degrades.** No repo, no docs, no sessions, no project — each just drops
   its section. A compile never raises for missing data.

Reading order below: gather → derive → detect → assemble → store/render.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from brief_types import (  # noqa: E402
    Actor,
    Artifact,
    Blocker,
    Conflict,
    Event,
    NextItem,
    Phase,
    ProjectBrief,
)

try:  # the attribution workstream owns actor.py; the compiler works without it
    from actor import ATTRIBUTION_FIX_AT  # type: ignore
    from actor import actor_to_dict as _actor_to_dict  # type: ignore
    from actor import describe as _describe  # type: ignore
    from actor import is_suspect_operator_row as _is_suspect_row  # type: ignore
except Exception:  # pragma: no cover - exercised only when actor.py is absent
    # Kept in step with actor.py, which is the definition. Duplicated only so
    # a missing actor.py degrades the brief rather than breaking it.
    ATTRIBUTION_FIX_AT = "2026-07-26T16:11:00"

    def _is_suspect_row(actor_str, actor_type, timestamp):
        return ((actor_str or "") == "operator"
                and (actor_type or "operator") == "operator"
                and str(timestamp or "") < ATTRIBUTION_FIX_AT)

    def _actor_to_dict(a):
        return {
            "kind": getattr(a, "kind", "unknown") or "unknown",
            "name": getattr(a, "name", "unknown") or "unknown",
            "session_id": getattr(a, "session_id", None),
            "at": getattr(a, "at", "") or "",
        }

    def _describe(actor, verb, subject):
        name = getattr(actor, "name", "unknown") or "unknown"
        kind = getattr(actor, "kind", "unknown")
        who = "You" if kind == "operator" else ("Someone" if kind == "unknown"
                                                else name[:1].upper() + name[1:])
        return f'{who} {verb} "{subject}"' if subject else f"{who} {verb}"


__all__ = [
    "compile_brief",
    "compile_all",
    "load_brief",
    "render_markdown",
    "set_narrative",
    "brief_to_dict",
    "brief_from_dict",
    "BRIEF_DIR",
]

# ── Locations ───────────────────────────────────────────────────────────

BRIEF_DIR = Path.home() / ".aos" / "data" / "project-briefs"
VAULT = Path.home() / "vault"
PROJECT_ROOT = Path.home() / "project"

MAX_EVENTS = 20
MAX_ARTIFACTS = 40
MAX_TAGS = 6
MAX_NEXT = 3
MAX_CONFLICTS_PER_KIND = 5
MAX_SESSION_ARTIFACTS = 8       # sessions must not crowd out commits and files
MAX_STRUCTURAL_TAGS = 4         # leave room in the six for real topic tags
PAIRS_PER_GROUP = 3             # duplicate pairs surfaced per subtree pairing
MAX_INFERRED_PHASES = 12        # above this, an inferred grouping is noise

# The real command that fixes an untracked repo. A brief that prints a command
# which does not exist is exactly the confident-but-wrong prose this compiler
# is supposed to prevent, so this string is pinned to cli.py by a test.
UNTRACKED_REPO_FIX = "work projects path"
LOOSE_GROUP = "(loose)"         # the bucket for childless top-level tasks
SESSION_SCAN_LIMIT = 200        # newest N session exports scanned for frontmatter
RECENT_SESSION_SCAN = 200       # newest N session rows examined for task links

MOVING_DAYS = 3
WARM_DAYS = 10
STALE_DOC_DAYS = 7

OPEN_STATUSES = ("todo", "active", "waiting", "inbox")


# ── Small utilities ─────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value) -> datetime | None:
    """Best-effort ISO8601 → datetime. Never raises."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for candidate in (text, text[:19], text[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            continue
    return None


def _max_iso(*values) -> str | None:
    """The latest of a set of ISO strings, ignoring anything unparseable."""
    best_dt, best_raw = None, None
    for v in values:
        dt = _parse_dt(v)
        if dt and (best_dt is None or dt > best_dt):
            best_dt, best_raw = dt, v
    return best_raw


def _ago(value) -> str:
    """'2 hours ago' / 'yesterday' / 'Jul 13'. Plain English, no false precision."""
    dt = _parse_dt(value)
    if dt is None:
        return "at an unknown time"
    delta = datetime.now() - dt
    secs = delta.total_seconds()
    if secs < 0:
        return "just now"
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} minutes ago"
    if secs < 86400:
        hours = int(secs // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(secs // 86400)
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    return dt.strftime("%b %-d") if os.name != "nt" else dt.strftime("%b %d")


def _days_since(value) -> float | None:
    dt = _parse_dt(value)
    if dt is None:
        return None
    return (datetime.now() - dt).total_seconds() / 86400.0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _short(text: str, limit: int = 70) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_loads(raw, default):
    if not raw:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        value = json.loads(raw)
    except Exception:
        return default
    return value if isinstance(value, type(default)) else default


# ── Layer 1: gather ─────────────────────────────────────────────────────
#
# Everything below reads. Nothing writes. Each reader returns empty on any
# failure and records what it consulted so `brief.sources` stays honest.

def _work_db_path() -> Path:
    """The work DB, resolved the same way backend.py resolves it."""
    env = os.environ.get("AOS_WORK_DB")
    if env:
        return Path(env).expanduser()
    try:
        import backend as _backend  # type: ignore
        return Path(_backend._resolve_db_path())
    except Exception:
        pass
    try:
        from core.engine.work import backend as _backend2  # type: ignore
        return Path(_backend2._resolve_db_path())
    except Exception:
        pass
    work_db = Path.home() / ".aos" / "data" / "work.db"
    return work_db if work_db.exists() else Path.home() / ".aos" / "data" / "qareen.db"


def _session_db_path() -> Path | None:
    """Where sessions/session_tasks live.

    They are Qareen-owned until aos#131, so after the work.db cutover they are
    only in qareen.db. When AOS_WORK_DB is injected (tests) we never escape to
    the real instance DB — same rule the adapter follows.
    """
    if os.environ.get("AOS_WORK_DB"):
        return None
    path = Path.home() / ".aos" / "data" / "qareen.db"
    return path if path.exists() else None


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    try:
        if not path or not Path(path).exists():
            return None
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _read_project_row(conn, project_id: str) -> dict | None:
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None and "short_id" in _columns(conn, "projects"):
            row = conn.execute(
                "SELECT * FROM projects WHERE short_id = ?", (project_id,)
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _read_tasks(conn, project_id: str) -> list[dict]:
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ?", (project_id,)
        ).fetchall()
    except Exception:
        return []
    tasks = []
    for row in rows:
        t = dict(row)
        t["tags"] = _json_loads(t.get("tags"), [])
        t["fields"] = _json_loads(t.get("fields"), {})
        tasks.append(t)
    tasks.sort(key=_task_sort_key)
    return tasks


def _task_sort_key(task: dict):
    """Natural order for scoped ids: hre#1, hre#1.2, hre#1.10, hre#2."""
    parts = []
    for chunk in re.split(r"[#.]", str(task.get("id") or "")):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return parts


def _read_handoffs(conn, task_ids: set[str]) -> dict[str, dict]:
    if conn is None or not task_ids:
        return {}
    out = {}
    try:
        for row in conn.execute("SELECT * FROM task_handoffs"):
            d = dict(row)
            if d.get("task_id") in task_ids:
                d["blockers"] = _json_loads(d.get("blockers"), [])
                d["decisions"] = _json_loads(d.get("decisions"), [])
                d["files"] = _json_loads(d.get("files"), [])
                out[d["task_id"]] = d
    except Exception:
        return {}
    return out


def _scoped_rows(conn, sql: str, id_column: str, task_ids: set[str],
                 order: str, limit: int) -> list[dict]:
    """Rows for exactly these task ids, filtered in SQL rather than in Python.

    Filtering a capped "newest N rows overall" list in Python silently loses a
    project whose activity is older than the cap — the same class of bug as an
    unscoped session list, just quieter, because it degrades to an empty
    section instead of a wrong one.
    """
    if conn is None or not task_ids:
        return []
    ids = sorted(task_ids)
    joiner = "AND" if " WHERE " in f" {sql} " else "WHERE"
    rows: list = []
    try:
        for start in range(0, len(ids), 400):     # SQLite caps bound variables
            chunk = ids[start:start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows += conn.execute(
                f"{sql} {joiner} {id_column} IN ({placeholders}) "
                f"ORDER BY {order} DESC LIMIT ?", chunk + [limit]).fetchall()
    except Exception:
        return []
    rows.sort(key=lambda r: str(r[order] or ""), reverse=True)
    return [dict(r) for r in rows[:limit]]


def _move_aliases(conn, task_ids: set[str]) -> dict[str, str]:
    """``{old_id: current_id}`` for tasks that were re-identified by a move.

    ``work move`` re-IDs a task and entity_history keys on entity_id, so a
    moved task's entire past sits under an id nobody queries — it silently
    reads as unattributed. The ``moved_from`` row written at move time is the
    bridge; this walks it backwards so history follows the task.
    """
    alias: dict[str, str] = {}
    frontier = set(task_ids)
    for _ in range(5):                     # chains are short; bound them anyway
        if not frontier:
            break
        rows = _scoped_rows(
            conn, "SELECT entity_id, old_value, timestamp FROM entity_history "
                  "WHERE field_name = 'moved_from' AND old_value IS NOT NULL",
            "entity_id", frontier, "timestamp", 500)
        nxt = set()
        for row in rows:
            old, current = row.get("old_value"), row.get("entity_id")
            if old and old not in alias and old not in task_ids:
                alias[old] = alias.get(current, current)
                nxt.add(old)
        frontier = nxt
    return alias


def _read_history(conn, task_ids: set[str], limit: int = 400) -> list[dict]:
    """The attribution spine of the timeline, following tasks across moves."""
    alias = _move_aliases(conn, task_ids)
    rows = _scoped_rows(
        conn, "SELECT * FROM entity_history", "entity_id",
        task_ids | set(alias), "timestamp", limit)
    for row in rows:                       # re-key history onto the current id
        row["entity_id"] = alias.get(row["entity_id"], row["entity_id"])
    return rows


def _read_sessions(task_ids: set[str], project_id: str, limit: int = 40) -> list[dict]:
    """Sessions linked to this project's tasks, newest first.

    Each row carries ``matched_task``: the task of *this* project that caused
    the match, or None when the session matched on ``project_id`` alone. That
    is the only task id callers may name. A session's own ``sessions.task_id``
    is its primary task, which frequently belongs to a different project —
    naming it would put a foreign task's title inside this brief.

    Related: ``backend.get_task(id)["sessions"]`` is known to be unscoped
    (~3,945 sessions for a single task). Nothing here goes through it; the
    scoping is done explicitly below and asserted in the tests.
    """
    conn = _connect_ro(_session_db_path()) if _session_db_path() else None
    if conn is None:
        return []
    # session_tasks holds 116k rows and is indexed only by session_id, so
    # querying it by task_id costs ~180ms — the whole compile budget for one
    # section. Three cheap index-friendly queries instead:
    #   a) the newest RECENT_SESSION_SCAN sessions, then their links by
    #      session_id (the primary key, so each probe is a lookup);
    #   b) sessions whose own task_id is ours (idx_sessions_task);
    #   c) sessions stamped with the project id.
    # (a) is what makes recent agent work visible; (b) and (c) catch older
    # links regardless of recency.
    columns = "id, started_at, ended_at, project_id, transcript_summary, task_id"
    ids = sorted(task_ids)
    rows: list = []
    try:
        recent = conn.execute(
            f"SELECT {columns} FROM sessions ORDER BY started_at DESC LIMIT ?",
            (RECENT_SESSION_SCAN,)).fetchall()
        links: dict[str, set[str]] = defaultdict(set)
        session_ids = [r["id"] for r in recent]
        for start in range(0, len(session_ids), 400):
            chunk = session_ids[start:start + 400]
            placeholders = ",".join("?" * len(chunk))
            for link in conn.execute(
                    "SELECT session_id, task_id FROM session_tasks "
                    f"WHERE session_id IN ({placeholders})", chunk):
                links[link["session_id"]].add(link["task_id"])
        for row in recent:
            ours = links[row["id"]] & task_ids
            if row["task_id"] in task_ids:
                rows.append((row, row["task_id"]))
            elif ours:
                rows.append((row, sorted(ours)[0]))
            elif row["project_id"] == project_id:
                rows.append((row, None))

        for start in range(0, len(ids), 400):    # SQLite caps bound variables
            chunk = ids[start:start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows += [(r, r["task_id"]) for r in conn.execute(
                f"SELECT {columns} FROM sessions WHERE task_id IN ({placeholders}) "
                f"ORDER BY started_at DESC LIMIT ?", chunk + [limit])]
        rows += [(r, None) for r in conn.execute(
            f"SELECT {columns} FROM sessions WHERE project_id = ? "
            f"ORDER BY started_at DESC LIMIT ?", (project_id, limit))]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    seen, out = set(), []
    for row, matched in sorted(rows, key=lambda p: str(p[0]["started_at"] or ""),
                               reverse=True):
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        d = dict(row)
        # Never let a foreign task id escape into the brief.
        d["matched_task"] = matched if matched in task_ids else None
        out.append(d)
        if len(out) >= limit:
            break
    return out


def _read_goal_title(conn, goal_id: str | None) -> str | None:
    if conn is None or not goal_id:
        return None
    try:
        row = conn.execute("SELECT title FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return row["title"] if row else None
    except Exception:
        return None


# ── Vault reading (cached by directory mtime) ───────────────────────────

_FM_CACHE: dict[str, tuple[float, list[dict]]] = {}
_DOC_CACHE: dict[tuple, tuple[float, list]] = {}      # enrich.find_project_docs


def _parse_frontmatter(text: str) -> dict:
    """A deliberately small YAML-frontmatter reader.

    Only the scalar/list forms the vault actually uses. Not a YAML parser —
    it must never raise, and a field it cannot read is simply absent.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.startswith("#") or line.startswith(" "):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value.startswith("[") and value.endswith("]"):
            out[key] = [v.strip().strip('"').strip("'")
                        for v in value[1:-1].split(",") if v.strip()]
        elif value:
            out[key] = value
    return out


# Any leading tag means HTML. Listing specific tags was too narrow: the HRE
# decks open with <title>, and their megabyte of base64 font pushes </html> far
# past any sane lookahead — so both the tag-list and the closing-tag check
# missed, and the file fell through to the markdown path.
_HTMLISH = re.compile(r"^\s*<[a-z!/]", re.I)
# A line of CSS rarely starts with '<', so the markdown skip-list waves it
# through: the HRE decks were being previewed as
# "@font-face{font-family:'Kitab';...base64,d09GMg...". Recognise the shapes.
_CSS_NOISE = re.compile(
    r"^\s*(@(font-face|media|import|charset|keyframes)\b"      # at-rules
    r"|--[a-z0-9-]+\s*:"                                        # custom props
    r"|[.#]?[a-z0-9_\-\[\]='\"., >:()]+\s*\{"                   # selectors
    r"|[a-z-]+\s*:\s*[^;]+;\s*\}?$"                             # declarations
    r"|\}|\*/|/\*)", re.I,
)


def _html_excerpt(text: str) -> str | None:
    """Readable text from an HTML document — never its stylesheet.

    Order: <title>, then the first heading, then the first real paragraph.
    <style> and <script> bodies are removed before anything is considered.
    """
    cleaned = re.sub(r"<(style|script)\b.*?</\1>", " ", text,
                     flags=re.S | re.I)
    m = re.search(r"<title[^>]*>(.*?)</title>", cleaned, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if len(title) > 3:
            return _short(title, 160)
    for pattern in (r"<h[1-3][^>]*>(.*?)</h[1-3]>", r"<p[^>]*>(.*?)</p>"):
        for hit in re.finditer(pattern, cleaned, re.S | re.I):
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", hit.group(1))).strip()
            if len(plain) > 12:
                return _short(plain, 160)
    return None


def _first_prose_line(text: str) -> str | None:
    """The first line that is neither frontmatter, heading, nor decoration."""
    body = text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:]

    if _HTMLISH.match(body) or "</html>" in body[:4000].lower():
        return _html_excerpt(body)

    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "---", "|", "```", ">", "!", "<")):
            continue
        if _CSS_NOISE.match(line) or "base64," in line:
            continue
        line = re.sub(r"\*\*|__|\*|`", "", line)
        if len(line) > 12:
            return _short(line, 160)
    return None


def _scan_docs(directory: Path, limit: int | None = None,
               newest_first: bool = False) -> list[dict]:
    """Frontmatter + first prose line for every markdown/html doc in a directory.

    Cached per directory and invalidated by the directory's mtime, so a
    ``compile_all()`` over eleven projects reads the vault once, not eleven
    times.
    """
    key = f"{directory}|{limit}|{newest_first}"
    try:
        stamp = directory.stat().st_mtime
    except Exception:
        return []
    cached = _FM_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]

    try:
        paths = [p for p in directory.iterdir()
                 if p.is_file() and p.suffix.lower() in (".md", ".html")]
    except Exception:
        return []
    paths.sort(key=lambda p: p.name, reverse=newest_first)
    if limit:
        paths = paths[:limit]

    docs = []
    for path in paths:
        try:
            head = path.read_text(errors="ignore")[:4000]
        except Exception:
            continue
        docs.append({
            "path": path,
            "name": path.name,
            "slug": path.stem,
            "fm": _parse_frontmatter(head),
            "excerpt": _first_prose_line(head),
        })
    _FM_CACHE[key] = (stamp, docs)
    return docs


def _vault_rel(path: Path) -> str:
    try:
        return "vault/" + str(path.relative_to(VAULT))
    except Exception:
        return str(path)


# ── Git ─────────────────────────────────────────────────────────────────

# Spawning git costs 50–120ms per repo, and a project with five nested repos
# pays it five times on every task mutation. Results are cached for a few
# seconds: the recompile-on-mutation path fires repeatedly within that window,
# and a commit timestamp lagging by seconds is immaterial against a state
# machine whose finest bucket is three days.
GIT_CACHE_TTL = 10.0
_GIT_CACHE: dict[tuple, tuple[float, list]] = {}


def _git_log(repo_path: str | None, limit: int = 20, *,
             label: str = "") -> list[dict]:
    """Recent commits. ``label`` names the repo they came from ("" = the root).

    A commit must never be presented as belonging to a repo it isn't in, so
    the label travels with every row and the artifact path carries the repo
    that actually holds the sha.
    """
    if not repo_path:
        return []
    root = Path(repo_path).expanduser()
    if not (root / ".git").exists():
        return []

    key = (str(root), limit, label)
    cached = _GIT_CACHE.get(key)
    if cached and (time.monotonic() - cached[0]) < GIT_CACHE_TTL:
        # A copy, always: callers concatenate nested-repo commits onto this
        # list, and handing out the cached object let that mutation accumulate
        # into the cache itself — every recompile duplicated the nested rows.
        return list(cached[1])

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", f"-{limit}",
             "--format=%H%x09%aI%x09%an%x09%s"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        commits.append({"sha": parts[0], "at": parts[1],
                        "author": parts[2], "subject": parts[3],
                        "repo": label, "repo_path": str(root)})
    _GIT_CACHE[key] = (time.monotonic(), commits)
    return list(commits)


# A project's real history often lives in a repo *inside* the linked one: hre
# is checked in at ~/project/hre, but the Next.js app that is actually being
# shipped is a separate repo at hre/app with its own remote. Missing that is
# how the work system reported HRE as "0/6, not started" while an application
# was being built.
NESTED_REPO_MAX_DEPTH = 3
NESTED_REPO_MAX = 5             # report the nearest few, not every submodule
NESTED_SCAN_MAX_DIRS = 800      # a 359M content tree must not cost seconds
NESTED_COMMITS_EACH = 10

_SKIP_DIRS = frozenset({
    "node_modules", ".venv", "venv", "_archive", "archive", "__pycache__",
    ".next", ".nuxt", "dist", "build", "out", "target", ".cache", "vendor",
    "Pods", "DerivedData", ".build", ".tox", "site-packages", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".terraform", ".gradle",
})


def _nested_repos(repo_path: str | None) -> list[tuple[str, Path]]:
    """``(label, path)`` for git repos *beneath* ``repo_path``, nearest first.

    Bounded on every axis — depth, directory count, and result count — because
    this runs on every task mutation over trees that can be hundreds of
    megabytes. A directory that cannot be read is skipped, not guessed at.
    """
    if not repo_path:
        return []
    root = Path(repo_path).expanduser()
    if not root.is_dir():
        return []

    found: list[tuple[str, Path]] = []
    frontier = [(root, 0)]
    scanned = 0
    while frontier and len(found) < NESTED_REPO_MAX:
        current, depth = frontier.pop(0)
        if depth >= NESTED_REPO_MAX_DEPTH or scanned >= NESTED_SCAN_MAX_DIRS:
            continue
        try:
            entries = list(os.scandir(current))
        except Exception:
            continue                      # unreadable: drop it silently
        scanned += 1
        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name
            if name in _SKIP_DIRS or (name.startswith(".") and name != ".git"):
                continue
            child = Path(entry.path)
            if (child / ".git").exists():
                found.append((str(child.relative_to(root)), child))
                continue                  # a repo's own contents are its business
            frontier.append((child, depth + 1))
    return found[:NESTED_REPO_MAX]


_REMOTE_URL_RE = re.compile(r"^\s*url\s*=\s*(\S+)", re.M)


def _git_remote(repo_path: Path) -> str | None:
    """The origin URL, normalised to something a person can click.

    Read straight out of ``.git/config`` rather than by spawning git — this
    runs once per nested repo per compile, and a subprocess costs more than
    the whole rest of the section.
    """
    config = Path(repo_path) / ".git" / "config"
    try:
        text = config.read_text(errors="ignore")
    except Exception:
        return None                       # unreadable: say nothing
    section = None
    url = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped
            continue
        if section and section.startswith("[remote") and "url" in stripped:
            match = _REMOTE_URL_RE.match(line)
            if match:
                url = match.group(1)
                if '"origin"' in section:
                    break                 # origin wins; anything else is a fallback
    if not url:
        return None
    if url.startswith("git@") and ":" in url:            # git@host:owner/repo.git
        host, _, path = url[4:].partition(":")
        url = f"https://{host}/{path}"
    return url[:-4] if url.endswith(".git") else url


# ── Layer 2: title similarity (for duplicate_spine) ─────────────────────
#
# Deliberately generic: no project, task id, or phrase is special-cased. The
# metric is IDF-weighted token overlap *within one project*, which is what
# makes a term like "velocity" count for more than a term like "build".

_STOPWORDS = frozenset("""
about across added adding after also always another any anything are around
because been before being best better both build building built came can cant
come coming could couldnt current currently did didnt does doesnt doing done
during each else etc even ever every everything few finish finished first fix
fixed fixing for from full get gets getting give given going gone good got
had has have having here how however into isnt itself its just keep kept know
known last later least less let lets like make makes making many may maybe
might more most much must need needed needs never new next nothing now off
once only onto open other others our out over own part parts per phase phases
put ran real really run running said same say says second see seen set sets
setting several shall she should shouldnt show showing shows since some
something soon start started starting states step steps still stop stopped
such take taken takes than that the their them then there these they thing
things this those though three through thus time times too took toward turn
turned two under until upon use used uses using very want wants was way ways
well went were what when where whether which while who whom whose why will
with within without wont work worked working works would wouldnt yet you your
task tasks item items todo wire wired wires ship shipped shipping add adds
""".split())

_WORD_RE = re.compile(r"[a-z0-9]+")
_ORDINAL_PREFIX = re.compile(
    r"^\s*(part|phase|step|stage|milestone|wave|track|round|sprint)"
    r"\s*[0-9a-z]{0,3}\s*[:\-—.]\s*", re.I)
_NAMESPACE_PREFIX = re.compile(r"^\s*[a-z][a-z0-9\-]{1,14}\s*:\s*", re.I)


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("es") and not word.endswith("ses"):
        return word[:-2]
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    if len(word) > 6 and word.endswith("ing"):
        return word[:-3]
    return word


def _title_tokens(title: str) -> set[str]:
    """Content tokens of a task title, with structural prefixes stripped.

    ``"Part 2: The claim spine"`` and ``"HRE: build the claim spine"`` both
    reduce to their content words, so a shared decomposition shows through the
    different naming conventions two planning passes used.
    """
    text = (title or "").lower()
    text = _ORDINAL_PREFIX.sub(" ", text)
    text = _NAMESPACE_PREFIX.sub(" ", text)
    tokens = set()
    for raw in _WORD_RE.findall(text):
        if raw.isdigit() or len(raw) < 4:
            continue
        word = _stem(raw)
        if len(word) < 4 or word in _STOPWORDS:
            continue
        tokens.add(word)
    return tokens


def _subtree_root(task: dict) -> str:
    """The top-level ancestor id — the subtree a task belongs to."""
    tid = str(task.get("parent_id") or task.get("id") or "")
    return tid.split(".")[0]


# ── Layer 3: derivation ─────────────────────────────────────────────────

def _project_meta(description: str | None) -> dict:
    """``"appetite:2-weeks, initiative:auto-tracker"`` → a dict.

    That comma-separated string is how ``add_project`` stores fields with no
    column of their own, so this is a reader for an existing convention.
    """
    meta = {}
    for part in (description or "").split(","):
        key, sep, value = part.partition(":")
        if sep and key.strip() and value.strip():
            meta[key.strip()] = value.strip()
    return meta


def _derive_counts(tasks: list[dict]) -> dict:
    counts = {"task_count": len(tasks), "done_count": 0, "active_count": 0,
              "todo_count": 0, "waiting_count": 0}
    for t in tasks:
        status = t.get("status")
        if status == "done":
            counts["done_count"] += 1
        elif status == "active":
            counts["active_count"] += 1
        elif status == "todo":
            counts["todo_count"] += 1
        elif status == "waiting":
            counts["waiting_count"] += 1
    total = counts["task_count"]
    counts["pct"] = int(round(counts["done_count"] * 100 / total)) if total else 0
    return counts


def _derive_last_activity(tasks, handoffs, sessions, commits) -> tuple[str | None, str]:
    """The newest timestamp across every signal, and which signal it came from."""
    best: tuple[datetime, str, str] | None = None

    def offer(value, source):
        nonlocal best
        dt = _parse_dt(value)
        if dt and (best is None or dt > best[0]):
            best = (dt, str(value), source)

    for t in tasks:
        offer(t.get("completed_at"), "task")
        offer(t.get("started_at"), "task")
        offer(t.get("created_at"), "task")
    for h in handoffs.values():
        offer(h.get("timestamp"), "handoff")
    for s in sessions:
        offer(s.get("ended_at") or s.get("started_at"), "session")
    for c in commits:
        offer(c.get("at"), "git")

    if best is None:
        return None, "unknown"
    return best[1], best[2]


def _derive_state(project_row, counts, last_activity, last_source,
                  blockers, tasks) -> tuple[str, str]:
    """State + a plain-English reason. Strict order, first match wins."""
    total = counts["task_count"]
    done = counts["done_count"]
    active = counts["active_count"]
    status = (project_row or {}).get("status")

    if status == "completed":
        return "done", "the project record is marked completed"

    # A cancelled project is not "about to start". The state vocabulary the
    # contract locks has no `cancelled` member, so the state stays in-vocabulary
    # and the reason carries the fact — reporting a gap beats inventing a
    # seventh state that the API and UI do not handle.
    if status == "cancelled":
        moved = " and its tasks were moved elsewhere" if total == 0 else ""
        return "cold", f"the project record is marked cancelled{moved}"
    if total and counts["pct"] == 100:
        return "done", f"all {_plural(total, 'task')} are done"

    if blockers:
        first = blockers[0]
        extra = f" (and {len(blockers) - 1} more)" if len(blockers) > 1 else ""
        return "blocked", (f'"{_short(first.title, 50)}" is waiting on '
                           f"{_short(first.blocked_on, 60)}{extra}")

    if done == 0 and active == 0:
        if total == 0:
            return "not_started", "no tasks have been filed yet"
        created = _max_iso(*[t.get("created_at") for t in tasks])
        return "not_started", (f"{_plural(total, 'task')} created "
                               f"{_ago(created)}, none started")

    days = _days_since(last_activity)
    if days is None:
        return "cold", "no dated activity on any signal"
    if days <= MOVING_DAYS:
        return "moving", f"last {last_source} activity {_ago(last_activity)}"
    if days <= WARM_DAYS:
        return "warm", f"last {last_source} activity {_ago(last_activity)}"
    return "cold", f"no activity in {int(days)} days"


def _derive_tags(tasks, docs, conflicts, repo_path) -> list[str]:
    """Derived, never hand-applied. Union of task tags, doc tags, structural tags.

    Structural tags come first inside the cap of six. They are the ones the
    compiler *knows* are true of the project right now, whereas an inherited
    doc tag like ``#education`` is background — and a topic tag crowding out
    ``#blocked`` would be the wrong trade in a six-slot budget.
    """
    kinds = {c.kind for c in conflicts}
    structural: list[str] = []
    if tasks and all(t.get("status") not in ("done", "active") for t in tasks):
        structural.append("not-started")
    if any(t.get("status") == "waiting" for t in tasks):
        structural.append("blocked")
    if "stale_doc" in kinds:
        structural.append("stale-doc")
    if "untracked_repo" in kinds or not repo_path:
        structural.append("no-repo")
    if kinds & {"duplicate_spine", "status_disagreement"}:
        structural.append("needs-decision")
    structural = structural[:MAX_STRUCTURAL_TAGS]

    freq: dict[str, int] = defaultdict(int)

    def add(raw):
        tag = re.sub(r"[^a-z0-9]+", "-", str(raw).strip().lower()).strip("-")
        if tag and len(tag) > 1 and tag not in structural:
            freq[tag] += 1

    for t in tasks:
        for tag in t.get("tags") or []:
            add(tag)
    for doc in docs:
        for tag in doc.get("fm", {}).get("tags") or []:
            add(tag)

    derived = [t for t, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]
    return (structural + derived)[:MAX_TAGS]


_PHASE_RE = re.compile(
    r"^\s*(?:part|phase|stage|step|milestone|wave|track)\s*"
    r"([0-9]+(?:\.[0-9]+)?|[A-Z])\s*[:\-—.]\s*(.+)$", re.I)


def _phase_sort_key(key: str):
    """Numbered phases in numeric order, then any named ones alphabetically."""
    match = re.match(r"^phase-([0-9]+)$", key)
    return (0, int(match.group(1)), "") if match else (1, 0, key)


def _derive_phases(tasks: list[dict]) -> list[Phase]:
    """Group the task tree into phases, in strict order of how explicit the
    grouping is.

    1. **Declared** — ``fields.phase`` on the task (``part-3``, ``foundation``).
       An explicit declaration always wins; nothing below is consulted.
    2. **Numbered titles** — ``Part 3: Content pipeline``. Still the operator's
       own decomposition, just written in the title instead of a column.
    3. **Parent tasks** — inferred from the tree shape, and the only tier that
       can be wrong. A 552-task project yields ~100 single-task "phases" this
       way, which is noise pretending to be structure, so an inferred grouping
       that fails the shape check is dropped entirely and the UI falls back to
       status grouping. Tiers 1 and 2 are never suppressed.
    """
    children = defaultdict(list)
    for t in tasks:
        if t.get("parent_id"):
            children[t["parent_id"]].append(t)

    phases: list[Phase] = []
    claimed: set[str] = set()

    declared: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        fields = t.get("fields") or {}
        value = fields.get("phase") if isinstance(fields, dict) else None
        if value:
            declared[str(value)].append(t)

    if declared:
        for value in sorted(declared, key=lambda v: _phase_sort_key(_phase_key(v))):
            members = declared[value]
            claimed.update(m["id"] for m in members)
            phases.append(_make_phase(_phase_key(value),
                                      _phase_label(value, members), members))
    else:
        numbered = [(t, _PHASE_RE.match(t.get("title") or "")) for t in tasks]
        numbered = [(t, m) for t, m in numbered if m]

        # Numbered titles only describe the *project* when their numbers are
        # unique across it. In aos they repeat — six different parents each
        # own a "Phase 1: …" — which means the numbering is per-parent and
        # says nothing about project order. Reading it as project structure
        # produced 100 phases with six colliding `phase-1` keys.
        keys = [f"phase-{m.group(1).lower()}" for _, m in numbered]
        if len(numbered) >= 2 and len(set(keys)) != len(keys):
            numbered = []

        if len(numbered) >= 2:
            for task, match in numbered:
                members = [task] + children.get(task["id"], [])
                members = [m for m in members if m["id"] not in claimed]
                if not members:
                    continue
                claimed.update(m["id"] for m in members)
                number, label = match.group(1), match.group(2).strip()
                phases.append(_make_phase(f"phase-{number.lower()}",
                                          f"Phase {number} — {label}", members))
        else:
            for task in tasks:
                if task.get("parent_id") or not children.get(task["id"]):
                    continue
                members = [task] + children[task["id"]]
                claimed.update(m["id"] for m in members)
                phases.append(_make_phase(_slug(task["id"]),
                                          _short(task.get("title") or task["id"], 60),
                                          members))
            if not _is_meaningful_structure(phases):
                return []

    loose = [t for t in tasks if t["id"] not in claimed]
    # A parent whose children were all claimed is a header, not work of its
    # own — it would otherwise show up as a phantom unphased task.
    loose = [t for t in loose if not (children.get(t["id"]) and
                                      all(c["id"] in claimed for c in children[t["id"]]))]
    if loose and phases:
        phases.append(_make_phase("unphased", "Not in any phase", loose))
    elif loose and not phases:
        phases.append(_make_phase("all", "All tasks", loose))
    # A phase whose every member was cancelled has nothing left to report.
    return [p for p in phases if p.total]


def _phase_key(value: str) -> str:
    """``"part-3"`` → ``"phase-3"`` so numbered phases sort and gate together."""
    match = re.match(r"^(?:part|phase|stage|step)[-_ ]?([0-9]+)$", str(value), re.I)
    return f"phase-{match.group(1)}" if match else _slug(value)


def _phase_label(value: str, members: list[dict]) -> str:
    """Name a declared phase from a member's own title where one supplies it.

    ``part-3`` alone says nothing a human wants to read; the task titled
    ``Part 3: Content to drill pipeline`` supplies the words, and using them
    means the label is quoted from the plan rather than invented here.
    """
    key = _phase_key(value)
    number = key[len("phase-"):] if key.startswith("phase-") else None
    for member in members:
        match = _PHASE_RE.match(member.get("title") or "")
        if match and (number is None or match.group(1).lower() == number):
            return f"Phase {match.group(1)} — {match.group(2).strip()}"
    if number is not None:
        return f"Phase {number}"
    return str(value).replace("-", " ").replace("_", " ").strip().capitalize()


def _is_meaningful_structure(phases: list[Phase]) -> bool:
    """Does an *inferred* grouping actually say anything?

    Too many buckets, or buckets that are mostly one task each, means the
    grouping is an artefact of the tree shape rather than a plan.
    """
    if not phases:
        return False
    if len(phases) > MAX_INFERRED_PHASES:
        return False
    sizes = sorted(p.total for p in phases)
    median = sizes[len(sizes) // 2]
    return median > 1


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "phase"


def _make_phase(key: str, label: str, members: list[dict]) -> Phase:
    # Cancelled work is not outstanding work. Counting it would leave every
    # phase permanently short of its total — hre's Phase 0 read "1/2" with
    # nothing left to do, because hre#5 was cancelled into hre#1.1.
    members = [m for m in members if m.get("status") != "cancelled"]
    done = sum(1 for m in members if m.get("status") == "done")
    total = len(members)
    if any(m.get("status") == "waiting" for m in members):
        state = "blocked"
    elif total and done == total:
        state = "done"
    elif done or any(m.get("status") == "active" for m in members):
        state = "in_progress"
    else:
        state = "not_started"
    return Phase(key=key, label=label,
                 task_ids=[m["id"] for m in members],
                 done=done, total=total, state=state)


def _derive_blockers(tasks, handoffs) -> list[Blocker]:
    """A blocker is a waiting task that says *what* it is waiting on.

    A bare ``waiting`` status with no note is not a blocker — it is an
    unexplained status, and inventing a reason for it would be exactly the
    confident-prose failure the contract forbids.
    """
    blockers = []
    for t in tasks:
        handoff = handoffs.get(t["id"]) or {}
        notes = [str(b) for b in (handoff.get("blockers") or []) if str(b).strip()]
        if t.get("status") == "waiting":
            reason = notes[0] if notes else _clean_note(t.get("description"))
            if reason:
                blockers.append(Blocker(
                    task_id=t["id"], title=t.get("title") or t["id"],
                    blocked_on=_short(reason, 140),
                    since=t.get("started_at") or handoff.get("timestamp")
                          or t.get("created_at")))
        elif notes and t.get("status") != "done":
            blockers.append(Blocker(
                task_id=t["id"], title=t.get("title") or t["id"],
                blocked_on=_short(notes[0], 140),
                since=handoff.get("timestamp")))
    return blockers


def _clean_note(description: str | None) -> str:
    text = re.sub(r"<!--.*?-->", "", description or "", flags=re.S)
    return " ".join(text.split())


def _derive_next_up(tasks, phases, handoffs) -> list[NextItem]:
    """1–3 things that can actually be picked up now.

    Dependency-aware in the two senses the data supports: a parent is not
    actionable while its children are open, and a numbered phase is not
    actionable while an earlier numbered phase is unfinished.
    """
    children = defaultdict(list)
    for t in tasks:
        if t.get("parent_id"):
            children[t["parent_id"]].append(t)

    # Position of each task in the numbered phase order, when there is one.
    order: dict[str, int] = {}
    for index, phase in enumerate(phases):
        if phase.key.startswith("phase-"):
            for tid in phase.task_ids:
                order[tid] = index
    earliest_open = min(
        (i for i, p in enumerate(phases)
         if p.key.startswith("phase-") and p.state != "done"),
        default=None)

    candidates = []
    for t in tasks:
        if t.get("status") not in ("todo", "active"):
            continue
        open_children = [c for c in children.get(t["id"], [])
                         if c.get("status") in OPEN_STATUSES]
        if open_children:
            continue                      # a parent is done when its children are
        position = order.get(t["id"])
        if (position is not None and earliest_open is not None
                and position > earliest_open):
            continue                      # a later phase waits on an earlier one
        candidates.append((t, position))

    def rank(item):
        task, position = item
        return (
            0 if task.get("status") == "active" else 1,
            position if position is not None else 99,
            task.get("priority") if task.get("priority") is not None else 3,
            str(task.get("created_at") or ""),
        )

    candidates.sort(key=rank)
    out: list[NextItem] = []
    for task, position in candidates[:MAX_NEXT]:
        out.append(NextItem(
            task_id=task["id"],
            title=task.get("title") or task["id"],
            why=_why_next(task, position, phases, earliest_open, handoffs),
            priority=task.get("priority") if task.get("priority") is not None else 3,
        ))
    return out


def _why_next(task, position, phases, earliest_open, handoffs) -> str:
    """Every clause here is checkable against a row that was read."""
    reasons = []
    if task.get("status") == "active":
        days = _days_since(task.get("started_at"))
        reasons.append(f"in progress for {int(days)} days" if days and days >= 1
                       else "in progress")
    if position is not None and earliest_open is not None and position == earliest_open:
        done_before = sum(1 for p in phases[:position] if p.state == "done")
        if done_before:
            reasons.append(f"earliest unfinished phase; {done_before} before it are done")
        else:
            reasons.append("earliest unfinished phase")
    priority = task.get("priority")
    if priority is not None and priority <= 2:
        reasons.append(f"priority P{priority}")
    if handoffs.get(task["id"], {}).get("next_step"):
        reasons.append("has a handoff with a next step")
    if not reasons:
        reasons.append("nothing blocks it")
    return "; ".join(reasons)


# ── Layer 4: conflict detection ─────────────────────────────────────────

def _detect_duplicate_spine(tasks: list[dict]) -> list[Conflict]:
    """Two plans for the same work, living side by side in one project.

    Generic by construction — no ids, titles, or projects are hard-coded:

    1. Tokenize every task title into content words and weight each word by
       its IDF *within this project*, so a word that shows up in two titles
       carries far more evidence than one that shows up in twenty.
    2. Score every pair of tasks that sit in **different top-level subtrees**
       by IDF-weighted overlap relative to the shorter title. A single shared
       word only counts when it is genuinely distinctive: present in exactly
       two of the project's titles and at least six characters long.
    3. Group the surviving pairs by the subtree pair they connect. A subtree
       pair with strong or repeated evidence is a duplicated spine — two
       decompositions covering the same ground.

    Reported strongest-first and capped, because a brief that lists thirty
    problems reports none.
    """
    PAIR_THRESHOLD = 0.30
    STRONG_PAIR = 0.75
    if len(tasks) < 2:
        return []

    tokens = {t["id"]: _title_tokens(t.get("title") or "") for t in tasks}
    df: dict[str, int] = defaultdict(int)
    for toks in tokens.values():
        for word in toks:
            df[word] += 1
    total = len(tasks)
    idf = {w: math.log(1 + total / c) for w, c in df.items()}

    by_id = {t["id"]: t for t in tasks}
    roots = {t["id"]: _subtree_root(t) for t in tasks}

    # Top-level tasks with no children are pooled into one bucket. Five loose
    # tasks that between them re-plan a spine are one competing plan, not five
    # unrelated coincidences — without the pool, that evidence never adds up.
    has_children = {r for r in roots.values()
                    if sum(1 for v in roots.values() if v == r) > 1}
    groups_of = {tid: (root if root in has_children else LOOSE_GROUP)
                 for tid, root in roots.items()}
    size: dict[str, int] = defaultdict(int)
    for group in groups_of.values():
        size[group] += 1

    # Candidate pairs come from an inverted index so the cost tracks shared
    # vocabulary, not n² — 552 tasks stays in single-digit milliseconds.
    postings: dict[str, list[str]] = defaultdict(list)
    for tid, toks in tokens.items():
        for word in toks:
            postings[word].append(tid)

    candidate_pairs = set()
    for word, ids in postings.items():
        if len(ids) > 40:            # a word that generic carries no evidence
            continue
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if roots[a] != roots[b]:
                    candidate_pairs.add((a, b) if a < b else (b, a))

    weight = {tid: sum(idf[w] for w in toks) for tid, toks in tokens.items()}
    scored = []
    for a, b in candidate_pairs:
        ta, tb = tokens[a], tokens[b]
        if len(ta) < 2 or len(tb) < 2:
            continue
        # Cancelling one of two duplicates *is* the resolution — hre#5 was
        # cancelled precisely because it had been folded into hre#1.1. Keeping
        # the warning would mean every resolved duplicate leaves a permanent
        # complaint behind.
        if "cancelled" in (by_id[a].get("status"), by_id[b].get("status")):
            continue
        shared = ta & tb
        if not shared:
            continue
        if len(shared) == 1:
            # One shared word is only evidence when it is genuinely
            # distinctive — used in exactly these two titles, long enough to
            # name a thing, and set against titles with enough other content
            # that the match is not simply half of a two-word title.
            word = next(iter(shared))
            if df[word] > 2 or len(word) < 6:
                continue
            if len(ta) < 3 or len(tb) < 3:
                continue
        score = sum(idf[w] for w in shared) / min(weight[a], weight[b])
        if score >= PAIR_THRESHOLD:
            scored.append((score, a, b, sorted(shared)))

    groups: dict[tuple[str, str], list] = defaultdict(list)
    for score, a, b, shared in scored:
        key = tuple(sorted((groups_of[a], groups_of[b])))
        groups[key].append((score, a, b, shared))

    ranked = []
    for (ga, gb), pairs in groups.items():
        pairs.sort(reverse=True)
        density = len(pairs) / max(1, min(size[ga], size[gb]))
        ranked.append((pairs[0][0], density, len(pairs), pairs))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)

    # One conflict per duplicated *pair*, because that is the unit the
    # operator acts on — but ordered by how strong the evidence is for the
    # subtree pair as a whole, so a single coincidence never outranks a
    # genuinely duplicated plan.
    conflicts = []
    for _, _, count, pairs in ranked:
        for index, (score, a, b, shared) in enumerate(pairs[:PAIRS_PER_GROUP]):
            if len(conflicts) >= MAX_CONFLICTS_PER_KIND:
                return conflicts
            ta, tb = by_id[a], by_id[b]
            overlap = ", ".join(f'"{w}"' for w in shared[:4])
            message = (
                f'{a} "{_short(ta.get("title") or "", 60)}" ({ta.get("status")}) and '
                f'{b} "{_short(tb.get("title") or "", 60)}" ({tb.get("status")}) '
                f"look like the same work — they share {overlap}."
            )
            refs = [a, b]
            if index == 0 and count > 1:
                extra = [pid for _, x, y, _ in pairs[1:4] for pid in (x, y)]
                refs += [r for r in dict.fromkeys(extra) if r not in refs]
                message += (f" {count} such pairs cross the same two subtrees; "
                            f"this looks like two plans for one piece of work.")
            conflicts.append(Conflict(
                kind="duplicate_spine",
                severity="error" if score >= STRONG_PAIR else "warn",
                message=message, refs=refs))
    return conflicts


def _enricher_conflicts(project_id, initiative, tasks, has_docs: bool) -> list[Conflict]:
    """Pass through the enricher's findings — ``status_disagreement``, ``no_body``.

    Those two kinds are the enricher workstream's to detect (it owns the
    task↔doc-section matching), so the compiler calls its read-only detectors
    rather than reimplementing them. Every path here is non-writing, and the
    whole section is optional: if ``enrich.py`` is absent or changes shape, the
    brief simply drops these conflicts instead of failing to compile.
    """
    if not has_docs:
        # Artifact discovery already walked the vault and found nothing that
        # belongs to this project, so there is no source doc to disagree with.
        return []
    try:
        import enrich  # type: ignore
    except Exception:
        return []

    # find_project_docs parses the vault from scratch (~350ms) — more than the
    # whole compile budget — so its result is cached here and invalidated by
    # the initiatives directory's mtime. Caching on this side keeps enrich.py,
    # which another workstream owns, untouched.
    key = (project_id, initiative)
    try:
        stamp = (VAULT / "knowledge" / "initiatives").stat().st_mtime
    except Exception:
        stamp = 0.0
    cached = _DOC_CACHE.get(key)
    if cached and cached[0] == stamp:
        docs = cached[1]
    else:
        try:
            docs = enrich.find_project_docs(project_id, initiative)
        except Exception:
            return []
        _DOC_CACHE[key] = (stamp, docs)
    if not docs:
        return []

    conflicts: list[Conflict] = []
    for task in tasks:
        if len(conflicts) >= MAX_CONFLICTS_PER_KIND * 2:
            break
        try:
            found = enrich.detect_status_disagreements(task, docs)
        except Exception:
            continue
        conflicts.extend(c for c in found if isinstance(c, Conflict))
    return conflicts[: MAX_CONFLICTS_PER_KIND * 2]


def _detect_orphan_tasks(tasks, all_task_ids) -> list[Conflict]:
    """A task pointing at a parent or a source doc that is not there."""
    conflicts = []
    known = set(all_task_ids)
    for t in tasks:
        parent = t.get("parent_id")
        if parent and parent not in known:
            conflicts.append(Conflict(
                kind="orphan_task", severity="error",
                message=(f'{t["id"]} "{_short(t.get("title") or "", 60)}" lists '
                         f"{parent} as its parent, but no such task exists."),
                refs=[t["id"], parent]))
            continue
        source_ref = _source_ref_of(t)
        if source_ref and not _source_ref_exists(source_ref):
            conflicts.append(Conflict(
                kind="orphan_task", severity="warn",
                message=(f'{t["id"]} "{_short(t.get("title") or "", 60)}" cites '
                         f"{source_ref}, which is not on disk."),
                refs=[t["id"], source_ref]))
    return conflicts[:MAX_CONFLICTS_PER_KIND]


def _source_ref_of(task: dict) -> str | None:
    match = re.search(r"source_ref:([^,\s>]+)", task.get("description") or "")
    return match.group(1) if match else None


def _source_ref_exists(source_ref: str) -> bool:
    if "://" in source_ref or source_ref.startswith("conversation/"):
        return True                          # not a filesystem claim
    raw = source_ref.split("#", 1)[0]
    for candidate in (Path(raw).expanduser(),
                      Path.home() / raw.lstrip("~/"),
                      Path.home() / raw):
        try:
            if candidate.exists():
                return True
        except Exception:
            continue
    return False


def _detect_stale_docs(docs, tasks) -> list[Conflict]:
    """A plan doc that still says 'shaping' while the work is already moving."""
    conflicts = []
    newest_task = _max_iso(*[t.get("created_at") for t in tasks])
    newest_dt = _parse_dt(newest_task)
    started = any(t.get("status") in ("active", "done") for t in tasks)

    for doc in docs:
        fm = doc.get("fm", {})
        status = str(fm.get("status", "")).lower()
        rel = _vault_rel(doc["path"])
        if started and status in ("shaping", "planning", "draft", "proposed"):
            conflicts.append(Conflict(
                kind="stale_doc", severity="warn",
                message=(f"{doc['name']} still says status: {status}, but the "
                         f"project already has started or completed tasks."),
                refs=[rel]))
            continue
        doc_dt = _parse_dt(fm.get("updated") or fm.get("date"))
        if doc_dt and newest_dt and (newest_dt - doc_dt).days > STALE_DOC_DAYS:
            conflicts.append(Conflict(
                kind="stale_doc", severity="warn",
                message=(f"{doc['name']} was last dated "
                         f"{doc_dt.strftime('%b %-d')} but tasks were filed as "
                         f"recently as {_ago(newest_task)} — the doc predates "
                         f"the plan by {(newest_dt - doc_dt).days} days."),
                refs=[rel]))
    return conflicts[:MAX_CONFLICTS_PER_KIND]


def _detect_untracked_repo(project_id, short_id, repo_path,
                           project_status=None) -> list[Conflict]:
    """A directory that is obviously this project's, with no path on the record."""
    if repo_path:
        return []
    if project_status in ("cancelled", "completed"):
        return []                    # a finished project needs no repo linked
    names = {n for n in (project_id, short_id) if n}
    try:
        entries = [p for p in PROJECT_ROOT.iterdir() if p.is_dir()]
    except Exception:
        return []
    matches = []
    for entry in entries:
        name = entry.name.lower()
        for candidate in names:
            if name == candidate or name.startswith(f"{candidate}-"):
                matches.append(entry)
                break
    if not matches:
        return []
    matches.sort(key=lambda p: (len(p.name), p.name))
    listed = ", ".join(str(p) for p in matches[:3])
    return [Conflict(
        kind="untracked_repo", severity="warn",
        message=(f"{listed} looks like this project's code, but the project "
                 f"record has no path — git history and files are missing from "
                 f"the brief. Set it with: {UNTRACKED_REPO_FIX} {project_id} "
                 f"{matches[0]}"),
        refs=[str(p) for p in matches[:3]])]


# ── Layer 5: artifacts and timeline ─────────────────────────────────────

def _doc_matches_project(doc, project_id, short_id, initiative) -> bool:
    fm = doc.get("fm", {})
    fm_project = str(fm.get("project", "")).strip().lower()
    names = {n.lower() for n in (project_id, short_id, initiative) if n}
    if fm_project and fm_project in names:
        return True
    slug = doc["slug"].lower()
    for name in names:
        if slug == name or slug.startswith(f"{name}-"):
            return True
    return False


_DOC_KINDS = {"initiative": "initiative", "decision": "decision",
              "spec": "spec", "council": "council"}


def _discover_artifacts(project_id, short_id, initiative, tasks, sessions,
                        commits, repo_path, sources,
                        nested_repos=()) -> tuple[list[Artifact], list[dict]]:
    """Everything this project produced, deduped by path. Order = the contract's."""
    artifacts: list[Artifact] = []
    seen: set[str] = set()
    matched_docs: list[dict] = []

    def push(kind, title, path, date=None, excerpt=None):
        if path in seen:
            return
        seen.add(path)
        artifacts.append(Artifact(kind=kind, title=title, path=path,
                                  date=date, excerpt=excerpt))

    # 1 + 2 — vault docs: initiatives, then decisions/councils.
    for directory in (VAULT / "knowledge" / "initiatives",
                      VAULT / "knowledge" / "decisions"):
        docs = _scan_docs(directory)
        if docs:
            sources.append(_vault_rel(directory) + "/")
        for doc in docs:
            if not _doc_matches_project(doc, project_id, short_id, initiative):
                continue
            fm = doc["fm"]
            kind = _DOC_KINDS.get(str(fm.get("type", "")).lower())
            if kind is None:
                kind = ("decision" if directory.name == "decisions"
                        else ("deck" if doc["path"].suffix == ".html" else "initiative"))
            if "council" in doc["slug"]:
                kind = "council"
            push(kind, str(fm.get("title") or doc["slug"]),
                 _vault_rel(doc["path"]), fm.get("date"), doc.get("excerpt"))
            matched_docs.append(doc)

    # 3 — sessions: linked ones first, then exports whose frontmatter names us.
    for session in sessions[:MAX_SESSION_ARTIFACTS]:
        push("session",
             _short(session.get("transcript_summary") or f"Session {session['id'][:8]}", 70),
             f"session:{session['id']}",
             (session.get("started_at") or "")[:10])
    exports = _scan_docs(VAULT / "log" / "sessions",
                         limit=SESSION_SCAN_LIMIT, newest_first=True)
    if exports:
        sources.append("vault/log/sessions/")
    names = {n.lower() for n in (project_id, short_id) if n}
    export_budget = MAX_SESSION_ARTIFACTS
    for doc in exports:
        if export_budget <= 0:
            break
        fm = doc["fm"]
        if str(fm.get("project", "")).strip().lower() not in names:
            continue
        push("session", str(fm.get("title") or doc["slug"]),
             _vault_rel(doc["path"]), fm.get("date"), doc.get("excerpt"))
        export_budget -= 1

    # 4 — commits. The path names the repo that actually holds the sha, so a
    # nested repo's commit is never filed under the outer one.
    for commit in commits[:20]:
        push("commit", commit["subject"],
             f"{commit.get('repo_path') or repo_path}@{commit['sha'][:8]}",
             commit["at"][:10],
             f"in {commit['repo']}/" if commit.get("repo") else None)

    # 4b — the nested repos themselves, with their remotes. The operator should
    # be able to see github.com/…/ahhs-quran from the project page.
    for label, path in nested_repos:
        remote = _git_remote(path)
        push("repo", f"{label}/ — {remote}" if remote else f"{label}/",
             str(path), None, remote)

    # 5 — top-level docs in the repo.
    if repo_path:
        root = Path(repo_path).expanduser()
        for name in ("README.md", "CLAUDE.md", "DESIGN.md", "SPEC.md",
                     "ARCHITECTURE.md", "CHANGELOG.md"):
            path = root / name
            try:
                if path.is_file():
                    push("file", name, str(path),
                         datetime.fromtimestamp(path.stat().st_mtime).date().isoformat(),
                         _first_prose_line(path.read_text(errors="ignore")[:2000]))
            except Exception:
                continue

    return artifacts[:MAX_ARTIFACTS], matched_docs


_ACTOR_KINDS = ("operator", "agent", "cron", "import", "unknown")


def _actor_name(raw: str | None) -> str:
    """``"agent:chief"`` → ``"chief"``. The kind lives in its own field."""
    name = (raw or "").strip()
    if ":" in name:
        head, _, tail = name.partition(":")
        if head.lower() in _ACTOR_KINDS + ("system",) and tail.strip():
            name = tail.strip()
    return name or "unknown"


# ``tasks.created_by`` is an *intake source*, not a person: 1,227 rows say
# "manual" and 679 say "subtask". None of that is evidence anybody in
# particular acted. Rendering "manual" as "You" would re-introduce at the
# render layer exactly the false attribution the attribution workstream
# exists to remove — so every source token below resolves to unattributed,
# and only a token that genuinely names an actor resolves to one.
_INTAKE_SOURCES = frozenset({
    "manual", "subtask", "initiative", "inbox",
    "api", "cli", "seed", "migration", "sync", "unknown", "",
})
_NAMED_ACTORS = {
    "operator": ("operator", "operator"),
    "cron": ("cron", "cron"),
}


def _created_actor(created_by: str | None, at: str) -> Actor:
    """Resolve an actor from a ``created_by`` token — usually to *unknown*."""
    source = (created_by or "").strip().lower()
    if source in _NAMED_ACTORS:
        kind, name = _NAMED_ACTORS[source]
    elif source in _INTAKE_SOURCES:
        kind, name = "unknown", "unknown"
    elif source.endswith("-import") or source.startswith("import"):
        kind, name = "import", source
    elif source:
        kind, name = "agent", _actor_name(source)
    else:
        kind, name = "unknown", "unknown"
    return Actor(kind=kind, name=name, at=at or "")


def _is_defaulted_operator(row: dict) -> bool:
    """Was this row's "operator" just the old default rather than a signature?

    Before ``ATTRIBUTION_FIX_AT`` the work adapter defaulted an unset actor to
    "operator", so such a row is indistinguishable from an unattributed agent
    mutation and is not evidence the human did anything. The rule itself lives
    in actor.py — this only feeds it a history row.
    """
    return _is_suspect_row(row.get("actor"), row.get("actor_type"),
                           row.get("timestamp"))


# What each timeline event expects to find in entity_history. Matching on the
# field as well as the timestamp matters: hre#3 has a title and a description
# change recorded in the same second, and attributing a status event to the
# actor of an unrelated field edit would be a coincidence dressed up as a fact.
_EVENT_FIELD = {
    "completed_by": ("status", "done"),
    "started_by": ("status", "active"),
    "created_by": ("created", None),
}


def _actor_for(task_id, at, history_index, tasks_by_id, field=None) -> Actor:
    """Who made a change, according to ``entity_history`` and nothing else.

    entity_history is the whole attribution story (the parallel
    ``fields.attribution`` blob was removed). Rows are matched on timestamp
    *and* field, preferring an exact field match; anything unmatched, and
    anything from the defaulted-operator era, is unattributed.
    """
    rows = [r for r in history_index.get(task_id, []) if r.get("timestamp") == at]
    if not rows:
        return Actor(kind="unknown", name="unknown", at=at or "")

    expected = _EVENT_FIELD.get(field or "")
    if expected:
        name, value = expected
        exact = [r for r in rows
                 if r.get("field_name") == name
                 and (value is None or r.get("new_value") == value)]
        rows = exact or rows

    row = rows[0]
    if _is_defaulted_operator(row):
        return Actor(kind="unknown", name="unknown", at=at or "")
    kind = row.get("actor_type") or "unknown"
    return Actor(kind=kind if kind in _ACTOR_KINDS else "unknown",
                 name=_actor_name(row.get("actor")),
                 session_id=row.get("session_id"), at=at or "")


BURST_WINDOW_MINUTES = 20
BURST_MIN = 3


def _collapse_creation_bursts(tasks) -> tuple[list[tuple], set[str]]:
    """Fold "13 subtasks filed one minute apart" into a single timeline row.

    Decomposing a plan produces a dozen creations inside a minute. Listing
    each one buries every other signal in a 20-row timeline, so a run of
    siblings created close together becomes one line — the shape the contract
    asks for: *"Advisor created 13 subtasks — Jul 25"*.
    """
    by_parent = defaultdict(list)
    for t in tasks:
        parent = t.get("parent_id")
        if parent and t.get("created_at"):
            by_parent[parent].append(t)

    bursts, members = [], set()
    window = timedelta(minutes=BURST_WINDOW_MINUTES)
    for parent, children in by_parent.items():
        children.sort(key=lambda c: str(c["created_at"]))
        run: list[dict] = []

        def flush(run):
            if len(run) < BURST_MIN:
                return
            newest = run[-1]
            bursts.append((newest["created_at"], parent, len(run),
                           _created_actor(newest.get("created_by"),
                                          newest["created_at"])))
            members.update(c["id"] for c in run)

        for child in children:
            at = _parse_dt(child["created_at"])
            if run and at and _parse_dt(run[-1]["created_at"]) and \
                    at - _parse_dt(run[-1]["created_at"]) <= window:
                run.append(child)
            else:
                flush(run)
                run = [child]
        flush(run)
    return bursts, members


def _build_timeline(tasks, handoffs, history, sessions,
                    commits, artifacts) -> list[Event]:
    """One merged, signed, newest-first timeline across every signal.

    Attribution comes from ``entity_history`` alone. ``task_activity`` is a
    narrative log whose ``actor`` column holds intake tokens like "subtask",
    and it carries no ``actor_type``; feeding it in meant guessing a kind from
    a source string, which is the fabrication this layer exists to stop.
    """
    tasks_by_id = {t["id"]: t for t in tasks}
    history_index = defaultdict(list)
    for row in history:
        history_index[row["entity_id"]].append(row)

    events: list[Event] = []
    bursts, burst_members = _collapse_creation_bursts(tasks)

    def add(at, kind, text, ref, actor):
        if at:
            events.append(Event(at=str(at), kind=kind, text=text, ref=ref, actor=actor))

    for t in tasks:
        title = _short(t.get("title") or t["id"], 60)
        if t.get("completed_at"):
            actor = _actor_for(t["id"], t["completed_at"], history_index,
                               tasks_by_id, field="completed_by")
            add(t["completed_at"], "task_done",
                _describe(actor, "completed", title), t["id"], actor)
        if t.get("started_at"):
            actor = _actor_for(t["id"], t["started_at"], history_index,
                               tasks_by_id, field="started_by")
            add(t["started_at"], "task_started",
                _describe(actor, "started", title), t["id"], actor)
        if t.get("created_at") and t["id"] not in burst_members:
            # A `created` history row is a signature; `created_by` on the task
            # is only an intake source, so it is the weaker fallback.
            actor = _actor_for(t["id"], t["created_at"], history_index,
                               tasks_by_id, field="created_by")
            if actor.kind == "unknown":
                actor = _created_actor(t.get("created_by"), t["created_at"])
            add(t["created_at"], "task_created",
                _describe(actor, "filed", title), t["id"], actor)

    for at, parent_id, count, actor in bursts:
        parent_title = _short((tasks_by_id.get(parent_id) or {}).get("title")
                              or parent_id, 50)
        who = _describe(actor, "created", "")
        add(at, "task_created",
            f'{who} {_plural(count, "subtask")} under "{parent_title}"',
            parent_id, actor)

    for task_id, handoff in handoffs.items():
        title = _short((tasks_by_id.get(task_id) or {}).get("title") or task_id, 50)
        actor = _actor_for(task_id, handoff.get("timestamp"), history_index, tasks_by_id)
        add(handoff.get("timestamp"), "handoff",
            f'Handoff written on "{title}"', task_id, actor)

    # A day of agent work links dozens of sessions to one task. Rolled up by
    # day and task, they read as work; listed individually they are noise that
    # pushes every other signal out of a twenty-row timeline.
    #
    # The task named here must be ``matched_task`` — the task of *this*
    # project that caused the session to match — not the session's own
    # ``task_id`` column, which routinely points at another project's task.
    # Naming that one would put a foreign task's title in this brief.
    session_days: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for session in sessions:
        at = session.get("ended_at") or session.get("started_at")
        if at:
            matched = session.get("matched_task") or ""
            session_days[(str(at)[:10], matched)].append(session)
    for (_day, task_id), group in session_days.items():
        newest = max(group, key=lambda s: str(s.get("ended_at") or s.get("started_at")))
        at = newest.get("ended_at") or newest.get("started_at")
        subject = (f'"{_short((tasks_by_id.get(task_id) or {}).get("title") or task_id, 45)}"'
                   if task_id in tasks_by_id else "this project")
        count = (f"{_plural(len(group), 'session')}" if len(group) > 1
                 else f"Session {newest['id'][:8]}")
        add(at, "session", f"{count} worked on {subject}",
            f"session:{newest['id']}",
            Actor(kind="agent", name="session",
                  session_id=newest["id"], at=str(at or "")))

    for commit in commits:
        # A git author name is a name, not a role. Only an obviously-automated
        # committer is classified; a human name is left `unknown` rather than
        # asserted to be the operator, who may not be this repo's committer.
        author = commit["author"]
        automated = any(tag in author.lower() for tag in ("claude", "[bot]", "-bot"))
        actor = Actor(kind="agent" if automated else "unknown",
                      name=author, at=commit["at"])
        # Name the repo when it isn't the linked one, so a nested repo's commit
        # is never read as the outer repo's work.
        where = f" in {commit['repo']}/" if commit.get("repo") else ""
        add(commit["at"], "commit",
            f'{author} committed "{_short(commit["subject"], 60)}"{where}',
            commit["sha"][:8], actor)

    for artifact in artifacts:
        if artifact.kind in ("decision", "council") and artifact.date:
            add(artifact.date, "decision", f"Decision recorded: {_short(artifact.title, 70)}",
                artifact.path, Actor(kind="unknown", name="unknown", at=artifact.date))

    events.sort(key=lambda e: (_parse_dt(e.at) or datetime.min), reverse=True)
    return events[:MAX_EVENTS]


# ── Layer 6: deterministic prose ────────────────────────────────────────

_STATE_PHRASE = {
    "moving": "moving",
    # Not "warm but slowing" — the data says when it last moved, not whether
    # the pace is falling. The state_reason that follows carries the fact.
    "warm": "warm",
    "cold": "cold",
    "not_started": "not started",
    "blocked": "blocked",
    "done": "done",
}


def _build_summary(brief: ProjectBrief) -> str:
    """The always-present prose. Templates only — every clause is a fact above."""
    sentences = []
    counts = (f"{brief.done_count} of {_plural(brief.task_count, 'task')} done "
              f"({brief.pct}%)" if brief.task_count else "no tasks filed")
    sentences.append(f"{brief.title} is {_STATE_PHRASE.get(brief.state, brief.state)} — "
                     f"{brief.state_reason}. {counts[0].upper() + counts[1:]}.")

    if brief.phases:
        done_phases = sum(1 for p in brief.phases if p.state == "done")
        moving = [p for p in brief.phases
                  if p.state == "in_progress" and p.key not in ("unphased", "all")]
        line = f"{done_phases} of {_plural(len(brief.phases), 'phase')} complete"
        if moving:
            line += f"; {_short(moving[0].label, 50)} is in progress"
        sentences.append(line + ".")

    if brief.blockers:
        first = brief.blockers[0]
        sentences.append(f'Blocked on {_short(first.title, 45)}: '
                         f'{_short(first.blocked_on, 80)}.')

    if brief.next_up:
        nxt = brief.next_up[0]
        sentences.append(f'Next up is {nxt.task_id} "{_short(nxt.title, 55)}" '
                         f"({nxt.why}).")

    if brief.conflicts:
        kinds = sorted({c.kind.replace("_", " ") for c in brief.conflicts})
        sentences.append(f"{_plural(len(brief.conflicts), 'structural problem')} "
                         f"found in the plan ({', '.join(kinds)}).")

    if not brief.repo_path:
        sentences.append("No repository is linked, so no code or commit history "
                         "is included here.")

    return " ".join(sentences)


def _state_hash(brief: ProjectBrief) -> str:
    """What the narrative was describing, so staleness is detectable, not guessed."""
    import hashlib
    seed = "|".join([
        brief.state, str(brief.task_count), str(brief.done_count),
        str(brief.active_count), str(brief.waiting_count),
        str(brief.last_activity or ""),
        ",".join(sorted(c.kind for c in brief.conflicts)),
    ])
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


# ── Serialization ───────────────────────────────────────────────────────

def _as_dict(obj):
    if isinstance(obj, list):
        return [_as_dict(o) for o in obj]
    if isinstance(obj, Actor):
        return _actor_to_dict(obj)
    if hasattr(obj, "__dataclass_fields__"):
        return {f: _as_dict(getattr(obj, f)) for f in obj.__dataclass_fields__}
    return obj


def brief_to_dict(brief: ProjectBrief) -> dict:
    """JSON-safe dict of a brief. Round-trips through ``brief_from_dict``."""
    return _as_dict(brief)


def _build(cls, data: dict):
    fields = cls.__dataclass_fields__
    return cls(**{k: v for k, v in (data or {}).items() if k in fields})


def brief_from_dict(data: dict) -> ProjectBrief:
    """Rebuild a brief from stored JSON, ignoring keys it does not know."""
    data = dict(data or {})
    data["phases"] = [_build(Phase, p) for p in data.get("phases") or []]
    data["next_up"] = [_build(NextItem, n) for n in data.get("next_up") or []]
    data["blockers"] = [_build(Blocker, b) for b in data.get("blockers") or []]
    data["conflicts"] = [_build(Conflict, c) for c in data.get("conflicts") or []]
    data["artifacts"] = [_build(Artifact, a) for a in data.get("artifacts") or []]
    events = []
    for raw in data.get("recent_activity") or []:
        raw = dict(raw)
        raw["actor"] = _build(Actor, raw.get("actor") or {})
        events.append(_build(Event, raw))
    data["recent_activity"] = events
    return _build(ProjectBrief, data)


def _brief_path(project_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(project_id))
    return BRIEF_DIR / f"{safe}.json"


def _read_stored(project_id: str) -> dict | None:
    path = _brief_path(project_id)
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception:
        return None


# The agent-written fields. The compiler cannot regenerate any of them, so a
# write that dropped one would destroy work rather than a cache entry.
_NARRATIVE_KEYS = ("narrative", "narrative_written_at",
                   "_narrative_state_hash", "_narrative_actor")


def _write_stored(project_id: str, payload: dict, *,
                  keep_narrative: bool = True) -> None:
    """Atomic write, preserving the narrative that is on disk *right now*.

    Recompiles are triggered by every task mutation, so one can easily be in
    flight when ``set_narrative`` lands. Carrying the narrative forward from
    the copy read at the *start* of a compile would lose a paragraph written
    during it, so the merge reads the file again here, immediately before the
    rename. A failure to store loses a cache entry; losing a narrative loses
    the one thing in the brief no machine can rebuild.
    """
    path = _brief_path(project_id)
    try:
        BRIEF_DIR.mkdir(parents=True, exist_ok=True)
        if keep_narrative:
            current = _read_stored(project_id) or {}
            for key in _NARRATIVE_KEYS:
                if current.get(key) and not payload.get(key):
                    payload[key] = current[key]
            if payload.get("narrative"):
                payload["narrative_aged"] = (
                    payload.get("_narrative_state_hash") != payload.get("_state_hash"))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        os.replace(tmp, path)
    except Exception:
        pass


# ── The compiler ────────────────────────────────────────────────────────

def compile_brief(project_id: str, *, store: bool = True) -> ProjectBrief:
    """Compile a project's brief from every signal that touched it.

    Never raises for missing data: an unknown project id yields a brief in
    state ``not_started`` whose ``state_reason`` says the record is missing.
    """
    started = time.perf_counter()
    sources: list[str] = []

    db_path = _work_db_path()
    conn = _connect_ro(db_path)
    if conn is not None:
        sources.append(str(db_path))

    project_row = _read_project_row(conn, project_id) or {}
    canonical_id = project_row.get("id") or project_id
    tasks = _read_tasks(conn, canonical_id)
    task_ids = {t["id"] for t in tasks}

    handoffs = _read_handoffs(conn, task_ids)
    history = _read_history(conn, task_ids)
    all_task_ids: set[str] = set()
    if conn is not None:
        try:
            all_task_ids = {r[0] for r in conn.execute("SELECT id FROM tasks")}
        except Exception:
            all_task_ids = set(task_ids)
    goal_id = project_row.get("goal")
    goal_title = _read_goal_title(conn, goal_id)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    meta = _project_meta(project_row.get("description"))
    short_id = project_row.get("short_id") or meta.get("short_id")
    initiative = meta.get("initiative")
    repo_path = project_row.get("path")
    if repo_path and not Path(repo_path).expanduser().exists():
        repo_path = None

    sessions = _read_sessions(task_ids, canonical_id)
    commits = _git_log(repo_path)
    if commits:
        sources.append(f"{repo_path} (git log -20)")

    # The linked repo is often not where the work is. Nested repos contribute
    # their own commits, labelled, so a project shipping from a subdirectory
    # does not read as dormant.
    nested_repos = _nested_repos(repo_path)
    for label, path in list(nested_repos):
        # One unreadable nested repo must cost that repo's commits, not the
        # whole brief. Dropped silently — the alternative is guessing.
        try:
            nested = _git_log(path, limit=NESTED_COMMITS_EACH, label=label)
        except Exception:
            nested_repos = [r for r in nested_repos if r[0] != label]
            continue
        if nested:
            commits += nested
            sources.append(f"{path} (git log -{NESTED_COMMITS_EACH}, nested)")
    commits.sort(key=lambda c: _parse_dt(c["at"]) or datetime.min, reverse=True)

    artifacts, matched_docs = _discover_artifacts(
        canonical_id, short_id, initiative, tasks, sessions, commits,
        repo_path, sources, nested_repos=nested_repos)
    sources.extend(a.path for a in artifacts if a.path.startswith("vault/"))

    counts = _derive_counts(tasks)
    blockers = _derive_blockers(tasks, handoffs)
    last_activity, last_source = _derive_last_activity(
        tasks, handoffs, sessions, commits)

    conflicts: list[Conflict] = []
    conflicts += _detect_duplicate_spine(tasks)
    conflicts += _detect_orphan_tasks(tasks, all_task_ids)
    conflicts += _detect_stale_docs(matched_docs, tasks)
    conflicts += _detect_untracked_repo(canonical_id, short_id, repo_path,
                                        project_row.get("status"))
    conflicts += _enricher_conflicts(canonical_id, initiative, tasks,
                                     has_docs=bool(matched_docs))

    phases = _derive_phases(tasks)
    next_up = _derive_next_up(tasks, phases, handoffs)
    timeline = _build_timeline(tasks, handoffs, history, sessions,
                               commits, artifacts)

    if project_row:
        title = project_row.get("title") or canonical_id
    else:
        title = canonical_id

    brief = ProjectBrief(
        id=canonical_id,
        title=title,
        goal=goal_id,
        goal_title=goal_title,
        done_when=project_row.get("done_when"),
        appetite=meta.get("appetite"),
        repo_path=repo_path,
        last_activity=last_activity,
        last_activity_source=last_source,
        tags=_derive_tags(tasks, matched_docs, conflicts, repo_path),
        phases=phases,
        next_up=next_up,
        blockers=blockers,
        conflicts=conflicts,
        artifacts=artifacts,
        recent_activity=timeline,
        sources=list(dict.fromkeys(sources)),
        compiled_at=_now_iso(),
        **counts,
    )

    if not project_row:
        brief.state, brief.state_reason = "not_started", (
            f"no project record exists for '{project_id}'")
    else:
        brief.state, brief.state_reason = _derive_state(
            project_row, counts, last_activity, last_source, blockers, tasks)

    brief.summary = _build_summary(brief)

    # The narrative is the one thing this module does not compute — it is
    # carried forward from the store and marked aged when the state it
    # described has moved on.
    stored = _read_stored(canonical_id) or {}
    narrative = stored.get("narrative")
    if narrative:
        brief.narrative = narrative
        brief.narrative_written_at = stored.get("narrative_written_at")
        brief.narrative_aged = (
            stored.get("_narrative_state_hash") != _state_hash(brief))

    brief.compile_ms = int((time.perf_counter() - started) * 1000)

    if store:
        payload = brief_to_dict(brief)
        payload["_state_hash"] = _state_hash(brief)
        _write_stored(canonical_id, payload)
    return brief


def load_brief(project_id: str) -> ProjectBrief | None:
    """The stored brief, without recompiling. ``None`` if never compiled."""
    stored = _read_stored(project_id)
    if not stored:
        return None
    try:
        return brief_from_dict(stored)
    except Exception:
        return None


def _newest_change(project_id: str) -> str | None:
    """Newest mutation timestamp for a project, from the DB. None if unknown.

    Cheap: two indexed max() lookups, no row materialisation.
    """
    conn = _connect_ro(_work_db_path())
    if conn is None:
        return None
    try:
        stamps = []
        row = conn.execute(
            "SELECT max(max(coalesce(modified_at,'')), max(coalesce(completed_at,'')), "
            "       max(coalesce(started_at,'')), max(coalesce(created_at,''))) "
            "FROM tasks WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row and row[0]:
            stamps.append(row[0])
        try:
            row = conn.execute(
                "SELECT max(h.timestamp) FROM entity_history h "
                "JOIN tasks t ON t.id = h.entity_id "
                "WHERE h.entity_type = 'task' AND t.project_id = ?", (project_id,)
            ).fetchone()
            if row and row[0]:
                stamps.append(row[0])
        except sqlite3.Error:
            pass
        return max(stamps) if stamps else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def load_or_refresh(project_id: str) -> ProjectBrief:
    """The brief, recompiled if anything changed since it was last compiled.

    Why this exists: recompiles used to be pushed only by mutations that went
    THROUGH the API. Work done from the CLI, by an agent, or by a hook writes
    straight to the DB, so the API happily served a brief compiled hours
    earlier — the page showed stale state while claiming "last activity just
    now". Most of this operator's work arrives exactly that way, so pushing was
    the wrong model.

    Pulling is authoritative instead: compare the stored ``compiled_at`` against
    the newest mutation in the DB and recompile when it is behind, whoever made
    the change. Warm compiles are 11-90ms, and the comparison is two indexed
    max() lookups, so the common case (nothing changed) stays cheap.

    Push-on-mutation is still worth keeping for the SSE nudge — it is what makes
    an open page update without interaction. This just means correctness no
    longer depends on it.
    """
    stored = load_brief(project_id)
    if stored is None:
        return compile_brief(project_id)

    newest = _newest_change(project_id)
    if newest and (not stored.compiled_at or newest > stored.compiled_at):
        return compile_brief(project_id)
    return stored


def set_narrative(project_id: str, text: str, actor: Actor) -> None:
    """Attach the agent-written paragraph, stamped with what it described.

    The stamp is why the narrative can never silently lie: the next compile
    compares it to live state and flags ``narrative_aged`` when they diverge.
    """
    brief = compile_brief(project_id, store=False)
    brief.narrative = text
    brief.narrative_written_at = _now_iso()
    brief.narrative_aged = False
    payload = brief_to_dict(brief)
    payload["_state_hash"] = _state_hash(brief)
    payload["_narrative_state_hash"] = payload["_state_hash"]
    payload["_narrative_actor"] = _actor_to_dict(actor)
    # keep_narrative would merge the *old* narrative back over this one.
    _write_stored(brief.id, payload, keep_narrative=False)


def compile_all() -> list[ProjectBrief]:
    """Compile every project on the record, newest activity first."""
    conn = _connect_ro(_work_db_path())
    if conn is None:
        return []
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM projects ORDER BY id")]
    except Exception:
        ids = []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    briefs = [compile_brief(pid) for pid in ids]
    briefs.sort(key=lambda b: _parse_dt(b.last_activity) or datetime.min, reverse=True)
    return briefs


# ── Rendering ───────────────────────────────────────────────────────────

_STATE_BADGE = {"moving": "MOVING", "warm": "WARM", "cold": "COLD",
                "not_started": "NOT STARTED", "blocked": "BLOCKED", "done": "DONE"}

_PHASE_MARK = {"done": "[x]", "in_progress": "[~]", "blocked": "[!]",
               "not_started": "[ ]"}


def render_markdown(brief: ProjectBrief) -> str:
    """The brief as markdown. Sections with no signal are omitted, not faked."""
    out: list[str] = []
    out.append(f"# {brief.title}  ·  {_STATE_BADGE.get(brief.state, brief.state)}")
    out.append("")

    facts = [f"**{brief.done_count}/{brief.task_count}** tasks ({brief.pct}%)"]
    if brief.last_activity:
        facts.append(f"last activity {_ago(brief.last_activity)} "
                     f"({brief.last_activity_source})")
    if brief.goal_title:
        facts.append(f"goal: {brief.goal_title}")
    if brief.appetite:
        facts.append(f"appetite: {brief.appetite}")
    out.append(" · ".join(facts))
    if brief.tags:
        out.append(" ".join(f"`#{t}`" for t in brief.tags))
    out.append("")

    if brief.narrative:
        out.append(brief.narrative)
        stamp = (f" (written {_ago(brief.narrative_written_at)})"
                 if brief.narrative_written_at else "")
        if brief.narrative_aged:
            out.append(f"\n> Narrative is aged — state has changed since it was "
                       f"written{stamp}.")
        out.append("")
        out.append(f"_Compiled state:_ {brief.summary}")
    else:
        out.append(brief.summary)
    out.append("")

    if brief.done_when:
        out.append(f"**Done when:** {brief.done_when}")
        out.append("")

    if brief.next_up:
        out.append("## Next up")
        for item in brief.next_up:
            out.append(f"- **{item.task_id}** {item.title}  \n  _{item.why}_ "
                       f"(P{item.priority})")
        out.append("")

    if brief.blockers:
        out.append("## Blocked")
        for b in brief.blockers:
            since = f" — since {_ago(b.since)}" if b.since else ""
            out.append(f"- **{b.task_id}** {b.title}: {b.blocked_on}{since}")
        out.append("")

    if brief.conflicts:
        out.append("## Problems in the plan")
        for c in brief.conflicts:
            mark = "ERROR" if c.severity == "error" else "warn"
            out.append(f"- `{mark}` **{c.kind}** — {c.message}")
            if c.refs:
                out.append(f"  refs: {', '.join(c.refs)}")
        out.append("")

    if brief.phases:
        out.append("## Phases")
        for p in brief.phases:
            out.append(f"- {_PHASE_MARK.get(p.state, '[ ]')} {p.label} "
                       f"({p.done}/{p.total})")
        out.append("")

    if brief.recent_activity:
        out.append("## Recent activity")
        for e in brief.recent_activity:
            out.append(f"- {e.text} — {_ago(e.at)}")
        out.append("")

    if brief.artifacts:
        out.append("## Artifacts")
        by_kind: dict[str, list[Artifact]] = defaultdict(list)
        for a in brief.artifacts:
            by_kind[a.kind].append(a)
        for kind in sorted(by_kind):
            items = by_kind[kind]
            out.append(f"**{kind}** ({len(items)})")
            for a in items[:8]:
                date = f" · {a.date}" if a.date else ""
                out.append(f"- {a.title} — `{a.path}`{date}")
        out.append("")

    out.append("---")
    out.append(f"_Compiled {brief.compiled_at} in {brief.compile_ms}ms from "
               f"{_plural(len(brief.sources), 'source')}._")
    return "\n".join(out)
