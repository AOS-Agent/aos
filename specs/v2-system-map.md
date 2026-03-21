# AOS v2 — System Map

**Date**: 2026-03-21
**Status**: Locked

---

## The Stack

```
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   INTERFACE ──── Telegram · Dashboard · CLI · Mobile      ║
║        ↑                                                  ║
║   AGENTS ─────── Chief · Steward · Advisor · User agents  ║
║        ↑                                                  ║
║   WORK ────────── Goals · Tasks · Inbox · Reviews         ║
║        ↑                                                  ║
║   KNOWLEDGE ──── Vault · Search · Sessions · Patterns     ║
║        ↑                                                  ║
║   SERVICES ───── Bridge · Dashboard · Listen · Memory     ║
║        ↑                                                  ║
║   HARNESS ────── CLAUDE.md · Agents · Skills · Hooks      ║
║        ↑                                                  ║
║   INFRA ────────  macOS · Keychain · Tailscale · Git      ║
║                                                           ║
║ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ ║
║   INTEGRATIONS    Telegram · WhatsApp · Email · Calendar  ║
║   (plug into      Obsidian · SuperWhisper · HealthKit     ║
║    any layer)     Each: manifest + setup + health check   ║
║ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ ║
║   ONBOARDING      install.sh → onboarding agent →        ║
║   (configures     Advisor recommendations (ongoing)       ║
║    everything)    Touches every layer for this user.       ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
```

---

## Layer Definitions

### INFRA — The foundation

macOS, Homebrew, Keychain (hardware-backed secrets), Tailscale (remote access),
LaunchAgents (process management), Git, Python/Bun/Node runtimes.

Not AOS code. Prerequisites the install script ensures exist.

### HARNESS — Claude Code as runtime

AOS doesn't build an agent framework. It configures Claude Code.

```
~/.claude/CLAUDE.md          Global kernel (every session)
~/.claude/settings.json      Default agent, teams, permissions
~/.claude/agents/            Active agents (system + catalog + user)
~/aos/.claude/skills/      Knowledge modules (loaded on demand)
~/aos/.claude/rules/       Conditional policies (loaded by context)
~/aos/.claude/hooks/       Deterministic lifecycle handlers
```

### SERVICES — Always-on processes

Persistent daemons managed by LaunchAgents. Run independently of Claude sessions.

| Service | Purpose | Port |
|---------|---------|------|
| Bridge | Telegram messaging + heartbeat | daemon |
| Dashboard | Web UI (monitoring + operations) | 4096 |
| Listen | Job server (background tasks) | 7600 |
| Memory | Semantic search (ChromaDB MCP) | stdio |

Code: `core/services/`. Runtime data: `~/.aos/services/`.

### KNOWLEDGE — What the system remembers

```
~/vault/                    Obsidian vault (daily notes, sessions, ideas, materials)
QMD                         Hybrid search (BM25 + semantic + reranking)
core/bin/session-export     Sessions → vault summaries (cron, every 2h)
core/bin/session-analysis   Sessions → friction patterns (cron, weekly)
core/bin/compile-patterns   Repeated tasks → deterministic scripts (cron, daily)
~/.claude/projects/*/memory Auto-memory per project (Claude Code native)
```

### WORK — What needs to be done (the big build)

The connective tissue. Everything flows through here.

```
Goals → Projects → Tasks → Sessions → Knowledge → Reviews → Goals
```

| Component | What | Where |
|-----------|------|-------|
| Work engine | Parse, query, metrics | `core/work/` |
| Work data | Tasks, goals, inbox, threads, reviews | `~/.aos/work/` |
| Project work | Per-project tasks/goals (rolls up) | `~/project/work/` |
| Skills | /work, work-awareness, /review, /triage | `.claude/skills/` |
| Hooks | Session→task linking, context injection | `.claude/hooks/` |

Spec: `specs/work-system-architecture.md` (1100 lines, data models, trust, reviews).

### AGENTS — Who does the work

Three tiers:

| Tier | Agents | Updated by |
|------|--------|-----------|
| System | Chief (required), Steward, Advisor | AOS updates |
| Catalog | Engineer, Developer, Marketing, etc. | AOS templates, user customizes |
| User | Whatever they create | User |

