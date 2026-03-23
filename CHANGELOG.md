# AOS Changelog

All notable changes. Sent as Telegram release notes after each 4am update.
Each version: short summary line + categorized changes.

## v0.3.0 — 2026-03-23

Summary: Dev/runtime split, automatic drift repair, cleaner updates.

Added: reconcile system — 8 invariant checks auto-repair drift on every update cycle
Added: CLAUDE.md managed sections — AOS updates its content without touching your customizations
Added: `aos reconcile` command — run checks manually anytime
Changed: execution logs write to ~/.aos/ instead of the system repo
Changed: no more hourly "update available" spam — just release notes after the 4am update
Fixed: mcp.json wrong location auto-detected and merged
Fixed: drift repair runs even when no new code shipped (catches Homebrew updates, etc.)
Removed: auto-commit on ~/aos/ — runtime data no longer pollutes git history

## v0.2.0 — 2026-03-22

Summary: Onboarding, voice notes, agent renaming, 35+ bug fixes.

Added: onboarding v2 — conversation-first flow with personalized setup
Added: morning ramble — voice note to tasks via Telegram
Added: 7-day learning drip via Telegram
Added: agent renaming — `aos rename-agent <name>`
Added: AirDrop connect script for operator's MacBook
Added: `aos repair` command — full rebuild in one shot
Added: ramble skill — conversational voice/text processor
Added: reboot recovery — auto-reload services after restart
Added: file locking in work system — prevents concurrent corruption
Changed: voice transcription auto-detects backend (mlx-whisper → faster-whisper)
Changed: secrets moved to login keychain — no more password prompts
Changed: service venvs find Python 3.11+ automatically
Fixed: SessionStart hook crash on Python 3.9
Fixed: dashboard RAM calculation on Intel Macs
Fixed: bridge restart after Telegram credentials stored
Fixed: hooks format in settings.json
Fixed: scheduler shebang portability
Removed: NLTK phantom dependency from memory service

## v0.1.0 — 2026-03-21

Summary: Initial release.

Added: install script, 3 system agents (Chief, Steward, Advisor), work system, dashboard, bridge (Telegram + voice), listen server, memory MCP, 15 skills, 12+ cron jobs, vault with QMD search
