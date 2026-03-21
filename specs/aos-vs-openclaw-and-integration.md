# AOS Architecture Analysis: Components, Differentiation & Work System Integration

**Date**: 2026-03-21

---

## Part 1: AOS Main Components

AOS has 7 layers, each building on the one below:

```
┌─────────────────────────────────────────────────┐
│  7. INTERFACE LAYER                             │
│     Telegram bridge, Dashboard, CLI, Chief app  │
├─────────────────────────────────────────────────┤
│  6. AGENT LAYER                                 │
│     Specialized agents with trust levels        │
│     engineer, ops, technician, cmo, per-project │
├─────────────────────────────────────────────────┤
│  5. EXECUTION LAYER                             │
│     Capability map → Pattern cache → Anti-excuse│
│     Steer, Chrome MCP, AppleScript, CLI         │
├─────────────────────────────────────────────────┤
│  4. KNOWLEDGE LAYER                             │
│     Vault (Obsidian) + QMD search + ChromaDB    │
│     Session export + Friction analysis          │
├─────────────────────────────────────────────────┤
│  3. WORK LAYER        ← THIS IS THE GAP        │
│     Goals, Projects, Tasks, Threads, Reviews    │
│     (Currently fragmented — this spec fills it) │
├─────────────────────────────────────────────────┤
│  2. SERVICE LAYER                               │
│     Bridge, Dashboard, Listen, Memory, Phoenix  │
│     WhatsApp, Health, Transcriber               │
├─────────────────────────────────────────────────┤
│  1. INFRASTRUCTURE LAYER                        │
│     macOS + LaunchAgents + Tailscale + Keychain │
│     Cron + Git + Homebrew + OrbStack            │
└─────────────────────────────────────────────────┘
```

The work system is Layer 3 — the connective tissue between knowledge (what you know) and execution (what gets done).

---

## Part 2: AOS vs OpenClaw — Fundamental Differences

### What OpenClaw Is

OpenClaw (328K stars, MIT, TypeScript) is a **universal personal AI assistant**. One Gateway process, 22+ messaging channels, 60+ skills, native apps for macOS/iOS/Android. It routes messages to an AI agent that can use tools.

### The Fundamental Difference

**OpenClaw is an assistant. AOS is an operating system.**

An assistant waits for you to ask something, then responds. An operating system runs continuously — it has goals, it learns, it improves, it acts proactively.

| Dimension | OpenClaw (Assistant) | AOS (Operating System) |
|-----------|---------------------|------------------------|
| **Core metaphor** | "Talk to your AI" | "Your machine works for you" |
| **Agency** | Reactive — responds to messages | Proactive — morning briefings, pattern compilation, drift detection |
| **Memory** | Plugin slot (no built-in) | Compounding vault: sessions → friction → patterns → scripts |
| **Learning** | None — same capability on day 1 and day 100 | Gets better: execution logs → pattern compiler → 0-token scripts |
| **Goals** | None — no concept of objectives | OKRs with key results, progress tracking, drift detection |
| **Trust** | Binary (allow/deny per tool) | Graduated (shadow → approval → semi-auto → full-auto), per-capability |
| **Projects** | No isolation | Per-project: own bot, agents, goals, config, directory |
| **Observability** | Basic logging | Phoenix tracing, friction reports, execution analytics |
| **Identity** | Generic assistant | Role-based agents with specialized knowledge |

### Where OpenClaw Is Better

1. **Channel breadth**: 22+ channels vs AOS's 3 (Telegram, WhatsApp, iMessage). If you need Discord, Slack, Signal, Matrix, IRC, LINE, Teams — OpenClaw has it.
2. **Cross-platform**: Any OS. AOS is Mac-only by design.
3. **Community**: 328K stars, massive contributor base, skill registry (ClawHub). AOS is custom-built, not publicly distributed.
4. **Native apps**: macOS menu bar, iOS, Android companions. AOS uses Telegram as its primary interface.
5. **Plugin ecosystem**: npm-distributed extensions with formal SDK. AOS has skills but no registry or distribution system.

### Where AOS Is Fundamentally Better

#### 1. The Learning Loop (OpenClaw Has Nothing Like This)