Source: `core/agents/`. Templates: `templates/agents/`. Active: `~/.claude/agents/`.

Trust ramp per-capability: Shadow → Approval → Semi-auto → Full-auto.

### INTERFACE — How users interact

Telegram (primary), Dashboard (visual), CLI (direct), Mobile app (future).
Slash commands via skills: /gm, /status, /tasks, /review, /inbox.

### INTEGRATIONS — Plug-in capabilities

Each integration has: manifest (what it needs/provides), setup script,
health check, optional service. Plugs into any layer.

```
core/integrations/
├── registry.yaml            All available integrations
├── telegram/                manifest.yaml + setup + health + service
├── whatsapp/
├── email/
├── calendar/
├── obsidian/
├── superwhisper/
└── ...
```

Activated during onboarding or later via Advisor recommendation.

### ONBOARDING — Zero to working

Two phases:
1. **Bootstrap** (`install.sh`) — deterministic, installs deps, clones repo
2. **Onboarding agent** — conversational, configures system to fit the user

Stages: Identity → Essentials → Communication → Your Work → Agents → Activate.

---

## Build Plan

### Phase A: Foundation

Build the harness and agents so the system can talk.

| What | Layer | Effort |
|------|-------|--------|
| Agent definitions (chief, steward, advisor) | Harness + Agents | M |
| Core skills (recall, step-by-step, onboarding) | Harness | S |
| Integration framework (registry + manifest pattern) | Integrations | S |

**Done when**: Open a session in ~/aos/, Chief responds with full context,
can dispatch Steward and Advisor.

### Phase B: Substance

Build the work system and connect knowledge. This is where the system
becomes useful, not just talkable.

| What | Layer | Effort |
|------|-------|--------|
| Work engine (parser, query, metrics) | Work | L |
| Work skills (/work, work-awareness, /review) | Work | M |
| Work hooks (session→task, context injection) | Work | M |
| Port knowledge layer (vault, QMD, session-export) | Knowledge | S |
| Port execution layer (capabilities, patterns) | Knowledge | S |

**Done when**: /work add "Buy groceries" creates a task. /review daily
generates a summary. Sessions auto-link to tasks.

### Phase C: Infrastructure

Now there's something to install and serve. Build the delivery layer.

| What | Layer | Effort |
|------|-------|--------|
| install.sh (bootstrap) | Infra | M |
| Onboarding agent (conversational setup) | Onboarding | M |
| Port services (bridge, dashboard, listen) | Services | M |
| Integration setup scripts | Integrations | M |

**Done when**: `curl install.sh | bash` on a fresh Mac Mini → onboarding
agent guides user → system running with Telegram + dashboard.

### Phase D: Polish

Connect everything. Make it feel like one system, not parts.

| What | Layer | Effort |
|------|-------|--------|
| Dashboard operations view (work system) | Interface | M |
| Bridge work commands (/tasks, /goals, /inbox) | Interface | S |
| Agent catalog (5+ templates) | Agents | S |
| Trust system (functional, not decorative) | Agents + Work | M |
| Drift detection + morning briefings | Work + Agents | S |

**Done when**: Full loop works — goals → tasks → agents execute → sessions
logged → knowledge captured → patterns compiled → reviews generated →
goals updated. System improves itself measurably over 30 days.

---

## Filesystem Reference

```
~/aos/                         SYSTEM (git repo)
├── CLAUDE.md                    System dev context
├── .claude/                     Skills, hooks, rules
├── core/
│   ├── agents/                  System agent source (chief, steward, advisor)
│   ├── services/                Bridge, dashboard, listen, memory
│   ├── integrations/            Telegram, WhatsApp, email, etc.
│   ├── work/                    Work engine (parser, query, metrics)
│   ├── onboarding/              Setup stages + integration scripts
│   └── bin/                     Utilities
├── config/                      Machine-specific config
├── templates/
│   ├── project/                 New project scaffold
│   └── agents/                  Agent catalog
├── vendor/                      Third-party deps
└── specs/                       Architecture docs

~/.aos/                       USER DATA (never in git)
├── work/                        Goals, tasks, inbox, reviews
├── services/                    Runtime state
└── logs/                        All logs

~/vault/                         KNOWLEDGE (independent)
~/project/                       PROJECTS (self-contained)
```
