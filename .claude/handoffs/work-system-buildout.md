# Work System Buildout — Working Document

**Date**: 2026-03-21
**Status**: In progress
**Spec**: ~/aos/specs/work-system-architecture.md (1,100 lines, the source of truth)

## Goal

Close the loop: **Goals → Tasks → Execute → Sessions logged → Knowledge captured → Reviews → Goals updated**

The work engine (CRUD, CLI, schema, query) exists. What's missing is the **wiring** — making it flow through daily usage without manual effort.

## What's Built (~45%)

| Component | Location | Status |
|-----------|----------|--------|
| Data model (tasks, projects, goals, threads, inbox) | core/work/schema.yaml | Done |
| Engine (CRUD + session linking + threads) | core/work/engine.py | Done |
| CLI (add, done, list, search, inbox, link, thread, promote) | core/work/cli.py | Done |
| Query/filter/search | core/work/query.py | Done |
| /work skill | .claude/skills/work/ | Done |
| /review skill | .claude/skills/review/ | Done |
| SessionStart context injection (shows threads + project tasks) | core/work/inject_context.py | Wired |
| SessionEnd hook (links sessions to tasks + threads) | core/work/session_close.py | Wired |
| Session→Task linking (multi-session, dedup) | core/work/engine.py | Done |
| Thread continuity (auto-create per cwd, accumulate sessions) | core/work/engine.py | Done |
| Thread promotion → project | core/work/engine.py + cli.py | Done |
| stale-detector cron | core/bin/stale-detector | Done |

## Build Plan

### Part 1: Session→Task Linking (M)
Wire sessions to actually update work.yaml. When a session works on a task, it gets recorded. When work completes, the task status updates.

### Part 2: work-awareness Skill (M)
The always-on skill that detects when a session involves trackable work and auto-creates/updates tasks and threads. Layer 1 of the three-layer cascade.

### Part 3: Stop Hook / Reconcile (M)
The reactive hook that fires after Claude responds. Detects implicit task completion from file changes. Layer 2 of the cascade.
Depends on: Part 1

### Part 4: Flow Metrics (S)
Compute cycle time, throughput, WIP from work.yaml data. Write to ~/.aos-v2/work/metrics/

### Part 5: Drift Detection (S)
Compare goal weights vs actual task completion distribution. Flag when work doesn't match stated priorities.
Depends on: Part 4

### Part 6: CLI Extensions (S)
Add missing commands: today, next, drift, metrics (thread + promote done in Part 1)

### Part 7: Review Automation (M)
Make /review actually save to vault. Wire daily review into nightly-pipeline. Ensure vault paths exist.
Depends on: Parts 4, 5

### Part 8: Bridge Work Commands (M)
/tasks, /add, /done, /inbox from Telegram. Needs bridge integration.

### Part 9: Dashboard Work Panel (M)
Replace _load_tasks() with real work data. Add goals, projects, metrics views.
Depends on: Part 4

## Progress

(Updated as parts complete)

- [x] Part 1: Session→Task Linking
- [ ] Part 2: work-awareness Skill
- [ ] Part 3: Stop Hook / Reconcile
- [ ] Part 4: Flow Metrics
- [ ] Part 5: Drift Detection
- [ ] Part 6: CLI Extensions
- [ ] Part 7: Review Automation
- [ ] Part 8: Bridge Work Commands
- [ ] Part 9: Dashboard Work Panel