```
Session happens
  → Session exported to vault (every 2h)
  → Friction mined from sessions (weekly)
  → Friction patterns → CLAUDE.md improvements
  → Execution logs analyzed (daily)
  → 3+ similar tasks → compiled to deterministic script (0 tokens)
  → Script added to pattern cache
  → Next time: instant execution, no AI needed

Day 1: Agent spends 500 tokens figuring out how to restart a service
Day 30: Pattern script does it in 0 tokens, instantly
```

OpenClaw processes every request fresh. AOS gets faster and cheaper over time.

#### 2. The Execution Framework (OpenClaw Just Calls Tools)

AOS has a **prioritized execution model**:
```
Zero-token (pattern cache)   → instant, free
  ↓ miss
Low-token (API/CLI/URI)      → fast, cheap
  ↓ miss
Medium-token (AppleScript)   → reliable, moderate
  ↓ miss
High-token (Steer/Chrome)    → visual, expensive
  ↓ miss
Very-high-token (OCR/vision) → last resort
```

Plus the **anti-excuse protocol**: before an agent says "I can't," it must check the capability map and exhaust all approaches. OpenClaw has no equivalent — if a tool call fails, that's it.

#### 3. Multi-Project Isolation (OpenClaw Has Workspaces, Not Projects)

```
AOS per-project isolation:
  ~/nuchay/
  ├── CLAUDE.md          ← Project-specific context
  ├── .claude/agents/    ← Project-specific agents
  ├── config/goals.yaml  ← Project-specific goals
  └── Telegram bot       ← Own @BotFather bot, own forum topic

Goals roll up: project goals → system goals → morning briefing.
Agents are scoped: nuchay-agent can only access ~/nuchay/.
Messages are routed: topic in forum → correct project agent.
```

OpenClaw can route different channels to different agents, but there's no goal rollup, no project-scoped agents, no per-project knowledge isolation.

#### 4. Knowledge Compounding (OpenClaw Is Stateless)

```
AOS knowledge flow:
  Daily notes (mood, energy, sleep, tasks)
  Session summaries (auto-exported every 2h)
  Friction reports (weekly pattern mining)
  Materials (transcripts, articles, research)
  Project notes (per-project knowledge)
  Reviews (weekly, monthly, quarterly)
      ↓
  QMD indexes everything (BM25 + semantic + reranking)
      ↓
  Any agent can search: qmd query "shipping rates" -n 5
      ↓
  Knowledge compounds over time
```

OpenClaw has MEMORY.md (a text file) and optional memory plugins. There's no structured vault, no automatic session export, no friction analysis, no search infrastructure.

#### 5. Proactive Behavior (OpenClaw Waits, AOS Acts)

| Time | AOS Does | OpenClaw Does |
|------|----------|---------------|
| 4:00 AM | Compile patterns from execution logs | Nothing |
| 8:00 AM | Morning briefing with goals, focus, schedule | Nothing |
| Every 30 min | Heartbeat check on all services | Nothing |
| Every 2h | Export sessions to vault | Nothing |
| 9:00 PM | Evening check-in, save to daily note | Nothing |
| Sunday 10PM | Weekly friction analysis | Nothing |

### Security Comparison

Both have serious security models, but they're different in philosophy:

| Aspect | OpenClaw | AOS |
|--------|----------|-----|
| **Secret storage** | Environment variables + `.env` files | macOS Keychain (hardware-backed encryption) |
| **Network exposure** | 127.0.0.1 + Tailscale/SSH (similar) | 127.0.0.1 + Tailscale only (similar) |
| **Auth model** | DM pairing codes per channel | Chat ID lockdown per bot |
| **Execution sandbox** | Optional Docker sandbox | No sandbox, but capability map limits approach selection |
| **Plugin trust** | Install-time trust decision | `/scan-skill` security audit before any external skill |
| **Secret scanning** | CI-only (detect-secrets) | Never in files, never in CLAUDE.md (enforced by convention + review) |
| **Process isolation** | Single Node.js process for everything | Separate LaunchAgent per service |
| **Audit** | `openclaw security audit --deep` | Execution logs + session exports + Phoenix tracing |

