# AOS Changelog

All notable changes to AOS. Release notes sent via Telegram after each 4am update.

## v0.6.26 — 2026-07-22

Summary: Kanban Phase 4 — the generic runner. "Delegate to agent" stops being a label and becomes a running worker. When a task is delegated to an agent, an opt-in service picks it up off the board, assembles a lean brief, spawns a headless `claude` in its own process group, and lets the agent narrate its work into the task's activity log. It NEVER auto-completes: a finished worker parks the task in review for a human to approve, a failed or silent worker parks it for attention — nothing is ever silently dropped. This is the *domain-agnostic* runner (Sentinel-shaped spawn, poll-the-board authority); islah's iOS dispatch strategy rides on top as a later code-task-class plugin and its guts never leak in here.

- **The runner engine** (`core/engine/work/runner.py`). Polls `work.db` for tasks held by an agent (`held_by = 'agent:<name>'`) — the board is the queue (spec §3.4), so a killed runner restarts and resumes with no reconciliation; the `task.delegated` event is an optional accelerant (`handle_delegation`), never the source of truth. Bounded concurrency (default 2 — this is a Claude *subscription*, not an API key: no fan-out storms), a per-task timeout, and a `task_runs` ledger keyed `UNIQUE(task_id, delegation_ts)` that makes one delegation spawn exactly one worker even on a replayed event or a re-poll. All SQLite writes happen on one thread; worker threads only run the subprocess and hand the result back through a queue.
- **Trust enforced at the spawn point** (spec §3.8). A delegation only auto-spawns if the delegate agent's capability trust in `trust.yaml` meets the configured floor (default 1); below it the task is parked "awaiting operator go", not spawned. `always_escalate` capabilities (financial, delete-prod-data, …) are a hard floor regardless of level. This is the first place any work operation consults the agent trust tree.
- **Process-group discipline** (`core/engine/work/proc_group.py`, generalised from the comms enrich engine). Every worker is spawned in its own session (`start_new_session=True`) and tracked; a SIGTERM/SIGINT to the runner tears down every live worker's whole group with `killpg`, so a killed or redeployed runner never orphans a quota-burning `claude` (+ its node grandchild). Verified with a real child process in the test suite — no orphan survives shutdown.
- **Lifecycle narration, never a silent drop.** The runner writes the task's activity log on pickup ("Runner picked up — worker starting"), on success ("Agent X finished — ready for review" → `in_review`), on failure ("Agent X did not complete: …" → `waiting`), and on a trust-park. A clean exit that produced no activity is treated as a failure. The task's coarse status is moved through the sanctioned `update_task` path — the runner never marks a task `done`.
- **Declarative isolation seam.** A task's isolation ('worktree' | 'none') is resolved declaratively — the bug pipeline defaults to a worktree, everything else runs in place, an explicit `fields.isolation` overrides. The generic git-worktree primitive ships here; the iOS build/DerivedData layer stays out for the Phase-5 code-task plugin.
- **Ships OFF, operator opts in.** New optional service `work-runner` (`core/services/work_runner/`, manifest + LaunchAgent template, migration `092`). `runner.enabled` defaults false and the plist is not deployed on install — autonomous agent spawning is a deliberate opt-in. `work runner enable` renders + loads the service and flips the switch; `work runner disable` is the global kill switch (pauses spawning, leaves the service loaded; in-flight workers finish and park). `work runner status` / `work runner cancel <task>` are the controls. Because the manifest is `status: optional`, ServiceLoadedCheck treats an un-opted-in runner as fine.

## v0.6.25 — 2026-07-21

Summary: Kanban Phase 3 — the islah data plane. The operator's real bug ledger (`~/.aos/islah/bugs.yaml`, 19 bugs across quran-garden + deenoverdunya) is imported into `work.db` as first-class bug-pipeline tasks, and islah's App Store Connect intake is transplanted into the framework so TestFlight feedback and beta crashes file themselves onto the board automatically. Both terminate in the work system: an external signal becomes a `pipeline='bug'` task with a faithful `task_activity` narrative reconstructed at the *original* timestamps — nothing flattened. A one-way mirror keeps the board consistent with the ledger during the pre-cutover window; `work.db` never writes back.

