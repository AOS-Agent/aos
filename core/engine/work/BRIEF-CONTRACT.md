# Project Brief Compiler — Data Contract

**Status:** locked for the v1 build. All builders code against this document.

## The idea

A project record today is a row someone typed once. It goes stale immediately.
A **brief** is *compiled* from every signal that already touched the project —
tasks, sessions, handoffs, git, vault docs, decisions. Nobody maintains it. It
recompiles when work happens.

Precedent in this codebase: `core/engine/people/profile.py` compiles a person
profile from SQL. Projects get the same treatment.

## Hard constraints

1. **No LLM calls in the compiler.** This machine has no Anthropic API key —
   only a Claude Code subscription. The compiler is 100% deterministic. Prose is
   generated from templates over real structured signal.
2. **Every claim cites a source.** Same discipline as `comms-recall`. A brief
   line that can't point at a `source_ref` does not get emitted. Vague confident
   prose is the failure mode we are explicitly guarding against.
3. **Compile must be fast** (< 300ms warm) — it runs on every task mutation.
4. **Degrade gracefully.** Missing repo, missing initiative doc, zero sessions —
   each just drops its section. Never crash, never block the UI.

## Narrative: the one non-deterministic field

`brief.narrative` is the only prose an agent writes. Chief/Advisor writes it at
session close (or on demand) and it is **cached** in the brief store with the
`narrative_written_at` timestamp and the git SHA / task-state hash it described.

- If absent → the UI renders the deterministic `summary` instead. Never blank.
- If stale (state changed materially since it was written) → UI marks it `aged`.

This keeps the system honest: facts are always live and compiled; the human-feel
paragraph is a bonus layer that can never silently lie about current state,
because it carries the stamp of what it was describing.

## `ProjectBrief` schema

```python
@dataclass
class ProjectBrief:
    # identity
    id: str
    title: str
    goal: str | None
    goal_title: str | None
    done_when: str | None
    appetite: str | None
    repo_path: str | None

    # derived state  (see rules below)
    state: str                    # moving | warm | cold | not_started | blocked | done
    state_reason: str             # plain English, e.g. "no activity in 14 days"
    last_activity: str | None     # ISO8601, max over ALL signals
    last_activity_source: str     # task | git | session | handoff

    # counts (authoritative — from project record, never from capped rows)
    task_count: int
    done_count: int
    active_count: int
    todo_count: int
    waiting_count: int
    pct: int

    # compiled content
    summary: str                  # deterministic prose, always present
    narrative: str | None         # agent-written, cached, may be None
    narrative_written_at: str | None
    narrative_aged: bool

    tags: list[str]               # derived, see below
    phases: list[Phase]           # grouped task structure
    next_up: list[NextItem]       # 1-3 actionable, dependency-aware
    blockers: list[Blocker]
    conflicts: list[Conflict]     # structural problems found in the plan
    artifacts: list[Artifact]     # EVERYTHING this project produced
    recent_activity: list[Event]  # newest 20, merged timeline

    # provenance
    sources: list[str]            # every file/record consulted
    compiled_at: str
    compile_ms: int
```

### Sub-types

```python
@dataclass
class Phase:
    key: str                # slug
    label: str              # "Phase 2 — The claim spine"
    task_ids: list[str]
    done: int
    total: int
    state: str              # not_started | in_progress | done | blocked

@dataclass
class NextItem:
    task_id: str
    title: str
    why: str                # "nothing blocks it; 3 tasks cite it"
    priority: int

@dataclass
class Blocker:
    task_id: str
    title: str
    blocked_on: str         # plain English
    since: str | None

@dataclass
class Conflict:
    kind: str               # duplicate_spine | orphan_task | stale_doc | untracked_repo
    severity: str           # warn | error
    message: str            # plain English, actionable
    refs: list[str]         # task ids / file paths involved

@dataclass
class Artifact:
    kind: str               # initiative | spec | decision | council | session | commit | file | deck
    title: str
    path: str               # vault path, repo path, or session id
    date: str | None
    excerpt: str | None     # first meaningful line

@dataclass
class Event:
    at: str
    kind: str               # task_done | task_started | commit | session | handoff | decision
    text: str               # plain English one-liner
    ref: str | None
    actor: Actor            # WHO did it — never anonymous
```

## Attribution — every change is signed

**Requirement:** if an agent completed a task, the agent signs off. If the
operator changed it by hand, it's attributed to the operator. No anonymous
state changes.

```python
@dataclass
class Actor:
    kind: str        # operator | agent | cron | import | unknown
    name: str        # "operator" | "chief" | "advisor" | "engineer" | "session-close"
    session_id: str | None
    at: str          # ISO8601
```

### Storage

Add to the task record (all optional, backward compatible):

