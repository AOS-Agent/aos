# Qareen — Architecture Reference

**Version:** 0.1 | **Date:** 2026-03-30 | **Status:** Active

Qareen is an AI-powered operating system for individuals and businesses. AOS is the engine. Qareen is the product.

---

## What Qareen Is

A single process (FastAPI, port 7603) that serves:
- A live intelligence screen (voice, cards, real-time context)
- API endpoints for all system management
- An agent platform (autonomous workers for marketing, accounting, CS, operations)
- An overnight processor (reconciliation, pattern discovery, competitive intelligence)

Installed on a Mac (Mini, Studio, or cluster). Maintained remotely via Tailscale. Closed-source, installed as a service for clients.

---

## System Layers

```
QAREEN          Screen, Voice, Telegram, Terminal
BRAIN           Intelligence Engine, Agents, Skills, Trust
NERVOUS SYSTEM  Event Bus, Actions, Audit, Pipelines, Notifications
ONTOLOGY        Typed Objects, Relationships, Unified Search
MEMORY          Vault, Work, People, Messages, Sessions
CHANNELS/TOOLS  Telegram, WhatsApp, Email, Slack, Klaviyo, Shopify, etc.
INFRA           Services, Crons, Config, Storage, Updates
```

---

## Data Architecture

### Three databases + vault

| Store | Technology | What's in it |
|-------|-----------|-------------|
| `people.db` | SQLite (existing) | 1,089 contacts, 15 tables. Read by People adapter. |
| `qareen.db` | SQLite (new, 35 tables) | Work, sessions, governance, links, agents, pipelines, people extensions, metrics, intelligence, self-improvement |
| `comms.db` | SQLite (new, 3 tables) | Cross-channel messages, conversations, entity extraction |
| `vault/` | Markdown + QMD | Knowledge — notes, research, decisions, procedures. Virtual objects (never duplicated to SQLite). |
| `config/` | YAML | Operator profile, accounts, integrations, trust. Read directly. |

### Object types (19)

| Type | Store | Description |
|------|-------|-------------|
| Person | people.db | Contact — client, donor, team member, supplier |
| Task | qareen.db | Atomic unit of work with assignee, context, energy, quality |
| Project | qareen.db | Bounded effort with definition of done |
| Goal | qareen.db | Time-bound objective with key results |
| Area | qareen.db | Permanent domain (Health, Marketing, Operations) — never "done" |
| Workflow | qareen.db | Reusable template that generates tasks when triggered |
| WorkflowRun | qareen.db | Single execution of a workflow |
| Message | comms.db | Cross-channel message (WhatsApp, Telegram, Email, Slack) |
| Conversation | comms.db | Thread-level container for messages |
| Note | vault/ | Knowledge artifact (capture, research, synthesis, expertise) |
| Decision | vault/ | Locked conclusion with rationale and stakeholders |
| Procedure | qareen.db | Executable SOP — step-by-step, not prose |
| Session | qareen.db | Work session with transcript and outcomes |
| Agent | markdown files | AI worker with skills, tools, trust level |
| Channel | discovered | Communication adapter |
| Integration | discovered | Tool adapter |
| PipelineEntry | qareen.db | Person's position in a named process (sales, hiring, donor) |
| Reminder | qareen.db | Scheduled follow-up linked to a person |
| Transaction | qareen.db | Financial record linked to a person |

### Relationship graph (links table)

All relationships between objects are stored in qareen.db's `links` table:

```sql
links(id, link_type, from_type, from_id, to_type, to_id, direction, properties, created_at, created_by)
```

Indexed on `(from_type, from_id, link_type)` and `(to_type, to_id, link_type)`. Supports cross-store references (person in people.db linked to task in qareen.db). Also stores provenance chains (utterance → intent → card → action → result).

### 24 link types

Person links: assigned_to, mentioned_in, created_by, sent_by, sent_to, about, member_of, client_of, reports_to, knows, referred_by

Task links: belongs_to, blocks, subtask_of, worked_on_in, resulted_in

Note links: mentions, references, links_to, scoped_to

Other: received_via, uses, run_by, participants

---

## Core Systems

### Work (Tadbir model)

Six abstractions: **Inbox → Area → Objective → Project → Task → Workflow**

- **Inbox**: raw captures before triage
- **Area**: permanent domain (Health, Marketing, Operations). Never completed. Has a standard.
- **Objective**: time-bound goal with key results (formerly "Goal"). Quarterly.
- **Project**: bounded effort with definition of done. Has appetite.
- **Task**: atomic action with status, priority, assignee, context (@computer/@phone), energy (high/med/low), quality standard
- **Workflow**: reusable template that generates tasks on trigger (manual, scheduled, or event)

