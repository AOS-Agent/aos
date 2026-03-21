# Work System — Agentic Task, Project & Goal Infrastructure

**Version**: 0.2 (Architecture Draft)
**Date**: 2026-03-21
**Status**: Awaiting approval

---

## What This Is

Infrastructure for managing work — not an app, not a SaaS product, but a layer that sits underneath everything. Like Git is infrastructure for code, this is infrastructure for work.

- Agents read it. Humans read it. Cron jobs read it. Dashboards read it.
- Same data, different interfaces.
- Works for one person with a notebook. Works for 10 projects with 50 agents.
- File-based, local-first, version-controlled.
- Claude Code native — built as a plugin, not a separate tool.

### Who This Is For

| User | What they get |
|------|--------------|
| A teacher who just got a Mac Mini + AOS | "I have a million things everywhere. Now they're in one place and I can see what matters." |
| A freelancer with 3 clients | "Each client is a project. I see which ones are moving and which are stuck." |
| A chef running a restaurant + catering side business | "My agents handle ordering and scheduling. I focus on cooking." |
| An operator running 7 side projects with agents | "Everything rolls up. I see drift. Agents execute. I steer." |

---

## Design Principles

1. **Useful on day 1.** No 3-month calibration period. Add a task, see your tasks. That's it. Complexity is opt-in.
2. **File-first.** Structured files in a directory. No database. The filesystem is the database. Git gives you history for free.
3. **Claude Code native.** This is a Claude Code plugin — skills, hooks, and optional MCP. Not a separate CLI that Claude happens to talk to.
4. **Agent-native, human-friendly.** Agents are first-class workers. But the system works perfectly for a person with zero agents.
5. **Progressive complexity.** Start with a flat task list. Add projects when you need them. Add goals when you're ready. Add agents when you trust them.
6. **Minimum viable overhead.** If maintaining the system takes more than 5 min/day, it's too complex. The system should run itself.
7. **One-way sync.** Files are truth. Push to visual tools (Linear, Notion, ClickUp). Never pull back. Bidirectional sync is a maintenance nightmare.
8. **Packageable.** Clone the repo, run the installer, you have the system. Updates ship as versioned releases that don't break existing data.

---

## Philosophical Foundations

Three traditions inform the design. None are imposed on the user — they're baked into how the system works.

### From Productivity Research
- **GTD**: Capture everything, clarify into actionable items, review weekly. The inbox-to-action pipeline.
- **OKRs**: Goals with measurable key results. Progress is a number, not a feeling.
- **Shape Up**: Appetite-based scoping — "how much is this worth?" not "how long will it take?"
- **Kanban**: WIP limits. Flow metrics. Little's Law (double WIP = double cycle time).
- **PARA**: Projects (completable) vs Areas (ongoing). Archive aggressively.
- **Minimum viable system**: Calendar + task list + notes. Everything else is optional.

### From Islamic Management Tradition (Inspiration)
- **Excellence in work**: Every task has a quality standard. Done means done well, not just done.
- **Trust as sacred responsibility**: Delegation is transfer of trust. Delegate to the competent.
- **Plan then release**: Plan thoroughly, execute diligently, release attachment to outcomes.
- **Self-accounting**: Built-in review cadences. Daily, weekly, quarterly, annual.
- **Priority hierarchy**: Obligations before recommendations before nice-to-haves.
- **Blessed time**: Not all hours are equal. Schedule high-value work in high-energy windows.

### From Agentic Systems Research
- **Filesystem as memory** (Manus): Context windows are volatile. Write everything important to disk.
- **Claim-execute-release** (TICK.md): Coordination protocol for multi-agent work. TTL on claims.
- **Confidence-gated capture** (Superhuman): High → auto-create. Medium → confirm. Low → log.
- **Single in-progress constraint** (Claude Code): One task active per worker. Prevents thrashing.
- **Identity files** (PAI/TELOS): Mission, goals, beliefs as persistent files that shape all agent behavior.

---

## What We Learned From Existing Systems

