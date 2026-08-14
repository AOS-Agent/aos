<p align="center">
  <img src="https://img.shields.io/badge/status-beta-F5A623?style=flat-square" alt="Beta" />
  <img src="https://img.shields.io/badge/platform-macOS-000?style=flat-square&logo=apple" alt="macOS" />
  <img src="https://img.shields.io/badge/runtime-Claude_Code-D9730D?style=flat-square" alt="Claude Code" />
  <img src="https://img.shields.io/badge/version-0.7.1-blue?style=flat-square" alt="v0.7.1" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT" />
</p>

<h1 align="center">AOS</h1>
<p align="center"><strong>Agentic Operating System</strong></p>
<p align="center">
  Turn a Mac into an autonomous workstation.<br/>
  AI agents manage your work, run tasks, compound knowledge, and improve over time.
</p>

> [!NOTE]
> **AOS is in beta.** It's stable enough to run daily, and we're continuing to
> improve features and fix issues as we go.

---

## Quick Start

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/hishamalhadi/aos/main/bootstrap.sh)"
```

Idempotent — safe to re-run. Budget **20–30 minutes**: it provisions its own Python,
builds service environments, and downloads local speech models.

When it finishes it opens **cmux** and hands you to Sahib, who walks you through
setup: your profile, your tools, the agents, remote access, and a first task.

---

## What is AOS?

AOS is an operating system layer for macOS. It doesn't build an agent framework — it
configures **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** as its
runtime with structured context, agent definitions, skills, and hooks.

The filesystem is persistent memory. CLAUDE.md files are the kernel. Agents are
markdown with frontmatter. Most things are files.

<table>
<tr>
<td width="50%">

**For a solo person** (teacher, chef, freelancer)
> One machine, one place for everything. Add a task, see your tasks. That's it.

</td>
<td width="50%">

**For multi-project operators**
> 3 businesses, 7 projects. Visibility across all of them. Agents handling the routine.

</td>
</tr>
</table>

---

## The Stack

```
INTERFACE ──── Qareen (web)  ·  Telegram  ·  cmux + CLI  ·  Mobile
     |
AGENTS ─────── Chief  ·  Steward  ·  Advisor  ·  Catalog  ·  Councils
     |
WORK ────────── Initiatives  ·  Projects  ·  Tasks  ·  Inbox  ·  Reviews
     |
KNOWLEDGE ──── Vault  ·  QMD search  ·  Sessions  ·  Patterns
     |
PEOPLE ─────── Contacts  ·  Comms history  ·  Trust cascade
     |
SERVICES ───── Qareen  ·  Bridge  ·  Transcriber  ·  WhatsApp  ·  n8n
     |
HARNESS ────── CLAUDE.md  ·  Agents  ·  Skills  ·  Hooks  ·  Reconcile
     |
