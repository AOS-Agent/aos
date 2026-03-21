# AOS v2 — Automation Engine Spec

**Date**: 2026-03-21
**Status**: Draft — approved for implementation

---

## Overview

The Automation Engine is the background brain of AOS. It handles three types of work:

1. **Scheduled Tasks** — things that run on a clock (export sessions every 2h, compile daily log at 11:30 PM)
2. **Event-Driven Triggers** — things that react when something happens (health data arrives, new file in inbox)
3. **Pipelines** — multi-step chains where each step depends on the previous (nightly analysis chain)

All three are managed by a single system: one LaunchAgent, one YAML config, one status file.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     AOS AUTOMATION ENGINE                     │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │  SCHEDULER   │  │  WATCHERS    │  │  HEARTBEAT         │  │
│  │              │  │              │  │                    │  │
│  │ Wakes every  │  │ macOS native │  │ Internal (bridge)  │  │
│  │ 5 min, runs  │  │ WatchPaths   │  │ + External (ping   │  │
│  │ what's due   │  │ file system  │  │   Healthchecks.io) │  │
│  │              │  │ triggers     │  │                    │  │
│  │ crons.yaml   │  │ watchers in  │  │ Dead man's switch  │  │
│  │              │  │ crons.yaml   │  │ for whole machine  │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬───────────┘  │
│         │                 │                    │              │
│         └────────┬────────┘                    │              │
│                  ▼                             ▼              │
│         ┌──────────────────┐        ┌──────────────────┐     │
│         │   STATUS.JSON    │        │  HEALTHCHECKS.IO │     │
│         │                  │        │                  │     │
│         │ Last run times   │        │ External monitor │     │
│         │ Exit codes       │        │ Alerts if Mac    │     │
│         │ Durations        │        │ goes offline     │     │
│         │ Dashboard reads  │        │ Free tier: 20    │     │
│         │ Watchdog reads   │        │ checks           │     │
│         └──────────────────┘        └──────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

---

## The Scheduler

### Implementation

One LaunchAgent (`com.aos.scheduler`) runs every 5 minutes via `StartCalendarInterval`.
It executes `~/aosv2/core/bin/scheduler`, which:

1. Reads `~/aosv2/config/crons.yaml` (what to run, how often)
2. Reads `~/.aos-v2/logs/crons/status.json` (when things last ran)
3. Decides what's due
4. Runs each due job with timeout + lock file
5. Updates status.json + per-job logs

### Why LaunchAgent, Not Crontab

- **Sleep/wake**: Cron jobs missed during sleep are lost forever. LaunchAgent `StartCalendarInterval` coalesces and runs on wake.
- **One plist**: Instead of 13+ crontab entries or 13 XML plists, one scheduler handles everything.
- **Config-driven**: Adding a job = adding 3 lines of YAML.
- **Observable**: status.json is machine-readable — dashboard and watchdog can monitor it.

### Why NOT Individual LaunchAgents

- "Every 2 hours" requires 12 XML dict entries in a plist. Absurd.
- 13 plists to manage, load, unload. Fragile.
- No unified status view.

### Config Format

```yaml
# ~/aosv2/config/crons.yaml

jobs:
  # --- Tier 1: System breaks without these ---

  watchdog:
    command: bash ~/aosv2/core/bin/watchdog
    every: 5m
    timeout: 60
    ping: https://hc-ping.com/UUID  # dead man's switch

  auto-commit:
    command: bash ~/aosv2/core/bin/auto-commit cron
    every: 30m
    timeout: 120
    ping: https://hc-ping.com/UUID

  qmd-reindex:
    command: bash ~/aosv2/core/bin/qmd-reindex
    every: 30m
    timeout: 120

  session-export:
    command: python3 ~/aosv2/core/bin/session-export
    every: 2h
    timeout: 300
    ping: https://hc-ping.com/UUID

  rotate-logs:
    command: bash ~/aosv2/core/bin/rotate-logs
    at: "03:00"
    timeout: 60

  compile-daily:
    command: python3 ~/aosv2/core/bin/compile-daily
    at: "23:30"
    timeout: 120

  # --- Tier 2: System is dumber without these ---

  inbox-collect:
    command: python3 ~/aosv2/core/bin/inbox-collect
    every: 30m
    timeout: 120

  session-analysis:
    command: python3 ~/aosv2/core/bin/session-analysis
    at: "22:00"
    weekday: sunday
    timeout: 180

  weekly-digest:
    command: python3 ~/aosv2/core/bin/weekly-digest
    at: "20:00"
    weekday: sunday
    timeout: 300

  stale-detector:
    command: python3 ~/aosv2/core/bin/stale-detector
    at: "08:00"
    timeout: 60

  # --- Tier 3: System becomes proactive ---

  morning-context:
    command: python3 ~/aosv2/core/bin/morning-context
    at: "06:30"
    timeout: 60

  email-digest:
    command: python3 ~/aosv2/core/bin/email-digest
    at: "07:30"
    timeout: 120
    enabled: false  # until email access is configured

  nightly-pipeline:
    command: python3 ~/aosv2/core/bin/nightly-pipeline
    at: "23:00"
    timeout: 600

  compile-patterns:
    command: python3 ~/aosv2/core/bin/compile-patterns
    at: "04:00"
    timeout: 120
    enabled: false  # until execution logging exists

  meeting-prep:
    command: python3 ~/aosv2/core/bin/meeting-prep
    every: 15m
    active_hours: "08:00-20:00"
    timeout: 60
    enabled: false  # until calendar integration exists

  monthly-review:
    command: python3 ~/aosv2/core/bin/monthly-review
    at: "10:00"
    monthday: 1
    timeout: 300
    enabled: false  # until 4+ weekly digests exist
```

### Status File

```json
// ~/.aos-v2/logs/crons/status.json
{
  "watchdog": {
    "last_run": "2026-03-21T13:45:02",
    "exit_code": 0,
    "duration_s": 3,
    "last_failure": null
  },
  "session-export": {
    "last_run": "2026-03-21T12:00:14",
    "exit_code": 0,
    "duration_s": 12,
    "last_failure": "2026-03-20T10:00:03"
  }
}
```

---

## Event-Driven Triggers (File Watchers)

macOS LaunchAgents support `WatchPaths` — when a file/directory changes, the agent runs a script. No polling.

### Planned Watchers

| Watch Path | Script | Purpose |
|------------|--------|---------|
| `~/.aos-v2/data/health/` | `bin/on-health-update` | Health JSON arrives → update daily log, check 3-day trends |
| `~/vault/inbox/` | `bin/on-inbox-file` | New file → classify, route to correct vault folder, reindex |
| `~/.aos-v2/work/work.yaml` | `bin/on-work-change` | Task completed → update project status, check goal progress |

### Implementation

Either:
- Separate LaunchAgent per watcher (simplest, macOS-native)
- Watchers registered in crons.yaml and the scheduler polls for mtime changes (unified, but polling)

Recommendation: Separate LaunchAgents for watchers. They're simple (3 lines of XML each for WatchPaths), and macOS handles the efficiency. Don't overload the scheduler.

---

## Heartbeats and External Monitoring

### The Gap

If the Mac Mini crashes, no internal system can alert you. The watchdog can't watch itself.

### Solution: Dead Man's Switch

1. Register checks on Healthchecks.io (free tier: 20 checks)
2. Critical jobs ping their check URL on each successful run
3. If Healthchecks.io doesn't hear back within the expected window, it alerts via Telegram

### Which Jobs Get Pings

| Job | Expected Interval | Grace Period |
|-----|-------------------|-------------|
| watchdog | 5 min | 10 min |
| auto-commit | 30 min | 45 min |
| session-export | 2 hours | 3 hours |
| bridge heartbeat | 15 min | 30 min |

---

## Pipelines (Multi-Step Chains)

### The Problem

Some tasks must run in sequence:
- session-analysis → friction-rules propose (friction-rules reads the analysis output)
- session-export → qmd-reindex (new exports need indexing)

v1 solved this with `&&` chains in crontab, which is brittle.

### Solution: Nightly Pipeline Script

A simple Python script that runs steps sequentially, checks exit codes, and stops on failure:

```yaml
# Embedded in nightly-pipeline script, not a separate config yet
steps:
  - session-analysis --days 1
  - friction-rules propose
  - stale-detector
  - token-report
on_failure: notify_telegram
```

If/when we need more complex pipelines (5+), extract to `config/pipelines.yaml` and build a generic runner.

---

## Full Job Inventory

### Daily Timeline

| Time | Job | Tier | Type |
|------|-----|------|------|
| 06:30 | morning-context | 3 | Scheduled |
| 07:00 | inbox-collect | 2 | Scheduled |
| 07:30 | email-digest | 3 | Scheduled |
| 08:00 | stale-detector | 2 | Scheduled |
| */5 min | watchdog | 1 | Scheduled |
| */15 min | meeting-prep (active hours) | 3 | Scheduled |
| */30 min | qmd-reindex | 1 | Scheduled |
| */30 min | auto-commit | 1 | Scheduled |
| */2 hours | session-export | 1 | Scheduled |
| on change | health-update | — | Event-driven |
| on change | inbox-file | — | Event-driven |
| on change | work-change | — | Event-driven |
| 21:00 | message-digest | 2 | Scheduled |
| 21:30 | evening-summary | 3 | Scheduled |
| 23:00 | nightly-pipeline | 2 | Pipeline |
| 23:30 | compile-daily | 1 | Scheduled |
| 03:00 | rotate-logs | 1 | Scheduled |
| 04:00 | compile-patterns | 2 | Scheduled |

### Weekly

| Day/Time | Job | Tier |
|----------|-----|------|
| Saturday 04:00 | dep-scan + backup-check + process-audit | 2 |
| Sunday 19:00 | execution-report | 2 |
| Sunday 20:00 | weekly-digest | 2 |
| Sunday 22:00 | session-analysis (7-day) | 2 |

### Monthly

| Day/Time | Job | Tier |
|----------|-----|------|
| 1st 10:00 | monthly-review | 3 |

---

## Install Script Requirements (Phase C)

When `install.sh` is built, it must:

1. **Create directories**:
   - `~/.aos-v2/logs/crons/`
   - `~/.aos-v2/data/health/`
   - `~/.aos-v2/work/`
   - `~/.aos-v2/inbox/`

2. **Install LaunchAgent**: `com.aos.scheduler.plist` → `~/Library/LaunchAgents/`

3. **Optionally install watchers**: `com.aos.watcher-health.plist`, etc.

4. **Seed config**: Copy `crons.yaml` template (Tier 1 enabled, Tier 2-3 disabled)

5. **Initialize status**: `echo '{}' > ~/.aos-v2/logs/crons/status.json`

6. **TCC permissions**: Prompt user to grant Full Disk Access to `/opt/homebrew/bin/python3`

7. **External monitoring**: Guide user through Healthchecks.io setup, store ping URLs in Keychain

8. **Remove v1 crontab**: Strip old `~/aos/bin/` entries

9. **Verify**: Scheduler appears in `launchctl list`, first status.json update within 5 minutes

---

## Scripts to Build

### Exists (needs fixes)
| Script | Fix |
|--------|-----|
| `session-export` | Ready — minor: hardcoded PROJECT_ALIASES |
| `session-analysis` | Ready |
| `compile-daily` | **Fix HEALTH_DIR** — points to v1 path `~/aos/data/health/` |
| `compile-patterns` | No-op until execution logging exists. Move output to `~/.aos-v2/patterns/` |
| `qmd-reindex` | Add `set -e` and `command -v qmd` check |

### Port from v1
| Script | Effort |
|--------|--------|
| `watchdog` | M — update paths, add Tailscale/internet checks, add Healthchecks.io ping |
| `auto-commit` | S — update repo path to `~/aosv2/` |
| `rotate-logs` | S — update log dir to `~/.aos-v2/logs/` |
| `inbox-collect` | M — rewrite for v2 paths, stage to `~/.aos-v2/inbox/` |
| `weekly-digest` | M — rewrite for v2 paths |

### New
| Script | Effort | Priority |
|--------|--------|----------|
| **scheduler** | M | P0 — the engine itself |
| **stale-detector** | S | P1 — feeds morning briefing |
| **nightly-pipeline** | S | P1 — consolidates nightly chain |
| **morning-context** | S | P2 — weather, prayer times |
| **email-digest** | M | P2 — highest-value intelligence |
| **meeting-prep** | M | P3 — calendar lookahead |
| **token-report** | S | P3 — usage tracking |
| **process-audit** | S | P3 — zombie cleanup |
| **monthly-review** | M | P3 — after 4+ weekly digests |
| **backup-check** | S | P3 — Time Machine + git verification |
| **dep-scan** | S | P3 — brew/pip/npm outdated |

---

## Visual Management (Phase D)

The `status.json` file is machine-readable. Two visual layers planned:

1. **Dashboard panel** (:4096) — "Crons" tab reads status.json, renders table: job name, last run, status, duration, next due. Simple HTML/JS.
2. **Chief iOS app** — same data via Listen API endpoint (`/crons/status`). Card-based view of job health.
3. **Healthchecks.io** — external dashboard for critical jobs (watchdog, auto-commit, session-export, bridge). Free tier.

No separate cron management UI needed. The YAML config is the source of truth, the dashboard is the read-only view.

## Decisions Made

1. **Single scheduler LaunchAgent** over crontab or individual LaunchAgents
2. **YAML config** for job definitions (not XML plists, not crontab syntax)
3. **Healthchecks.io** for external dead man's switch monitoring
4. **WatchPaths LaunchAgents** for file system triggers (separate from scheduler)
5. **No n8n / workflow engine** — lightweight pipeline scripts cover the need
6. **5-minute granularity** is sufficient for all current jobs
7. **Event-driven for health/inbox/work** — don't wait for schedules when data arrives