| System | Stars | What they do well | What they miss |
|--------|-------|-------------------|----------------|
| **Claude Task Master** | 26K | MCP-first, PRD→tasks, multi-model | Cloud-dependent, no goal hierarchy, no knowledge layer |
| **PAI (Miessler)** | 10K | Person-centric, TELOS identity, USER/SYSTEM split | Heavy (331 workflows), steep learning curve |
| **Backlog.md** | 5.2K | One file per task, spec-driven, MCP protocol | No goals, no agent orchestration |
| **Mission Control** | 287 | JSON-as-IPC, agent daemon, Eisenhower matrix | JSON not human-friendly, no knowledge layer |
| **Alfred** | 193 | Background workers, Obsidian vault, multi-interface | Complex setup (Temporal), knowledge-first not operations-first |
| **TICK.md** | new | One file, Git-backed, file locking | Doesn't scale, no hierarchy |
| **Taskwarrior** | 5.7K | Battle-tested, powerful filtering, hook system | No AI awareness, complex, format not human-readable |
| **todo.txt** | 5.4K | Simplest possible format, huge ecosystem | No hierarchy, no dependencies, no agent awareness |

### The Gap

Nobody has built: **operations-first + multi-project isolation + knowledge integration + agent orchestration with trust levels + progressive complexity + packageable for non-technical users.**

That's this system.

---

## Data Model

### The Hierarchy (Progressive — Use Only What You Need)

```
Level 0 (day 1):   Tasks
Level 1 (week 2):  Tasks → Projects
Level 2 (month 1): Tasks → Projects → Goals
Level 3 (month 2): Tasks → Projects → Goals → Areas
```

Plus two cross-cutting concepts:
- **Inbox**: Uncategorized captures awaiting triage
- **Threads**: Tracked explorations that might become projects (experiments, research, conversations-turned-work)

### Why "Threads"?

Not everything is a task or a project. Sometimes you're exploring. Sometimes a conversation turns into something. This very conversation started as "research task systems" and became "build a work management system." The thread concept captures that evolution without forcing premature structure.

```
Thread: "Work management system research"
  → Started as: Conversation
  → Evolved into: Architecture spec
  → Promoted to: Project "Build work system for AOS"
  → Under goal: "AOS infrastructure"
```

Threads are lightweight — a title, a status, and optional notes. When they crystallize into something actionable, they get promoted to a project.

### Storage Strategy (Scale-Adaptive)

The system adapts its storage format to the user's scale:

**Small (< 50 tasks)**: Single `work.yaml` file with everything.
```yaml
# work.yaml — all-in-one for simple setups
tasks:
  - id: t1
    title: "Buy groceries"
    status: todo
    priority: 3
  - id: t2
    title: "Fix leaking faucet"
    status: active
    priority: 2
    project: home-maintenance
```

**Medium (50-500 tasks)**: One file per project, plus a global file.
```
work/
├── work.yaml           # Config + standalone tasks
├── projects/
│   ├── client-a.yaml   # Client A tasks
│   └── website.yaml    # Website project tasks
└── archive/            # Completed items (auto-moved)
```

**Large (500+ tasks, multi-project, agents)**: Full directory structure.
```
work/
├── work.yaml           # Global config
├── inbox/              # Raw captures (one file per item)
├── areas/              # Areas of responsibility
├── goals/              # Goals with key results
├── projects/           # Project definitions + tasks
├── threads/            # Explorations and experiments
├── templates/          # Recurring task templates
├── archive/            # Completed items
├── agents/             # Agent trust + assignment state
├── sync/               # Push state for external tools
└── .work/              # Internal (locks, metrics, history)
```

The system auto-detects which mode you're in based on what files exist. Upgrade path is additive — you never have to restructure.

### Core Entity: Task

```yaml
# Minimal (what a human types via quick-add)
- id: t1
  title: "Buy groceries"
  status: todo

# Standard (what the system fills in)
- id: t1
  title: "Buy groceries"
  status: todo           # inbox | todo | active | waiting | done | cancelled
  priority: 3            # 1=urgent, 2=high, 3=normal, 4=low, 0=unset
  project: null          # Project ID or null
  assignee: null         # Person name, agent ID, or null
  created: 2026-03-21
  due: null
  source: manual         # manual | email | message | calendar | agent | cron | session

# Full (for agent-managed, multi-project setups)
- id: t1
  title: "Configure shipping rates"
  status: active
  priority: 2
  project: store-setup
  goal: launch-mvp
  assignee: store-agent
  assignee_type: agent   # human | agent
  created: 2026-03-21T09:00:00Z
  due: 2026-03-25
  started: 2026-03-22T10:30:00Z
  completed: null
  source: session
  source_ref: "session-abc123"
  context: "@computer"   # GTD context
  energy: low            # high | medium | low
  estimate: 30m
  actual: null
  quality: "All provinces covered, rates verified"
  blocked_by: []
  blocks: [t5]
  trust_gate: approval   # shadow | approval | semi-auto | full-auto
  tags: [shipping, config]
```