```yaml
completed_by:  {kind: agent, name: chief, session_id: abc123, at: "..."}
started_by:    {kind: operator, name: operator, at: "..."}
created_by:    {kind: agent, name: advisor, session_id: abc123, at: "..."}
audit:                                  # append-only, capped at 20 entries
  - {at: "...", actor: {...}, change: "status todo -> done"}
```

### How actor is resolved

The work engine resolves the actor at mutation time, in this order:

1. Explicit `--actor` flag on the CLI, or `actor` field in the API payload.
2. Env var `AOS_ACTOR` (set by agent dispatch wrappers).
3. `CLAUDE_SESSION_ID` present → `{kind: agent, name: <agent from env or "claude">, session_id: ...}`.
4. Interactive TTY with no session → `{kind: operator, name: "operator"}`.
5. Otherwise → `{kind: unknown}`.

**Never invent an actor.** `unknown` is an honest answer and the UI renders it
as "unattributed" rather than guessing.

### In the UI

Timeline rows read as plain English with the actor named:

- `Chief completed "Draft the constitution" — 2h ago`
- `You marked "Part 2: The claim spine" done — yesterday`
- `Advisor created 13 subtasks — Jul 25`

## Task bodies — tasks must say what they are

**The problem this solves:** `hre#1.3 "Part 2: The claim spine"` has no
description, no acceptance criteria, no link. It is unreadable on its own. Yet
the full plain-English body **already exists** in `hre-mvp-scope.md` under
`## 4C · Part 2 — The claim spine`.

Add to the task record:

```yaml
body: |                  # plain English — what this is, in prose
  ...
acceptance: [...]        # what "done" means, bullet list
body_source: "vault/knowledge/initiatives/hre-mvp-scope.md#4C"
body_synced_at: "..."    # when pulled from source
```

### The enricher

`work enrich <project>` — a **link-and-pull** pass, not a generator:

1. For each task, find its section in the project's source docs by matching
   the task title against headings (`Part 2` → `## 4C · Part 2 — …`).
2. Pull the first prose paragraph as `body`, any checklist/criteria as
   `acceptance`, and record `body_source` as an anchored path.
3. **Never invent text.** If no source section matches, leave `body` empty and
   emit a `Conflict(kind="no_body", ...)` so the gap is visible instead of
   papered over with generated filler.

Bodies are pulled, cited, and re-syncable — the doc stays the source of truth.

### New conflict kind: `status_disagreement`

Sources that disagree about completion must be surfaced, never silently
reconciled. Live example in `hre`:

| Source | Part 1 | Part 2 |
|---|---|---|
| `hre-mvp-scope.md` index (line 91) | ⬜ | ⬜ |
| `hre-mvp-scope.md` section heading | ✅ | ✅ |
| work system (`hre#1.2`, `hre#1.3`) | todo | todo |

The compiler parses status markers (`⬜ 🔶 ✅`) from source docs, compares them
against task status, and emits:

```
Conflict(kind="status_disagreement", severity="warn",
         message='"Part 2: The claim spine" is marked done in hre-mvp-scope.md
                  (section 4C) but is still todo in the tracker.',
         refs=["hre#1.3", "vault/knowledge/initiatives/hre-mvp-scope.md#4C"])
```

Resolution is always the operator's call — the compiler reports, never
auto-resolves. Internal contradictions *within* one doc (index says ⬜, heading
says ✅) are reported too.

## State derivation (strict order — first match wins)

| State | Rule |
|---|---|
| `done` | project.status == completed, OR pct == 100 |
| `blocked` | any task status == waiting AND has a blocker note |
| `not_started` | done_count == 0 AND active_count == 0 |
| `moving` | last_activity within 3 days |
| `warm` | last_activity within 10 days |
| `cold` | last_activity older than 10 days (or unknown) |

`state_reason` is always a plain-English sentence explaining the match, e.g.
`"19 tasks created 12 days ago, none started"`.

`last_activity` = max over: task.completed, task.started, task.created,
handoff timestamps, linked session timestamps, last git commit in `repo_path`.

## Tag derivation

Tags are **derived, never hand-applied**. Union of:

1. Explicit `task.tags` present on the project's tasks.
2. Initiative-doc frontmatter `tags:`.
3. Structural tags emitted by the compiler:
   - `#not-started`, `#blocked`, `#stale-doc`, `#no-repo`, `#needs-decision`
     (emitted when a `Conflict` of matching kind exists)

Cap at 6, ordered by frequency then alphabetically. Lowercase, hyphenated.

## Conflict detection (v1 rules)

- **`duplicate_spine`** — two task subtrees under one project whose titles overlap
  above a similarity threshold. This is a real, live bug in the `hre` project:
  `hre#1` (13 subtasks) and `hre#2..6` (5 siblings) describe the same work;
  `hre#1.1` and `hre#5` are the same task. Must be detected generically, not
  special-cased.
