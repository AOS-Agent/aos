# AOS System — Developer Context

This is the AOS system codebase. Read this when working on the OS itself.
For the global kernel (loaded every session), see `~/.claude/CLAUDE.md`.

## What AOS Is

A packageable agentic operating system. Clone onto a Mac Mini, run the installer,
get an autonomous workstation that manages work, runs agents, compounds knowledge,
and improves itself over time.

## Target Users

| User | Scenario |
|------|----------|
| Solo person | Teacher, chef, freelancer. One machine, one life to organize. |
| Multi-project operator | 3 businesses, 7 projects. Needs visibility and movement. |
| Full agentic operator | Autonomous agents running parts of businesses. Trust ramp. |

The architecture serves all three without overwhelming the simple user
or limiting the power user.

## System Layout

```
~/aos/                       SYSTEM CODE (this repo)
├── core/                      System code
│   ├── services/              Bridge, dashboard, listen, memory
│   ├── integrations/          WhatsApp, health, roborock, etc. (optional)
│   ├── agents/                System agent source files (chief, steward, advisor)
│   └── bin/                   Utilities (agent-secret, session-export, etc.)
├── config/                    Machine-specific system config
│   ├── state.yaml             Machine info, ports
│   ├── projects.yaml          Project registry
│   └── ...                    Grows as layers are built
├── templates/                 Scaffolding
│   ├── project/               New project template
│   └── agents/                Agent catalog (engineer, developer, marketing, etc.)
├── vendor/                    Third-party dependencies
└── specs/                     Living architecture docs

~/.aos/                     USER DATA (never in this repo)
├── work/                      Work system data (goals, tasks, inbox, reviews)
├── services/                  Runtime state for services
└── logs/                      All logs

~/vault/                       KNOWLEDGE (independent, Obsidian-native)
```

## Agent Architecture

Three tiers:
1. **System agents** — ship with AOS, updated with the OS (chief, steward, advisor)
2. **Catalog agents** — ship as templates, user activates (engineer, developer, etc.)
3. **User agents** — user creates for their specific needs

Source lives in `core/agents/`. Installed to `~/.claude/agents/` during setup.
Catalog templates in `templates/agents/`. Copied on activation.

## Development Rules

- Keep global CLAUDE.md under 50 lines — it loads every session everywhere
- Keep this file under 150 lines — detail goes in specs/
- Skills for domain knowledge, rules for conditional policies
- Hooks for deterministic actions only — no LLM judgment in hooks
- Test agent changes by opening a fresh session and verifying context loads

## Key Specs

| Spec | What |
|------|------|
| `specs/aos-v2-brief.md` | The original brief and research summary |
| `specs/work-system-architecture.md` | Work system v0.2 data models and integration |
| `specs/aos-vs-openclaw-and-integration.md` | 7-layer architecture, what's broken, what's right |

## Build Plan

Full system map: `specs/v2-system-map.md`
Architecture decisions: `specs/v2-architecture-decisions.md`

| Phase | What | Status |
|-------|------|--------|
| **A: Foundation** | Agent definitions, core skills, integration framework | Done |
| **B: Substance** | Work engine, work skills/hooks, vault restructure, knowledge + execution | Done |
| **C: Infrastructure** | install.sh, onboarding agent, port services | — |
| **D: Polish** | Dashboard ops, bridge commands, catalog, trust, drift | — |