Only `id`, `title`, and `status` are required. Everything else is optional and progressive. The system never forces fields on you.

### Core Entity: Project

```yaml
# projects/store-setup.yaml
id: store-setup
title: "Set up online store"
status: active           # draft | active | paused | completed | cancelled
goal: launch-mvp         # Optional parent goal
appetite: 2w             # How much time is this worth? (Shape Up)
done_when: "Store live, products listed, checkout working"
started: 2026-02-15
due: 2026-03-15
owner: null              # Person or agent
progress: 0.58           # Auto-calculated from tasks
hill: downhill           # uphill | peak | downhill (Shape Up hill chart)
```

### Core Entity: Goal

```yaml
# goals/launch-mvp.yaml
id: launch-mvp
title: "Launch MVP to first 10 customers"
area: career             # Optional parent area
type: committed          # committed | aspirational
timeframe:
  start: 2026-01-01
  end: 2026-03-31
key_results:
  - description: "Store live with 20+ products"
    target: 20
    current: 12
    unit: products
  - description: "10 paying customers"
    target: 10
    current: 0
    unit: customers
progress: 0.35           # Auto-calculated from key results
weight: 0.25             # Relative priority among concurrent goals
```

### Core Entity: Thread

```yaml
# threads/work-system-research.yaml
id: work-system-research
title: "Researching agentic work management systems"
status: active           # exploring | active | paused | promoted | abandoned
started: 2026-03-21
sessions:                # Claude Code sessions that contributed
  - session-abc123
  - session-def456
notes: |
  Started as research question. Turned into full architecture.
  Three research tracks: Islamic, business, technical.
  Now has a 700-line spec.
promoted_to: build-work-system    # Project ID if promoted
```

### Core Entity: Area

```yaml
# areas/health.yaml
id: health
title: "Health & Fitness"
standard: "Exercise 4x/week, sleep 7+ hours"
review: weekly
```

### Status Model

Fixed categories. Users can rename them but not add new categories.

```
inbox → todo → active → done
                  ↓
               waiting → (back to active when unblocked)

Any status → cancelled
```

### Priority Model

```
1 = Urgent    — Drop everything. Consequences if not done today.
2 = High      — This week. Important but not emergency.
3 = Normal    — This cycle/sprint. Standard work.
4 = Low       — When capacity allows. Nice to have.
0 = Unset     — Not yet triaged.
```

---

## Claude Code Integration (The Core)

This system is a **Claude Code plugin** — a bundle of skills, hooks, and optional MCP that makes Claude Code natively aware of work tracking.

### Architecture

```
~/.claude/plugins/work-system/
├── .claude-plugin/
│   └── plugin.json          # Manifest (name, version, description)
├── skills/
│   ├── work-awareness/      # Always-on: detects task-relevant work
│   │   └── SKILL.md
│   ├── work-manage/         # Invokable: /work — manage tasks, projects, goals
│   │   └── SKILL.md
│   ├── work-review/         # Invokable: /review — guided review sessions
│   │   └── SKILL.md
│   └── work-triage/         # Invokable: /triage — process inbox
│       └── SKILL.md
├── hooks/
│   └── hooks.json           # Session lifecycle hooks
├── agents/
│   └── work-reconciler.yaml # Background agent for session → task reconciliation
├── lib/
│   ├── parser.py            # Read/write work files
│   ├── query.py             # Filter/sort/search tasks
│   ├── metrics.py           # Flow metrics computation
│   └── sync.py              # One-way push to external tools
└── bin/
    └── work                 # CLI wrapper (optional convenience)
```

### The Three-Layer Cascade

Three mechanisms ensure work is tracked, with dedup so they don't conflict:

```
Layer 1: SKILL (proactive)
  work-awareness skill — loaded description at session start.
  Claude auto-detects when conversation involves trackable work.
  Creates/updates tasks inline during the session.
  Example: "This conversation has become a multi-step project.
           I'll track it as a thread."

  ↓ If Layer 1 didn't catch it...

Layer 2: HOOK (reactive)
  Stop hook fires when Claude finishes responding.
  Lightweight check: "Did this session create/complete any work?"
  If yes: update task files. Log session reference.
  SessionEnd hook: final reconciliation.

  ↓ If neither caught it...

Layer 3: COMMAND (explicit)
  /work add "Buy groceries"
  /work done t1
  /work track — "Track this conversation as a thread"
  Human always has manual override.
```

