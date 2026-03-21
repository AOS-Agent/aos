# Phase C Infrastructure — Handoff

**Date**: 2026-03-21
**Status**: In progress
**Task**: t2 (active), t15/t16/t17 (todo)

## What's Done

### Migration System (t12 - done)
- 10 numbered migrations in `core/migrations/` with `up()` + `check()` pattern
- Runner at `core/migrations/runner.py` (migrate, status, discover)
- Version tracked in `~/.aos/.version`

### Framework/Instance Contract (t13 - done)
- Source code: `~/aos/core/services/{bridge,dashboard,listen,memory}/`
- Runtime venvs: `~/.aos/services/{name}/.venv/`
- LaunchAgents point to: framework source + instance venv
- WORKSPACE in all service files = `Path.home() / "aos"`
- Compatibility symlinks at `~/aos/`: `bin` -> `core/bin`, `data` -> `~/.aos/data`, `apps` -> `~/.aos/services`, `logs` -> `~/.aos/logs`
- Config loader: `core/work/config_loader.py` merges `~/aos/config/` + `~/.aos/config/`
- Event bus: `core/work/events.py` writes to `~/.aos/events.jsonl`

### Service Porting (t14 - done)
- Bridge, dashboard, listen source extracted to `core/services/`
- Instance dirs cleaned (only `.venv` and `data/` remain)
- All three services verified running after restart

### `aos` CLI (`core/bin/aos`)
- Commands: migrate, status, discover, update, version, self-test, deploy
- `aos deploy [name]` — creates/rebuilds venv from framework pyproject.toml

## What's Left

### t15: install.sh — bootstrap script for fresh Mac Mini
- Install prereqs (Homebrew, Python, uv, bun, qmd)
- Clone repo to `~/aos/`
- Run `aos migrate` + `aos deploy`
- Hand off to onboarding agent

### t16: Onboarding agent — conversational setup after install
- Stages: Identity -> Essentials -> Communication -> Your Work -> Agents -> Activate
- Spec outline in `core/onboarding/README.md`

### t17: Integration setup scripts
- Manifest pattern exists at `core/integrations/telegram/manifest.yaml`
- Need setup scripts for: telegram, whatsapp, email, calendar, obsidian

## Key Files

| File | Purpose |
|------|---------|
| `core/bin/aos` | CLI entry point |
| `core/migrations/runner.py` | Migration runner |
| `core/work/config_loader.py` | Config merge (framework + user) |
| `core/work/events.py` | Event bus |
| `core/services/*/pyproject.toml` | Service dependencies |
| `specs/v2-system-map.md` | Canonical architecture (locked) |
| `specs/v2-architecture-decisions.md` | 10 architecture decisions (locked) |

## Services Status

All running as of 2026-03-21:
- Bridge: daemon (Telegram + Slack)
- Dashboard: :4096
- Listen: :7600
