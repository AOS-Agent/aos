# Work System Buildout — Working Document

**Date**: 2026-03-21
**Status**: COMPLETE (9/9 parts done)
**Spec**: ~/aos/specs/work-system-architecture.md (1,100 lines, the source of truth)

## Goal

Close the loop: **Goals → Tasks → Execute → Sessions logged → Knowledge captured → Reviews → Goals updated**

## Design Principles (earned, not theoretical)

1. **Build for how Claude Code actually works, not how we wish it worked.** Skills load on trigger match, not "always-on." Hooks and rules ARE always-on. Use the right mechanism for the right job.

2. **The CLI is the agent API, not a human interface.** Humans use natural language, Telegram, and Dashboard. CLI is plumbing for agents, hooks, and crons.

3. **Threads are the continuity layer.** Work in the same directory across sessions automatically accumulates under one thread. When it crystallizes, promote to project. This solves multi-session work tracking.

4. **inject_context.py IS the awareness layer.** It runs every SessionStart and PostCompact. Behavioral guidance goes there, not in a separate "always-on" skill that won't reliably trigger.

5. **Don't force plugin patterns onto Claude Code.** The spec was written pre-build. Where the spec assumes a plugin SDK that doesn't exist, adapt to what Claude Code actually provides: hooks, rules, skills, agents.

6. **Vault and work system stay separate, generators bridge them.** Vault = knowledge layer (human-readable, Obsidian, compounding). Work = operations layer (machine-readable, agent-accessible). They don't merge — the generators (compile-daily, weekly-digest, reviews) cross-reference both at generation time.

## What's Built (100%)

| Component | Location | Status |
|-----------|----------|--------|
| Data model (tasks, projects, goals, threads, inbox) | core/work/schema.yaml | Done |
| Engine (CRUD + session linking + threads) | core/work/engine.py | Done |
| CLI (20 commands) | core/work/cli.py | Done |
| Query/filter/search | core/work/query.py | Done |
| Flow metrics (throughput, cycle time, lead time, WIP, goal health) | core/work/metrics.py | Done |
| Drift detection (goal weights vs actual work distribution) | core/work/metrics.py | Done |
| /work skill (with proactive triggers) | .claude/skills/work/ | Done |
| /review skill | .claude/skills/review/ | Done |
| work-awareness rule (always-on via glob) | .claude/rules/work-awareness.md | Done |
| SessionStart context injection | core/work/inject_context.py | Done |
| SessionEnd hook (session→task/thread linking) | core/work/session_close.py | Done |
| Session→Task linking (multi-session, dedup) | core/work/engine.py | Done |
| Thread continuity (auto-create per cwd) | core/work/engine.py | Done |
| Thread promotion → project | core/work/engine.py + cli.py | Done |
| compile-daily pulls completed tasks | core/bin/compile-daily | Done |
| weekly-digest includes flow metrics + drift | core/bin/weekly-digest | Done |
| Weekly metrics snapshots | ~/.aos-v2/work/metrics/ | Done |
| Vault paths created | ~/vault/ | Done |
| stale-detector cron | core/bin/stale-detector | Done |
| **Work API (16 endpoints)** | ~/chief-ios-app/server_endpoints.py | Done |
| **Bridge work commands (4 intents)** | ~/aos/apps/bridge/intent_classifier.py | Done |
| **Dashboard work panel** | ~/aos/apps/dashboard/main.py + template | Done |
| **Chief iOS app v2 models** | ~/chief-ios-app/Chief/{Models,APIService,TasksView}.swift | Done |

## Build Plan Progress

- [x] Part 1: Session→Task Linking (engine + session_close.py + inject_context.py)
- [x] Part 2: work-awareness (rule + enhanced inject_context + updated /work skill)
- [x] Part 3: Stop Hook — SKIPPED (Layer 1 guidance prevents, not detects)
- [x] Part 4: Flow Metrics (metrics.py + cli metrics + weekly snapshots)
- [x] Part 5: Drift Detection (metrics.py compute_drift + cli drift)
- [x] Part 6: CLI Extensions (today, next, drift, metrics, thread, promote, link)
- [x] Part 7: Review Automation (weekly-digest + compile-daily + vault paths)
- [x] Part 8: Bridge + API Endpoints (v2 work API + bridge intent handlers)
- [x] Part 9: Dashboard + Chief App (work panel + iOS model/API/view updates)

## Three Surfaces — One Backend

All read/write to `~/.aos-v2/work/work.yaml` via the v2 engine:

| Surface | How it connects |
|---------|-----------------|
| **Telegram (Bridge)** | intent_classifier.py → `cli.py` subprocess |
| **Dashboard (:4096)** | `_load_tasks()` → `engine.py` via importlib |
| **Chief iOS App** | `/chief/work/*` API endpoints → `engine.py` via importlib |
| **CLI / Agents** | `python3 cli.py <command>` directly |

## API Endpoints Added (on Listen :7600)

```
GET  /chief/work/all              — Full state (tasks, projects, goals, threads, inbox)
GET  /chief/work/tasks            — List tasks (?status=, ?project=, ?sort=)
GET  /chief/work/tasks/{id}       — Single task
POST /chief/work/tasks            — Create task
PATCH /chief/work/tasks/{id}      — Update task fields
POST /chief/work/tasks/{id}/done  — Complete task
POST /chief/work/tasks/{id}/start — Start task
GET  /chief/work/goals            — List goals
GET  /chief/work/projects         — Projects with task counts
GET  /chief/work/threads          — List threads
GET  /chief/work/inbox            — Inbox items
POST /chief/work/inbox            — Add to inbox
GET  /chief/work/summary          — Quick stats
GET  /chief/work/today            — Today's work plan
GET  /chief/work/metrics          — Flow metrics + goal health
GET  /chief/work/drift            — Drift analysis
```

## Bridge Intents Added

| Pattern | Handler | What |
|---------|---------|------|
| "show my tasks", "what are my tasks" | handle_list_tasks | Lists tasks grouped by status |
| "add task X", "new task X" | handle_add_task | Creates task via CLI |
| "done tN", "mark tN done" | handle_done_task | Completes task via CLI |
| "inbox X", "capture X" | handle_inbox | Captures to inbox via CLI |

## Known Gaps / Future Work

- **v1 vault tasks orphaned** — `~/vault/tasks/*.md` still exist but nothing reads from them. Can migrate or archive.
- **SSE push from work engine** — Dashboard/app could auto-update when tasks change (currently requires refresh)
- **Deep links from Telegram** — Task responses could link to Chief app
- **iPhone widget** — Today's work plan on home screen
- **Typed Swift models for goals/projects** — Currently using `[String: Any]` for fetchGoals/fetchProjects

## Key Files Modified (Parts 8-9)

```
~/chief-ios-app/server_endpoints.py       — +16 v2 work API endpoints
~/aos/apps/bridge/intent_classifier.py     — +4 intent handlers (list, add, done, inbox)
~/aos/apps/dashboard/main.py               — _load_tasks() wired to v2, /api/work endpoint
~/aos/apps/dashboard/templates/dashboard.html — Work panel replaces clickup_tasks
~/chief-ios-app/Chief/Models.swift         — ChiefTask +project,tags,source,energy,sessions
~/chief-ios-app/Chief/APIService.swift     — All task endpoints → /chief/work/*, +5 methods
~/chief-ios-app/Chief/TasksView.swift      — Active/Todo/Waiting, project chips, project create
```
