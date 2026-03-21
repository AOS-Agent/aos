# AOS v2 — Architecture Decisions Log

**Date**: 2026-03-21
**Session**: Filesystem design & scaffolding (Step 1 of 7)
**Status**: Locked — these decisions are final unless explicitly revisited.

---

## Decision 1: Directory Boundaries

```
~/aosv2/       SYSTEM CODE    Git repo. Safe to pull/reset/clone.
~/.aos-v2/     USER DATA      Never in git. Never touched by updates.
~/vault/       KNOWLEDGE      Independent. Obsidian-native. Path configurable.
~/project/     PROJECTS       Self-contained. Own work/, .claude/, CLAUDE.md.
```

**Rule**: No user data inside the system repo. Ever.

**Rationale**: PAI's USER/SYSTEM split. A `git clean -fd` on the system repo
must never destroy user data. Backup story: `~/.aos-v2/` + `~/vault/` + project
`work/` dirs = everything.

**Why `~/.aos-v2/` (hidden)?** This machine IS the agent. Users interact via
IDE (VS Code/Cursor remote — hidden folders visible by default), SSH, or Telegram.
Nobody browses Finder on a dedicated agent machine. Hidden keeps home dir clean.

**Why `~/vault/` stays independent?** It existed before AOS. Obsidian opens it by
path. QMD indexes it independently. It's a peer to the OS, not subordinate.
Vault path is a config setting, not hardcoded.

**Naming**: `~/aosv2/` is temporary during parallel development with v1. Renamed
to `~/aos/` when v1 is retired. Single rename, no other changes needed.

---

## Decision 2: CLAUDE.md Hierarchy

Three layers, no overlap:

| File | Loaded when | Purpose | Size |
|------|------------|---------|------|
| `~/.claude/CLAUDE.md` | Every session, everywhere | OS kernel — identity, boundaries, rules | 37 lines |
| `~/aosv2/CLAUDE.md` | Working on AOS itself | System architecture, layout, dev guide, build status | 85 lines |
| `~/aosv2/.claude/CLAUDE.md` | Working on AOS itself | Harness model reference, agent install mechanism | 25 lines |

**Rule**: Global kernel under 50 lines. System CLAUDE.md under 150 lines.
Detail goes in specs/ and skills.

**Rationale**: Claude Code loads CLAUDE.md hierarchy automatically. Global level
means every session on this machine is AOS-aware — including project sessions.
A session in `~/nuchay/` gets global kernel + nuchay context. No duplication.

**No `@` imports between siblings** — Claude Code's natural hierarchy handles it.

---

## Decision 3: Agent Architecture

Three tiers:

### System Agents (ship with AOS, updated with the OS)

| Agent | Role | Metaphor |
|-------|------|----------|
| **Chief** | Orchestrator. Receives all requests. Delegates or acts. | Brain |
| **Steward** | Health monitoring, self-correction, maintenance. | Immune system |
| **Advisor** | Analysis, knowledge curation, work planning, reviews. | Nervous system |

- Chief is **required** (the kernel — always active).
- Steward and Advisor are **recommended** (activated during onboarding).

### Catalog Agents (ship as templates, user activates)

Templates in `~/aosv2/templates/agents/`. Copied to `~/.claude/agents/` on activation.
Examples: engineer, developer, marketing, researcher, writer.

Copied (not symlinked) so users can customize. Frontmatter tracks source:
`_source: catalog/engineer@1.0` for update awareness.

### User Agents (user creates)

Created directly in `~/.claude/agents/` or `~/project/.claude/agents/`.
No template, no restrictions. Whatever the user needs.

### Where agents live on disk

```
~/aosv2/core/agents/         Source for system agents (chief, steward, advisor)
~/aosv2/templates/agents/    Catalog templates (engineer, developer, etc.)
~/.claude/agents/            Active agents (installed here, available everywhere)
~/project/.claude/agents/    Project-scoped agents (only in that project)
```

Install mechanism: TBD (determined when building install script).

### Why 3 system agents, not 5