- **Work-system intake package** (`core/engine/work/intake/`). New home for external-signal → work-task adapters, distinct from `core/engine/comms/` (the person-to-person message plane): these produce work items, share the work layer's app registry and bug pipeline, and never touch a person or a message. `bug_tasks.file_bug` is the shared primitive — create the task (`narrate=False`) and reconstruct its activity narrative with original timestamps, idempotently keyed on `source_ref` (`islah:<id>`, `testflight:<id>`, `asc-crash:<id>`).
- **One-shot `bugs.yaml` import** (`islah_import.py` + migration `091_islah_bugs_import`). Each bug becomes a `pipeline='bug'` task; its 13-state islah status maps 1:1 onto the bug pipeline stage; all richness (root_cause, code_refs, fix_approach, severity, build, screen, repo, branch, commits) lands in `fields` JSON; the lifecycle (reported → triaged → attempts[] → proof[] → status) is reconstructed as typed activities carrying the ledger's original timestamps. Idempotent by activity marker — the migration, a re-run, and the mirror share one path and never duplicate. The qg#1-shaped round-trip (import → read → every field + attempt narrative intact) is the acceptance fixture (FAKE ledger, never operator data).
- **App Store Connect intake transplanted** (`ascbuild.py`, from islah — the strongest keeper organ). TestFlight beta feedback (screenshots + tester comments) and beta crash submissions, symbolicated via `xcsym`, now file `pipeline='bug'` tasks (feedback → classification `ux`; crash → severity 1, classification `crash`) with the payload as an activity beat — instead of `bugs.yaml` appends. Config-driven on entry (discharges islah's aos#164 debt): app registry from `apps_registry` (extended with `platform` + `asc_app_id`), ASC credentials from Keychain via `agent-secret`. Hourly via the new `ascbuild-sync` cron (graceful skip where no ASC creds are configured); replaces islah's `am.hish.islah.crashsync`.
- **One-way mirror** (`islah-mirror` cron, every 30m). New rows appended to `bugs.yaml` by the operator's islah CLI mirror into `work.db` on the same idempotent path; `work.db` never writes back. A reversible seam (dossier risk-1) that keeps both worlds consistent until the Phase-7 cutover retires the ledger, at which point the cron finds nothing and is removed.
- **Core**: `WorkAdapter.create` / `backend.add_task` gain a `narrate` flag so intake importers can suppress the auto "created" beat and write one at the original report timestamp; `apps_registry.AppEntry` gains `platform` and `asc_app_id` for ASC matching. Framework `config/apps.yaml` template documents the new fields (example entries only).

## v0.6.24 — 2026-07-21

Summary: Kanban Phase 2 — the activity log, the keystone. Every task now carries an append-only, immutable narrative of what happened to it — "created → triaged → delegated → attempt (with branch + commits) → proof (tests fail→pass) → in review" — written automatically on every mutation and readable as one coherent story. This is the layer that makes a delegated task legible after the fact, and it generalizes islah's `attempts[]`/`proof[]` append-only discipline (a bug's history is never rewritten) to all work.

- **The narrative layer** (`core/qareen/ontology/activity.py` + `task_activity` table, migration `089_kanban_phase2`). One row per *logical* event — `created | status_changed | delegated | held | comment | attempt | proof | blocked | unblocked | edited | linked` — with a human one-liner and a structured JSON payload. Kept deliberately separate from `entity_history`: activity is the **narrative** ("what happened, and why"), history is the **forensic** per-field log ("what value changed"). A single delegation writes ~4 history rows but exactly ONE activity line. Append-only is enforced twice: `BEFORE UPDATE`/`BEFORE DELETE` triggers that `RAISE(ABORT)`, and the adapter exposing no mutate-past-entry path in code.
- **Auto-narration from one choke point.** Every mutation flows through the `WorkAdapter` (both the CLI and the API), so narration lives there once — create, status change, priority/field edit, delegate, hold, start, complete, and the subtask cascade each write their own line without any caller doing it by hand. Status is a *derived* consequence of the log, never written around it.
- **Agents append the rich beats.** `work activity <task> --kind attempt --body "..." --data '{branch,commits,outcome}'` (CLI) and `POST /tasks/{id}/activity` (governed `append_activity` action) let a delegated agent log attempts, proof artifacts, comments, and blockers. Each append emits `task.activity` for SSE liveness. The auto-narration kinds are refused on the manual path — a caller can't forge a system narration. Migration `089` backfills existing `entity_history` into the narrative once (idempotent) so no task's story starts blank.
- **The card story (UI).** The task overlay gains an Activity timeline — newest-first, actor avatars (operator / purple agent / system-gray), per-kind icons, expandable data payloads for attempts and proof, and an inline comment composer. Board cards show a subtle "last activity" line (actor dot + latest beat + relative time); the list/database view gains an Activity count column. `useSSE` invalidates the open card's timeline on any task event, so it live-updates from CLI, API, and auto-narration alike.
- **Islah-shape acceptance gate.** A qg#1-shaped fixture (FAKE data) walks the full bug narrative and asserts the timeline reads as a coherent story with the `attempts[]`/`proof[]` richness (branch, commits, fail→pass) surviving verbatim — the round-trip gate the Phase 3 `bugs.yaml` import must clear.

## v0.6.23 — 2026-07-21

Summary: Kanban Phase 1 — typed states, agent delegation, and the bug task-class, built on the now-restored Phase 0 board. A task can be delegated to an agent as a real state transition, the board's columns are category-typed, and bugs arrive as a first-class task class carrying islah's 13-state pipeline without flattening its richness. (Phase 0 itself — the honest board that the envoy merge had silently reverted — was restored to main separately; this release builds on it and adds migration `088_kanban_phase1` on top of the restored `087`.)

