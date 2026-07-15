# AOS Changelog

All notable changes to AOS. Release notes sent via Telegram after each 4am update.

## v0.6.11 — 2026-07-15

Summary: The last batch before v0.6.10+ promotes to friends. Role-aware session rules so an operator's machine stops being lectured to use a developer's dev workspace, a guarded service restart that can no longer leave a LaunchAgent silently unloaded (the bridge-vanish incident), the last reconcile check that still crashed every cycle, and a pass over the Telegram messages so a briefing reads like plain English on a phone. Migration 081.

- Made the shipped session rules role-aware (`core/infra/reconcile/checks/claude_md.py`, migration 081): every machine's `~/.claude/CLAUDE.md` used to carry "NEVER edit ~/aos — all framework changes go in ~/project/aos (dev workspace)", which is meaningless on a friend's install that has no dev workspace. A `role` flag (`developer` iff `~/project/aos` exists, else `operator`; stamped by migration 081, inferred at runtime until then) now selects the rules block: developer machines keep the dev-workspace discipline, operator machines are told to fix their own instance directly and report framework bugs upstream via the report skill. Both variants also carry the network policy — localhost + Tailscale, with Cloudflare Tunnel allowed only as an explicit operator opt-in via the remote-access wizard. Managed blocks now also re-sync on content drift, so a machine that changes role converges on the next reconcile.
- Fixed a LaunchAgent restart that could leave a service silently unloaded (`core/bin/crons/check-update`): the phase-2 restart ran `bootout` → `bootstrap` → `kickstart` back-to-back with every error swallowed. `bootout` is asynchronous, so `bootstrap` raced the teardown and, when it lost, the job was left booted-out — which is how `com.aos.bridge` vanished during the v0.6.10 cycle (plist and venv intact, job gone). All restarts now go through one guarded helper that settles after bootout, verifies the bootstrap actually registered the job, retries, and never returns having left the service unloaded. A structural test gate keeps the racy inline sequence from returning.
- Fixed the `vault_contract` reconcile check failing every cycle with "No module named 'core'" (`core/infra/reconcile/checks/vault_contract.py`): the runner only puts `core/infra/reconcile` on `sys.path`, so the check's `from core.engine.intelligence.inventory import scan_vault` never resolved. It now adds the AOS root to `sys.path` before importing, matching the idiom used across the codebase.
- Rewrote the Telegram messages in plain human English (`core/services/bridge/`): the daily briefing no longer emits internal codes — "— P1 active" → "— top priority", "[executing]" → "in progress", "overdue by 3d" → "overdue by 3 days", "phase 2/3" → "phase 2 of 3". Ships a `MESSAGE_STYLE.md` guide (no codes/IDs/commit-hashes/gate jargon, bottom-line-up-front, bold headers, scannable on a phone) that the templates' docstrings point to.
- Fixed the weekly digest KeyError-ing every week when there were no session exports (`core/bin/crons/weekly-digest`): `scan_sessions`'s empty-directory early return omitted `total_duration_min` and `avg_duration_min`, which the report body reads unconditionally — so the digest crashed 12 weeks running. The early return now carries the full key set (both at 0). A regression test pins the empty return's keys to the normal return's.
- Fixed three shipped docs still probing the retired `:4096/health` endpoint (`core/agents/steward.md`, `core/skills/step-by-step/SKILL.md`): Qareen's health moved to `/api/health`, so Steward and the step-by-step skill were checking a 404. Updated to `:4096/api/health`.

## v0.6.10 — 2026-07-14

Summary: Promote-readiness hotfixes before v0.6.9+ moves from the edge lane to friends' stable machines. A safe service-venv rebuild that can no longer strand a service without a venv, two silently-dead sync commands repointed at where the code and harness actually live, a poll-liveness heartbeat that catches the bridge's Telegram loop wedging while the process looks healthy, and a test gate that stops the kickstart-timeout bug from shipping a fifth time. Also un-breaks the reconcile loader, which had been crashing on import (so no reconcile check was running).