**AOS advantage**: macOS Keychain is hardware-backed encryption. Environment variables (OpenClaw's approach) can be read by any process with the right permissions. Keychain items require explicit user/service authorization.

**AOS advantage**: Separate processes per service. If the bridge crashes, the dashboard and listen service keep running. In OpenClaw, one Gateway crash takes down everything.

**OpenClaw advantage**: Formal Docker sandbox mode for execution. AOS relies on trust levels and capability maps rather than process isolation.

**OpenClaw advantage**: `openclaw doctor` and `security audit` commands. AOS doesn't have an automated security self-check.

---

## Part 3: How the Work System Integrates Into AOS

The work system is not a new service. It's **connective tissue** that links existing AOS components.

### Current State: Fragmented

```
Goals (config/goals.yaml) ──── manually updated, no task link
Tasks (vault/tasks/*.md) ──── orphaned, bridge returns placeholder
Daily notes (vault/daily/) ── checkboxes, no rollup
Sessions ────────────────── exported but not linked to tasks
Bridge ──────────────────── /goals works, /tasks is placeholder
Dashboard ───────────────── no task/goal display
Execution logs ──────────── not linked to tasks/goals
Pattern compiler ─────────── not linked to goals
```

### Target State: Connected

```
                    ┌─────────────────┐
                    │   GOALS (OKRs)  │
                    │  with key results│
                    └────────┬────────┘
                             │ rolls up
                    ┌────────▼────────┐
                    │    PROJECTS     │
                    │  with appetite  │
                    │  + hill chart   │
                    └────────┬────────┘
                             │ breaks into
              ┌──────────────▼──────────────┐
              │           TASKS             │
              │  assigned to humans/agents  │
              └──┬──────────┬───────────┬───┘
                 │          │           │
        ┌────────▼──┐  ┌───▼────┐  ┌───▼────────┐
        │ SESSIONS  │  │ CRON   │  │  AUTO-     │
        │ (Claude   │  │ JOBS   │  │  CAPTURE   │
        │  Code)    │  │        │  │ (email,    │
        │           │  │        │  │  messages) │
        └─────┬─────┘  └───┬────┘  └───┬────────┘
              │             │           │
              └─────────────▼───────────┘
                            │
              ┌─────────────▼─────────────┐
              │     EXECUTION LAYER       │
              │  capability map → pattern │
              │  cache → anti-excuse      │
              └─────────────┬─────────────┘
                            │ logs to
              ┌─────────────▼─────────────┐
              │     KNOWLEDGE VAULT       │
              │  sessions, learnings,     │
              │  friction, patterns       │
              └─────────────┬─────────────┘
                            │ feeds
              ┌─────────────▼─────────────┐
              │     REVIEW SYSTEM         │
              │  daily, weekly, quarterly │
              │  drift detection          │
              └─────────────┬─────────────┘
                            │ updates
                    ┌───────▼─────────┐
                    │   GOALS (loop)  │
                    └─────────────────┘
```

### Integration Points (What Connects To What)

#### 1. Bridge ↔ Work System

```
Current:
  /goals → reads config/goals.yaml → shows progress bars ✅
  /tasks → returns placeholder ❌

Target:
  /tasks → reads work files → shows active tasks, grouped by project
  /inbox → shows uncaptured items awaiting triage
  /triage → interactive triage via Telegram buttons
  /add <text> → creates task in inbox
  /done <id> → marks task complete
  /goals → reads work/goals/ → shows OKR progress with key results
  /review → triggers daily/weekly review
  /thread <text> → creates exploration thread
  /drift → shows priority vs actual work comparison
```

The bridge already has `vault_tasks.py` with `get_all_tasks()`, `create_task()`, `update_task_status()`. The work system replaces this with a more capable library that the bridge imports.

#### 2. Dashboard ↔ Work System

```
Current:
  _load_tasks() → returns empty list ❌

Target:
  Dashboard reads work files directly:
  /work/inbox     → triage queue
  /work/today     → today's tasks
  /work/projects  → project cards with progress
  /work/goals     → goal progress bars
  /work/threads   → active explorations
  /work/agents    → agent trust + activity
  /work/metrics   → flow metrics (cycle time, throughput)
  /work/drift     → priority alignment chart
```

#### 3. Claude Code Sessions ↔ Work System

```
Current:
  Sessions are exported to vault as markdown summaries.
  No link between sessions and tasks/goals.

Target:
  SessionStart hook:
    → inject active tasks + current threads into context
    → "You are currently working on: [task list]"

  work-awareness skill (always-on):
    → detects when session work relates to a task
    → updates task progress inline
    → detects when conversation becomes a thread/project

  Stop hook (async):
    → reconcile: what tasks were touched?
    → create entries for implicit work

  SessionEnd hook:
    → final reconciliation
    → link session to tasks it touched
    → update execution log

  PostCompact hook:
    → re-inject active task context after compaction
```

#### 4. Execution Framework ↔ Work System

```
Current:
  execution_log/*.jsonl tracks task executions.
  pattern compiler turns repeated tasks into scripts.
  No link to work system tasks.

Target:
  When an agent executes a task:
    → execution log entry linked to work system task ID
    → pattern compiler considers task metadata (project, type)
    → compiled patterns linked back to task categories
    → execution success/failure updates agent trust metrics

  When pattern cache hits:
    → work system task auto-completed (0 tokens)
    → logged as "pattern execution" in metrics
```

#### 5. Knowledge Vault ↔ Work System

```
Current:
  vault/tasks/*.md exist but disconnected from goals/projects.
  QMD indexes vault content.

Target:
  On task creation:
    → QMD search for related knowledge
    → attach top-k relevant context to task
    → agent receives task WITH relevant vault content

  On task completion:
    → extract learnings → write to vault
    → link task ↔ knowledge note (bidirectional)
    → new knowledge available to future tasks

  Session friction analysis:
    → friction patterns linked to task types
    → "Tasks in project X take 2x longer than estimated"
    → suggest process improvements per project
```

#### 6. Cron System ↔ Work System

```
Current:
  Crons are LaunchAgent plists or bridge-scheduled.
  Not connected to work tracking.

Target:
  Recurring task templates:
    → cron fires → creates task from template
    → assigned to agent or human based on template config
    → completion tracked like any other task

  Review automation:
    → daily cron generates review summary (evening)
    → weekly cron generates GTD-style review document
    → quarterly cron scores goals, generates retrospective draft

  Drift detection:
    → weekly cron compares goal weights vs task completion distribution
    → alerts if drift exceeds threshold
```

#### 7. Per-Project Isolation ↔ Work System

```
Current:
  Each project has config/goals.yaml and config/tasks.yaml (mostly empty).
  No rollup to system level.

Target:
  ~/nuchay/work/           ← Nuchay project work files
  ~/chief-ios-app/work/    ← Chief project work files
  ~/work/                  ← Personal/system-level work files

  Rollup:
    nuchay goal "Launch MVP" (weight 0.3)
      → rolls up to personal goal "Build business" (auto-calculated)
    chief goal "Ship v1.0" (weight 0.2)
      → rolls up to personal goal "AOS development" (auto-calculated)

  Morning briefing:
    → reads all project goals + personal goals
    → shows unified progress view
    → highlights drift across projects

  Agent scoping:
    → nuchay-agent only sees ~/nuchay/work/
    → engineer-agent sees ~/work/ (system level)
    → main agent (Chief) sees everything for rollup
```

### What Changes In Existing AOS Components

| Component | Change Required | Effort |
|-----------|----------------|--------|
| **config/goals.yaml** | Migrate to work/goals/ format. Backward-compatible — can symlink initially. | Small |
| **config/tasks.yaml** | Replace with work/tasks/ (currently empty anyway). | Trivial |
| **vault/tasks/*.md** | Migrate to work/tasks/ format. 8 files to convert. | Small |
| **apps/bridge/vault_tasks.py** | Replace with work system parser library. Same API surface. | Medium |
| **apps/bridge/intent_classifier.py** | Update handle_list_tasks() to use work system. handle_goals() already works, needs to point to new location. | Small |
| **apps/dashboard/main.py** | Update _load_tasks() to read work files. Add goal/project/metrics views. | Medium |
| **bin/session-hook** | Add work system reconciliation to Stop handler. | Small |
| **.claude/settings.json** | Add SessionStart hook for context injection. Add PostCompact hook. | Small |
| **LaunchAgents** | No changes. Work system is a plugin, not a service. | None |
| **MCP servers** | Optional: add work system as MCP server for richer tool integration. | Optional |

### What Stays The Same

- All services (bridge, dashboard, listen, memory, phoenix)
- All LaunchAgents
- All agents (engineer, ops, technician, etc.)
- All existing skills (recall, autonomous-execution, bridge-ops, etc.)
- All bin utilities
- All cron jobs (add new ones, don't change existing)
- Keychain secrets model
- Tailscale network model
- Project registration (config/projects.yaml)
- Knowledge vault structure (add work/, don't restructure existing)

---

## Part 4: Packaging — How Others Get This

### The Distribution Model

AOS itself is the operating system. The work system is a plugin within it. Two distribution paths:

#### Path A: AOS User (Full System)

Someone clones AOS → runs installer → gets everything including the work system.

```bash
# Install AOS on a fresh Mac Mini
git clone <aos-repo> ~/aos
cd ~/aos && ./install.sh

# install.sh does:
#   1. Homebrew dependencies
#   2. Python/Node setup
#   3. LaunchAgent installation
#   4. Keychain initialization
#   5. Work system plugin installation
#   6. First-run onboarding
```

The work system is bundled with AOS. Updates come through `git pull` + `aos update`.

#### Path B: Claude Code User (Plugin Only)

Someone who already uses Claude Code but doesn't have AOS.

```bash
# Install just the work system
claude plugin install work-system
# or
git clone <work-system-repo> ~/.claude/plugins/work-system
```

They get: skills, hooks, CLI, file-based tracking. They don't get: bridge, dashboard, agents, vault, execution framework. But the work system functions independently — those are enhancements, not requirements.

### Update Safety

```
User data:    ~/work/           ← NEVER touched by updates
System code:  ~/.claude/plugins/work-system/  ← Updated via git pull or plugin update
AOS code:     ~/aos/            ← Updated via git pull

Versioning:
  work.yaml has: version: "1.0"
  plugin.json has: min_data_version, max_data_version

Update flow:
  1. Pull new code
  2. Check version compatibility
  3. If migration needed:
     a. Backup: work/ → work.bak.v1.0/
     b. Run stepwise migration (v1.0 → v1.1 → v1.2)
     c. Validate migrated data
     d. Report changes
  4. If incompatible (major version):
     a. Refuse to start
     b. Tell user: "Run: work migrate --to v2"
     c. Migration is explicit, never automatic for breaking changes

Rules:
  - Additive fields: no migration needed (new fields default to null)
  - Renamed fields: automatic migration with backup
  - Removed fields: major version, explicit migration
  - Format changes: major version, explicit migration
  - Schema validation: run on every read, warn on unknown fields
```

### Configuration Isolation (PAI's USER/SYSTEM Pattern)

Borrowed from Personal AI Infrastructure — the cleanest separation pattern:

```
SYSTEM (our code, updated):
  ~/.claude/plugins/work-system/
  ├── skills/         ← How the system works
  ├── hooks/          ← When things fire
  ├── lib/            ← Parser, query, metrics
  ├── migrations/     ← Data format migrations
  └── templates/      ← Default configs, onboarding

USER (their data, never touched):
  ~/work/
  ├── work.yaml       ← Their config + tasks
  ├── projects/       ← Their projects
  ├── goals/          ← Their goals
  └── reviews/        ← Their review history
```

Updates to SYSTEM never touch USER. USER data is theirs forever.

---

## Summary: Why Build This

AOS already has:
- Agents that can execute work (Layer 6)
- An execution framework that makes them efficient (Layer 5)
- A knowledge vault that compounds learning (Layer 4)
- Services that provide interfaces (Layer 2)
- Infrastructure that keeps it all running (Layer 1)

What's missing is **Layer 3 — the work layer** that answers:
- What should be done? (Goals)
- What's being done? (Tasks)
- What got done? (Reviews)
- Is the right stuff getting done? (Drift detection)
- Is it getting done well? (Quality + metrics)

The work system fills this gap. It's not a new service — it's connective tissue that links everything AOS already has into a coherent loop:

**Goals → Tasks → Agents execute → Sessions logged → Knowledge captured → Patterns compiled → Reviews generated → Goals updated.**

That loop is what makes AOS an operating system, not just a collection of services.
