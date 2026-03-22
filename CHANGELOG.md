# AOS Changelog

## v0.2.0 — 2026-03-22

### New
- Onboarding v2 — conversation-first flow with Sahib personality
- Morning ramble system — voice note → tasks/ideas/vault
- 7-day learning drip — daily Telegram tips teaching the system
- Agent renaming — `aos rename-agent <name>`
- AirDrop connect script for laptop remote access
- Screen sharing enabled alongside SSH

### Improved
- All services auto-detect correct Python (no more broken venvs)
- Voice transcription auto-selects mlx-whisper or faster-whisper
- Telegram dispatch has full tool access (agent, chrome, all tools)
- Keychain uses login keychain (no more password prompts)
- Always-on: auto-restart, auto-login, reboot recovery via scheduler
- Work system has file locking (no more corruption from concurrent sessions)

### Fixed
- SessionStart hook works on Python 3.9 (graceful fallback)
- Bridge restarts after Telegram credentials are stored during onboarding
- Trust dialog pre-accepted (no interactive prompt on first run)
- Installer resolves Python correctly on fresh Mac (homebrew over system)

## v0.1.0 — 2026-03-21

### Initial Release
- Install script (2000 lines, idempotent, checkpointed)
- 3 system agents: Chief, Steward, Advisor
- Work system: tasks, projects, goals, subtasks, handoffs
- Dashboard: real-time SSE, work tracking, session history
- Bridge: Telegram + Slack, voice transcription, Claude dispatch
- Listen: background job server with Claude Code workers
- Memory: ChromaDB semantic search (MCP server)
- 15 default skills, 9 developer skills
- Scheduler with 12+ automated cron jobs
- Knowledge vault with QMD search
