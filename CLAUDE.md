# AOS System — Developer Context

This is the AOS codebase. A packageable agentic operating system.
Clone onto a Mac Mini, run the installer, get an autonomous workstation.

## Two Copies — Know Which One You're In

```
~/aos/              RUNTIME — always on main, never edit directly
~/project/aos/      DEV WORKSPACE — make changes here, push to ship
```

- `~/aos/` auto-updates at 4am. Every user's machine runs this.
- `~/project/aos/` is your working copy. Branch, edit, test here.
- Auto-commit is disabled on both (`.no-auto-commit`).

## How to Ship Changes

1. Make changes in `~/project/aos/` (on a branch or directly)
2. Test: open a Claude Code session there, run scripts, verify
3. Push to main — `git push origin HEAD:main` (or merge a PR)
4. Every machine pulls at 4am via `check-update --apply`
5. Update flow: `git pull → reconcile → migrations → sync → rebuild venvs → restart services`

Update the CHANGELOG.md with every release:
```
## vX.Y.Z — YYYY-MM-DD

Summary: One line describing the release.

Added: feature description
Changed: what changed
Fixed: what was broken
Removed: what was removed
```
Bump `VERSION` file. Users get release notes on Telegram after the 4am update.

## Reconcile System (core/infra/reconcile/)

Runs on EVERY update — not once like migrations. Checks invariants and auto-repairs drift.

```
core/infra/reconcile/
├── runner.py          # Loads checks, runs them, logs results
├── base.py            # ReconcileCheck: check() → bool, fix() → CheckResult
└── checks/
    ├── claude_md.py       # CLAUDE.md managed sections current
    ├── mcp_location.py    # mcp.json in right place
    ├── hooks.py           # settings.json hooks + permissions correct
    ├── launchagents.py    # Plist Python paths exist
    ├── symlinks.py        # Agent/skill symlinks correct
    └── log_location.py    # Runtime data not in ~/aos/
```

**To fix a structural bug across all machines:** add a check to `checks/`, add to `ALL_CHECKS` in `__init__.py`. Push. Runs everywhere next morning.

**CLAUDE.md managed sections:** AOS owns content inside `<!-- AOS:MANAGED -->` markers. User content outside markers is never touched. Bump version number to trigger update.

## Migrations vs Reconcile vs Sync

| Mechanism | Runs | Purpose |
|-----------|------|---------|
| **Migrations** (`core/infra/migrations/`) | Once per version | Structural changes (new dirs, new files, schema) |
| **Reconcile** (`core/infra/reconcile/`) | Every update cycle | Fix drift, repair broken state, keep invariants |
| **Sync** (`aos sync-skills/agents/mcp`) | Every update | Re-symlink skills/agents to framework |

## System Layout

```
~/aos/                       SYSTEM CODE (this repo)
├── core/
│   ├── services/              Bridge, dashboard, listen, memory
│   ├── agents/                chief.md, steward.md, advisor.md
│   ├── work/                  Work engine + hooks (inject_context, session_close)
│   ├── infra/reconcile/       Invariant checks (runs every update)
│   ├── infra/migrations/      Numbered one-shot migrations
│   └── bin/                   CLI tools, crons, utilities
├── config/                    Shipped config (crons.yaml, etc.)
├── templates/                 Agent catalog + project scaffold
└── specs/                     Architecture docs

~/.aos/                      USER DATA (never in git)
├── work/                      Tasks, goals, inbox
├── services/                  Service venvs + runtime
├── config/                    operator.yaml, onboarding.yaml
└── logs/                      All logs including execution/

~/vault/                       KNOWLEDGE (Obsidian, separate repo)
```

## Key Rules

- Hooks must NEVER crash — always output valid JSON and exit 0
- Runtime data goes in `~/.aos/`, never in `~/aos/`
- Skills are symlinked from `~/aos/.claude/skills/` — edits in framework propagate
- Settings.json harness files need explicit `allow` permissions (bypassPermissions doesn't cover them)
- Test with both Homebrew Python and system Python 3.9 (some users only have 3.9)

## Key Specs

| Spec | What |
|------|------|
| `specs/aos-v2-brief.md` | Original brief and research |
| `specs/work-system-architecture.md` | Work system data models |
| `specs/v2-system-map.md` | Full system map |