- **Bug-pipeline definition** (`core/engine/work/pipelines.py`) — islah's 13 states (`new → triaging → confirmed/needs-* → fixing → verifying → awaiting-approval → approved → shipped`, plus reopened/duplicate/wont-fix) mapped onto the locked six-value category spine, each with a coarse board status. Single source of truth shared by the migration and the adapter so the DB and code cannot drift. Adds the generic `in_review` status.
- **Typed states + guard.** `statuses.pipeline` separates the generic board columns from bug-pipeline stages; `board_tasks` is now category-driven so a new column appears without editing the query. Status writes validate against the `statuses` table — an unknown status is rejected (no more free-text drift). `TaskStatus` gains `triage`/`backlog`/`in_review`.
- **Delegation as a state transition** (spec §3.1). `tasks.delegate`/`held_by` columns; `work delegate <task> --to <agent>` and `work hold <task>`; `POST /tasks/{id}/delegate` + `/hold`. Delegating sets the agent as holder and moves the task into a started stage while `assigned_to` (the accountable human) is untouched. Emits `task.delegated {task_id, holder, by, ts}` — the exact hook the future runner (Phase 4-5) subscribes to. No runner yet; this is the tagging vocabulary being born.
- **Bug task-class.** `pipeline='bug'` tasks carry richness (root_cause, code_refs, fix_approach, severity, app, build, screen, attempts, proof, branch) in a `tasks.fields` JSON column — never flattened — while a synced coarse status keeps list/board queries cheap. Apps registry (`config/apps.yaml` framework template + `~/.aos/config/apps.yaml` instance override) replaces islah's hardcoded APPS/REPOS (aos#164); the template ships example entries only.
- **UI.** Status chips render category color + fine stage label; the board gains an In Review column; delegate action on cards and in the detail panel (inline agent picker, purple agent edge + chip reusing the Phase 0 agent hue); bug cards show an app badge + severity dot + stage chip, and the detail panel shows the unflattened bug richness. `useSSE` listens to `task.delegated` so the board live-updates on delegation.

## v0.6.22 — 2026-07-21

Summary: Every AOS→Telegram *system alert* now reads like a person wrote it. The bridge got humanized copy back in v0.6.11, but the alert paths — reconcile findings, the watchdog, the heartbeat, the enrichment auth-pause, the event bus — still shipped raw: check slugs, file paths, "plist"/"migration 083"/"backfill" jargon, and dumped lists of internal names. A reconcile ping used to read "⚠️ dead_code: Dead code detected — 7 orphaned bin scripts: aos-report, eventd…"; it now reads "🧹 Found 7 old scripts nobody uses anymore. Nothing urgent — I'll list them for cleanup whenever you're ready." The translation is centralized so new checks inherit the voice for free.

- Added `core/infra/reconcile/alert_copy.py` — the single place reconcile findings become human. `humanize_finding()` maps each check name to a friendly template (emoji lead + what happened + what I did / what you should do), and `render_report()` assembles the bundled alert. A `strip_jargon()` fallback scrubs paths, filenames, version/commit/migration/task refs, bracketed codes, and snake_case slugs, so an untemplated finding never lands verbatim. The runner keeps the raw `message`/`detail` for the JSONL log and terminal output — only the phone-facing message routes through the translator.
- Rewrote every non-bridge Telegram emitter to the alert standard: the watchdog's service/network/disk pings (dropped `[AOS]`, `DEGRADED`, `migration 083`, "flapping"), the bridge heartbeat's problem list (dropped "is DOWN", "free pages", "pending task(s)"), the enrichment auth-pause ("😴 Overnight reading paused — my login expired. Run /login…"), the event-bus notifier (no more raw `— source_slug` tail), and the intelligence hook (dropped the bracketed `[0.87]` relevance code).
- Extended `core/services/bridge/MESSAGE_STYLE.md` with a "System alerts" section: alert anatomy, the never-list (slugs, paths, raw item lists, CI jargon), before/after transformations, and the rule to centralize the check-name→template map with a scrubbing fallback.
- Tests: `tests/test_alert_copy.py` (22 cases — jargon stripping, path removal, the operator's dead_code example, length bounds, report assembly) plus emitter-regression guards in `tests/test_telegram_message_style.py` (watchdog/heartbeat/enrich/bus/hook copy can't drift back). Updated `tests/test_reconcile_periodic.py` to assert the humanized ping instead of the raw check slug.

## v0.6.21 — 2026-07-21

Summary: Releases now ship their frontends. `git archive` only extracts tracked files, so gitignored vite build output (`core/qareen/screen/dist`) never landed in a release — every runtime since the release system took over :4096 served a UI-less qareen backend. The release pipeline now builds vite frontends into each release before freezing it read-only. Also ships envoy — autonomous outbound conversations.

- Added `build_frontends()` to `core/bin/internal/release-manager` — discovers every tracked `package.json` whose `build` script invokes `vite build` (no hardcoded list) and builds it into the release tree during `create` and `convert`, before `chmod a-w`. Reuses the dev repo's `node_modules` via a temporary symlink on dev machines; falls back to `npm ci` on fleet machines. `node_modules` is removed after the build — releases ship `dist/` only.
- Changed `activate` validation to warn when a vite frontend has no built `dist/index.html`, so a UI-less release can never go unnoticed again. Build failures are non-fatal (backend still runs; the UI is just absent) but loudly logged.
- Added envoy — autonomous outbound conversations (`core/engine/comms`): the agent can open and carry outbound conversations under the comms trust cascade.

## v0.6.20 — 2026-07-21

Summary: Ambient Knowledge Phase 5 — injection + knowing→doing. The enriched entity store (Phase 4) now reaches the agent while it works, and starts acting on it behind the scenes. Sessions open already aware of what the operator owes, what's owed to him, his unanswered questions, recent purchases, and the top people nudges; naming a person pulls up what the agent already knows about them; the operator's own promises flow toward the work inbox; and the 600+ nudges that had been rotting unseen finally surface, take feedback, and expire. All read paths are read-only over `comms.db` (the backfill can hold write locks), all access control is reused verbatim from the recall contract, and a login-expiry now pauses the enrichment engine cleanly instead of grinding out a wasted run.

