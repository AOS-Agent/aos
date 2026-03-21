# Tadbir — Agentic Task, Project & Goal Infrastructure

**Version**: 0.1 (Architecture Draft)
**Date**: 2026-03-20
**Status**: Awaiting operator approval

---

## Name

**Tadbir** (تدبير) — Arabic for "management, planning, arrangement, wise administration." From the root د-ب-ر (d-b-r), meaning to reflect on the consequences of affairs and arrange them wisely. Used in the Quran: "yudabbiru al-amr" (يُدَبِّرُ الأمر) — "He arranges/manages all affairs" (Yunus 10:3, As-Sajdah 32:5).

The name captures the essence: not just task tracking, but the wise arrangement of affairs with foresight.

---

## What Tadbir Is

Tadbir is **infrastructure for managing work** — not an app, not a SaaS product, but a layer that sits underneath everything. Like Git is infrastructure for code, Tadbir is infrastructure for work.

- Agents read it. Humans read it. Cron jobs read it. Dashboards read it.
- Same data, different interfaces.
- Works if you're one person with a notebook. Works if you're running 10 projects with 50 agents.
- File-based, local-first, version-controlled. Optional sync to cloud tools (Linear, Notion, ClickUp) as visibility layers.

### Design Principles

1. **File-first** — YAML/Markdown in Git. No database required. The filesystem *is* the database.
2. **Agent-native, human-friendly** — Agents are first-class workers, but the system must be usable by a person with zero agents.
3. **Progressive complexity** — Start with a single file. Scale to multi-project, multi-team, multi-agent. Never force complexity on someone who doesn't need it.
4. **Calibration over configuration** — The system learns you before it acts for you. No rigid taxonomy imposed.
5. **Itqan over velocity** — Quality standards on every task. Done means done *well*, not just done.
6. **Barakah-aware scheduling** — Time structure aligned with natural rhythms (prayer times, energy cycles, seasons).
7. **Trust is earned** — Shadow → Approval → Semi-autonomous → Fully autonomous. Per-capability, not per-agent.
8. **Minimum viable overhead** — If maintaining the system takes more than 15 min/day, it's too complex. The system should reduce friction, not add it.

---

## Philosophical Foundation

Tadbir draws from three traditions:

### Islamic Principles (the "why")