Status model (fixed categories): inbox → backlog → todo → active → waiting → done → cancelled

Full spec: `~/vault/knowledge/references/tadbir-architecture.md`

### People (CRM model)

Six concepts: **Person → Relationship → Interaction → Pipeline Entry → Reminder → Transaction**

- **Person**: stable core, one record per human, never duplicated. Context lives on edges, not the node.
- **Relationship**: typed edge (friend/client/vendor/donor/team). Links person to projects and other people.
- **Interaction**: channel-agnostic timestamped log, bridged to comms.db via message reference.
- **Pipeline Entry**: person's position in a named process (sales/hiring/donor cultivation). Stages on the entry, NOT the person.
- **Reminder**: scheduled follow-up. What separates a CRM from a phonebook.
- **Transaction**: financial record. Lifetime value computed, not stored on person.

Pipeline definitions are configurable per business type (sales, hiring, donor cultivation, client lifecycle).

### Knowledge (Vault model)

Stage pipeline (personal maturity): capture → triage → research → synthesis → decision → expertise

Operational types (business-facing, bypass the pipeline): procedure, template, policy, FAQ

Authority dimension: audience (personal/team/public), verified_by, verified_date, review_interval

Promotion from personal → business knowledge is an explicit "publish" action that adds authority metadata.

Full spec: `~/vault/knowledge/decisions/2026-03-27-vault-restructure-v3.md`

### Communication (Unified Comms model)

Channel adapters → Message bus → Conversations → Messages with person_id → Entity extraction → Task creation

- **Conversation**: the unit of work (not individual messages). Person-anchored threading.
- **Message**: carries person_id (resolved), intent (request/info/social/commitment), urgency (0-3).
- **Commitment extraction**: "I'll send it tomorrow" → auto-create follow-up task.

Full spec: `~/vault/knowledge/initiatives/unified-comms.md`

---

## Governance

### Trust levels (per agent, per action type)

| Level | Behavior |
|-------|----------|
| 0 — Observe | Logs what it would do. No action. |
| 1 — Surface | Shows recommendations. Human decides. |
| 2 — Draft | Prepares proposed actions. Human approves/edits/dismisses. |
| 3 — Act with digest | Executes routine cases. Human gets daily digest. |
| 4 — Act with audit | Broader autonomy. Anomaly detection flags exceptions. |
| 5 — Autonomous | Full scope. Policy-based, not per-action. Rare. |

Trust stored per `(agent_id, action_type)` pair. Circuit breaker: 2 corrections in 5 actions → instant demotion.

### Approval queue

First-class table. Agent queues action → SSE pushes to UI → operator approves/rejects → action executes. Low-risk approvals auto-expire. Agent does NOT block waiting.

### Audit trail

Every action logged: who, when, what, why (context), outcome, cost (tokens, USD). Append-only. Full provenance chain traceable from utterance to result.

---

## Intelligence

### Live context surfacing

Entity detection is fast NER (not LLM). Pre-built context cards cached in qareen.db. Surfaced in <100ms when an entity is mentioned. LLMs only touch the cold path (synthesis, not retrieval).

### Context assembly (concentric layers)

```
Layer 0 (center): Current utterance (~200 tokens)
Layer 1: Immediate context — last 2 minutes (~400 tokens)
Layer 2: Active context — people, project, initiative (~500 tokens)
Layer 3: Retrieved context — vault, decisions, messages (~800 tokens)
Layer 4: System context — operator profile, trust, schedule (~300 tokens)
```

### External intelligence (five layers)

| Layer | What | Examples |
|-------|------|---------|
| 0 — Internal | Ontology data | Tasks, people, vault, sessions |
| 1 — Network | Your relationships | Messages, interaction history |
| 2 — Hyper-local | Immediate environment | Weather, prayer times, local events |
| 3 — Local/Regional | Your market | Regulations, competitors, MLS, permits |
| 4 — Industry | Your domain | Best practices, professional trends |
| 5 — Global | World state | Markets, geopolitics, supply chain |

### Self-improvement cycle

Observe → Reflect → Research → Deliberate → Propose → (Approve) → Implement → Verify

Friction detection → improvement proposals → constrained deliberation (10 min, 50K tokens, 5 perspectives) → operator approval → execution. The system never modifies its own architecture without approval.

### Overnight processing

Night shift stages: collect → enrich (Batch API, 50% cost reduction) → reconcile → synthesize → build links → stage → inject into morning briefing. Prioritizes link-building (compounding network effect).

