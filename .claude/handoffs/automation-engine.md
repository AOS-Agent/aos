# Automation Engine — Handoff

**Date**: 2026-03-21
**Status**: Phase 1+2 complete — scheduler live, all Tier 1+2 jobs built, dashboard panel live

## What Was Built

### Phase 1 (previous session)
- `core/bin/scheduler` — Python scheduler engine, runs every 5 min via LaunchAgent
- `config/crons.yaml` — 19 job definitions (10 enabled, 9 disabled)
- `config/launchagents/com.aos.scheduler.plist` — StartCalendarInterval, sleep/wake safe
- `config/state.yaml` — Service registry read by watchdog
- `core/bin/watchdog` — Monitors 7 services + internet + Tailscale + disk
- `core/bin/auto-commit` — Commits aosv2, vault, nuchay, chief-ios-app
- `core/bin/rotate-logs` — Rotates ~/.aos-v2/logs/ at 10MB threshold

### Phase 2 (this session)
- `core/bin/inbox-collect` — Ported from v1. Calendar + voice memos + notes → daily note
- `core/bin/weekly-digest` — Weekly summary from work + sessions + vault
- `core/bin/stale-detector` — Flags stale tasks (>7d active, >14d todo, overdue, orphan goals)
- `core/bin/morning-context` — Weather (wttr.in) + prayer times (Aladhan API)
- `core/bin/nightly-pipeline` — Chains: session-analysis → stale-detector → compile-daily
- `core/bin/setup-healthchecks` — Provisions Healthchecks.io checks via API
- Scheduler updated with Healthchecks.io ping support (per-job + self-ping)
- Dashboard: `/api/crons` endpoint + Crons panel on main page + dedicated `/crons` page
- v1 crontab fully decommissioned (backed up to /tmp/crontab-v1-backup.txt)
- 3 remaining v1 jobs added to crons.yaml as disabled placeholders

## What Was Fixed

- `core/bin/compile-daily` — HEALTH_DIR pointed to v1 path
- `core/bin/qmd-reindex` — Added set -e and qmd existence check
- `core/bin/session-export` — Auto-detects project names, prunes tracker at 500KB
- `core/bin/compile-patterns` — Output moved to ~/.aos-v2/patterns/, proper YAML parsing
- `core/bin/session-analysis` — Filename includes period suffix (-daily/-weekly)

## Current Job Status

### Enabled (10)
| Job | Schedule | Notes |
|-----|----------|-------|
| watchdog | every 5m | |
| auto-commit | every 30m | |
| qmd-reindex | every 30m | |
| session-export | every 2h | |
| rotate-logs | daily 3AM | |
| compile-daily | daily 11:30PM | |
| inbox-collect | every 30m | Ported from v1 |
| session-analysis | Sun 10PM | |
| stale-detector | daily 8AM | New |
| morning-context | daily 6:30AM | New |
| nightly-pipeline | daily 11PM | New |

### Disabled (9)
| Job | Reason |
|-----|--------|
| weekly-digest | Needs a week of data |
| compile-patterns | Needs execution logging |
| meeting-prep | Needs calendar integration |
| monthly-review | Needs 4+ weekly digests |
| email-digest | Needs email access |
| channel-update | v1 script, needs v2 rewrite |
| phoenix-weekly | v1 script, needs v2 rewrite |
| friction-rules | Needs bridge approval workflow |

## What Remains

### To activate
- **Healthchecks.io** — User needs to: sign up, get API key, `agent-secret set HEALTHCHECKS_API_KEY`, run `setup-healthchecks`
- **weekly-digest** — Enable after 1 week of data accumulation

### To build (future)
- `channel-update` — rewrite for v2 (Telegram status posts)
- `phoenix-weekly` — rewrite for v2 (observability report)
- `friction-rules` — needs bridge approval workflow first
- `email-digest` — needs email access configured
- `meeting-prep` — needs calendar integration
- `monthly-review` — needs 4+ weekly digests
- File watchers — WatchPaths LaunchAgents for health data, vault inbox

## Key Files
- Spec: `specs/automation-engine.md`
- Config: `config/crons.yaml`
- Status: `~/.aos-v2/logs/crons/status.json`
- Healthchecks: `~/.aos-v2/config/healthchecks.yaml`
- Logs: `~/.aos-v2/logs/crons/{job-name}.log`
- Dashboard: http://127.0.0.1:4096/crons
