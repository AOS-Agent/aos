# AOS Changelog

All notable changes to AOS. Release notes sent via Telegram after each 4am update.

## v0.6.1 — 2026-06-30

Summary: Repaired the self-update system on release-converted machines — it had been silently broken since the release-system rollout, generating ~1000 false "cron failed" alerts and never actually applying updates.

- Fixed check-update reporting a cron failure on every run on release-system installs (~/aos is a symlink with no git remote) — this produced nearly 1000 false "cron failed" Telegram alerts. A "nothing to do" check now exits 0, not 1.
- Fixed the `--apply` path being dead code — `check()` never returned the sentinel `apply()` waited for, so `aos update` and the 4am auto-update cron never applied anything, even on git installs.
- Added a release-aware update path — check-update now detects release vs git installs and drives release-system updates through release-manager (create + activate) off the source repo, instead of a git pull against the read-only release symlink.
- Changed phase-2 deploy to be install-aware — it computes changed services from the source repo on release installs (where ~/aos has no .git) and reads the new commit hash from the handoff.
- Changed update state writes to pass values via the environment instead of interpolating them into inline Python, hardening against commit messages that contain quotes.
- Fixed release-manager activate/rollback never switching the ~/aos symlink on macOS — `mv -f` followed the symlink into the read-only release dir ("Permission denied"). They now use a shared `_swap_link` helper (atomic `mv -fT` on Linux, `ln -sfn` fallback on macOS, matching convert()). This means auto-update could not have applied a release on any macOS machine.
- Fixed release-manager's release validator reporting false "fails to parse" warnings — it now parse-checks scripts in memory instead of letting py_compile try to write bytecode into the read-only release dir.

## v0.6.0 — 2026-03-28

System revamp — restructured `core/` for navigability, hardened infrastructure, added tests and documentation.

- Reorganized `core/bin/` into `cli/`, `crons/`, `setup/`, `internal/` — scripts are now self-documenting by location
- Grouped `core/work/`, `bus/`, `comms/` under `core/engine/` — the active intelligence layer
- Grouped `core/reconcile/`, `migrations/`, `integrations/`, `lib/` under `core/infra/` — system plumbing
- Added `core/infra/lib/safe_io.py` — atomic file writes with `fsync`, safe YAML load/dump, atomic JSONL append
- Added `core/infra/lib/log.py` — structured JSON logging (`{"ts":"...","level":"...","source":"...","msg":"..."}`)
- Added `core/infra/lib/rate_limit.py` — token-bucket rate limiter, wired into Telegram sends (1 msg/sec)
- Added `core/infra/lib/validate.py` — validates `operator.yaml`, `crons.yaml`, `bridge-topics.yaml` at startup
- Added `fsync` to `engine.py` atomic writes — protects against power loss, not just crashes
- Fixed unsafe write in `metrics.py` — was the only file using raw `open()` instead of atomic write
- Pinned all service dependencies with `requirements.lock` files — `pip install` no longer grabs latest from PyPI
- Updated `aos deploy` to prefer `requirements.lock` over unpinned `pyproject.toml`
- Added pre-push git hook — 5 checks: Python syntax, Bash syntax, YAML syntax, critical imports, secret scanning
- Converted bridge service to structured JSON logging via shared `get_logger()`
- Added 30 pytest tests covering task CRUD, fuzzy resolution, subtask cascade, context injection, handoffs
- Added README.md to all 6 services, 3 engine directories, and reconcile — each with restart commands and key files
- Added `docs/ARCHITECTURE.md` — one-page system architecture overview
- Added migration 022 — updates instance-side path references for existing installs
- Updated 150+ hardcoded path references across 40+ files

## v0.5.1 — 2026-03-26

Trust Graduation — the system learns your communication patterns and graduates from observing to assisting.

- Added retroactive extraction pipeline — mines iMessage + WhatsApp history through existing adapters (18,000+ messages → 1,900 interactions in one pass)
- Added WhatsApp local adapter — reads ChatStorage.sqlite directly for 6 years of history (vs 5 days from the bridge)
- Added communication patterns: per-person response baselines, preferred hours, message style ratios
- Added auto-classification of importance tiers from interaction data (inner circle / active / acquaintance / peripheral)
- Added transactional contact filtering — detects delivery services, shops, one-time contacts
- Added graduation engine — evaluates trust per-person, queues promotions for approval, applies demotions instantly
- Added draft engine — assembles conversation context + person profile + style samples, generates reply drafts via Claude Code CLI
- Added draft feedback loop — accept/edit/discard via Telegram, every action feeds graduation
- Added style learning — operator edits to drafts are saved and fed back into future draft prompts
- Added autonomous layer — Level 3 handles routine messages (confirmations, scheduling, greetings) with hard guardrails and confidence gate
- Added circuit breaker — 2 corrections out of 5 autonomous actions triggers instant demotion
- Added daily extraction lifecycle hook — auto-detects channels, runs on fresh install + new channel + daily
- Added cron chain: extract (05:00) → patterns (05:30) → graduation (06:00)
- Added dashboard trust page at /trust — trust map, graduation timeline, pending proposals
- Added 5 trust API endpoints on the dashboard
- Added Telegram /trust commands — check status, override levels
- Added contact resolver: 340 aliases, 10 relationships, 308 auto-generated last-name aliases
- Added TelegramAdapter for comms bus — bridge writes to JSONL queue, adapter reads during poll
- Changed Comms Intelligence from executing to review — all 6 phases complete
- Changed Contact Resolution to archived — consolidated into Comms Intelligence Phase 1

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