- Ambient digest (`core/engine/comms/ambient/digest.py`). A bounded (≤15-line) markdown block distilled from `message_entities` + the nudge queue: **you owe** (operator's own commitments, ≥0.80, from OUTBOUND messages — direction is ground truth since the model's free-text `who` is null on ~2/3 of live rows), **owed to you** (inbound commitments), **unanswered** (recent inbound `question_open` with no later outbound reply to that person), **purchases** (transactions, last 7 days), and **top people nudges**. Injected at SessionStart (`inject_context.py`), into bridge sessions (`session_manager.py` + `persistent_session.py` append-system-prompt), and available via `comms-ambient digest`. Privacy is the recall rule reused: contacts with `privacy_level ≥ 2` are excluded unless the operator passes `--include-private`.
- Mention-triggered context (`core/hooks/mention_context.py`, a UserPromptSubmit hook wired by migration 085). When a prompt names a known person, it injects that person's mini-profile — last interaction, open commitments both ways, their unanswered questions, recent topics. HARD budget <100ms with NO DB or model calls: the expensive resolve+query runs nightly and is frozen to per-person JSON snapshots + a `names.json` index (`ambient/profile.py`); the hook is a dict lookup plus at most two file reads (measured ~35ms end-to-end incl. interpreter start). Restricted contacts get no snapshot, so they can never be surfaced — fail-closed by construction.
- Commitments → work inbox (`ambient/proposer.py`) — the knowing→doing seam. Operator commitments (≥0.80, outbound) are proposed to the work INBOX (not tasks — the operator triages, trust starts at proposal). Provenance travels in the item text (`src <message_id>`). Dedup and no-reproposal both ride the entity's own `ontology_type`/`ontology_id` columns: a proposed entity is stamped and never selected again, so a dismissed item is never re-proposed. Paced by a newest-first per-run cap. GATED OFF by default (`config/enrichment.yaml` `ambient.propose_commitments: false`) — dry-run until the operator opts in; `comms-ambient propose-tasks --commit` runs it manually.
- Nudge surfacing + writeback (`ambient/nudges.py`) — the roach-motel fix. Showing a nudge stamps `surfaced_at`; `comms-ambient nudges dismiss|act <id>` moves its status and records a `surface_feedback` row so the generator can learn; and `expire` (run nightly) marks any pending nudge older than 30 days `expired` so the queue can never accumulate unbounded again.
- Auth-aware engine pause (`enrich/authcheck.py`). The extraction path now recognises an expired/invalid login signature in the `claude` CLI output and returns a distinct `auth_failure`; the engine stops submitting, drains in-flight, checkpoints what's done, and exits `42` (distinct from clean-0 and crash-1) with a Telegram alert ("backfill paused — login expired, run /login"). The nightly cron treats 42 as paused, still runs pure-DB maintenance, and preserves the code. Fixes yesterday's failure where an expired session burned a whole run producing nothing, silently.
- CLI + cron: `core/bin/cli/comms-ambient` (digest / nudges / profile / propose-tasks / rebuild / nightly); the `enrich-comms` nightly cron now runs `comms-ambient nightly` after enrichment (rebuild snapshots, expire nudges, propose-if-enabled).
- Tests: `tests/engine/comms/ambient/` (26 cases) — digest direction/privacy/line-bound/surfacing, mention-hook match/stopwords/privacy/cap/latency(<100ms), proposer operator-only/dedup/no-reproposal/cap, nudge dismiss/act/expire feedback, and auth-detect signature + engine-pause (model stubbed — no quota). Fixtures only, no real message content.

## v0.6.19 — 2026-07-20

Summary: Ambient Knowledge Phase 4 — the enrichment engine. Haiku now reads the operator's own message history and distills it into typed, provenance-bearing entities (topics, commitments, transactions, events, mentions, open questions) in `comms.db`, so later phases can answer "what did I buy" or "what did I promise" without re-reading raw chat. This ships the engine, the frozen `message_entities` schema, storage safety (growth ceiling, GC, nightly backup), a nightly cron, and a resumable backfill mode. The 254K-message backfill is deliberately NOT auto-launched — it runs off-peak under operator control.

