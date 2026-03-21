# install.sh — Design Notes

Captured during v1→v2 migration (2026-03-21).

## What install.sh Must Do

### For Fresh Installs (new user, empty machine)
1. Check prerequisites (Python3, PyYAML, Git, Homebrew)
2. Clone repo to `~/aos/`
3. Create `~/.aos/` user data structure (migration 001)
4. Set up config layering (migration 002)
5. Symlink agents (migration 003) — only system agents (chief, steward, advisor)
6. Symlink skills (migration 004) — only core skills
7. Wire hooks (migration 005)
8. Initialize event bus (migration 006)
9. Install LaunchAgent for scheduler
10. Run `aos self-test`
11. Print "run onboarding agent to configure"

### For Custom Setup Users (has Claude Code + Mac Mini, no AOS)
1. Run `aos discover` first — shows what they have
2. `aos migrate` — applies all migrations
3. Their existing agents/skills preserved (backed up as .pre-aos)
4. Their CLAUDE.md preserved
5. Their MCP config preserved

### For v1→v2 Migrants (has ~/aos/)
1. Run `restructure.sh` — archives v1, promotes v2, moves services
2. All services continue from `~/.aos/services/`
3. LaunchAgents updated automatically

## Key Decisions

### Framework vs Instance Split
```
~/aos/              FRAMEWORK — git repo, packageable, read-only at runtime
  core/services/    Service SOURCE CODE (Python files, pyproject.toml)
  core/agents/      Agent source
  core/bin/         Utility scripts
  .claude/skills/   Skill protocols
  config/           System configuration

~/.aos/             INSTANCE — never in git, machine-specific
  services/         Service DEPLOYMENTS (.venv, data, runtime state)
    bridge/         Full bridge deployment with .venv
    dashboard/      Full dashboard deployment with .venv
    listen/         etc.
  config/           User config overrides (merged with defaults at runtime)
  work/             Work system data
  data/             Runtime data (health, phoenix, etc.)
  logs/             All logs (services, crons, etc.)
```

### Service Deployment Model
- Framework ships `core/services/<name>/` with source + pyproject.toml
- install.sh (or migration) creates `~/.aos/services/<name>/`
- Runs `uv sync` inside to create .venv
- LaunchAgent points to `~/.aos/services/<name>/.venv/bin/python`
- Updates to service code: `aos update` pulls new source, user re-deploys

### LaunchAgent Paths (after restructure)
All plists in `~/Library/LaunchAgents/`:
- WorkingDirectory → `~/.aos/services/<name>/`
- Binary → `~/.aos/services/<name>/.venv/bin/python`
- Logs → `~/.aos/logs/<name>.out.log`

### What install.sh Should NOT Do
- Delete anything
- Overwrite user config
- Start services without asking
- Install optional integrations (that's onboarding agent's job)

### Size Considerations
- v1 apps/ totaled 4.1GB (mostly .venvs)
- v1 vendor/ totaled 543MB
- Framework repo (no venvs) should be <50MB
- Instance grows with service deployments

### Vendor Strategy
- Steer (mac-mini-agent-tools): needed for desktop automation, keep in vendor/
- iphone-mirror-mcp: needed for mobile, keep in vendor/
- clickup-mcp: deprecated (replaced by Plane), don't port

## Migration System
- Versioned migrations in `core/migrations/NNN_name.py`
- Each has `up()` and `check()` (idempotent)
- Version tracked in `~/.aos/.version`
- `aos update` = `git pull` + `aos migrate`
- Users receive framework updates, their instance data untouched