**Dedup mechanism**: Each task/thread has a `source_ref` field. Hooks check if a skill already created the entry. Commands check if hook/skill already handled it. The `source_ref` (session ID + timestamp) prevents duplicates.

### Hook Configuration

```json
{
  "hooks": {
    "SessionStart": [{
      "type": "command",
      "command": "python3 ${CLAUDE_PLUGIN_ROOT}/lib/inject_context.py",
      "matchers": ["startup", "resume", "compact"]
    }],
    "Stop": [{
      "type": "command",
      "command": "python3 ${CLAUDE_PLUGIN_ROOT}/lib/reconcile.py",
      "async": true
    }],
    "SessionEnd": [{
      "type": "command",
      "command": "python3 ${CLAUDE_PLUGIN_ROOT}/lib/session_close.py"
    }],
    "PostCompact": [{
      "type": "command",
      "command": "python3 ${CLAUDE_PLUGIN_ROOT}/lib/inject_context.py",
      "matchers": ["auto", "manual"]
    }]
  }
}
```

### What Each Hook Does

**SessionStart / PostCompact — `inject_context.py`**:
```
1. Read work.yaml (or project files)
2. Find tasks with status: active (currently in progress)
3. Find tasks due today or overdue
4. Find threads linked to this project directory
5. Output JSON with additionalContext:
   "Active tasks: [list]. Due today: [list]. Current threads: [list]."
```

Claude sees this context and naturally references it during the session. After compaction, the context is re-injected so it survives.

**Stop — `reconcile.py` (async)**:
```
1. Receive tool use history from stdin
2. Scan for patterns:
   - Files created/modified → did this complete a task?
   - Conversation mentions of tasks → status changes?
   - Multi-step work → should this be a thread?
3. If changes detected:
   - Update task files
   - Log session reference
   - No stdout (async, fire-and-forget)
```

**SessionEnd — `session_close.py`**:
```
1. Final reconciliation
2. Compute session metrics (duration, tasks touched, files changed)
3. Append to session log
4. If thread exists for this session → update thread notes
```

### Skill: work-awareness (Always-On)

```yaml
---
name: work-awareness
description: |
  Detects when the current conversation involves trackable work.
  Auto-activates when: multi-step tasks are discussed, projects are
  planned, goals are set, or exploratory work should be tracked as
  a thread. Reads and updates work files in ~/work/ or ./work/.
  DO NOT activate for simple questions, chat, or single-action requests.
allowed-tools: Read, Write, Edit, Glob, Bash
---
```

The SKILL.md content (loaded on activation) contains:
- How to read/write the task file format
- When to create tasks vs threads vs projects
- How to check for existing entries (dedup)
- Quality standards for task descriptions
- When to ask vs act autonomously

### Skill: work-manage (User-Invoked)

```yaml
---
name: work
description: |
  Manage tasks, projects, goals, and threads. Invoke with /work.
  Supports: add, done, list, edit, move, archive, status, goals, threads.
allowed-tools: Read, Write, Edit, Glob, Bash
---
```

### Skill: work-review (User-Invoked)

```yaml
---
name: review
description: |
  Guided review sessions. Invoke with /review [daily|weekly|quarterly].
  Daily: What got done, what's tomorrow, quick reflection.
  Weekly: GTD weekly review — inbox zero, all projects have next action,
  someday/maybe check, metrics review.
  Quarterly: Score goals, close rocks, set next quarter, retrospective.
allowed-tools: Read, Write, Edit, Glob, Bash
---
```

---

## Trust & Autonomy Model

### The Trust Ramp

Every agent starts at Level 0. Trust is **per-capability, not per-agent**.

```
Level 0: SHADOW
  Agent observes. Logs what it would do. Takes no action.
  Duration: ~1 week or 20 observations where suggestion matches reality.

Level 1: APPROVAL
  Agent proposes. Human approves/rejects each action.
  Graduation: 30 weighted approvals (routine=0.5, novel=2.0) with <5% revert.

Level 2: SEMI-AUTONOMOUS
  Agent acts on high-confidence items (>0.85). Asks on uncertain ones.
  Graduation: 50 autonomous actions with <2% revert rate.

Level 3: FULLY AUTONOMOUS
  Agent handles everything. Escalates only on defined exceptions.
  Exceptions (always escalate):
    - Financial commitment above threshold
    - External communication on behalf of user
    - Deleting/archiving items older than N days
    - Changing goal priorities or project scope
    - Anything in user's personal exception list
```

### Trust Config