---

## Process Model

```
PRIMARY PROCESS: qareen (port 7603)
  API routes, SSE stream, WebSocket audio, static frontend
  In-process: ontology, event bus, actions, audit, intelligence, proactive scheduler
  Subprocesses: voice pipeline (STT/TTS), agent workers

SEPARATE: whatsmeow (Go, port 7601), bridge (Telegram)
```

### Frontend

Vite + React SPA. Built locally or in CI. Served as static files by FastAPI.

### Event system

Async pub/sub event bus. 20+ event types. Wildcard subscriptions (`task.*`). SSE broadcasts all events to browser. Every mutation emits an event.

### Action system

Every write goes through governed actions: validate → execute → audit log → emit event → side effects. The `@action` decorator enforces this.

---

## Directory Structure

```
~/project/aos/core/qareen/
├── ARCHITECTURE.md          This document
├── main.py                  FastAPI app entry point
├── sse.py                   Server-Sent Events stream
├── requirements.txt         Single dependency file
├── ontology/                Data model + adapters
│   ├── types.py             19 object types, 24 link types, all enums
│   ├── model.py             Ontology class (get, list, search, act, linked)
│   └── adapters/            Storage adapters (work.py, people.py, vault.py)
├── events/                  Nervous system
│   ├── types.py             20+ event types
│   ├── bus.py               Async event bus (implemented)
│   ├── actions.py           Governed action decorator + registry
│   └── audit.py             Append-only audit trail
├── api/                     47 API endpoints across 11 route modules
│   ├── schemas.py           71 Pydantic request/response models
│   ├── work.py, config.py, agents.py, services.py, people.py,
│   │   vault.py, system.py, channels.py, metrics.py, pipelines.py
│   └── (each module is a FastAPI APIRouter)
├── intelligence/            Brain
│   ├── types.py             Intents, cards, context packets
│   └── engine.py            Intelligence engine interface
├── pipelines/               Automation
│   ├── types.py             Pipeline definitions, stages, runs
│   ├── engine.py            Pipeline execution engine
│   └── definitions/         YAML pipeline definitions
├── agents/                  Agent workers
│   ├── types.py             Agent definitions, tasks, memory
│   └── registry.py          Agent discovery + lifecycle
├── voice/                   Voice pipeline (STT, TTS, VAD)
├── channels/                Channel adapters (telegram, whatsapp, email)
├── overnight/               Night shift processing
├── proactive/               Proactive behaviors (briefings, nudges)
├── schemas/                 Database SQL
│   ├── qareen.sql           35 tables (canonical store)
│   └── comms.sql            3 tables (message store)
├── screen/                  Vite + React frontend
│   ├── src/                 Source
│   └── dist/                Built output (served by FastAPI)
└── templates/               Starter templates per business type
```

---

## Key Decisions

45 architectural decisions documented in `~/vault/knowledge/initiatives/qareen-architecture.md`. The most critical:

- **D-04**: One name — Qareen. Dashboard, Mission Control, CENTCOM retired.
- **D-10**: AOS Ontology as semantic layer over heterogeneous storage.
- **D-25**: Three databases (people.db + qareen.db + comms.db).
- **D-26**: Version history on mutable objects.
- **D-27**: Audit captures intent and provenance, not just action.
- **D-30**: Links table stores relationships AND provenance chains.
- **D-36**: Self-improvement cycle as first-class capability.
- **D-37**: External intelligence as five layers.
- **D-41**: Work model — six abstractions (Tadbir).
- **D-42**: People model — six concepts (Person, Relationship, Pipeline, Reminder, Transaction).
- **D-43**: Knowledge model — stage pipeline + operational types + authority.
- **D-44**: Communication model — conversations + person-anchored + commitment extraction.

---

## Migration

Services being deprecated: dashboard (:4096), listen (:7600), eventd (:4097), transcriber (:7602), companion (:7603), mission-control (:3000). All absorbed into the single Qareen process.

Migration is parallel — old services stay alive until Qareen proves parity. Full migration map: `~/vault/knowledge/decisions/qareen-migration-map.md`.

---

## For New Developers

1. Read this document first.
2. Read `ontology/types.py` — every data type in the system.
3. Read `schemas/qareen.sql` — every database table.
4. Read `main.py` — how the app starts and wires together.
5. Run `cd ~/project/aos/core && python3 -c "from qareen.main import app"` — verify it imports.
6. The vault doc at `~/vault/knowledge/initiatives/qareen-architecture.md` has the full reasoning behind every decision.