Analyst, Curator, and Planner were considered as separate agents but merged into
Advisor. Reasoning:
- On Day 1, there isn't enough knowledge to curate or work to plan
- One agent with multiple skills (analysis, curation, planning) starts lean
- Split when evidence shows a function needs its own trust level
- Fewer agents = simpler delegation, less context overhead

### What's NOT an agent

Deterministic work runs as cron jobs, not agents:
- session-export, pattern-compile, log-rotate, health-check
- These don't need LLM judgment — they're scripts

The test: "Does it take autonomous actions that need a graduated trust level?"
If no → script/cron. If yes → agent.

---

## Decision 4: Global .claude/ as OS Layer

`~/.claude/` IS the OS layer. Since this machine IS the agent:
- `~/.claude/settings.json` — global settings (chief as default, teams enabled)
- `~/.claude/agents/` — active system agents (available in every session)
- `~/.claude/CLAUDE.md` — OS kernel (loaded everywhere)

Project .claude/ directories add project-specific agents, skills, and rules.
They inherit from global automatically.

**Settings already configured**:
```json
{
  "agent": "chief",
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
    "CLAUDE_CODE_TEAMMATE_MODE": "in-process"
  }
}
```

---

## Decision 5: Scaffold Structure

```
~/aosv2/
├── .claude/agents/        Agent definitions (empty, built in agent layer step)
├── .claude/skills/        Skills (empty, ported from v1 when relevant)
├── .claude/hooks/         Hook scripts (empty, built with work system)
├── .claude/CLAUDE.md      Harness reference
├── CLAUDE.md              System architecture
├── core/                  System code (services, integrations, agents, bin)
├── config/                Machine-specific config (state, projects)
├── templates/             Scaffolding (project template, agent catalog)
├── vendor/                Third-party dependencies
└── specs/                 Architecture docs

~/.aos-v2/
├── work/                  Work system data
├── services/              Service runtime state
└── logs/                  All logs
```

No `.claude/commands/` — deprecated in favor of skills.

---

## Decision 6: Core Concepts from Research

### AOS = Configured Claude Code Harness

Claude Code is already the agent framework. AOS doesn't build a new one.
It configures the existing one with CLAUDE.md, agents, skills, hooks, and state.

### Chief = Init Process

Every request enters through Chief. Chief decides: handle directly or dispatch.
Subagents run in isolated context via the Agent tool. State shared via filesystem.

### Filesystem = Persistent Memory

Context window = RAM (volatile, limited). Filesystem = disk (persistent).
Anything important gets written to files. Config YAML, vault notes, execution logs.

### Five Loops

```
Chief:     Request → Decision → Dispatch → Response
Steward:   Monitor → Detect → Correct → Report
Advisor:   Observe → Pattern → Compile → Recommend
           Ingest → Connect → Surface → Archive
           Capture → Prioritize → Review → Brief
```

### Trust Ramp (per-capability, not per-agent)

```
Level 0: SHADOW     — observe only, log what it would do
Level 1: APPROVAL   — propose, human approves each action
Level 2: SEMI-AUTO  — act on high-confidence, ask on uncertain
Level 3: FULL-AUTO  — handle everything, escalate exceptions only
```

Graduation is weighted: routine=0.5, novel=2.0, complex=3.0, revert=-5.0.

---

## What's Next (from the v2 brief)

| Step | What | Status |
|------|------|--------|
| 1 | Filesystem design & scaffolding | ✅ Complete |
| 2 | Install script (zero to working) | Next |
| 3 | Onboarding agent (first user experience) | Blocked on 2 |
| 4 | Port proven components (bridge, vault, keychain) | Blocked on 2 |
| 5 | Build work system natively (Layer 3) | Can start independently |
| 6 | Agent definitions (chief, steward, advisor) | Can start independently |
| 7 | Wire the loop (goals → tasks → agents → knowledge → reviews) | Blocked on 5, 6 |

---

## Research Sources

- Claude Code best practices: skills, agents, hooks, CLAUDE.md hierarchy, settings
- Agent harness patterns: Manus (3-file), PAI (USER/SYSTEM), Paperclip (SOUL.md)
- Work system v0.2 spec: data models, trust ramp, auto-capture, reviews
- AOS v1 analysis: 7-layer architecture, what's broken, what carries forward