- Fixed the update system's service-venv rebuild (`core/bin/crons/check-update`): it assumed pyproject.toml (so the requirements.txt-based crawler could never rebuild) and deleted the live venv *before* building the replacement, leaving the crawler with no venv when a rebuild failed on the v0.6.8 update. The rebuild now stages the new venv at a temp path, installs from the service's own dep-file style (requirements.lock → requirements.txt → pyproject.toml), verifies the primary dependency actually imports, then atomically swaps it in — the live venv is never removed until a good replacement exists.
- Fixed two sync commands that silently did nothing (`core/bin/cli/aos`): `sync-skills` pointed at `.claude/skills`, which stopped existing when skills moved to `core/skills`, so it linked zero skills while reporting success; and `sync-mcp` wrote `~/.claude/mcp.json`, which Claude Code only reads for sessions in `~/.claude/`, so memory and crawler were never actually registered for AOS sessions. Skills now link from `core/skills`; MCP servers register as user-scope servers in `~/.claude.json` with an atomic write.
- Fixed the bridge's Telegram poll loop wedging silently (`core/services/bridge/`): a new poll heartbeat records a timestamp on every successful getUpdates (exposed on `:4098/health` and in a state file), and a `bridge_poll_liveness` reconcile check restarts the bridge — via the guarded-kickstart pattern — when the last poll is over 5 minutes old during active hours. Conservative by design: never restarts a bridge that isn't loaded, is outside active hours, or has no recorded poll yet.
- Fixed the reconcile loader crashing on import: three checks (`dev_backend_plist`, `deployment_health`, `vault_contract`) had been auto-linted to `from ..base`, which fails under the runner's flat check load — so every reconcile check had silently stopped running (the crash was swallowed). Restored the flat import the other checks use.
- Added a pattern gate (`tests/test_migration_patterns.py`) that fails CI if any migration or reconcile check runs `launchctl kickstart` without a `try/except subprocess.TimeoutExpired` guard — the bug that shipped four times. It immediately caught two more unguarded instances (the transcriber and n8n reconcile checks), now fixed.
- Fixed ship-check's service-import check, which flagged 38 false positives on any changed service (a broken stdlib guard filtered nothing, so stdlib, local, and internal modules were all reported as undeclared deps). Replaced it with an AST-based checker (`core/infra/service_import_check.py`) that filters stdlib, local, AOS-internal, and relative imports, maps distribution names to import names via the service venv, and parses pyproject with tomllib so extras don't truncate the dep list. ship-check drops from 42 to 4 (pre-existing) warnings; four fixture tests pin the behavior.

## v0.6.9 — 2026-07-14

Summary: Council deliberation substrate, Qareen remote access over Cloudflare Tunnel, a harness-update cron, and a task-system foreign-key fix. Chief can now convene a multi-persona council in the background on high-stakes decisions; Qareen can be reached from the internet behind Cloudflare Access as an explicit, operator-provisioned opt-in; Claude Code stays current so new models stay reachable; and `--project <short-id>` no longer crashes on a foreign-key constraint. Migrations 079–080 (069 is an intentional hole).

- Added the council deliberation substrate (`core/engine/council/`): a token-passing chat engine with four builtin personas (architect, builder, skeptic, dreamer), a scheduler, and a synthesis pass that writes a QMD-indexed decision memo per adjourned council. Chief auto-convenes a background council on gate-check CONCERNS and mid-execution architecture changes rather than asking the operator to run anything (`core/agents/chief.md`). CLI at `core/bin/cli/council`; skill at `core/skills/council/`. The Mission Control council route already shipped as a stub — this lands its backend.
- Added Qareen remote access (`core/qareen/{api/remote_access,integrations/cloudflare,services/tunnel_manager,services/remote_access_state}.py`): reach local Qareen at `aos.<domain>` behind Cloudflare Access (email-OTP + allow-list). Structurally opt-in — nothing is provisioned or exposed until the operator pastes a scoped Cloudflare token into the Settings wizard. Connecting rebinds Qareen from `0.0.0.0` to `127.0.0.1` only after a health poll (so it never locks out the LAN), and disconnect always restores `0.0.0.0`. Both secrets (CF API token, tunnel run-token) live only in the Keychain. Migration 080 adds the `remote_access` state table and the `AOS_QAREEN_HOST` bind env var. UI: a Remote Access section in Settings with a paste-a-token wizard and live status.
- Added the `harness-update` cron (`core/bin/crons/harness-update`, daily 04:15, right after the framework auto-update): runs `npm update -g @anthropic-ai/claude-code` and Telegram-notifies on a version change, so the harness that gates model availability doesn't fall behind the framework. Best-effort — skips cleanly when npm or the package is absent, never touches user state.
- Fixed `work add "Task" --project <short-id>` raising `sqlite3.IntegrityError: FOREIGN KEY constraint failed` — `add_project` now stores the short-id in its first-class `projects.short_id` column instead of encoding it into the description, and `move_tasks_to_project` resolves a short-id target to the canonical id before setting the FK. Migration 079 adds the column + partial index and backfills any legacy `short_id:` token. 7 regression tests.