| Principle | Application |
|-----------|-------------|
| **Itqan** (إتقان) — excellence in work | Every task has a quality standard, not just a checkbox. "Done" = done well. |
| **Amanah** (أمانة) — trust/responsibility | Task delegation is transfer of sacred trust. Delegate to the competent + trustworthy. |
| **Tawakkul** (توكل) — reliance after action | Plan thoroughly, execute diligently, release attachment to outcomes. Prevents both paralysis and recklessness. |
| **Muhasabah** (محاسبة) — self-accounting | Built-in review cadences: daily (evening), weekly (Jumu'ah), quarterly, annual (Ramadan). |
| **Shura** (شورى) — consultation | Decisions that affect others require genuine consultation before execution. |
| **Maqasid** (مقاصد) — objectives hierarchy | Priority framework: deen → life → intellect → family → wealth. Higher-order objectives override lower. |
| **Fiqh al-Awlawiyyat** — jurisprudence of priorities | Obligations (fard) before recommendations (sunnah) before nice-to-haves (nafl). |
| **Barakah** (بركة) — blessing in time | Early morning work, sincere intention (niyyah), beginning with Bismillah. Schedule around blessed times. |
| **Awqat** (أوقات) — appointed times | Everything has its season. Don't force what isn't ready. Recognize natural rhythms. |

### Business Frameworks (the "how")

| Framework | What we take |
|-----------|-------------|
| **GTD** | The capture → clarify → organize → reflect → engage pipeline. The Weekly Review. Contexts. |
| **OKRs** | Goal → measurable key results. Committed vs aspirational. 0.0-1.0 scoring. |
| **EOS Rocks** | 90-day priorities. Binary on-track/off-track. The IDS process (Identify, Discuss, Solve). |
| **Kanban** | WIP limits. Flow metrics (cycle time, throughput). Little's Law. |
| **Shape Up** | Appetite-based scoping ("how much is this worth?"). No infinite backlog. Hill charts. |
| **PARA** | Projects (completable) vs Areas (ongoing) distinction. Archive aggressively. |
| **Eisenhower** | Urgent/Important matrix for daily triage. Q2 (important + not urgent) is where leverage lives. |
| **RICE** | Multi-factor scoring for backlog prioritization when you have too many good ideas. |

### Agentic Patterns (the "what")

| Pattern | Source | Application |
|---------|--------|-------------|
| **Filesystem as memory** | Manus | Write everything important to disk. Context windows are volatile. |
| **Claim → Execute → Release** | TICK.md | Coordination protocol for multi-agent work. TTL on claims. |
| **Confidence-gated capture** | Superhuman | High confidence → auto-create. Medium → confirm. Low → log for review. |
| **Fixed categories, custom names** | Linear | Status categories are fixed (backlog/active/done/cancelled). Names flex per user/team. |
| **Attention recitation** | Manus todo.md | Rewrite objectives at end of context to prevent goal drift in long sessions. |
| **Single in-progress constraint** | Claude Code | Exactly one task active per worker at any time. Prevents context thrashing. |
| **Progressive trust ramp** | AOS trust.yaml | Shadow → Approval → Semi-autonomous → Fully autonomous. Per-capability. |

---

## Data Model

### The Hierarchy

```
Life Purpose / Mission
  └── Areas of Responsibility (ongoing, never "done")
        └── Goals (time-bound outcomes, quarterly/annual)
              └── Projects (bounded scope, has a finish line)
                    └── Tasks (actionable units of work)
                          └── Subtasks (atomic steps)
```

Plus two cross-cutting concepts:
- **Inbox** — uncategorized captures awaiting triage
- **Recurring Templates** — patterns that generate tasks on schedule

### Core Entities

#### Area

An ongoing domain of responsibility. You never "complete" an area — you maintain a standard.

```yaml
# areas/health.yaml
id: health
name: Health & Fitness
standard: "Exercise 4x/week, sleep 7+ hours, annual checkup done"
maqsad: nafs            # Which maqasid objective this serves
review_cadence: weekly
metrics:
  - key: exercise_sessions
    target: 4
    unit: per_week
  - key: sleep_hours
    target: 7
    unit: per_night
```

Examples: Health, Family, Faith, Career, Finances, Education, Home, Relationships.

#### Goal

A time-bound outcome with measurable key results. Lives within an area.

```yaml
# goals/2026-q1-launch-nuchay.yaml
id: launch-nuchay
title: "Launch Nuchay MVP to first 10 customers"
area: career
type: committed          # committed | aspirational
timeframe:
  start: 2026-01-01
  end: 2026-03-31
key_results:
  - id: kr1
    description: "Shopify store live with 20+ products"
    target: 20
    current: 12
    unit: products
  - id: kr2
    description: "First 10 paying customers"
    target: 10
    current: 0
    unit: customers
  - id: kr3
    description: "Email list at 500 subscribers"
    target: 500
    current: 180
    unit: subscribers
progress: 0.35           # Auto-calculated from key results
priority_weight: 0.25    # Relative weight among concurrent goals
```

#### Project

A bounded effort with a definition of done. Belongs to a goal (or standalone under an area).

```yaml
# projects/nuchay-shopify-store.yaml
id: nuchay-shopify-store
title: "Set up Nuchay Shopify store"
goal: launch-nuchay
status: active           # draft | active | paused | completed | cancelled
appetite: 2w             # Shape Up: how much time is this worth?
definition_of_done: "Store live, products listed, checkout working, connected to shipping"
started_at: 2026-02-15
due_date: 2026-03-15
owner: hisham
hill_position: downhill  # uphill (figuring out) | peak | downhill (executing)
tasks_summary:           # Auto-generated
  total: 12
  done: 7
  in_progress: 2
  blocked: 1
```

#### Task

The atomic unit of actionable work. This is where humans and agents live.

```yaml
# tasks/setup-shipping-rates.yaml
id: setup-shipping-rates
title: "Configure shipping rates for Canadian orders"
project: nuchay-shopify-store
status: todo             # inbox | todo | active | waiting | done | cancelled
priority: 2              # 0=none, 1=urgent, 2=high, 3=normal, 4=low
assignee: nuchay-agent   # human ID or agent ID — same field
assignee_type: agent     # human | agent | unassigned
created_by: hisham
created_at: 2026-03-15T09:00:00Z
due_date: 2026-03-20
context: "@computer"     # GTD context: where/what you need
energy: low              # high | medium | low — what energy level this needs
time_estimate: 30m       # ISO 8601 duration
actual_time: null
quality_standard: "All provinces covered, rates match Canada Post calculator"

# Dependencies
blocked_by: []
blocks: [test-checkout-flow]

# Recurrence (if recurring)
recurrence: null         # cron string or null

# Source (how was this created?)
source:
  type: manual           # manual | email | message | calendar | cron | agent
  ref: null              # link to source message/email/event

# Trust gate (for agent-assigned tasks)
trust_gate: approval     # shadow | approval | semi_auto | full_auto
```

#### Inbox Item

Raw capture before triage. Lightweight — just enough to not lose it.

```yaml
# inbox/1710936000.yaml (timestamp-named)
id: 1710936000
raw: "Need to call the insurance company about the car renewal"
source:
  type: message          # manual | email | message | voice | calendar
  channel: whatsapp
  from: "+1234567890"
  timestamp: 2026-03-20T14:00:00Z
  ref: "msg-abc123"
classification:          # Filled by triage (agent or human)
  type: null             # task | project | goal | reference | delegate | trash
  confidence: null       # 0.0-1.0
  suggested_project: null
  suggested_priority: null
  suggested_due: null
triaged: false
triaged_at: null
triaged_by: null         # human or agent ID
```

### Status Model (Fixed Categories, Flexible Names)

Following Linear's pattern — categories are structural and fixed. Display names are customizable.

```yaml
# Core status categories (these never change):
statuses:
  inbox:      # Uncategorized, needs triage
    default_name: "Inbox"
    terminal: false
  backlog:    # Acknowledged but not planned
    default_name: "Backlog"
    terminal: false
  todo:       # Planned, ready to pick up
    default_name: "Todo"
    terminal: false
  active:     # Currently being worked on
    default_name: "Active"
    terminal: false
  waiting:    # Blocked on external input
    default_name: "Waiting"
    terminal: false
  done:       # Completed successfully
    default_name: "Done"
    terminal: true
  cancelled:  # Will not be done
    default_name: "Cancelled"
    terminal: true
```

### Priority Model (Islamic + Business Hybrid)

```yaml
# Priority levels (mapped to both systems):
priorities:
  0:
    name: "None"
    islamic: null
    business: "Unset"
  1:
    name: "Urgent"
    islamic: "Fard Ayn — individual obligation, serious consequences if not done"
    business: "P0 — drop everything"
    sla_hours: 4
  2:
    name: "High"
    islamic: "Fard Kifayah — communal obligation, someone must do it"
    business: "P1 — this week"
    sla_hours: 24
  3:
    name: "Normal"
    islamic: "Sunnah — recommended, brings reward"
    business: "P2 — this sprint/cycle"
    sla_hours: 72
  4:
    name: "Low"
    islamic: "Nafl — voluntary, nice to do"
    business: "P3 — when capacity allows"
    sla_hours: 168
```

---

## Directory Structure

Tadbir uses a simple directory layout that works at any scale.

### Minimal (one person, no agents)

```
~/tadbir/
├── tadbir.yaml          # Config (name, timezone, preferences)
├── inbox/               # Raw captures
├── tasks/               # Active tasks (one file each)
├── archive/             # Completed/cancelled tasks
└── reviews/             # Weekly/monthly/quarterly reviews
```

### Standard (one person, agents, multiple projects)

```
~/tadbir/
├── tadbir.yaml          # Global config
├── inbox/               # Uncategorized captures
├── areas/               # Areas of responsibility
├── goals/               # Goals with key results
├── projects/            # Project definitions
├── tasks/               # All tasks (flat, not nested in projects)
├── templates/           # Recurring task templates
├── archive/             # Completed items (auto-moved)
├── reviews/             # Review notes
├── agents/              # Agent trust configs
│   ├── trust.yaml       # Trust levels per agent per capability
│   └── assignments.yaml # Current agent assignments
├── sync/                # MCP sync state (Linear, Notion, etc.)
└── .tadbir/             # Internal state
    ├── locks/           # Task claim locks (TTL-based)
    ├── history/         # Append-only action log
    └── metrics/         # Flow metrics (cycle time, throughput)
```

### Multi-project / business

```
~/tadbir/                # Personal/system-level
├── tadbir.yaml
├── areas/
├── goals/
├── ...

~/nuchay/tadbir/         # Project-level (goals roll up to personal)
├── tadbir.yaml          # Project config
├── goals/
├── projects/
├── tasks/
├── ...

~/other-business/tadbir/ # Another project
├── ...
```

Goal rollup: each project's `tadbir/goals/` can reference parent goals in `~/tadbir/goals/`. Progress flows upward automatically.

---

## The Pipelines

### Pipeline 1: Capture

Everything enters through capture. Zero friction. Multiple input channels.

```
Sources:
  Manual entry (CLI, Telegram, dashboard, voice)
  Email (IMAP polling or forwarding)
  Messages (WhatsApp, iMessage, Telegram)
  Calendar (event → pre/post tasks)
  Agent output (agent creates task during execution)
  Cron (recurring templates fire on schedule)
  Web clipper (browser extension)
      │
      ▼
  ┌─────────┐
  │  INBOX  │  ← Raw YAML files, timestamped
  └────┬────┘
       │
       ▼
  Classification (NLP or human)
       │
       ├── High confidence (>0.85) ──→ Auto-create task (if trust level allows)
       ├── Medium confidence (0.5-0.85) ──→ Suggest + ask for approval
       └── Low confidence (<0.5) ──→ Log for manual review
```

### Pipeline 2: Triage

The GTD "clarify" step, adapted for agents.

```
  Inbox Item
      │
      ▼
  Is it actionable?
      │
      ├── No ──→ Is it reference? ──→ File to knowledge vault
      │          Is it trash? ──→ Delete
      │          Is it someday/maybe? ──→ Backlog with P4
      │
      └── Yes ──→ What's the next action?
                      │
                      ├── < 2 minutes? ──→ Do it now (or agent does it now)
                      ├── Multi-step? ──→ Create project + first task
                      ├── Someone else? ──→ Delegate (assign to human or agent)
                      └── Single step ──→ Create task with:
                                           - Project assignment
                                           - Priority (1-4)
                                           - Context (@computer, @phone, etc.)
                                           - Energy level (high/med/low)
                                           - Due date (if applicable)
                                           - Quality standard
```

### Pipeline 3: Execution

How work gets done — by humans or agents.

```
  Available Tasks (status: todo, not blocked)
      │
      ▼
  Worker Selection
      │
      ├── Human ──→ Picks from filtered view (context + energy + priority)
      │              Moves to "active"
      │              Does the work
      │              Marks "done" (with quality self-check)
      │
      └── Agent ──→ Claims task (lock with TTL)
                     Checks trust gate:
                       shadow: observe only, log what it would do
                       approval: do the work, present for review before marking done
                       semi_auto: do + mark done, escalate on uncertainty
                       full_auto: do + mark done, report on completion
                     Executes
                     Updates progress
                     Releases claim
                     Unblocks dependent tasks
```

### Pipeline 4: Review (Muhasabah Cycle)

Built-in review cadences that compound over time.

```
  ┌─────────────────────────────────────────────────┐
  │                  REVIEW CADENCE                  │
  ├─────────────┬───────────────┬───────────────────┤
  │   Daily     │   Weekly      │   Quarterly       │
  │  (Evening)  │  (Jumu'ah)    │   (90-day Rock)   │
  ├─────────────┼───────────────┼───────────────────┤
  │ • What got  │ • GTD Weekly  │ • Score OKRs      │
  │   done?     │   Review      │ • Close Rocks     │
  │ • What's    │ • All projects│ • Set next quarter│
  │   tomorrow? │   have next   │ • Review areas    │
  │ • Muhasabah │   action?     │ • Adjust strategy │
  │ • Adhkar    │ • Inbox zero  │ • Retrospective   │
  │ • Niyyah    │ • Someday/    │ • Update appetite │
  │   for       │   maybe check │   estimates       │
  │   tomorrow  │ • Surah       │                   │
  │             │   al-Kahf     │                   │
  │             │ • Metric      │                   │
  │             │   review      │                   │
  └─────────────┴───────────────┴───────────────────┘
                        │
                        ▼
              Annual Review (Muharram / Ramadan)
              • Wheel of Life assessment
              • Area standards audit
              • Life purpose alignment check
              • Major goal setting
```

---

## Trust & Autonomy Model

### The Trust Ramp

Every agent starts at **Shadow** and earns its way up. Trust is **per-capability**, not per-agent — an engineering agent might be Level 3 for code changes but Level 1 for infrastructure provisioning.

```
Level 0: SHADOW
  Agent observes. Logs what it would do. Takes no action.
  Human sees: "Agent would have created task: 'Follow up with supplier'"
  Purpose: Calibration. System learns your patterns.
  Graduation: After 20 observations where agent's suggestion matches human action.

Level 1: APPROVAL
  Agent proposes actions. Human approves or rejects each one.
  Human sees: "Agent suggests: Create task 'Follow up with supplier' — P2, due Friday. [✓ Approve] [✗ Reject] [✎ Edit]"
  Purpose: Building trust. System learns your preferences.
  Graduation: After 30 consecutive approvals with zero reverts.

Level 2: SEMI-AUTONOMOUS
  Agent acts on high-confidence items (>0.85). Asks on uncertain ones.
  Human sees: "Agent created 3 tasks from today's emails. 1 item needs your input: [Review]"
  Purpose: Reducing human load while maintaining quality.
  Graduation: After 50 autonomous actions with <2% revert rate.

Level 3: FULLY AUTONOMOUS
  Agent handles everything. Escalates only on defined exceptions.
  Human sees: Weekly summary of agent actions. Exception alerts.
  Exceptions (always escalate):
    - Financial commitment > $X
    - External communication on behalf of human
    - Deleting/archiving items older than 7 days
    - Changing goal priorities
    - Any action flagged in personal exception list
```

### Trust Configuration

```yaml
# agents/trust.yaml
agents:
  nuchay-agent:
    capabilities:
      task_creation: approval        # Can suggest tasks, human approves
      task_completion: semi_auto     # Can mark tasks done for routine items
      project_updates: shadow        # Just learning the patterns
      email_responses: shadow        # Definitely not ready yet
      shopify_management: approval   # Can suggest changes, human approves
    stats:
      total_actions: 47
      approved: 42
      rejected: 3
      reverted: 2
      approval_rate: 0.89
      revert_rate: 0.04
    last_promotion: 2026-03-01
    next_review: 2026-04-01

  engineer-agent:
    capabilities:
      task_creation: semi_auto
      code_changes: semi_auto
      infrastructure: approval       # Higher stakes, keep human in loop
      service_restarts: approval
    stats:
      total_actions: 156
      approved: 150
      rejected: 4
      reverted: 2
      approval_rate: 0.96
      revert_rate: 0.01
```

---

## Time Structure (Barakah-Aware Scheduling)

Tadbir understands that not all hours are equal.

### Prayer-Anchored Time Blocks

```yaml
# tadbir.yaml (user config)
schedule:
  timezone: America/Toronto
  prayer_times: auto      # Calculate from location, or manual

  blocks:
    - name: "Dawn Deep Work"
      after: fajr
      before: sunrise
      type: deep_work
      energy: peak
      notes: "Most blessed window. Highest barakah. Reserved for most important work."

    - name: "Morning Block"
      after: sunrise
      before: dhuhr
      type: deep_work
      energy: high
      notes: "Primary productive hours."

    - name: "Midday"
      after: dhuhr
      before: asr
      type: light_work
      energy: medium
      notes: "Qailulah (power nap) after Dhuhr. Admin tasks, meetings, email."

    - name: "Afternoon Block"
      after: asr
      before: maghrib
      type: mixed
      energy: medium
      notes: "Second wind. Good for collaborative work."

    - name: "Evening"
      after: maghrib
      before: isha
      type: family
      energy: low
      notes: "Family time. No work unless urgent."

    - name: "Night"
      after: isha
      until: "22:00"
      type: review
      energy: low
      notes: "Muhasabah, planning tomorrow, light reading."
```

### Task-Block Matching

When an agent (or the system) suggests what to work on next, it considers:
1. **Current time block** — what type of work fits now?
2. **Task energy level** — match high-energy tasks to high-energy blocks
3. **Task context** — @computer tasks only during work blocks
4. **Priority** — urgent overrides time block preferences
5. **WIP limit** — never suggest new work if WIP limit is hit

---

## Onboarding Flow

### Phase 1: Setup (Day 0)

```
Welcome to Tadbir.

Step 1: Tell me about yourself.
  - What's your name?
  - What timezone are you in?
  - Do you pray 5 daily prayers? (adjusts time blocks)
  - What's your work schedule? (teaching, 9-5, freelance, etc.)

Step 2: Connect your sources (optional — each one unlocks capture).
  □ Email (IMAP credentials)
  □ WhatsApp (pair device)
  □ Telegram (bot token)
  □ Calendar (CalDAV or Google)
  □ iMessage (local access)

Step 3: Brain dump.
  "Tell me everything on your mind. Projects, tasks, worries, ideas, dreams.
   Don't organize — just dump. I'll help you sort it."

  → System captures everything to inbox
  → Guided triage session: "Is this a task, a project, or just a thought?"
  → Areas auto-detected from patterns
```

### Phase 2: Calibration (Week 1-2) — Shadow Mode

```
System watches. Learns. Suggests nothing yet.

What it's learning:
  - What you consider urgent vs. normal
  - How you name things
  - Your natural review cadence
  - Which emails generate tasks
  - Which messages are actionable vs. social
  - Your typical project scope
  - How you break down work

End of Week 1:
  "I've been watching how you work. Here's what I've learned:
   - You tend to prioritize [X] over [Y]
   - You check email at [times]
   - Your projects usually have [N] tasks
   - You prefer [short/long] task descriptions

   Does this sound right? [Adjust]"
```

### Phase 3: Suggestions (Week 2-4) — Approval Mode

```
System starts suggesting. Human approves/rejects every suggestion.

  "New email from supplier. This looks like a task:
   'Follow up on order #4521 — shipping delayed'
   Priority: High
   Due: Tomorrow
   Project: Nuchay Operations
   [✓ Approve] [✗ Reject] [✎ Edit]"

Each approval/rejection refines the model.
After 30 consecutive good suggestions → promote to Semi-Autonomous.
```

### Phase 4: Semi-Autonomous (Month 2+)

```
System handles routine captures automatically.
Asks on uncertain items only.

  "Today I auto-created 5 tasks from your inbox.
   3 from email, 1 from WhatsApp, 1 recurring.
   1 item I'm not sure about — can you check? [Review]"
```

### Phase 5: Fully Autonomous (Month 3+, earned)

```
System runs. You get a weekly summary.
Exceptions always escalate.

  Weekly Tadbir Report:
  ─────────────────────
  Tasks created: 23 (18 auto, 5 manual)
  Tasks completed: 19
  Projects advanced: 4/6
  Goals on track: 3/4 (Nuchay email list behind — need 320 more subscribers)

  Exceptions this week: 1
    → Supplier sent contract amendment. Needs your review. [Open]
```

---

## Sync Layer (Optional Visual Interfaces)

Tadbir files are the source of truth. External tools are mirrors.

### MCP Adapters

```
tadbir/sync/
├── linear.yaml         # Sync config for Linear
├── notion.yaml         # Sync config for Notion
├── clickup.yaml        # Sync config for ClickUp
└── dashboard.yaml      # Built-in web dashboard config
```

Each adapter:
1. **Reads** tadbir files → pushes to external tool
2. **Watches** external tool → pulls changes back to files
3. **Resolves conflicts** — tadbir file timestamp wins (local-first)

### Sync Rules

```yaml
# sync/linear.yaml
adapter: linear
api_key_ref: "keychain:linear-api-key"   # Never stored in file
workspace: "my-workspace"
sync:
  direction: bidirectional
  conflict_resolution: local_wins
  mappings:
    task.status.todo: "Todo"
    task.status.active: "In Progress"
    task.status.done: "Done"
    task.priority.1: "Urgent"
    task.priority.2: "High"
    task.priority.3: "Medium"
    task.priority.4: "Low"
  filters:
    include_projects: [nuchay-*]    # Only sync Nuchay tasks
    exclude_tags: [personal]        # Never sync personal tasks
```

### Built-in Dashboard

For users who don't want Linear/Notion — a simple web view that reads tadbir files directly.

```
http://localhost:4096/tadbir/

Views:
  /inbox          → Items awaiting triage
  /today          → Tasks for today (filtered by time block + energy + priority)
  /projects       → Project cards with hill charts
  /goals          → Goal progress with key result bars
  /areas          → Area health indicators
  /review         → Review templates (daily, weekly, quarterly)
  /agents         → Agent trust levels + activity log
  /metrics        → Flow metrics (cycle time, throughput, WIP)
```

---

## Knowledge Integration

Tasks don't exist in a vacuum — they connect to knowledge.

```
Task Created
    │
    ▼
Embed task title + description (vector)
    │
    ▼
Search knowledge vault (QMD / vector DB)
    │
    ▼
Attach top-k relevant context snippets
    │
    ▼
Agent receives task WITH context
    │
    ▼
On task completion:
  → Extract learnings → write to vault
  → Update related knowledge
  → Link task to knowledge note (bidirectional)
```

### Example Flow

1. Task: "Research shipping options for Nuchay"
2. System searches vault → finds: previous supplier conversation notes, pricing research, Canada Post rate card
3. Agent receives task + context → doesn't start from zero
4. Agent completes research → new findings written to `vault/projects/nuchay/shipping-research.md`
5. Task linked to knowledge note: `knowledge_refs: [vault/projects/nuchay/shipping-research.md]`

---

## Metrics & Observability

### Flow Metrics (Kanban)

```yaml
# .tadbir/metrics/weekly-2026-w12.yaml
period: 2026-W12
throughput: 23          # Tasks completed
avg_cycle_time: 2.3d    # From active → done
avg_lead_time: 4.1d     # From inbox → done
wip_avg: 4.2            # Average concurrent active tasks
wip_max: 7              # Peak concurrent active tasks
blocked_time: 12h       # Total time tasks spent in "waiting"
```

### Goal Health

```yaml
# Auto-generated from goal files
goals_health:
  - id: launch-nuchay
    progress: 0.35
    on_track: false      # Based on time elapsed vs progress
    risk: "Email list growth rate insufficient. Need 320 more in 11 days."
    suggested_action: "Increase email capture touchpoints or run targeted ad"
```

### Agent Performance

```yaml
# Per agent, per period
agent_metrics:
  nuchay-agent:
    tasks_completed: 12
    avg_quality_score: 0.87    # Based on approval/revert rates
    avg_execution_time: 45m
    escalation_rate: 0.08
    trust_trajectory: improving  # Based on last 30 actions
```

---

## CLI Interface

```bash
# Capture
tadbir add "Call insurance about car renewal"
tadbir add "Research shipping options" --project nuchay --priority 2

# Triage
tadbir inbox                    # Show inbox items
tadbir triage                   # Interactive triage session

# Work
tadbir next                     # What should I work on now? (considers time block, energy, priority)
tadbir active                   # What's currently in progress?
tadbir done <task-id>           # Mark complete

# Review
tadbir today                    # Today's plan
tadbir week                     # Weekly view
tadbir review daily             # Guided daily review
tadbir review weekly            # Guided weekly review (GTD-style)
tadbir review quarterly         # Guided quarterly planning

# Goals
tadbir goals                    # Goal progress overview
tadbir drift                    # Compare stated priorities vs actual work

# Agents
tadbir agents                   # Agent trust levels and activity
tadbir claim <task-id>          # Agent claims a task
tadbir release <task-id>        # Agent releases a claim

# Metrics
tadbir metrics                  # Flow metrics dashboard
tadbir health                   # System health overview

# Sync
tadbir sync                     # Push/pull from connected tools
tadbir sync status              # Check sync health
```

---

## Implementation Phases

### Phase 1: Foundation (Week 1-2)
- [ ] Define file format schemas (YAML specs for each entity)
- [ ] Build `tadbir` CLI (core commands: add, inbox, triage, next, done, goals)
- [ ] Build file reader/writer library (Python — used by CLI, bridge, dashboard, agents)
- [ ] Migrate existing AOS tasks/goals to tadbir format
- [ ] Basic daily/weekly review templates

### Phase 2: Agent Integration (Week 3-4)
- [ ] Claim/release protocol with TTL-based locks
- [ ] Trust gate system (shadow, approval, semi-auto, full-auto)
- [ ] Agent assignment logic (which agent gets which task?)
- [ ] Bridge integration (Telegram commands: /tasks, /goals, /inbox, /triage)
- [ ] Dashboard integration (tadbir views on :4096)

### Phase 3: Auto-Capture (Week 5-6)
- [ ] Email → inbox pipeline (IMAP polling + NLP classification)
- [ ] WhatsApp → inbox pipeline (message scanning + task detection)
- [ ] Calendar → task generation (pre/post meeting tasks)
- [ ] Confidence-gated routing (auto/suggest/log based on confidence)

### Phase 4: Knowledge & Metrics (Week 7-8)
- [ ] Knowledge context enrichment (QMD search on task creation)
- [ ] Flow metrics computation (cycle time, throughput, WIP)
- [ ] Goal health tracking (progress vs time, risk detection)
- [ ] Review cadence automation (daily prompt, weekly template, quarterly guide)

### Phase 5: Sync & Scale (Week 9-10)
- [ ] Linear MCP adapter
- [ ] Notion MCP adapter
- [ ] Multi-project rollup (project-level tadbir → personal tadbir)
- [ ] Onboarding flow (guided setup for new users)
- [ ] Packageable installer (clone + run for friends)

---

## What Tadbir Is NOT

- **Not an app** — it's infrastructure. Apps (Linear, Notion, dashboard) are optional views.
- **Not a database** — it's files. YAML and Markdown in a directory.
- **Not a framework** — it doesn't force a methodology. Use GTD, OKRs, Kanban, or nothing. Tadbir adapts.
- **Not SaaS** — it runs on your machine. Your data stays with you.
- **Not just for techies** — a teacher with a Mac Mini should be able to use it. A chef with no coding experience should be able to use it. The CLI is the power interface; Telegram and the dashboard are the accessible ones.

---

## Open Questions

1. **File format**: YAML frontmatter + Markdown body (like vault tasks now) vs pure YAML? Pure YAML is cleaner for agents; Markdown body is nicer for humans writing descriptions.
2. **Task ID format**: UUID vs sequential (ENG-123) vs timestamp-based? Sequential is human-friendly but needs a counter. UUID is conflict-free but ugly.
3. **Single file vs directory for inbox**: One big `inbox.yaml` with array vs one file per item? One file per item is better for concurrent agent access but noisier.
4. **Cron integration**: Should tadbir own its own cron scheduler, or hook into system cron/launchd?
5. **Voice capture**: Should Phase 1 include voice → text → inbox? (Whisper is already available.)

---

## References

### Islamic
- Yusuf al-Qaradawi, *Fiqh al-Awlawiyyat* (Jurisprudence of Priorities)
- Al-Shatibi, *Al-Muwafaqat* (Maqasid al-Shariah)
- Mohammed Faris, *The Productive Muslim* and *The Barakah Effect*
- Quran: Yunus 10:3, As-Sajdah 32:5, An-Nisa 4:58, 4:103, Al-Ahzab 33:72, Ash-Shura 42:38, Ali Imran 3:159
- Hadith: Itqan (Tabarani), Tawakkul (Tirmidhi), Barakah in mornings (Abu Dawud), Amanah (Bukhari), Muhasabah (Umar)

### Business
- David Allen, *Getting Things Done*
- Tiago Forte, *Building a Second Brain* (PARA method)
- Gino Wickman, *Traction* (EOS)
- John Doerr, *Measure What Matters* (OKRs)
- Ryan Singer, *Shape Up* (Basecamp)
- Brian Moran, *The 12 Week Year*
- Cal Newport, *Deep Work* (Time blocking)

### Technical
- Manus architecture (context engineering blog, reverse-engineered prompts)
- Claude Code internals (TodoWrite schema, Tasks API)
- Linear data model (GraphQL schema, triage workflow)
- TICK.md protocol (claim/release, file locking)
- Mem0 graph memory (dual-storage, hybrid retrieval)
