# AOS — Agentic Operating System

Turn a Mac Mini into an autonomous workstation that manages your work, runs agents, and learns over time.

## Install

1. Clone this repo:
   ```
   git clone https://github.com/agentalhadi/aos.git ~/aos
   ```

2. Double-click **`Install AOS.command`** in the `aos` folder.

That's it. The installer handles everything — dependencies, configuration, services, and system setup. It takes about 5 minutes.

When it's done, open Terminal and type `claude`. Chief (your AI operator) will guide you through the rest.

## What you get

- **Chief** — an AI agent that receives your requests and gets things done
- **Work system** — tasks, projects, goals tracked automatically
- **Knowledge vault** — everything you learn, indexed and searchable
- **Dashboard** — web UI at localhost:4096
- **Integrations** — Telegram, WhatsApp, email, calendar (configured during onboarding)

## Commands

| Command | What it does |
|---------|-------------|
| `claude` | Talk to Chief |
| `cld` | Talk to Chief (no permission prompts) |
| `aos status` | Check system health |
| `aos update` | Pull latest updates |
| `aos self-test` | Verify everything works |

## Requirements

- macOS (Apple Silicon or Intel)
- Internet connection (for initial setup)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) account

Everything else is installed automatically.