```yaml
# agents/trust.yaml
agents:
  store-agent:
    capabilities:
      task_creation: approval
      task_completion: semi-auto
      project_updates: shadow
      customer_emails: shadow
    metrics:
      weighted_approvals: 24.5    # routine*0.5 + novel*2.0
      revert_rate: 0.03
      last_promotion: 2026-03-01
```

### Graduation Is Weighted

A trust system that counts raw approvals is gameable — an agent optimizes for easy, safe suggestions. Instead:

```
weighted_score = sum(
  routine_approval * 0.5 +    # Easy stuff counts less
  novel_approval * 2.0 +      # New territory counts more
  complex_approval * 3.0 +    # Multi-step work counts most
  rejection * -1.0 +          # Rejections subtract
  revert * -5.0               # Reverts are expensive
)
```

The system classifies each action's difficulty based on: has the agent done this type of task before? How many steps? Does it involve external systems?

---

## Auto-Capture Pipeline

### Sources → Inbox → Triage

```
Sources:
  Manual (CLI, Telegram, dashboard, voice, /work add)
  Email (IMAP polling)
  Messages (WhatsApp, iMessage, Telegram)
  Calendar (pre/post meeting task generation)
  Claude Code sessions (hook-detected)
  Cron (recurring templates)
      │
      ▼
  ┌─────────┐
  │  INBOX  │  ← Lightweight entries, just enough to not lose it
  └────┬────┘
       │
  Confidence-gated classification:
       │
       ├── High (>0.85)  → Auto-create task (if trust level allows)
       ├── Medium (0.5-0.85) → Suggest, ask for approval
       └── Low (<0.5)    → Log for manual review in /triage
```

### Triage (GTD Clarify, Adapted)

```
Inbox Item
    │
    Is it actionable?
    ├── No  → Reference (vault) | Someday (backlog, P4) | Trash (delete)
    └── Yes → What's the next action?
                ├── < 2 min → Do it now
                ├── Multi-step → Create project + first task
                ├── Delegate → Assign (human or agent, with trust gate)
                └── Single step → Create task (project, priority, due)
```

### Task Detection from Messages

```python
# NLP patterns for task extraction from messages/emails
patterns:
  imperative: ["please send", "can you", "need to", "don't forget", "make sure"]
  deadline: ["by friday", "next week", "before the meeting", "end of day"]
  commitment: ["I'll", "I will", "let me", "I need to"]

confidence_scoring:
  has_imperative + has_deadline: 0.9   # Almost certainly a task
  has_imperative: 0.7                  # Probably a task
  has_commitment: 0.6                  # Maybe a task
  ambiguous: 0.3                       # Log for review
```

---

## Review System (Automatic, Not Aspirational)

The #1 reason productivity systems fail: people stop doing reviews. This system makes reviews **automatic with human opt-in**, not human-driven with system support.

### How It Works

```
Daily (evening, automatic):
  Cron generates review summary → pushes to user via Telegram/notification.
  "Today: 5 done, 2 carried over, 1 new. Tomorrow's top 3: [list]."
  User reads in 30 seconds. No action required unless they want to adjust.

Weekly (configurable day, automatic):
  System generates pre-filled review document:
  - Inbox: N items awaiting triage
  - All projects have next action? [list any without]
  - Completed this week: [list]
  - Overdue: [list]
  - Goal progress: [bars]
  - Someday/maybe: [anything to activate?]
  User reviews in 5-10 minutes. Adjusts as needed.

Quarterly (automatic):
  System auto-scores goals (time elapsed vs progress).
  Generates retrospective draft with metrics.
  User does a 30-min planning session for next quarter.

Annual:
  System generates year-in-review from all quarterly data.
  Area health assessment (which areas got attention, which didn't).
  Goal completion rate. Biggest wins. Biggest gaps.
```

### Review Output

Reviews are saved as files — they compound into a knowledge base over time.

```
reviews/
├── daily/
│   └── 2026-03-21.yaml
├── weekly/
│   └── 2026-w12.md
├── quarterly/
│   └── 2026-q1.md
└── annual/
    └── 2025.md
```

---

## Metrics & Observability

### Flow Metrics (Computed Automatically)

```yaml
# .work/metrics/2026-w12.yaml
period: 2026-W12
throughput: 23              # Tasks completed
cycle_time_avg: 2.3d        # Active → Done
lead_time_avg: 4.1d         # Inbox → Done
wip_avg: 4.2                # Average concurrent active tasks
wip_max: 7                  # Peak (did we exceed our limit?)
blocked_hours: 12           # Total time in "waiting"
capture_sources:
  manual: 8
  email: 6
  message: 5
  session: 3
  cron: 1
```