## v0.6.8 — 2026-07-14

Summary: Intelligence → Knowledge pipeline and content engine. The personal intelligence feed monitors internet sources and scores what matters; the knowledge pipeline turns captured items into vault notes with per-platform templates, shadow-mode compilation proposals, a nightly vault lint pass, and a Knowledge UI. Migrations 072–078.

- Added the personal intelligence feed (`core/engine/intelligence/ingest/`): RSS/RSSHub fetchers, content extraction, relevance triage, and a store keyed on `qareen.db`; `feed-ingest` (every 30m) and `feed-digest` (07:00) crons; the `/intelligence` UI and API. Migration 072 adds the feed columns to `intelligence_briefs`/`intelligence_sources`.
- Added the Intelligence → Knowledge pipeline (`core/engine/intelligence/`): a compile engine with per-platform frontmatter templates (tweet, blog, video, github, paper, generic), a content router with crawler/FxTwitter backends, a bootstrap engine, a vault inventory scanner with a doc-type contract, a nightly lint pass (orphans, stale docs, topic refresh, synthesis suggestions), and a topic builder. Migration 073 backfills `content_status`; 074 adds `compilation_proposals` (shadow-mode compilation); 075 `vault_inventory`; 076 `bootstrap_runs`.
- Added the Knowledge UI (`/knowledge` — Today, Feed, Library, Topics, Pipeline views) and its API, plus the `IntelligenceAdapter` that links captured entities into the ontology as `CAPTURE` objects. Added a Knowledge item to the nav.
- Enforced the compile-template frontmatter contract: `CaptureTemplate.validate_frontmatter()` checks a built capture against its template's declared mandatory fields, and the save path logs drift instead of letting an unpopulated mandatory field reach the vault silently.
- Added real cron telemetry: `cron-wrap` records each wrapped cron run into a `cron_runs` table (migration 077), read by the Knowledge Pipeline view. `feed-ingest`, `feed-digest`, and the new nightly `vault-maintenance` (04:30) cron run through it. Telemetry is best-effort — a wrapped cron's own exit code is what's returned, so a telemetry write failure never breaks the underlying job.
- Added the `vault_contract` and `dev_backend_plist` reconcile checks (both notify-only, never auto-mutate).
- Fixed the content engine's dedup ledger (migration 078): it now writes to `~/.aos/data/content-engine/` instead of the read-only framework tree, so dedup works on release installs and the CLI stops exiting non-zero.
- Ships the RSSHub LaunchAgent template un-deployed — feed sources routed through RSSHub need Docker, which is an operator opt-in; nothing auto-installs it.

## v0.6.7 — 2026-07-14

Summary: Qareen UI rehaul Phase A — foundation and honesty. Fixes the crashed Sessions route, wires the People page live into the nav, kills two stuck-loading bugs, fixes the wave-3 UI type errors, and adds a TypeScript gate to ship-check so UI type errors can't ship again.

