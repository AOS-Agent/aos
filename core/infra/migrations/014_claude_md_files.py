"""
Migration 014: Ensure CLAUDE.md files exist for fresh installs.

Three CLAUDE.md files form the context hierarchy:
1. ~/CLAUDE.md — Root pointer (tells Claude about the machine layout)
2. ~/.claude/CLAUDE.md — Global kernel (loaded every session everywhere)
3. ~/aos/CLAUDE.md — Already in git, no action needed

Migration 010 updates existing files but doesn't create them from scratch.
This migration handles fresh installs where these files don't exist yet.

For existing users: skips if files already exist (content is user's).
"""

DESCRIPTION = "Create CLAUDE.md context files for fresh installs"

from pathlib import Path

HOME = Path.home()

ROOT_CLAUDE = HOME / "CLAUDE.md"
GLOBAL_CLAUDE = HOME / ".claude" / "CLAUDE.md"

ROOT_CONTENT = """\
# AOS — Agentic Operating System

This Mac Mini runs AOS. The operating system lives at `~/aos/`.

## Layout

```
~/
├── aos/                 ← The operating system (framework, git-tracked)
│   ├── core/            ← System code (agents, services, bin, work engine)
│   ├── config/          ← System config + defaults
│   ├── .claude/         ← Skills, commands, rules
│   ├── templates/       ← Agent catalog + project scaffold
│   ├── vendor/          ← Third-party tools
│   └── specs/           ← Architecture docs
├── .aos/                ← Instance data (never in git)
│   ├── services/        ← Service deployments (.venv, runtime)
│   ├── config/          ← User config overrides
│   ├── work/            ← Tasks, goals, inbox
│   ├── data/            ← Runtime data
│   └── logs/            ← All logs
├── vault/               ← Knowledge (Obsidian + QMD indexed)
└── CLAUDE.md            ← This file
```

## Quick Reference

| What | Where |
|------|-------|
| Update system | `aos update` |
| Self-test | `aos self-test` |
| Work CLI | `python3 ~/aos/core/engine/work/cli.py list` |
| Secrets | `~/aos/core/bin/cli/agent-secret get/set` |
| Search vault | `~/.bun/bin/qmd query "<topic>" -n 5` |

## Rules
- Secrets in macOS Keychain only — never in files
- Framework (`~/aos/`) is read-only at runtime
- Instance data (`~/.aos/`) is machine-specific, never committed
"""

GLOBAL_CONTENT = """\
# AOS — Agentic Operating System

This machine runs AOS. Every session operates within this context.

## Boundaries

```
~/aos/       SYSTEM (code, safe to git pull)
~/.aos/      USER DATA (never in git)
~/vault/     KNOWLEDGE (Obsidian, QMD-indexed)
~/project/   PROJECTS (self-contained workspaces)
```

## Agents

| Agent | Role |
|-------|------|
| **Chief** | Orchestrator. Receives all requests. Delegates or acts directly. |
| **Steward** | System health, self-correction, maintenance. |
| **Advisor** | Analysis, knowledge curation, work planning, reviews. |

Additional agents activated from catalog or created by user.

## Skills

Skills at `~/.claude/skills/`. Each has `SKILL.md` with trigger phrases.
When a request matches, load and follow the skill's protocol.

## Rules

- Secrets: macOS Keychain only (`agent-secret get/set`). Never in files.
- Network: localhost only. Tailscale for remote access.
- Questions: one at a time, never batch.
- Research first: check vault, config, and available data before asking.
- Delegate: dispatch to specialist agents for domain work.

## Quick Reference

- Operator profile: ~/.aos/config/operator.yaml
- Config: ~/aos/config/
- User data: ~/.aos/
- Vault search: `qmd query "<topic>" -n 5`
- Secrets: `~/aos/core/bin/cli/agent-secret get/set`
"""


def check() -> bool:
    """Applied if both CLAUDE.md files exist."""
    return ROOT_CLAUDE.exists() and GLOBAL_CLAUDE.exists()


def up() -> bool:
    """Create CLAUDE.md files. Never overwrites existing content."""
    created = 0

    if not ROOT_CLAUDE.exists():
        ROOT_CLAUDE.write_text(ROOT_CONTENT)
        print(f"       Created {ROOT_CLAUDE}")
        created += 1
    else:
        print(f"       {ROOT_CLAUDE} exists — skipped")

    if not GLOBAL_CLAUDE.exists():
        GLOBAL_CLAUDE.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_CLAUDE.write_text(GLOBAL_CONTENT)
        print(f"       Created {GLOBAL_CLAUDE}")
        created += 1
    else:
        print(f"       {GLOBAL_CLAUDE} exists — skipped")

    return True