### Goal Health (Auto-Detected)

```yaml
goals_health:
  - id: launch-mvp
    progress: 0.35
    time_elapsed: 0.82       # 82% of timeframe used
    on_track: false
    risk: "Progress significantly behind timeline"
    drift: 0.47              # Gap between where you should be and where you are
```

### Drift Detection

Compare stated priorities (goal weights) vs actual work (task completions):
```
Goal "Launch MVP" has weight 0.25 (25% of effort)
But only 10% of completed tasks this quarter were MVP-related.
Drift score: 0.15 → "You're spending less time on MVP than you planned."
```

---

## Sync Layer (One-Way Push)

Files are truth. External tools are optional mirrors.

```
work files → [sync engine] → Linear / Notion / ClickUp / Custom Dashboard
                ↑
                Only this direction. Never pull back.
```

### Why One-Way Only

Every team that has tried bidirectional sync has regretted it:
- Conflict resolution is impossible to get right
- Someone updates Linear on their phone while an agent updates the file → data loss
- Two sources of truth = zero sources of truth

One-way push is simple and reliable:
- File changes → webhook/cron triggers push to external tool
- External tool is read-only mirror
- If someone wants to update a task, they update the file (via CLI, Telegram, agent)

### Sync Config

```yaml
# sync/linear.yaml
adapter: linear
credentials_ref: "keychain:linear-api-key"   # Never in files
sync:
  direction: push_only
  on: file_change              # Or: cron (every 5 min)
  mappings:
    status.todo: "Todo"
    status.active: "In Progress"
    status.done: "Done"
    priority.1: "Urgent"
    priority.2: "High"
  filters:
    include_projects: [store-*]
    exclude_tags: [personal]
```

### Built-In Dashboard

For users who don't want external tools — a web view reading files directly.

```
/work/inbox          → Items awaiting triage
/work/today          → Today's tasks (priority + energy + context filtered)
/work/projects       → Project cards with progress bars
/work/goals          → Goal progress with key results
/work/threads        → Active explorations
/work/agents         → Agent trust levels + activity
/work/metrics        → Flow metrics (cycle time, throughput, WIP)
/work/review         → Review history
```

---

## Packaging & Distribution

### Installation

```bash
# For AOS users (already have the infrastructure)
aos install work-system

# For standalone Claude Code users
claude plugin install work-system

# For manual setup
git clone <repo> ~/.claude/plugins/work-system
```

### Directory Layout (User's Machine)

```
~/work/                    # The user's work data (THEIR data, versioned)
├── work.yaml              # Config + tasks (small setup)
├── ...                    # Grows as needed

~/.claude/plugins/work-system/   # The system code (OUR code, updated)
├── .claude-plugin/
├── skills/
├── hooks/
├── lib/
└── bin/
```

**Critical separation**: User data (`~/work/`) is completely separate from system code (`~/.claude/plugins/work-system/`). Updates to the system never touch user data.

### Versioning & Updates

```yaml
# .claude-plugin/plugin.json
{
  "name": "work-system",
  "version": "1.2.0",
  "min_data_version": "1.0",    # Minimum data format this version supports
  "max_data_version": "2.0",    # Maximum data format this version supports
  "claude_code_min": "2.1.16"   # Minimum Claude Code version required
}
```

### Data Format Versioning

```yaml
# work.yaml — user's file
version: "1.0"              # Data format version
# ... tasks and config
```

**Migration rules**:
1. **Additive changes** (new optional fields): No migration needed. Old data works fine. New fields default to null.
2. **Structural changes** (field renames, format changes): Migration script runs automatically on first load after update.
3. **Breaking changes** (removed fields, incompatible formats): Major version bump. Migration script required. Old version still works until user explicitly migrates.

### Update Flow

```
1. User runs: claude plugin update work-system
   (or: git pull in plugin directory)

2. System checks version compatibility:
   - Plugin v1.2 reads data format v1.0-2.0
   - User's data is format v1.0
   - Compatible? Yes → proceed
   - Incompatible? → "Your data needs migration. Run: work migrate"

3. Migration (if needed):
   - Backs up current data: work.yaml → work.yaml.bak.v1.0
   - Runs migration script: v1.0 → v1.1 → v1.2 (stepwise, never skip)
   - Validates migrated data
   - Reports changes: "Migrated 47 tasks. Added 'energy' field (default: null)."

4. No data loss. Ever. Backups before every migration.
```

### Multi-Project Support

