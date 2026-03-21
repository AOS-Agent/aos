# AOS Architecture

## System Diagram

See `docs/diagrams/aos-architecture.svg` for the full interactive diagram (D2 source at `docs/diagrams/aos-architecture.d2`).

```
Operator (MacBook / Phone)
    │
    Tailscale (private mesh)
    │
┌───▼───────────────────────────────────────────────┐
│ AOS — Mac Mini (Apple Silicon, macOS)              │
│                                                    │
│  Bridge (daemon)          Dashboard (:4096)        │
│    Telegram channels        Agent registry         │
│    Forum topic routing      Activity stream (SSE)  │
│    Heartbeat (30min)        Log viewer             │
│    Daily briefing (8am)     System health          │
│    Voice transcription                             │
│    Claude CLI dispatch                             │
│                                                    │
│  Listen (:7600)           Memory (MCP)             │
│    HTTP job server          ChromaDB vectors       │
│    Claude Code workers      Semantic search        │
│    YAML job tracking        File recall            │
│                                                    │
│  Agents (.claude/agents/)                          │
│    engineer — infrastructure, installation         │
│    ops — health monitoring, heartbeat (haiku)      │
│    technician — messaging infra, bridge fixes      │
│    + per-project agents (e.g. nuchay)              │
│                                                    │
│  Projects (~/<name>/)                              │
│    Own CLAUDE.md, agents, goals, Telegram bot      │
│    Registered in config/projects.yaml              │
└───────────────────────────────────────────────────┘
```

## Services

| Service | Type | Port | LaunchAgent | Code |
|---------|------|------|-------------|------|
| Bridge | Daemon | — | `com.agent.bridge` | `apps/bridge/` |
| Dashboard | HTTP | 4096 | `com.agent.dashboard` | `apps/dashboard/` |
| Listen | HTTP | 7600 | `com.agent.listen` | `apps/listen/` |
| Memory | MCP (stdio) | — | on-demand | `apps/memory/` |

All HTTP services bound to `127.0.0.1`. Remote access via Tailscale only.

## Execution Model

Claude Code is the execution engine. The system does not use LangGraph, Paperclip, or any external orchestration framework.

- **Telegram message** → Bridge dispatches to Claude CLI (`claude -p`) with project-specific `--cwd` and `--agent`
- **HTTP job** → Listen spawns a Claude Code worker process, tracks via YAML
- **Agent invocation** → Claude Code's built-in subagent system (`.claude/agents/*.md` with YAML frontmatter)
- **Memory queries** → MCP server (`apps/memory/`) exposes semantic search to any Claude session

## Delegation Model

See `specs/sulaimanic-model.md` for the full framework.

Two dispatch patterns:
1. **أَيُّكُمْ (broadcast)** — task sent to assembly, best-fit agent self-nominates
2. **ٱذْهَب (direct)** — operator dispatches to a specific agent

Trust levels tracked in `config/trust.yaml`:
- Level 1: سَنَنظُرُ (verify everything) — default
- Level 2: مُؤْتَمَن (entrusted) — spot-checked
- Level 3: عِفْرِيت (autonomous) — earned through consistent performance

## Project Model

Each project lives at `~/<name>/` as a sibling to `~/aos/`. Projects are registered in `config/projects.yaml` and get:
- Own `CLAUDE.md` with project-specific context
- Own `.claude/agents/` for domain agents
- Own `config/goals.yaml` and `config/tasks.yaml`
- Own Telegram bot (forum topic routing via Bridge)

Goals roll up: project goals feed into the system-level morning briefing and dashboard.

## Knowledge Vault (`~/vault/`)

The compounding knowledge layer. Agents write, QMD indexes, Obsidian views.

| Folder | Content | Populated By |
|--------|---------|-------------|
| `daily/` | Daily notes (mood, energy, sleep frontmatter) | `/gm` auto-creates, evening check-in fills |
| `sessions/` | Claude Code session summaries | `bin/session-export` (cron every 2h) |
| `ideas/` | Quick captures | `/note` and `/capture` bridge commands |
| `materials/` | YouTube transcripts, articles, research | `/capture` + transcriber pipeline |
| `projects/` | Project-specific notes | Manual + agents |
| `reviews/` | Friction reports, reflections | `bin/session-analysis` (weekly cron) |

**QMD** indexes 9 collections with BM25 + vector + LLM reranking. Re-indexed every 30m.
**Obsidian** (v1.12.4) provides GUI views via Bases.
**Recall skill** loads relevant vault context at session start.

### Daily Loop

`/gm` (8AM) → work + `/capture` + `/note` → evening check-in (9PM) → tomorrow's `/gm` is smarter.
Session analysis (weekly) mines friction → suggests CLAUDE.md improvements → agents self-improve.

## Persistence

| What | How |
|------|-----|
| Secrets | macOS Keychain (`bin/agent-secret`) |
| Services | LaunchAgents (survive reboot) |
| Configuration | YAML in `config/` (git-versioned) |
| Knowledge | Markdown in `~/vault/` (QMD-indexed) |
| Memory | ChromaDB in `data/memory/chromadb/` |
| Sessions | JSON in `data/bridge/sessions.json` |
| Activity | SQLite in `data/dashboard/activity.db` |

## Design Decisions

**Why Claude Code as runtime?** It runs on the operator's Claude Max subscription (no API keys), has built-in subagent support, file/bash/web tools, and headless mode (`-p`) for daemon integration.

**Why macOS Keychain?** Native, encrypted, no external dependencies. `bin/agent-secret` wraps it for easy get/set.

**Why localhost-only?** Minimizes attack surface. Tailscale provides authenticated, encrypted remote access without exposing ports.

**Why one bot per project?** Isolation. Each project's Telegram bot sees only its own context. Forum topics within a group provide routing without cross-contamination.

**Why no external orchestration framework?** Claude Code's subagent system handles multi-agent dispatch. Adding LangGraph/Paperclip would add complexity without proportional benefit at current scale.
