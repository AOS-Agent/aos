<p align="center">
  <img src="https://img.shields.io/badge/platform-macOS-000?style=flat-square&logo=apple" alt="macOS" />
  <img src="https://img.shields.io/badge/runtime-Claude_Code-D9730D?style=flat-square" alt="Claude Code" />
  <img src="https://img.shields.io/badge/version-0.1.0-blue?style=flat-square" alt="v0.1.0" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT" />
</p>

<h1 align="center">AOS</h1>
<p align="center"><strong>Agentic Operating System</strong></p>
<p align="center">
  Turn a Mac Mini into an autonomous workstation.<br/>
  AI agents manage your work, run tasks, compound knowledge, and improve over time.
</p>

---

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/hishamalhadi/aos/main/install.sh | bash
```

Idempotent. Safe to re-run. Takes ~5 minutes. When it finishes, type `claude` and Chief will guide you through onboarding.

---

## What is AOS?

AOS is an operating system layer for macOS. It doesn't build an agent framework — it configures **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** as its runtime with structured context, agent definitions, skills, and hooks.

The filesystem is persistent memory. CLAUDE.md files are the kernel. Agents are markdown with frontmatter. Everything is files.

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
INTERFACE ──── Telegram  ·  Dashboard  ·  CLI  ·  Mobile
     |
AGENTS ─────── Chief  ·  Steward  ·  Advisor  ·  User agents
     |
WORK ────────── Goals  ·  Tasks  ·  Inbox  ·  Reviews
     |
KNOWLEDGE ──── Vault  ·  Search  ·  Sessions  ·  Patterns
     |
SERVICES ───── Bridge  ·  Dashboard  ·  Listen  ·  Memory
     |
HARNESS ────── CLAUDE.md  ·  Agents  ·  Skills  ·  Hooks
     |
INFRA ────────  macOS  ·  Keychain  ·  Tailscale  ·  Git
```

Seven layers. Each depends only on the one below. Integrations plug into any layer.

---

## Agents

Three tiers. Start with system agents, add from the catalog, or build your own.

| Agent | Role | Model |
|:------|:-----|:------|
| **Chief** | Orchestrator. Receives all requests, delegates or acts directly. | opus |
| **Steward** | Health monitoring, self-correction, drift detection. | haiku |
| **Advisor** | Analysis, knowledge curation, work planning, reviews. | sonnet |

**Catalog agents** ship as templates — Engineer, Developer, Marketing, and more. Activate what you need, customize freely.

### Trust Ramp

Trust is per-capability, not per-agent. An agent can be fully autonomous for file ops but require approval for messages.

```
Level 0  SHADOW      Observe only — log what it would do
Level 1  APPROVAL    Propose actions, you approve each one
Level 2  SEMI-AUTO   Act on high confidence, ask on uncertain
Level 3  FULL-AUTO   Handle everything, escalate exceptions
```

---

## Work System

The connective tissue. Like Git is infrastructure for code, this is infrastructure for work.

```
Goals → Projects → Tasks → Sessions → Knowledge → Reviews → Goals
```

- **File-based.** No database. The filesystem is the database. Git gives you history.
- **Agent-native.** Agents are first-class workers. But it works perfectly with zero agents.
- **Progressive.** Start with a flat task list. Add projects when you need them. Goals when you're ready.
- **Automatic.** Sessions link to tasks. Patterns compile into scripts. Reviews generate themselves.

```bash
/work add "Build the landing page"     # Create a task
/work done "landing page"              # Complete by fuzzy match
/review daily                          # Generate daily summary
```

---

## Services

Always-on processes via LaunchAgents. Survive reboots. Localhost-only.

| Service | What | Port |
|:--------|:-----|:-----|
| **Bridge** | Telegram messaging, voice transcription, Claude dispatch | daemon |
| **Dashboard** | Web UI — activity feed, work, agents, sessions, logs | `:4096` |
| **Listen** | Background job server with Claude Code workers | `:7600` |
| **Memory** | Semantic search via ChromaDB (MCP server) | stdio |

Remote access exclusively through **Tailscale** — authenticated, encrypted, zero config.

---

## Knowledge

Everything you learn, captured and compounding.

| Source | Destination | Frequency |
|:-------|:-----------|:----------|
| Claude sessions | Vault summaries | Every 2 hours |
| Session patterns | Friction reports | Weekly |
| Repeated tasks | Deterministic scripts | Daily |
| Vault contents | Search index (BM25 + vectors) | Every 30 min |

**The daily loop:** morning briefing → work → evening review → tomorrow is smarter.

---

## Filesystem

Four boundaries. Never crossed.

```
~/aos/          SYSTEM        Git repo. Safe to pull, reset, clone.
~/.aos/         USER DATA     Never in git. Never touched by updates.
~/vault/        KNOWLEDGE     Independent. Obsidian-native. Path configurable.
~/project/      PROJECTS      Self-contained. Own context, agents, work.
```

> **Rule**: No user data inside the system repo. A `git clean -fd` on `~/aos/` must never destroy user data.

<details>
<summary><strong>Full tree</strong></summary>

```
~/aos/
├── core/
│   ├── agents/            System agent definitions
│   ├── services/          Bridge, dashboard, listen, memory
│   ├── integrations/      Telegram, WhatsApp, email, etc.
│   ├── work/              Work engine (parser, query, metrics)
│   └── bin/               Utilities
├── config/                System configuration
├── templates/             Agent catalog + project scaffold
├── specs/                 Architecture documentation
└── vendor/                Third-party dependencies

~/.aos/
├── work/                  Goals, tasks, inbox, reviews
├── services/              Runtime state
├── config/                User config (operator.yaml, trust.yaml)
└── logs/                  All logs

~/vault/
├── daily/                 Daily notes
├── sessions/              Session summaries
├── materials/             Research, transcripts
└── reviews/               Friction reports
```

</details>

---

## Install

### One-liner

```bash
curl -fsSL https://raw.githubusercontent.com/hishamalhadi/aos/main/install.sh | bash
```

### Manual

```bash
git clone https://github.com/hishamalhadi/aos.git ~/aos
cd ~/aos && bash install.sh
```

### Requirements

| Requirement | Notes |
|:-----------|:------|
| macOS | Apple Silicon or Intel |
| Internet | For initial setup only |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Active subscription |

Everything else (Homebrew, Python, Bun, etc.) is installed automatically.

---

## Commands

```bash
claude                 # Talk to Chief
cld                    # Talk to Chief (auto-approve)
aos status             # System health
aos update             # Pull latest
aos self-test          # Verify installation
```

---

## Design Decisions

| Decision | Why |
|:---------|:----|
| Claude Code as runtime | Your subscription, no API keys. Built-in subagents, tools, headless mode. |
| macOS Keychain | Native hardware-backed encryption. No external deps. |
| Localhost-only | Zero attack surface. Tailscale for remote. |
| File-based work | No database to manage. Agents and humans read the same files. |
| No orchestration framework | Claude Code's subagents handle dispatch. Less complexity, same result. |
| Symlink system, copy catalog | System agents auto-update with the OS. Catalog agents are copied so user edits survive. |

---

## License

MIT

---

<p align="center">
  <sub>Built for people who want their computer to work for them.</sub>
</p>
