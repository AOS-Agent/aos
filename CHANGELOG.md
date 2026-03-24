# AOS Changelog

All notable changes. Sent as Telegram release notes after each 4am update.
Each version: short summary line + categorized changes.

## v0.4.0 — 2026-03-24

Summary: Initiative pipeline foundation, Bridge v2 mobile command center, Google Workspace integration.

Added: initiative pipeline Phase 1 — idea-to-execution system with vault-backed initiative documents
Added: initiative scanning in session hooks — auto-discovers active initiatives at session start
Added: `work initiatives` CLI command — list and manage initiative lifecycle
Added: `source_ref` linking — tasks trace back to their parent initiative
Added: stale-initiative cron (09:00 daily) — Telegram nudge when initiatives go untouched for 3+ days
Added: shared notify helper (`core/lib/notify.py`) — stdlib-only Telegram notifications from hooks
Added: Bridge v2 BLUF morning briefing — scannable 5-section format (URGENT/IMPORTANT/THINK ABOUT/PEOPLE/OVERNIGHT)
Added: Bridge v2 conversational evening wrap — celebrates done work, surfaces open items
Added: quick command shortcuts — sub-500ms responses bypassing Claude for common actions (add task, mark done, search vault)
Added: cross-session decision store (`shared_context.py`) — atomic writes, 30-day TTL
Added: progressive forum topic management — topics created on first use, not upfront
Added: bridge structured event logging (`bridge_events.py`)
Added: Google Workspace MCP integration — Calendar, Gmail, Drive, Docs, Sheets via workspace-mcp
Added: reconcile checks for initiative directories and bridge topics config
Added: migrations 017 (bridge topics) + 018 (initiative infrastructure)
Changed: daily briefing rewritten — delta-only BLUF format replacing old metrics dump
Changed: evening checkin rewritten — conversational wrap replacing form-style checklist
Changed: session_close uses surgical regex for frontmatter updates (not yaml.dump)
Changed: intent classifier expanded with 14 quick command intents

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