- **`orphan_task`** — task references a `parent` or `source_ref` that doesn't exist.
- **`stale_doc`** — initiative doc `status:` is `shaping`/`planning` while the
  project has active or completed tasks; or doc `date` predates newest task by
  >7 days.
- **`untracked_repo`** — a directory matching the project id/slug exists under
  `~/project/` but `project.path` is unset. (9 of 11 projects are in this state.)

## Artifact discovery

Walk, in order, and dedupe by path:

1. **Initiative + specs** — vault `knowledge/initiatives/` matching the project's
   `initiative:` key in `description`, plus any doc whose frontmatter
   `project:` matches, plus slug-prefix matches (`hre-*.md` → project `hre`).
2. **Decisions / councils** — `vault/knowledge/decisions/` where frontmatter
   `project:` or filename slug matches.
3. **Sessions** — `vault/log/sessions/` linked via `task.sessions`, plus any
   session whose frontmatter names the project.
4. **Commits** — if `repo_path` set: last 20 commits (`git log`).
5. **Files** — if `repo_path` set: top-level README / spec / design docs.

## Storage

`~/.aos/data/project-briefs/<project_id>.json` — the compiled brief.
Narrative is stored in the same file and is *preserved across recompiles*.

## API

```
GET  /api/work/projects/{id}/brief      -> ProjectBrief JSON
POST /api/work/projects/{id}/brief/narrative  {text} -> writes narrative
POST /api/work/projects/{id}/brief/recompile  -> force recompile
```

## Live updates

SSE event on the existing `/api/stream`:

```
event: project.brief.updated
data: {"project_id": "hre", "state": "moving", "compiled_at": "..."}
```

Emitted whenever a brief is recompiled. The UI refetches that project's brief.

**Recompile triggers:**
- Any task mutation through the work API or CLI (`done`, `start`, `add`, `subtask`, `handoff`)
- Session close (`session_close.py`)
- Explicit POST recompile

Debounce: coalesce recompiles for the same project within 2s.

## CLI

```
work brief <project>            # render the brief as markdown to stdout
work brief <project> --json
work brief --all                # one-line state per project
work enrich <project>           # link-and-pull task bodies from source docs
work enrich <project> --dry-run # show what would be pulled, change nothing
work who <task>                 # show attribution + audit trail for a task
```

## Module interfaces — LOCKED

These signatures are fixed so modules can be built in parallel against each
other without collision. Do not change them; import them exactly as written.

### `core/engine/work/brief.py`

```python
def compile_brief(project_id: str, *, store: bool = True) -> ProjectBrief: ...
def load_brief(project_id: str) -> ProjectBrief | None:    # cached, no compile
def render_markdown(brief: ProjectBrief) -> str: ...
def set_narrative(project_id: str, text: str, actor: Actor) -> None: ...
def brief_to_dict(brief: ProjectBrief) -> dict: ...        # JSON-safe
def compile_all() -> list[ProjectBrief]: ...
```

### `core/engine/work/enrich.py`

```python
def enrich_project(project_id: str, *, dry_run: bool = False) -> EnrichReport: ...

@dataclass
class EnrichReport:
    project_id: str
    matched: list[tuple[str, str]]      # (task_id, source_anchor)
    unmatched: list[str]                # task_ids with no source section
    disagreements: list[Conflict]       # status_disagreement findings
    changed: int
```

### `core/engine/work/actor.py`  (owned by the attribution workstream)

```python
def resolve_actor(explicit: str | None = None) -> Actor: ...
def record_change(task_id: str, change: str, actor: Actor) -> None: ...
def actor_to_dict(a: Actor) -> dict: ...
def actor_from_dict(d: dict | None) -> Actor | None: ...
def describe(actor: Actor, verb: str, subject: str) -> str:
    """Plain English: 'Chief completed "Draft the constitution"'.
    Operator actor renders as 'You'. Unknown renders as 'Someone'."""
```

`Actor`, `Conflict`, `ProjectBrief` and all sub-types live in
`core/engine/work/brief_types.py` — a dependency-free dataclass module that
every other module imports. Build it first, inside the compiler workstream.

## File ownership for this build

Strict — do not edit files owned by another workstream.

| Workstream | Owns |
|---|---|
| Compiler | `brief_types.py`, `brief.py`, `tests/engine/work/test_brief.py` |
| Attribution | `actor.py`, `engine.py`, `backend.py`, `cli.py` |
| Enricher | `enrich.py`, `tests/engine/work/test_enrich.py` |
| API | `core/qareen/api/work.py` |
| UI | `core/qareen/screen/src/**` |
| Wires | `session_close.py`, `detect_projects.py` |
