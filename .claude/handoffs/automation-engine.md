# Automation Engine — Handoff

**Date**: 2026-03-21
**Status**: Phase 1 complete — scheduler live, Tier 1 jobs running

## What Was Built

- `core/bin/scheduler` — Python scheduler engine, runs every 5 min via LaunchAgent
- `config/crons.yaml` — 16 job definitions (7 enabled, 9 disabled pending scripts)
- `config/launchagents/com.aos.scheduler.plist` — StartCalendarInterval, sleep/wake safe
- `config/state.yaml` — Service registry read by watchdog
- `core/bin/watchdog` — Monitors 7 services + internet + Tailscale + disk
- `core/bin/auto-commit` — Commits aosv2, vault, nuchay, chief-ios-app
- `core/bin/rotate-logs` — Rotates ~/.aos-v2/logs/ at 10MB threshold

## What Was Fixed

- `core/bin/compile-daily` — HEALTH_DIR pointed to v1 path
- `core/bin/qmd-reindex` — Added set -e and qmd existence check
- `core/bin/session-export` — Auto-detects project names, prunes tracker at 500KB
- `core/bin/compile-patterns` — Output moved to ~/.aos-v2/patterns/, proper YAML parsing
- `core/bin/session-analysis` — Filename includes period suffix (-daily/-weekly)

## What Remains

### Port from v1 (Tier 2)
- `inbox-collect` — needs v2 path rewrite, stage to ~/.aos-v2/inbox/
- `weekly-digest` — needs v2 path rewrite
- `friction-rules` — depends on bridge approval workflow

### Build new
- `stale-detector` — scan goals/tasks for staleness, feed into morning briefing
- `nightly-pipeline` — chain: session-analysis → friction-rules → stale-detector
- `morning-context` — weather + prayer times via free APIs

### Infrastructure
- Healthchecks.io setup — dead man's switch for watchdog, auto-commit, session-export
- File watchers — WatchPaths LaunchAgents for health data, vault inbox
- Dashboard "Crons" panel — read status.json, render table
- Chief iOS app endpoint — /crons/status via Listen

## Key Files
- Spec: `specs/automation-engine.md`
- Config: `config/crons.yaml`
- Status: `~/.aos-v2/logs/crons/status.json`
- Logs: `~/.aos-v2/logs/crons/{job-name}.log`
- Scheduler log: `~/.aos-v2/logs/crons/scheduler.log`

## v1 Crontab Remaining
5 entries still in v1 crontab (not yet ported):
- inbox-collect, channel-update, phoenix weekly, friction-rules, weekly-digest