- Froze the entity schema (migration 084). `comms.db` already carried an OLD `message_entities` table (~1,900 orphan `topic` rows from a retired path, referenced by no live code); the migration preserves it by renaming to `message_entities_legacy` — never a hard-delete — and creates the frozen table from research §4: `id` (`ent_<hash>`), `entity_type` enum, `value`, `fields_json`, `confidence`, `source_ids` (JSON array — entities legitimately span messages), `person_id` (scope), `batch_key`, `extractor_version`, `model`, `created_at`, `ontology_type`/`ontology_id` (lift bridge), `status` (active/superseded/dismissed), plus FTS5 over `value` and a `message_extraction` watermark (`status` = extracted/skipped_spam). Idempotent by schema inspection (old shape lacks `fields_json`) with a precise end-state `check()`, not a table-exists guard.
- Added the engine, `core/engine/comms/enrich/`. Selects messages past the per-version watermark (newest-first — most valuable), gates spam/phish BEFORE extraction (Gmail SPAM/TRASH labels + a phrase-level phish heuristic; skipped messages are watermarked terminally so they are never re-attempted), batches dense per person-day with tiny-day merge by ISO week (the fixed ~40K-token harness tax per call means cost tracks call COUNT, so batches must be dense), and runs Haiku via the subscription path (`claude --print --model haiku`, no API key) at a HARD concurrency cap of 3. Workers run in their own process groups (`start_new_session` + `killpg`); a SIGTERM/SIGINT tears down every live group before exit, so a killed run never orphans quota-burning `claude`/node children (the sample's observed failure). Each batch checkpoints atomically — entities (stored ≥0.60) plus per-message watermark in one transaction — so a mid-run kill loses only in-flight batches, which re-select next run. Surfaceability (≥0.80) is a derived predicate, keeping the frozen schema frozen; a new `extractor_version` re-extracts and supersedes prior rows.
- Storage gates land same-commit (council law). The engine REFUSES to run (loudly) if `comms.db` plus projected entities would exceed a configurable ceiling (default 1.5 GiB) or free disk falls below 10 GiB; superseded entities past a TTL (default 30 days) are GC'd; and `comms.db` (~160 MB, previously unbacked) is snapshotted via the sqlite3 backup API to `/Volumes/AOS-X/backups/comms/` (rotating 7) before each backfill session and nightly. Config in `config/enrichment.yaml` (all keys default in code — missing file degrades gracefully).
- Wired two nightly crons: `backup-comms` (03:15) snapshots the DB, then `enrich-comms` (03:30, after email ingest) runs incrementally, bounded to 45 min — stops cleanly at the budget and resumes the next night — then GCs. Backfill is operator-launched: `engine.py --backfill --max-hours N`, resumable and dense.
- Ontology lift (Decision #7), entities-side. `lift.py` maps surfaceable transaction→TRANSACTION and commitment→REMINDER (event/question→REMINDER, mention→MENTIONS link, topic not lifted), selects candidates, and stamps `ontology_type`/`ontology_id`. The object-store WRITE is a documented Phase 5 seam: the ontology `transactions`/`reminders` tables don't exist on main yet, so with no `writer` the lift produces payloads without consuming entities — a no-loss re-run once the store lands.
- Tests: `tests/engine/comms/enrich/` (40 cases) — batching/tiny-day merge, spam gate (label + phish + legit-payment negative), storage-gate refusal + GC + backup rotation, the process-group kill test (spawn a grandchild, `killpg`, assert no orphan), engine watermark-resume/threshold-flags/spam-terminal/version-supersede/storage-block end-to-end (model stubbed via a spawn seam — no quota), lift mapping + deferred behavior, and the migration contract (legacy rename, frozen shape, idempotency, fresh-install). Fixtures only — no real message content.

## v0.6.18 — 2026-07-20

Summary: Ambient Knowledge Phase 3 — Gmail becomes the live email pipe. The desktop email intake had gone dark: Apple Mail's Envelope Index froze when Mail.app stopped running foregrounded on this headless Mac, so the last email in `comms.db` was dated 2026-04-09 and every nightly run re-read the same stale cache. This ships a Gmail API adapter (pulled through the existing `gws` CLI), backfills the ~3.5-month gap live, and repoints the nightly cron. Email now flows again — append-only, into the same `channel='email'` rows the rest of the system already reads.

- Added `core/engine/comms/channels/gmail_ingest.py`: a Gmail source for the existing email channel. Accounts are **discovered** from `~/.google_workspace_mcp/credentials/*.json` (never hardcoded); each account's `refresh_token` is exchanged for a short-lived access token — a non-interactive server call, not the OAuth consent GUI — and handed to `gws` via `GOOGLE_WORKSPACE_CLI_TOKEN`. An account whose refresh fails is reported and skipped; the adapter never launches a consent flow. Bodies are extracted (prefer `text/plain`, else HTML→text via a stdlib parser), capped at 10KB with a `truncated` flag; Gmail labels ride along in `channel_metadata` (no schema change). Direction/sender/recipient mirror the Apple adapter so profiles and recall treat old and new email identically.
- Two-layer dedup. (a) Same-source/re-run: message `id` is `gmail:<msg_id>` with `INSERT OR IGNORE`, plus a per-account `internalDate` watermark in `~/.aos/data/.gmail-ingest-state.json` so nightly runs fetch only new mail. (b) Cross-source vs the 21,939 legacy Apple-Mail rows (`em_<rowid>`, which carry no RFC `Message-ID`): match on `(normalized_subject, counterpart_email, timestamp ±1 min)`, built only over the window Apple actually covered — 44 Gmail messages in the Apr overlap were recognized as already-present Apple rows and skipped.
- Backfilled the gap live (`--since 2026-04-01`, append-only): **931 messages added** across all four configured accounts — the personal Gmail (445), the Workspace primary (336), a custom-domain mailbox (78), and a school-managed account (72) — restoring email coverage from the mid-April freeze through the run date. The `email` channel now spans late-2009 through today (22,870 rows). Spam guard active: `SPAM`/`TRASH` excluded at query time and re-checked by label at ingest.
- Rewired `people-intel-refresh`: the nightly `email-ingest` step now runs `gmail_ingest` (incremental by watermark) instead of the frozen `apple_mail_desktop`, keeping v0.6.16's honest per-step exit-code accounting.
- Tests: `tests/engine/comms/test_gmail_ingest.py` (15 cases) over fake `gws` fixtures (no real mail; reserved-domain addresses) — header/body parsing, HTML stripping, 10KB body cap, inbound/outbound direction, spam guard, watermark/query construction, Apple cross-source dedup, and end-to-end ingest (insert / idempotent rerun / spam skip / Apple-dup skip / dry-run) — run against a `comms.db` that includes the FTS triggers so the insert-count is measured as real rows, not inflated trigger writes.

## v0.6.17 — 2026-07-19

Summary: Ambient Knowledge Phase 1 — the recall tool. Agents can now answer "I think I talked to this person about this thing" and "what did we say about X" by querying the operator's own 254K-message history directly, instead of asking him to re-explain. This ships the query facade only (verbatim FTS5 + person + timeframe retrieval); enrichment, email, and digest injection are later phases. No live service was touched; the change deploys via the normal update.

- Added the recall engine, `core/engine/comms/recall.py`: one query facade over `comms.db` (FTS5) + `people.db`, combinable by keywords (`messages_fts MATCH`), person (any handle/name/alias → the 5-tier resolver → `person_id`), and timeframe. Every result row is the locked contract — `{entity, confidence, source_refs[], scope}`, no field ever omitted — so a caller always has provenance (`source_refs` to message id + channel + date) and sensitivity (`scope`). Retrieval is verbatim, so `confidence` is `1.0`; the constant is named so a later derived-summary/vector layer can hedge below it without changing the shape. Results are snippet-first and bounded (default 20, hard cap 100); a single message expands to full text by-ref. The message store is opened read-only.
- Access control lives inside the tool, not the caller (the locked privacy decision). `recall` ATTACHes `people.db` and filters in SQL on `privacy_level` (1 = full AI, 2 = limited, 3 = no AI analysis): only full-AI contacts are returned by default; anything more restricted (`>= 2`) requires the explicit, operator-only `include_private` flag. Naming a private contact returns zero results, never a leak; `get` on a restricted message returns not-found. Messages with no resolved person carry no privacy signal → scoped `"unknown"`, included by default (an absent person record is not a private flag).
- Added the CLI wrapper `core/bin/cli/comms-recall` (`search` / `person` / `get`), with `--json` for agent consumption and honest exit codes (0 = results, 1 = no results / not found, 2 = usage/runtime error).
- Wired for agents: documented the tool and *when to reach for it* (a conversation referencing a person or topic from the past) in the comms-pipeline rule and the comms engine README, mirroring how `qmd` is advertised for the vault; and added a one-line pointer to Chief's Telegram system prompt in `session_manager.py` so bridge sessions recall past context instead of asking the operator to repeat himself.
- Tests: `tests/test_recall.py` (33 cases) proves the contract shape (all four fields, always), privacy filtering (limited + private excluded by default, surfaced only with the flag, private `get` returns None, named-private returns empty), resolver integration (person scoping, unresolvable → empty, never an unscoped dump), FTS correctness (keyword match, multi-term AND, punctuation/quote sanitisation), timeframe bounds, and result bounds — all against fake in-tmp fixtures, never the live DBs.

## v0.6.16 — 2026-07-19

Summary: Repairs the nightly comms/people-intelligence crons, which had been failing silently for weeks — cron telemetry read green while three jobs did no work. The audit traced it to four independent faults, each fixed here. No live service was touched; the change deploys via the normal update.

- Recovered the two lost desktop-ingest adapters (`imessage_desktop.py`, `apple_mail_desktop.py`) and their shared FDA helper (`macos_protected.py`). They vanished from `core/qareen/channels/` when the council-substrate branch was transplanted, so `people-intel-refresh`'s first two steps (iMessage + email ingest) died every night with "No module named core.qareen.channels…". Transplanted them into the current architecture at `core/engine/comms/channels/` (alongside `whatsapp_local.py`, the surviving sibling they mirror) and `core/engine/util/`, updated the module depth and the cron's `-m` invocation paths, and scrubbed the pre-scrub-era docstring examples to RFC/NANP-reserved values (privacy-scan clean). Both adapters are strictly append-only (`INSERT OR IGNORE`, COALESCE-guarded conversation upsert) and idempotent; verified against the live DBs — a `--days 7` iMessage run ingested 8 new messages and a rerun inserted 0.
- Fixed `comms-extract`: `core/engine/comms/extract/lifecycle.py` crashed with "attempted relative import with no known parent package" the moment the cron ran it as a file (`from .pipeline import …`). Added the canonical repo-root sys.path bootstrap and switched to absolute imports (matching `compute.py`), and pointed the best-effort inline patterns/graduation calls at the real `core.engine.comms.*` modules instead of a dev-only `~/project/aos` path. The extraction now runs end-to-end (iMessage + Telegram + WhatsApp → interactions).
- Hardened `comms-patterns`: `compute.py` bootstrapped its `import db` by walking the resolved path for a parent literally named `engine` — fragile across the `~/aos → aos-releases/<version>` symlink. Replaced it with fixed-depth resolution (`parents[2]/"people"`) and added a regression test that runs `compute.py` from a scheduler-equivalent context (foreign cwd, no PYTHONPATH) so the "No module named 'db'" failure can never return silently.
- Fixed silent-failure accounting in `people-intel-refresh`: failed steps logged "FAILED (exit 0)" (the `$(date)` in the log line reset `$?` before it was read) and never propagated to the script's exit code, so a night with 2 dead steps still recorded success. `run_step` now captures the real exit code first, and the script exits nonzero with a failed-step summary if ANY step failed — after running the rest (partial success is fine, a silent lie is not). Applied the same fix to `nightly-pipeline`, the other wrapper that swallowed step failures via `|| echo …`.

## v0.6.15 — 2026-07-18

Summary: The service registry (aos#180 follow-on). Every service incident in the reliability batch traced to ONE root cause: service identity — which services exist, their ports, their health endpoints, their status — was scattered across 6+ independently-drifting places (the watchdog's hardcoded list, `state.yaml`, per-check port constants, the bridge heartbeat and intent-classifier menus, `instance_hygiene`'s allowlist), so any one of them could go stale and silently break monitoring. This ships one declarative manifest per service and rewires every consumer to derive from it. No live service was touched; the change deploys via the normal update. `listen` and `eventd` are retired purely by flipping a manifest field — the proof that the design works.

- Added a per-service manifest, `service.yaml`, declaring a service once: name, purpose, status (`active`/`optional`/`retired`), type (`resident`/`interval`/`oneshot`), liveness strategy (`http`/`poll_timestamp`/`keepalive`/`interval`/`none`), port, health endpoint, plist template, owner layer, and dependencies. Wrote manifests for all nine `core/services/` dirs plus qareen (`core/qareen/service.yaml`) and n8n (`config/services.d/n8n.yaml`, for services with a launchd presence but no code dir). The schema is documented in `core/services/README.md` and in the loader.
- Added the loader `core/infra/lib/service_registry.py`: discovers the manifests (`core/services/*/service.yaml` + qareen + `config/services.d/*.yaml`), validates strictly (an unknown key, a missing required key, a bad enum, or a cross-field violation makes the whole registry raise), and returns typed records with derived helpers (`active_residents()`, `active_health_urls()`, `by_label()`, `ports()`, `watchdog_map()`). No dependency beyond PyYAML.
- Rewired every consumer to derive from the registry instead of a local constant: `ServiceLoadedCheck` (enforce active residents loaded/healthy; interval/keepalive/poll are loaded-is-enough; a retired service still loaded is NOTIFY, never auto-removed), the `transcriber`/`n8n`/`context_freshness` reconcile checks (health URLs + ports), `instance_hygiene` (a registry-declared service — any status — is never an orphan), the bridge `heartbeat.py` and `intent_classifier.py` (the service summary and the "check services" menu — this drops the retired `listen` probe and the qareen-mislabeled-as-"Dashboard" and transcriber-on-:7601 bugs at once), and the mesh heartbeat. `service_ctl.py` no longer hardcodes health URLs; the choke-point is now pure restart mechanics.
- Retired `listen` (zero producers since ~April) and `eventd` (aos#183) by setting `status: retired` in their manifests. Monitoring derives the rest: the heartbeat and intent-classifier no longer probe them, `ServiceLoadedCheck` flags them only if still loaded, and they are excluded from the watchdog map — a retired service can never again appear as DOWN. Instance data under `~/.aos/services/listen` is intentionally left in place (removal is a separate operator decision).
- Added migration 083: regenerates `~/.aos/config/state.yaml` from the registry (supersedes 082's plist-discovery). It is intent-based — every ACTIVE service, whether or not its plist is currently deployed, so an active service that lost its plist stays in the watchdog map and is reported DOWN instead of silently dropping off (the exact `listen`-dead-3-months failure). The bridge is written with an empty health URL (poll_timestamp → loaded-only check), so the watchdog never health-probes the API it doesn't reliably serve. The watchdog's degraded-mode fallback now asks the registry directly (`service_registry.py watchdog-map`) instead of a hardcoded list.
- Guarded the future with two tests: one fails if a `core/services/<dir>` ships without a `service.yaml` (every new service must declare itself), and one fails if a consumer file hardcodes a multi-service health-URL list (the exact drift pattern this change deletes).

## v0.6.14 — 2026-07-16

Summary: Service-reliability hardening (aos#180). Services were dying silently and staying dead because six different restart paths had six different guard levels and the two monitors that should catch a downed service didn't overlap and were each individually broken. This batch fixes both the wrong-guard fan-out and the monitoring gaps. No live service was touched during the build; the fixes deploy via the normal update. (Version taken as v0.6.14 to avoid colliding with the onboarding Phase 0 release that already claimed v0.6.13 on main.)

- Corrected two silent-death one-liners: the transcriber reconcile check probed :7601 (whatsmeow), not :7602 (the transcriber's real port), so it read a healthy transcriber as unhealthy and bounced it on every deploy; and the watchdog read `~/aos/config/state.yaml` (a path that doesn't exist since the service map moved to the instance layer), silently swallowed the `FileNotFoundError`, and shrank to a hardcoded 4-service list with no transcriber and no bridge health URL. The watchdog now reads `~/.aos/config/state.yaml` and FAILS LOUD (log + one Telegram alarm) when it's missing, with a correct-port minimal fallback.
- Promoted check-update's guarded `_restart_launchagent` (bootout → settle → bootstrap → verify → retry → kickstart) into ONE shared choke-point, `core/infra/lib/service_ctl.py`, usable by both bash and python callers, and routed all six restart paths through it (check-update, watchdog, `aos repair`, and the transcriber/bridge-poll/launchagent-python/sentinel/n8n reconcile checks). No raw `bootout`/`bootstrap`/`kickstart`/`unload`/`load` survives in the live restart surface — a routing test grep-asserts it. This kills the async-teardown race that left the bridge and transcriber booted-out (plist intact, job gone).
- Added a service lifecycle audit log: every restart through the choke-point appends a JSON line to `~/.aos/logs/service-lifecycle.jsonl` (ts, service, action, actor, result), turning "it silently vanished" into a one-grep answer and giving checks cross-process restart history for anti-flap.
- Added `ServiceLoadedCheck`: for every deployed `com.aos.*.plist`, assert a launchd job is actually loaded (and healthy where a health URL is known) — the exact silent state that ate the bridge and transcriber. It respects by-design interval jobs (scheduler, slack-watch: `StartInterval`, no `KeepAlive`) detected from the plist, and is the one service check allowed to repair on the periodic run.
- Added a periodic reconcile: a 30-min tier-1 cron (`reconcile-periodic`, active hours) runs the checks between deploys. It is report-only for everything except `periodic_fix` checks (`ServiceLoadedCheck`), which may repair — a dead service no longer waits for the next release. Notify dedup keeps it quiet; periodic and deploy runs keep separate dedup sets.
- Added migration 082: rebuilds `~/.aos/config/state.yaml` from the plists actually deployed (discovered, not hardcoded), attaching a known health URL per service (bridge :4098, transcriber :7602, qareen :4096, n8n :5678, listen :7600, whatsmeow :7601) so the watchdog can see a wedged-but-loaded service. Dead/retired entries with no plist don't reappear.

## v0.6.13 — 2026-07-16

Summary: Turned the terminal installer into a calm progress ceremony — the first phase of the Onboarding 10x initiative. Same bootstrap engine and migrations; only the presentation and handoff changed. A fresh install now reads as a beautiful machine waking up rather than a wall of tool output, and it ends by handing the operator to Qareen instead of a terminal. No Qareen code is touched in this phase.

- Wrapped `install.sh`'s phases in a stage presenter (`_stage`): each phase is one calm line — a spinner with a human-named stage ("Checking your Mac", "Installing the system", "Setting up your knowledge vault and memory", "Waking the agents", "Making it yours", "Final checks") that resolves to a checkmark. The phase's own stdout/stderr (brew/pip/git spew, per-item lines, migration logs) is redirected to `~/.aos/logs/install.log`; the screen never shows raw tool output. A private terminal file descriptor (`exec 3>&1`) carries the spinner and any failure panel so they survive the per-stage log redirect. Checkpoint resume is preserved — a completed stage shows "(already done)".
- Added a graceful failure panel: on any stage failure a bordered, plain-English block says what happened (human words), what it means (nothing is half-broken), the one command to recover (`bash ~/aos/install.sh`, which resumes from the last good stage), and where the full log lives. A stack trace is never the last thing on screen. `_die` now routes through this panel (drawing on fd 3 so it's visible mid-stage), and the health scorecard now returns non-zero on critical failures so a half-built system triggers the panel instead of a silent handoff. Non-zero exit is preserved for scripting.
- Hoisted the installer's only questions (git name/email, operator name) into an identity preflight that runs before the ceremony, so a prompt never appears under a spinner. The developer-vs-operator skill split is now derived from role instead of asked. Role is detected at install time the same way migration 081 does it (`~/project/aos` present → developer, else operator).
- Reworked the handoff by role: both roles get "Your system is alive." Operators auto-open `http://localhost:4096` in the default browser (graceful on headless/SSH — the URL is printed instead) and never see the terminal again; developers get the URL plus dev notes and keep the terminal handoff (`aos start` / `cld`), with no browser auto-open. (Lands on the Qareen home for now; the `/welcome` route arrives in Phase 1.)
- Added a dry-run walk mode (`--dry-run` or `INSTALL_DRY_RUN=1`) that walks the real stage ceremony without touching the machine and needs no admin access — useful for demos and CI. Shellcheck-clean at `--severity=warning`; no Python changed.

## v0.6.12 — 2026-07-15

Summary: Repainted the Qareen UI from warm brown + orange to a charcoal-and-bone system. The operator vetoed the orange accent; the whole surface now reads as neutral-warm charcoal (never blue-black) with a single restrained warm-bone accent, pure-white headings, and semantic status colors kept legible. No behavior change — palette only.

- Rewrote the design tokens in `core/qareen/screen/src/globals.css` (the Tailwind v4 `@theme` source) and its TS mirror `core/qareen/screen/src/lib/design.ts`: the background ladder is now neutral-warm charcoal (`#0B0B0A` → `#302E2A`, `R ≥ G ≥ B` by a hair so it never reads blue-black), borders are warm-neutral white alpha, and the brand orange accent (`#D9730D`) is replaced by a warm-bone accent (`#D6CCB4`). Because the accent is now light, a new `--color-on-accent` token (charcoal) carries text on solid accent fills; the ~34 `text-white`/`text-bg`-on-`bg-accent` call sites were migrated to `text-on-accent`.
- Chose bone over the two other candidates on purpose: muted gold was rejected as too close to the vetoed orange (and the operator explicitly warned against orange-adjacent ambers), and desaturated green was rejected because it collides with the semantic status-green (`connected`/`success`). Bone is the only option that is categorically not-orange, stays warm, and pairs with pure-white headings. Rationale recorded in `DESIGN.md`.
- Hunted and migrated every hardcoded orange/amber/brown stray outside the token file: the companion orb and its prayer-period glow (`Orb.tsx`, `SessionLauncher.tsx`), the app-wide prayer ambient (`usePrayerAmbient.ts`, neutralized to charcoal), the code-syntax theme (`Markdown.tsx`), agent identity colors (`Agents.tsx`), the org human-node gradient and opus model color (`Org.tsx`), task priority-2 (orange → yellow), notification/relationship/waveform accents, and the old brown surface/checkmark hexes — all repointed to the new charcoal/bone values. No orange remains anywhere in the source.
- Rewrote the `DESIGN.md` color system, philosophy, aurora, and rules sections to describe the charcoal-and-bone language, keeping the type scale, spacing, and Loading/Empty-state sections intact.

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
