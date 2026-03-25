# AOS Changelog

Detailed release history. Rendered on GitHub.
Telegram gets a separate, shorter message generated from the Summary + highlights.

## v0.5.0 — 2026-03-25

Summary: Bug reporting, system self-repair, cleaner updates.

### Added
- `/report` skill — say "there's a bug" and Chief investigates, diagnoses, and files it
- Report script — GitHub Issues + Telegram notification + PII scrubbing + offline queue
- Reconcile system — auto-repairs config drift overnight (8+ checks)
- `aos reconcile` — run system health checks manually
- CLAUDE.md managed sections — system updates its docs without touching yours
- `/ship` skill — push dev changes to main with safety checks
- `aos-report --queued` — see reports saved locally when GitHub was offline

### Changed
- Dev/runtime split — `~/aos/` is always on main (runtime), `~/project/aos/` for development
- Execution logs write to `~/.aos/logs/execution/` instead of inside the git-tracked `~/aos/` directory
- No more hourly "update available" Telegram spam — only release notes after the 4am update
- SessionStart hook wrapped in safe exit — always outputs valid JSON and exits 0, even on Python 3.9
- Settings.json permissions use blanket tool-level allows (`Bash`, `Read`, `Edit`, `Write`)
- Changelog format: user-facing bullets for Telegram, detailed breakdown on GitHub

### Fixed
- `mcp.json` in wrong location auto-detected and merged to `~/.claude/mcp.json`
- Auto-commit disabled on `~/aos/` — runtime data no longer pollutes git history
- PII scrubbing expanded to cover IPs, phone numbers, API keys (matching the old feedback script)
- `execution_log/` added to `.gitignore` and removed from git tracking
- `collect_context()` wrapped in try/except — report script can never crash silently

## v0.4.0 — 2026-03-24

Summary: Initiative tracking, smarter briefings, Google Workspace.

### Added
- Initiative pipeline Phase 1 — idea-to-execution system with vault-backed initiative documents
- Initiative scanning in session hooks — auto-discovers active initiatives at session start
- `work initiatives` CLI command — list and manage initiative lifecycle
- `source_ref` linking — tasks trace back to their parent initiative
- Stale-initiative cron (09:00 daily) — Telegram nudge when initiatives go untouched for 3+ days
- Shared notify helper (`core/lib/notify.py`) — stdlib-only Telegram notifications from hooks
- Bridge v2 BLUF morning briefing — scannable 5-section format (URGENT / IMPORTANT / THINK ABOUT / PEOPLE / OVERNIGHT)
- Bridge v2 conversational evening wrap — celebrates done work, surfaces open items
- Quick command shortcuts — sub-500ms responses bypassing Claude for common actions (add task, mark done, search vault)
- Cross-session decision store (`shared_context.py`) — atomic writes, 30-day TTL
- Progressive forum topic management — topics created on first use, not upfront
- Bridge structured event logging (`bridge_events.py`)
- Google Workspace MCP integration — Calendar, Gmail, Drive, Docs, Sheets
- Reconcile checks for initiative directories and bridge topics config
- Migrations 017 (bridge topics) + 018 (initiative infrastructure)

### Changed
- Daily briefing rewritten — delta-only BLUF format replacing old metrics dump
- Evening check-in rewritten — conversational wrap replacing form-style checklist
- `session_close` uses surgical regex for frontmatter updates (not `yaml.dump`)
- Intent classifier expanded with 14 quick command intents

## v0.3.0 — 2026-03-23

Summary: Automatic drift repair, cleaner updates.

### Added
- Reconcile system — 8 invariant checks auto-repair drift on every update cycle
- CLAUDE.md managed sections — AOS updates its content without touching your customizations
- `aos reconcile` command — run checks manually anytime

### Changed
- Execution logs write to `~/.aos/` instead of the system repo

### Fixed
- `mcp.json` wrong location auto-detected and merged
- Drift repair runs even when no new code shipped (catches Homebrew updates, etc.)

### Removed
- Auto-commit on `~/aos/` — runtime data no longer pollutes git history
- Hourly "update available" Telegram spam

## v0.2.0 — 2026-03-22

Summary: Onboarding, voice notes, agent renaming, 35+ bug fixes.

### Added
- Onboarding v2 — conversation-first flow with personalized setup
- Morning ramble — voice note to tasks via Telegram
- 7-day learning drip via Telegram
- Agent renaming — `aos rename-agent <name>`
- AirDrop connect script for operator's MacBook
- `aos repair` command — full rebuild in one shot
- Ramble skill — conversational voice/text processor
- Reboot recovery — auto-reload services after restart
- File locking in work system — prevents concurrent corruption

### Changed
- Voice transcription auto-detects backend (mlx-whisper → faster-whisper)
- Secrets moved to login keychain — no more password prompts
- Service venvs find Python 3.11+ automatically

### Fixed
- SessionStart hook crash on Python 3.9
- Dashboard RAM calculation on Intel Macs
- Bridge restart after Telegram credentials stored
- Hooks format in settings.json
- Scheduler shebang portability

### Removed
- NLTK phantom dependency from memory service

## v0.1.0 — 2026-03-21

Summary: Initial release.

### Added
- Install script with one-liner curl setup
- 3 system agents — Chief, Steward, Advisor
- Work system with project-scoped IDs and subtasks
- Dashboard with real-time SSE
- Bridge — Telegram + Slack + voice transcription
- Listen job server
- Memory MCP service
- 15 default skills
- 12+ scheduled cron jobs
- Knowledge vault with QMD semantic search