For users with multiple projects (each with their own work tracking):

```
~/project-a/work/          # Project A's tasks
~/project-b/work/          # Project B's tasks
~/work/                    # Personal/system-level tasks
```

Goal rollup: Each project's goals can reference parent goals in `~/work/goals/`. A project goal "Launch store" can roll up into a personal goal "Build business."

The plugin detects which `work/` directory to use based on the current working directory. `~/work/` is the fallback.

---

## How Sessions Become Work

This is the critical integration — how Claude Code sessions (the actual work) connect to the work tracking system.

### Scenario 1: Explicit Task Execution
```
User starts session: "Work on task t5 — configure shipping rates"
SessionStart hook → injects t5 details + context from knowledge vault
Claude works on it → uses work-awareness skill to update progress
Session ends → SessionEnd hook marks t5 as done (or updates progress)
```

### Scenario 2: Conversation Becomes Project
```
User starts session: "Research agentic task management systems"
... 2 hours of research, architecture design, spec writing ...
work-awareness skill detects: "This has become multi-step work"
Claude: "This has evolved into a significant effort. I'll track it
         as a thread: 'Work management system research'"
... more sessions contribute to the thread ...
Eventually: Thread promoted to project with tasks broken out
```

### Scenario 3: Implicit Task Detection
```
User starts session: "Fix the login bug on the dashboard"
Claude fixes it → Stop hook detects: file changes in dashboard code
Hook checks: is there a task matching "login bug" or "dashboard fix"?
  If yes → marks it done, logs session reference
  If no → creates a record: "Fixed login bug on dashboard [session-ref]"
```

### Scenario 4: Experiment / Exploration
```
User starts session: "I'm curious about using WebSockets for real-time updates"
Claude explores → reads docs, prototypes, tests
work-awareness skill: "This is exploratory. Track as thread?"
User: "Yeah"
Thread created: "WebSocket exploration for real-time"
  status: exploring
  notes: findings from this session
  sessions: [this-session-id]
```

### Session-Task Linking

Every task can reference the sessions that worked on it:
```yaml
- id: t5
  title: "Configure shipping rates"
  sessions:
    - id: session-abc123
      date: 2026-03-21
      outcome: "Completed initial setup for Ontario and BC"
    - id: session-def456
      date: 2026-03-22
      outcome: "Added remaining provinces, verified rates"
```

And every session summary (from session-export) links back to tasks it touched.

---

## The "Always-On" Problem (Solved)

