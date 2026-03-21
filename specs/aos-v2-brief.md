# AOS v2 — Ground-Up Architecture Brief

**Date**: 2026-03-21
**Context**: This brief was produced at the end of a deep research session. Start here.

---

## What Happened in the Previous Session

A multi-hour session that started as "research the best task/project/goal management system" evolved through three scope transitions:

1. **Research phase** — 5 parallel research tracks completed:
   - Islamic productivity frameworks (principles, time structure, delegation, priorities)
   - Business frameworks (GTD, OKRs, EOS, Kanban, Shape Up, data models, review cadences)
   - Technical internals (Manus architecture, Linear/Notion data models, auto-capture patterns)
   - Claude Code internals (22 hook events, skills system, plugins, session architecture)
   - Repo landscape (25+ projects: PAI, Claude Task Master, Mission Control, Alfred, Backlog.md, OpenClaw, Paperclip, etc.)

2. **Work system architecture** — Designed a file-based task/project/goal system as a Claude Code plugin. Two drafts (v0.1, v0.2). Includes: progressive complexity, trust ramp, auto-capture, threads, session-to-work linking, one-way sync, packaging.

3. **Honest critique & integration analysis** — Identified 10 breakpoints in the work system design, then analyzed how it fits into AOS. Discovered that AOS itself has structural issues that the work system can't paper over:
   - Work layer is a hole (two failed migrations, placeholder code everywhere)
   - Agents have no work loop (dispatched on-demand, no heartbeat, no assignment queue)
   - Trust model is decorative (exists in YAML, nothing reads it)
   - Dashboard shows monitoring, not operations
   - Sessions don't link to tasks
   - Cron jobs run independently, don't feed into work tracking
   - Reviews don't happen structurally

**Conclusion**: We need to redesign AOS from the ground up, not bolt features onto the current architecture.

---

## All Research Artifacts (Read These First)

| File | What's In It |
|------|-------------|
| `specs/work-system-architecture.md` | Work system v0.2 — file formats, data models, Claude Code integration, trust model, auto-capture, packaging, onboarding. **This is the most refined spec.** |
| `specs/aos-vs-openclaw-and-integration.md` | AOS 7-layer architecture map, OpenClaw comparison (where AOS wins, where it loses), integration wiring diagram, honest critique of what's broken, Paperclip analysis. |
| `specs/task-systems-research.md` | Deep technical research on Manus (28 tools, todo.md protocol, 3-file pattern), Claude Code internals (TodoWrite schema, hooks, skills), Linear data model, TICK.md protocol, agent queue patterns, auto-capture, knowledge integration. |
| `specs/tadbir-architecture.md` | Work system v0.1 (superseded by v0.2, but has fuller Islamic framework mapping and time structure details). |

The research agents also produced detailed findings that are summarized in these specs but the full outputs are available in session history.

---

## The Mission

Design and build **AOS v2** — a packageable agentic operating system that anyone can install on a Mac Mini (or similar) and have a working system that:

1. Manages their work (tasks, projects, goals) across personal life and multiple businesses
2. Runs AI agents as workers alongside human workers
3. Captures work from any source (email, messages, calendar, voice, sessions)
4. Gets smarter over time (pattern compilation, knowledge compounding, friction mining)
5. Provides observability (dashboard, mobile app, Telegram)
6. Scales from "one person, no agents" to "multi-project, multi-agent, multi-business"
7. Is secure by default (Keychain secrets, localhost-only, Tailscale for remote)

---

## Target Users (The Spectrum)

| User | Scenario |
|------|----------|
| **Solo person** | Teacher, chef, freelancer. Just got a Mac Mini + Claude Code. Wants to organize their life and maybe one side project. No agents initially. |
| **Multi-project operator** | Running 3 businesses, 7 side projects. Things everywhere. Needs it all organized, visible, and moving. Some agents helping. |
| **Full agentic operator** | Like the current AOS setup. Multiple agents running parts of businesses. Autonomous execution. Knowledge vault. Pattern compilation. Full trust ramp. |

The architecture must serve all three without the simple user being overwhelmed or the power user being limited.

---

## Core Architectural Decisions to Make

### 1. Filesystem Design
What is the canonical directory structure? Current AOS grew organically (`apps/`, `config/`, `bin/`, `specs/`, `data/`, `logs/`, `execution_log/`, `vendor/`). What should it actually be?