INFRA ────────  macOS  ·  Keychain  ·  Tailscale  ·  Git
```

Each layer depends only on the one below. Integrations plug into any layer.

> **See what *your* machine actually has:** `aos snapshot`. It reads the live system —
> services, agents, skills, scheduled jobs, connected integrations, vault structure —
> rather than trusting this page. Documentation drifts; the filesystem doesn't.

---

## Agents

Three tiers. Start with the system agents, activate from the catalog, or write your own.

| Agent | Role | Model |
|:------|:-----|:------|
| **Chief** | Orchestrator. Receives all requests, delegates or acts directly. | opus |
| **Steward** | Health monitoring, self-correction, drift detection. | haiku |
| **Advisor** | Analysis, knowledge curation, work planning, reviews. | sonnet |

**Catalog agents** ship as templates — `engineer`, `developer`, `cmo`, `ops`,
`technician`, `reverser`, and more. Activate with `aos activate <name>`; they're copied,
so your edits survive updates.

**Councils** convene several agents to argue a high-stakes decision from different
angles, then write a verdict with the dissent preserved. `council background "<question>"`.

### Trust Ramp

Trust is per-capability, not per-agent. An agent can be fully autonomous for file ops
but require approval before sending a message.

```
Level 0  SHADOW      Observe only — log what it would do
Level 1  APPROVAL    Propose actions, you approve each one
Level 2  SEMI-AUTO   Act on high confidence, ask on uncertain
Level 3  FULL-AUTO   Handle everything, escalate exceptions
```

Financial commitments, new external contacts, and destructive operations always escalate.

---

## Work System

The connective tissue. Like Git is infrastructure for code, this is infrastructure for work.

```
Initiatives → Projects → Tasks → Sessions → Knowledge → Reviews → Initiatives
```

- **SQLite-backed** (`~/.aos/data/work.db`), with an append-only activity log — every
  task carries the narrative of what happened to it and who did it.
- **Agent-native.** Tasks can be delegated to an agent as a real state transition. It
  works perfectly with zero agents.
- **Progressive.** Start with a flat task list. Add projects when you need them.
  Initiatives when the work spans weeks.
- **Automatic.** Sessions link to tasks. Patterns compile into scripts. Reviews generate
  themselves.

```bash
# From a Claude Code session, just say it — or use the CLI directly:
python3 ~/aos/core/engine/work/cli.py add "Build the landing page"
python3 ~/aos/core/engine/work/cli.py done "landing page"   # fuzzy match
python3 ~/aos/core/engine/work/cli.py today                 # what's on deck
```

**Initiatives** track multi-week efforts through research → shaping → planning →
executing → review, with gate checks between phases and a nudge when one goes stale.

---

## Services

Always-on processes via LaunchAgents. Survive reboots. Bound to loopback.

| Service | What | Port |
|:--------|:-----|:-----|
| **Qareen** | The web UI + ontology backend — board, knowledge, shipments, health | `:4096` |
| **Bridge** | Telegram messaging, voice transcription, agent dispatch | daemon |
| **Transcriber** | Local speech-to-text (mlx-whisper) | `:7602` |
| **WhatsApp** | WhatsApp relay for the comms pipeline | `:7601` |
| **n8n** | Workflow automation behind integrations | `:5678` |

Optional, opt-in: `companion`, `mesh`, `work-runner`, and the `crawler` / `memory` MCP
servers. Each service declares itself in a manifest (`core/services/*/service.yaml`), and
the installer, watchdog, and health checks all read that manifest rather than a
hardcoded list.

Remote access exclusively through **Tailscale** — authenticated, encrypted, zero config.

---

## Knowledge & People

Everything you learn, captured and compounding.

| Source | Destination | Frequency |
|:-------|:-----------|:----------|
| Claude sessions | Vault summaries | Continuous |
| Session patterns | Friction reports | Weekly |
| Repeated tasks | Deterministic scripts | Daily |
| Vault contents | QMD index (BM25 + vectors + reranker) | Every 30 min |

**QMD** is the single search system across the vault, skills, agents, and docs —
available to agents as native MCP tools.

**Comms and people** are first-class. Messages from every channel land in one store with
full-text search (`comms-recall search "..."`), resolved to a canonical person through a
5-tier identity resolver. Restricted contacts are excluded by default, enforced inside
the query tool rather than by convention.

**The daily loop:** morning briefing → work → evening review → tomorrow is smarter.

---

## Filesystem

Four boundaries. Never crossed.

```
~/aos/          SYSTEM        The running release. Read-only at runtime.
~/.aos/         USER DATA     Never in git. Never touched by updates.
~/vault/        KNOWLEDGE     Independent. Obsidian-native. Path configurable.
~/project/      PROJECTS      Self-contained. Own context, agents, work.
```

> **Rule**: No user data inside the system tree. Wiping `~/aos/` must never destroy
> user data.

<details>
<summary><strong>Full tree</strong></summary>

```
~/aos/
├── core/
│   ├── agents/            System agent definitions
│   ├── engine/            Work, comms, people, intelligence engines
│   ├── infra/             Migrations, reconcile checks, integrations
│   ├── qareen/            Qareen backend, ontology, tracking
│   ├── services/          Long-running service code + manifests
│   ├── skills/            Skill definitions
│   └── bin/               CLIs and cron entrypoints
├── apps/                  Qareen UI, content engine
├── config/                System configuration, LaunchAgent templates
├── templates/             Agent catalog + project scaffold
└── specs/                 Architecture documentation

~/.aos/
├── data/                  work.db, comms.db, people.db, qareen.db
├── services/              Per-service runtime environments
├── config/                operator.yaml, trust.yaml, channel
└── logs/                  All logs, including per-cron logs

~/vault/
├── log/                   Dailies, sessions, friction, weekly/monthly reviews
└── knowledge/             captures → research → synthesis → decisions → expertise
                           (plus references and initiatives)
```

</details>

---

## Install

### One-liner

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/hishamalhadi/aos/main/bootstrap.sh)"
```

### Manual

```bash
git clone https://github.com/hishamalhadi/aos.git ~/aos
cd ~/aos && bash install.sh
```

`bash install.sh --dry-run` walks the stages without touching the machine.

### Requirements

| Requirement | Notes |
|:-----------|:------|
| macOS 14+ | Apple Silicon or Intel |
| Internet | For initial setup |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Active subscription — no API key needed |

Everything else — Homebrew, a pinned Python, uv, bun, cmux, ffmpeg — is installed
automatically. AOS provisions its own interpreter rather than borrowing the system one.

If an install ends badly, it writes a scorecard to `~/.aos/config/install-report.yaml`
and a full transcript to `~/.aos/logs/install.log`.

---

## Release Channels

Two lanes, so the person developing AOS isn't shipping same-day code to everyone else.

| Channel | Tracks | For |
|:--------|:-------|:----|
| `edge` | `origin/main` | Same-day changes |
| `stable` | the `stable` tag | Promoted releases only (the default) |

```bash
aos channel            # show this machine's channel
aos channel edge       # switch lanes
aos promote            # move `stable` to the running release (guarded)
```

Promotion requires a soak period, an ancestry check, a clean `ship-check`, a passing
self-test, and a typed confirmation. Machines update themselves at 4am.

---

## Commands

```bash
aos start              # Open cmux with Claude Code ready
cld                    # Talk to Chief in the current terminal
aos snapshot           # What this system actually consists of (--json)
aos status             # Migration version + pending
aos self-test          # Verify the installation
aos update             # Pull latest + migrate + sync
aos reconcile          # Run invariant checks, repair drift
aos activate <agent>   # Activate a catalog agent
aos track              # Package tracking
```

---

## Design Decisions

| Decision | Why |
|:---------|:----|
| Claude Code as runtime | Your subscription, no API keys. Built-in subagents, tools, headless mode. |
| macOS Keychain | Native hardware-backed encryption. No external deps. |
| Loopback-only | Zero attack surface. Tailscale for remote. |
| AOS owns its interpreter | `brew upgrade` used to move Python out from under a running install. |
| No orchestration framework | Claude Code's subagents handle dispatch. Less complexity, same result. |
| Symlink system, copy catalog | System agents auto-update with the OS. Catalog agents are copied so user edits survive. |
| Manifests over hardcoded lists | Services, skills, agents, and integrations declare themselves. A directory can be listed; a list drifts. |
| Migrations ship with their consumers | A change that moves something the instance depends on carries its migration in the same commit. |

### Self-correction

Reconcile runs on every update and every ~30 minutes, asserting invariants and repairing
drift. Its own hardest-won rule: a check must be able to say *"I could not verify this"*.
A monitor that reports OK when it verified nothing hides every bug it was meant to catch —
so checks that can't see their inputs report SKIP, never a green tick nobody earned.

---

## License

MIT

---

<p align="center">
  <sub>Built for people who want their computer to work for them.</sub>
</p>
