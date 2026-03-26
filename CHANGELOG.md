# AOS Changelog

All notable changes to AOS. Release notes sent via Telegram after each 4am update.

## v0.4.0 — 2026-03-24

Initiative pipeline, Bridge v2 mobile command center, Google Workspace integration.

- Added initiative pipeline Phase 1 — idea-to-execution system with vault-backed initiative documents that track status from `research` through `executing` to `review`
- Added initiative scanning in `SessionStart` hook — auto-discovers active initiatives and injects their state into session context
- Added `work initiatives` CLI command for listing and managing initiative lifecycle
- Added `source_ref` linking so tasks trace back to their parent initiative
- Added stale-initiative cron (09:00 daily) — sends a Telegram nudge when initiatives go untouched for 3+ days
- Added shared notify helper (`core/lib/notify.py`) — stdlib-only Telegram notifications usable from any hook or script
- Added Bridge v2 BLUF morning briefing with 5-section scannable format: URGENT / IMPORTANT / THINK ABOUT / PEOPLE / OVERNIGHT
- Added Bridge v2 conversational evening wrap that celebrates completed work and surfaces open items
- Added quick command shortcuts — sub-500ms responses bypassing Claude for common actions (`add task`, `mark done`, `search vault`)
- Added cross-session decision store (`shared_context.py`) with atomic writes and 30-day TTL
- Added progressive forum topic management — topics created on first use, not upfront
- Added structured event logging for the bridge (`bridge_events.py`)
- Added Google Workspace MCP integration — Calendar, Gmail, Drive, Docs, Sheets via `workspace-mcp`
- Added reconcile checks for initiative directories and bridge topics config
- Added migrations 017 (bridge topics) and 018 (initiative infrastructure)
- Rewrote daily briefing as delta-only BLUF format, replacing the old metrics dump
- Rewrote evening checkin as conversational wrap, replacing form-style checklist
- Changed `session_close` to use surgical regex for frontmatter updates instead of `yaml.dump`
- Expanded intent classifier with 14 quick command intents

## v0.3.0 — 2026-03-23

Dev/runtime split, automatic drift repair, cleaner updates.

- Added reconcile system — 8 invariant checks that auto-repair drift on every update cycle
- Added `CLAUDE.md` managed sections — AOS updates its own content blocks without touching your customizations
- Added `aos reconcile` command to run checks manually anytime
- Changed execution logs to write to `~/.aos/` instead of the system repo
- Removed hourly "update available" spam — now just sends release notes after the 4am update
- Fixed `mcp.json` wrong location — auto-detected and merged into correct path
- Fixed drift repair to run even when no new code shipped (catches Homebrew updates, config changes, etc.)
- Removed auto-commit on `~/aos/` — runtime data no longer pollutes git history

## v0.2.0 — 2026-03-22

Onboarding, voice notes, agent renaming, 35+ bug fixes.

- Added onboarding v2 — conversation-first flow with personalized setup
- Added morning ramble — voice note to tasks via Telegram
- Added 7-day learning drip sent via Telegram
- Added agent renaming with `aos rename-agent <name>`
- Added AirDrop connect script for operator's MacBook
- Added `aos repair` command for full system rebuild in one shot
- Added ramble skill — conversational voice/text processor for free-form input
- Added reboot recovery — auto-reload services after restart
- Added file locking in work system to prevent concurrent corruption
- Changed voice transcription to auto-detect backend (`mlx-whisper` → `faster-whisper`)
- Moved secrets to login keychain — no more password prompts
- Changed service venvs to find Python 3.11+ automatically
- Fixed `SessionStart` hook crash on Python 3.9
- Fixed dashboard RAM calculation on Intel Macs
- Fixed bridge restart after Telegram credentials stored
- Fixed hooks format in `settings.json`
- Fixed scheduler shebang portability
- Removed NLTK phantom dependency from memory service

## v0.1.0 — 2026-03-21

Initial release.

- Added install script with guided setup
- Added 3 system agents: Chief (orchestrator), Steward (health), Advisor (analysis)
- Added work system with tasks, projects, goals, and threads
- Added dashboard service on `:4096`
- Added Telegram bridge with voice note transcription
- Added listen server on `:7600`
- Added memory MCP with QMD search
- Added 15 skills for common workflows
- Added 12+ cron jobs for automated maintenance
- Added vault with QMD-indexed markdown search