Key questions:
- Where does user data vs system code live? (PAI's USER/SYSTEM split is the right pattern)
- How do projects connect to the core system?
- Where do work files (tasks, goals, projects) live?
- How does the knowledge vault relate to the core system?

### 2. Agent Architecture
How do agents actually work? Current: on-demand subagent dispatch. No work loop, no heartbeat, no assignment queue.

Key questions:
- Do agents have a persistent work loop? (Paperclip's heartbeat model)
- How are agents defined? (YAML frontmatter? SOUL.md + HEARTBEAT.md?)
- How do agents claim and execute work?
- How does trust actually gate behavior? (Not decoratively)
- How do per-project agents relate to system agents?

### 3. Work System (Designed, Needs Integration)
The work system spec (v0.2) is solid. But it was designed as a plugin. In v2 it should be **native** — part of the core, not bolted on.

Key questions:
- Is the file format right? (YAML tasks in a directory)
- How does the three-layer cascade (skill → hook → command) integrate at the OS level?
- How do sessions become work? (SessionStart/Stop hooks)
- How does the review system drive real behavior?

### 4. Service Architecture
Current: 5+ services as separate LaunchAgents (bridge, dashboard, listen, memory, phoenix). Is this the right model?

Key questions:
- Should there be fewer, more capable services?
- Does the Listen job server still make sense, or should it be part of the bridge?
- How does the dashboard evolve from monitoring to operations?
- Is Phoenix (external observability) the right choice, or should observability be built-in?

### 5. Claude Code Integration
Current: hooks in `~/.claude/settings.json`, skills in `.claude/skills/`, agents in `.claude/agents/`. The system IS Claude Code — all work happens through it.

Key questions:
- Should the whole system be a Claude Code plugin? (Distributable, updatable, namespaced)
- What skills are system-wide vs project-specific?
- What hooks fire at the OS level vs project level?
- How does the CLAUDE.md hierarchy work across projects?

### 6. Onboarding
No onboarding exists today. New user gets a repo and has to figure it out.

Key questions:
- What does the install script do? (Dependencies, services, first-run config)
- What does the onboarding agent do? (Guided setup, service connection, brain dump, calibration)
- How long from `git clone` to "working system"?
- How do you onboard someone non-technical?

### 7. Packaging & Updates
Current: git repo, manual updates, no versioning, no migration.

Key questions:
- How are updates distributed?
- How are data migrations handled?
- How do you update system code without breaking user data?
- Can this eventually be distributed as a Claude Code plugin on a registry?

---

## What We're NOT Changing

Some things from the current AOS are right and should carry forward:

- **Claude Code as runtime** — not building our own agent framework
- **macOS Keychain for secrets** — hardware-backed, no `.env` files
- **Localhost + Tailscale** — security model is sound
- **Telegram as primary interface** — works, battle-tested
- **File-first configuration** — YAML/Markdown in Git
- **Pattern compilation** — repeated tasks → deterministic scripts
- **Knowledge vault** — Obsidian + QMD search
- **Per-project isolation** — own bots, agents, goals, directories

---

## What We Learned From Others

### Systems to study (with links in the research specs):

| System | Key lesson |
|--------|-----------|
| **PAI (Miessler, 10K stars)** | USER/SYSTEM separation. TELOS identity files. Person-centric, not task-centric. |
| **OpenClaw (328K stars)** | 22+ channels, plugin SDK, security model (DM pairing, sandbox, audit). Single Gateway process. |
| **Claude Task Master (26K stars)** | MCP-first task management. PRD → tasks. Multi-model. |
| **Mission Control (287 stars)** | JSON-as-IPC between humans and agents. Agent daemon mode. Eisenhower matrix. |
| **Alfred (193 stars)** | Background workers (Curator, Janitor, Distiller, Surveyor). Obsidian vault. Multi-interface. |
| **Paperclip** | SOUL.md + HEARTBEAT.md per agent. Checkout before work. Fact extraction. Run logs. |
| **Manus ($2B)** | todo.md as attention recitation. 3-file persistent pattern. Filesystem = disk, context = RAM. |
| **TICK.md** | Claim-execute-release. File locking. Git as backend. |
| **Backlog.md (5.2K stars)** | One file per task. Spec-driven. MCP protocol. |
| **Plain text accounting** | Strict validation > permissive parsing. Explicit declarations prevent drift. |
| **todo.txt** | Radical simplicity. One line = one task. Huge ecosystem from minimal spec. |

### Frameworks baked into the design:
- **GTD**: Capture → Clarify → Organize → Reflect → Engage. Weekly review.
- **OKRs**: Goals with measurable key results. Committed vs aspirational.
- **Shape Up**: Appetite-based scoping. Hill charts. No infinite backlog.
- **Kanban**: WIP limits. Flow metrics. Little's Law.
- **EOS**: 90-day Rocks. Level 10 meetings. IDS (Identify, Discuss, Solve).
- **Islamic management principles** (as inspiration, not naming): Excellence in work, trust as sacred responsibility, priority hierarchy, self-accounting, blessed time, consultation.

---

## Suggested Approach for Next Session

1. **Create `~/aos-v2/`** — clean directory, fresh start
2. **Design the filesystem** — canonical structure, documented, justified
3. **Write the install script** — from zero to working system
4. **Build the onboarding agent** — first thing a new user interacts with
5. **Port proven components** — bring forward what works from current AOS
6. **Build the work system natively** — not as a plugin, as core infrastructure
7. **Wire the loop** — goals → tasks → agents → sessions → knowledge → reviews → goals

The current `~/aos/` stays running. `~/aos-v2/` is built alongside it. When v2 is ready, migrate.

---

## One Last Thing

This conversation itself demonstrated the core problem:
- Started as research → became architecture → became full redesign
- Three scope transitions, none tracked
- Valuable artifacts produced but scattered across session history
- No clean handoff mechanism

The system we build must handle this natively. A conversation that evolves should be tracked as it evolves, without the human having to manually manage the meta-layer.

That's the whole point.