- Fixed the `/sessions` route crashing to a black screen (`TypeError: null.toLowerCase()` in `getSessionIcon`) — the session icon lookup now tolerates a null title, and `SessionRecord.title` is typed as nullable to match the API.
- Added a route-level error boundary (`components/layout/RouteErrorBoundary.tsx`) around every route so a single render exception shows a warm retry fallback instead of taking the whole app to black.
- Wired the People CRM page live: added the `/people` route and a People entry in the Focus nav group (shipped in wave 4 but previously unreachable from the nav).
- Fixed the System page's top verdict banner pulsing a skeleton forever when its fetch never resolved — a new `hooks/useLoadingTimeout.ts` bounds the skeleton, gating on "no data" rather than the loading flag (which a paused/hung query never clears); the optional banner degrades to hidden after the timeout, and the fetch gets its own `AbortSignal` timeout.
- Fixed the blank-on-first-load of lazy routes (most visibly Chat): the router's `Suspense` boundary now renders a loading state instead of nothing while a route chunk downloads.
- Fixed the three `TS2322` `StatusDotColor` type errors in `Automations.tsx` (pre-existing from wave 3, aos#159) with a typed fix — no `as any`.
- Added a UI type-safety gate to `ship-check`: when the diff touches `core/qareen/screen/`, it runs `tsc -b` and fails on any TypeScript error — the gap that let aos#159 ship.
- Amended `DESIGN.md` with a Loading & Empty States section codifying the skeleton pattern (hard timeout, gate on "no data", resolve to retry for primary surfaces or hidden for optional chrome) and the empty-state copy pattern.

## v0.6.6 — 2026-07-14

Summary: Sentinel — the autonomous commitment agent. When you tell someone "consider it done" or "@aos" in iMessage, Sentinel wakes, researches the favor headlessly, drafts a reply in your voice, gates it through confidence checks, and either surfaces it for approval or (at high confidence) sends it — the backend behind the Sentinel queue UI that already shipped.

- Added the Sentinel backend (`core/engine/comms/sentinel/`): a kqueue watcher on the iMessage chat.db that fires the instant an outbound message contains a trigger phrase, a spawner that runs a headless Sentinel session (`claude --agent Sentinel`), a confidence gate, a voice-matching context builder, and a dispatcher that sends approved drafts via message-person.
- Added word-boundary trigger detection (`core/engine/comms/triggers/detector.py`) for "consider it done" / "@aos".
- Added the Sentinel API (`core/qareen/api/sentinel.py`) — SSE stream, queue, and history endpoints behind the Sentinel screen that shipped earlier — and the `sentinel` CLI (status/pause/resume/tail).
- Added migration 070 (Sentinel infrastructure — agent_triggers table, work/log dirs, sentinel.yaml). Structural fix: an earlier draft dropped an inert `.schema_pending` marker and returned success when comms.db didn't exist yet, permanently stranding agent_triggers once the runner's monotonic watermark advanced; it now creates comms.db and the table directly (the table is self-contained, and comms-bus adds its own tables with IF NOT EXISTS).
- Added the Sentinel LaunchAgent as a proper framework template (`config/launchagents/com.aos.sentinel.plist.template`, `__HOME__` placeholder + runtime `~/aos` path) and migration 071 to materialize/reload it on machines past migration 012 — replacing the old hand-rolled plist with hardcoded operator paths.
- Added a reconcile check (`SentinelPlistDriftCheck`) that compares the deployed Sentinel plist against its template every update cycle and re-renders on drift — closing the gap the LaunchAgent Python-path check left (it never compared template vs deployed).
- Scope note: this wave ships Sentinel and its trigger detection only. Migration slot 069 is intentionally unallocated — council-substrate's `044_style_intelligence` was superseded by the People wave's `schema.sql`, which already ships `style_profiles`/`style_modes` verbatim — and the broader comms-pipeline enrichments (media enrichment, email/slack channel adapters) are deferred and not wired in here.

## v0.6.5 — 2026-07-14

Summary: People Intelligence engine — nine source adapters, a rule classifier that tiers everyone in your circle (core/active/emerging/fading/dormant), a People CRM UI, and a nightly refresh cron, all self-contained and independent of the comms pipeline that ships in a later wave.

- Added the People Intelligence signal-extraction layer (`core/engine/people/intel/`): nine source adapters (iMessage, calls, WhatsApp, Apple Photos, Apple Contacts, Apple Mail, Telegram, vault, work) plus Signal Desktop, a universal import path, and LinkedIn/Meta export converters — all read local data directly with stdlib only, no dependency on the comms connector pipeline.
- Added the rule classifier, profile compiler, and taxonomy that turn extracted signals into a tier per person, plus an operator-feedback loop that retrains future classification runs.
- Added the ontology layer (`core/engine/people/{graph,group_resolve,hygiene,identity,normalize,org}.py`): relationship-graph inference, WhatsApp group→circle resolution, canonical-name hygiene splitting/dedup, and cross-source identity enrichment.
- Added operator self-identity linking (`people.is_self`) and a universal profile compiler.
- Added the People CRM UI and API (`core/qareen/api/people.py`, `core/qareen/screen/src/pages/People.tsx`) — directory, detail panel, messaging, classification correction, circle/graph/org-chart/hygiene views.
- Added "Today's Relevant People" and daily birthday/drift/reconnect nudges to Chief's session-start context.
- Added a nightly `people-intel-refresh` cron (02:00) — extract, classify, generate nudges.
- Added migrations 060-068 for the ontology, signal-store, classification, and nudge-queue schema. Structural fix: these migrations now lazily create `people.db` via the framework's own `db.connect()` instead of skipping when it doesn't exist yet — on a fresh machine, skipping meant the runner's monotonic version watermark advanced past them permanently, stranding their tables forever the first time they ran before any comms activity had created the DB.
- Scope note: this wave's classifier and profiler are usable standalone via CLI/API but are not yet wired into `core/engine/comms/*`'s extraction pipeline — that integration is comms-pipeline territory and ships in a later wave, consistent with this wave's adapters having no import dependency on `core/engine/comms/connectors/*` in either direction.

## v0.6.4 — 2026-07-14

Summary: Hotfix for two bugs found during wave 3's live edge deployment that had to land before waves 1-3 promote to friend machines.

- Fixed the updater silently skipping pending migrations when VERSION was unchanged between releases (`core/bin/crons/check-update`). Wave 3 shipped 4 migrations without a VERSION bump; the "no version change — reconcile only" fast path skipped them all until run manually. The migration trigger is now `runner.py pending-count` (a new machine-readable subcommand), independent of the version delta — reconcile-only remains the fast path only when both VERSION is unchanged AND pending count is zero.
- Fixed `056_n8n_service.py`'s `up()` reporting failure when `launchctl kickstart -k` blocked past its 10s subprocess timeout while the previous n8n instance drained — observed live: the timeout fired, but n8n was healthy seconds later. The kickstart call is now non-fatal on timeout, and the health poll (the real success criterion) extends to 60s.
- Fixed the identical kickstart-timeout pattern in `054_qareen_service.py`, found via a sweep of migrations 050-059 for the same drain-blocking shape.

## v0.6.3 — 2026-07-13

Summary: Qareen becomes the primary AOS service on port 4096, retiring the old HTML-template dashboard — full task/work platform, companion mode, sessions, agents config, org chart, and system health, backed by a FastAPI + React app instead of static templates.

- Added the Qareen service (FastAPI + Vite/React) as the AOS web platform on port 4096, replacing the legacy dashboard service.
- Added the Qareen Tasks 100x data model (statuses, threaded comments, entity history, saved views, task participants, attachments) on top of qareen.db.
- Added `/api/ingest` endpoints (activity, conversations, session hooks) so external processes (bridge, work engine, hooks) keep working after the dashboard retires.
- Added migrations 053 (Qareen Tasks data model), 054 (deploy Qareen service), 055 (retire dashboard, create ingest tables) — renumbered from the unshipped council-substrate branch's 023/026/030 (see 050_work_db_ownership.py for the renumbering rationale).
- Removed the legacy dashboard service (`core/services/dashboard/`) and its LaunchAgent template.
- Changed service maps in watchdog, the `aos` CLI, and the scheduler from `dashboard`/`com.aos.dashboard` to `qareen`/`com.aos.qareen`.
- Fixed the migration runner silently recording a migration as applied when `up()` returned an error string instead of raising or returning `False` — the version watermark now only advances on `True`/`None`, and any other return value (or a raised exception) fails the migration and stops the batch.
- Scope note: this wave ships the Qareen platform shell and service only — automations/n8n, people intelligence, the comms pipeline, the knowledge/vault pipeline, sentinel, and remote access (Cloudflare tunnel) ship in later waves and are not wired into this build.

## v0.6.2 — 2026-07-13

Summary: Two-lane release channels — the operator rides `edge` (origin/main, same-day) while friend machines ride `stable` (promoted releases only), so day-to-day churn no longer lands on other people's machines at 4am.

- Added release channels — a machine's `~/.aos/config/channel` (edge|stable) decides which git ref the update path tracks. Absent or unrecognised resolves to `stable`, so every existing machine lands on the safe lane with zero action.
- Added the `stable` git tag as the stable pointer — stable-channel machines update to the tag's commit instead of origin/main. Until the first promotion the tag is absent, and stable gracefully falls back to main so nothing strands.
- Added `aos promote` — verifies the running release has soaked ≥ N days (default 2, `--force` to override), runs ship-check + self-test, prints an explicit plan, then moves and pushes the `stable` tag (a tag-only push, never main) and posts a Telegram note.
- Added `aos channel [edge|stable]` — show or set this machine's channel.
- Changed release-manager `create [ref]` and `activate <ver> [hash]` to build from an arbitrary ref and record an explicit deployed hash, so stable machines deploy the tag commit rather than main HEAD.
- Changed fresh installs to write `channel = stable` explicitly.
- Added migration 023 — normalizes an invalid channel file to `stable` (never overwrites a valid edge/stable choice).
- Added core/lib/channels.py (pure resolution + promotion-guard logic) with 29 unit tests.

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