The work-awareness skill needs to detect task-relevant work WITHOUT:
- Consuming too many tokens (description-only at startup = ~100 tokens)
- Creating false positives (not every conversation is work)
- Being annoying (don't suggest tracking "what's the weather")

### How It Works

1. **Startup**: Only the skill description loads (~100 tokens). Claude reads it and knows the capability exists.

2. **Activation trigger**: Claude's own judgment, informed by the description. Triggers when:
   - User mentions tasks, projects, goals, deadlines
   - Multi-step work is discussed
   - Session involves significant creation/modification
   - User explicitly references work system (/work, tasks, etc.)

3. **On activation**: Full SKILL.md loads. Claude reads work files, understands current state, acts accordingly.

4. **Dedup with hooks**: Skill sets a flag in tool output metadata. Hooks check for the flag before creating duplicate entries.

This means: zero overhead for simple conversations, automatic awareness for real work.

---

## CLI (Optional Convenience)

The CLI is a thin wrapper around the file operations. Not required — everything can be done through Claude Code skills, Telegram, or direct file editing.

```bash
# Quick operations
work add "Buy groceries"                    # Add task
work add "Fix login" -p website -P 2        # With project and priority
work done t5                                # Mark complete
work list                                   # Show active tasks
work list --project website                 # Filter by project

# Planning
work today                                  # Today's plan
work next                                   # What to work on (considers energy, context, priority)
work inbox                                  # Show inbox items
work triage                                 # Interactive triage

# Tracking
work thread "Exploring WebSocket approach"  # Create thread
work promote thread-1                       # Promote thread to project

# Goals
work goals                                  # Goal progress
work drift                                  # Priority vs actual work comparison

# Review
work review daily                           # Generate daily summary
work review weekly                          # Generate weekly review

# System
work metrics                                # Flow metrics
work agents                                 # Agent status
work sync                                   # Push to external tools
work migrate                                # Run data migrations
work export                                 # Export all data (JSON, CSV, or Markdown)
```

---

## Onboarding Flow

### Phase 1: First Run (5 minutes)

```
"Welcome. Let's set up your work system."

Step 1: Where do you want your work files?
  Default: ~/work/
  Or: specify a path

Step 2: Tell me what's on your plate right now.
  "Just dump everything — projects, tasks, ideas, worries.
   Don't organize. I'll help you sort it."

  → Everything goes to inbox
  → Guided triage: "Is this a task, a project, or just a thought?"
  → System learns vocabulary and priorities from how you triage

Step 3: Connect sources (optional, each one unlocks auto-capture):
  □ Email
  □ WhatsApp
  □ Telegram
  □ Calendar

Step 4: Done. You have a working system.
  "You have 12 tasks across 3 projects.
   Here's what I suggest for today: [top 3 by priority]"
```

### Phase 2: First Week (Passive Learning)

System runs normally. In the background:
- Learns what the user considers urgent vs normal
- Learns project naming patterns
- Learns energy rhythms (when do they work on hard stuff?)
- Learns capture preferences (do they prefer short or detailed task titles?)

No suggestions yet. Just working as a basic task system.

### Phase 3: First Month (Start Suggesting)

After enough data:
```
"I noticed you got 3 emails this week that looked like tasks.
 Want me to start capturing those to your inbox?
 [Yes, capture automatically] [Yes, but ask me first] [No thanks]"
```

Each "yes" moves that capability up the trust ramp.

### Phase 4: Ongoing (Progressive Autonomy)

Trust grows per-capability based on track record. The system gets more autonomous over time without any configuration — just consistent behavior.

---

## Implementation Phases

### Phase 1: Core (Week 1-2)
- [ ] Define file format schemas (YAML validation with JSON Schema)
- [ ] Build parser library (Python — read/write/query work files)
- [ ] Build work-manage skill (/work commands)
- [ ] Build work-awareness skill (always-on detection)
- [ ] Build SessionStart + SessionEnd hooks
- [ ] Basic CLI wrapper
- [ ] Migrate existing task/goal data to new format

### Phase 2: Agent Integration (Week 3-4)
- [ ] Claim/release protocol with TTL locks
- [ ] Trust gate system (all 4 levels)
- [ ] Agent assignment logic
- [ ] Telegram bridge integration (/tasks, /goals, /inbox)
- [ ] Dashboard integration

### Phase 3: Auto-Capture (Week 5-6)
- [ ] Email → inbox pipeline
- [ ] Message → inbox pipeline (WhatsApp, iMessage)
- [ ] Calendar → task generation
- [ ] Confidence-gated routing
- [ ] Thread detection from sessions

### Phase 4: Reviews & Metrics (Week 7-8)
- [ ] Automated daily/weekly review generation
- [ ] Flow metrics computation
- [ ] Goal health + drift detection
- [ ] Review history as files

### Phase 5: Packaging (Week 9-10)
- [ ] Claude Code plugin packaging
- [ ] Data migration tooling
- [ ] One-way sync adapters (Linear, Notion)
- [ ] Onboarding flow
- [ ] Documentation

---

## Open Questions for Review

1. **Plugin vs skill bundle**: Should this be a Claude Code plugin (new system, more packaging infrastructure) or a simpler skill+hook bundle installed via `~/.claude/skills/` + `~/.claude/settings.json`? Plugin is more distributable but newer and less battle-tested.

2. **Single work.yaml vs separate files**: For the small/medium case, should tasks live in one file (easier to read, atomic writes) or one-per-task (better for Git diffs, concurrent agent access)? Recommendation: start with single file, split when it hits a threshold.

3. **Thread lifecycle**: When does a thread get auto-promoted to a project? Human-only decision, or can the system suggest based on heuristics (number of sessions, time spent, artifacts created)?

4. **Knowledge integration depth**: Should the system auto-enrich tasks with context from a knowledge vault (if one exists), or keep the work system completely independent? Recommendation: optional integration, not dependency.

5. **Multi-user**: Is there a future where two humans share a work directory (team scenario)? If so, file locking matters from day 1. If not, we can keep it simpler.

---

## What This System Is NOT

- **Not an app.** It's infrastructure. Apps are optional views on top.
- **Not a database.** Files in a directory. That's it.
- **Not a methodology.** Use GTD, OKRs, Kanban, or nothing. It adapts.
- **Not SaaS.** Runs on your machine. Your data stays with you.
- **Not opinionated about tools.** Use Linear, Notion, a notebook, or just the CLI. The system doesn't care.
- **Not complex to start.** `work add "Buy groceries"` — that's the minimum viable interaction.
