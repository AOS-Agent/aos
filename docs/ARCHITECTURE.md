# AOS Architecture

AOS (Agentic Operating System) is a self-hosted AI agent runtime on a Mac Mini. It provides persistent services, a messaging bridge, task tracking, and self-healing infrastructure so Claude-based agents can act continuously — not just during a single chat session.

## Directory Map

```
~/aos/                          Runtime copy (read-only, pulled from git)
~/project/aos/                  Dev workspace (all edits happen here)

core/
  services/
    bridge/                     Telegram/Slack messaging bridge (daemon)
    dashboard/                  Web UI :4096
    eventd/                     Event daemon :4097
    listen/                     Job queue server :7600
    transcriber/                Voice-to-text :7602
    whatsmeow/                  WhatsApp adapter :7601
  engine/
    work/                       Task engine + CLI + session hooks
    comms/                      Contact resolution + channel routing
    bus/                        In-process pub/sub event bus
    integrations/               Third-party API clients
    lib/                        Shared utilities
    migrations/                 Data schema migrations
  infra/
    reconcile/                  Self-healing checks (runs every 2h)
.claude/
  skills/                       Skill definitions (SKILL.md per skill)
  rules/                        Standing rules injected into every session
docs/                           Architecture and guides (this file)
config/                         Default config templates
```

## How Updates Work

```
aos update
  └── git pull ~/project/aos → ~/aos       (pull latest framework)
  └── reconcile runner                     (19 health checks, auto-fix)
  └── migrate                              (run any pending data migrations)
  └── sync LaunchAgents                    (register new/changed plists)
  └── restart changed services             (graceful restart only what changed)
```

## How Sessions Work

```
Claude Code session opens
  └── SessionStart hook → inject_context.py
        reads active tasks, current thread, handoff notes
        injects them into the system prompt

  agent works — reads vault, runs tools, builds things

Claude Code session closes
  └── SessionEnd hook → session_close.py
        if tasks are in-progress: prompts agent to write handoff
        logs session summary to ~/.aos/logs/
```

## Services

| Name        | Port  | Purpose                              |
|-------------|-------|--------------------------------------|
| bridge      | —     | Telegram/Slack → agent routing       |
| dashboard   | 4096  | Live web UI for agent activity       |
| eventd      | 4097  | System-wide event fan-out            |
| listen      | 7600  | Async job queue and workers          |
| whatsmeow   | 7601  | WhatsApp adapter                     |
| transcriber | 7602  | Local voice-to-text (mlx-whisper)    |

All services bind `127.0.0.1`. Remote access goes through `tailscale serve`
or the Cloudflare tunnel — never a wildcard bind. See [networking.md](networking.md)
before adding a service or touching a bind address.

## Memory & Search

QMD is the memory/search layer over `~/vault` (the ChromaDB `memory` MCP
server it replaced was retired in migration 110 — it never indexed a
document on the reference machine, and the vault has thousands). `qmd
query "<topic>"` is the read path, used interactively and by the `recall`
skill. Curation — deciding what from the log is worth keeping permanently
— is a separate, weekly loop:

```
~/vault/log/**                    what happened (dailies, sessions, meetings)
        │
        ▼  memory-curate (weekly, Sunday 06:00, config/crons.yaml)
        │   gathers the last 7 days of log/** + tasks closed in that
        │   window, dispatches the Advisor headlessly to propose
        │   promotions per SCHEMA.md (target path, frontmatter)
        ▼
~/.aos/work/memory-proposals.yaml       proposals awaiting review
        │
        ▼  SessionStart nudge (inject_context.py) surfaces the count
        ▼  operator reviews and promotes by hand
        ▼
~/vault/knowledge/**               what we know — durable, QMD-indexed
```

`memory-curate` never writes into `knowledge/` itself; promotion is always
an operator action. Once a fact lands in `knowledge/`, the next `qmd-reindex`
cron picks it up like any other vault document — no separate registration
step.

## Key Commands

```bash
aos update                                      # Pull, reconcile, sync, restart
aos self-test                                   # Run smoke tests against all services
python3 ~/aos/core/engine/work/cli.py add "Title"      # Create a task
python3 ~/aos/core/engine/work/cli.py done "fuzzy"     # Complete a task
python3 ~/aos/core/engine/work/cli.py list             # List active tasks
python3 ~/aos/core/engine/work/cli.py handoff <id> ... # Write handoff before ending session
qmd query "<topic>"                             # Search vault + AOS docs
agent-secret get <key>                          # Read a secret from Keychain
```
