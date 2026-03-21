"""
Migration 010: Update CLAUDE.md files to reflect new structure.

The root ~/CLAUDE.md and ~/.claude/CLAUDE.md still reference old paths.
Update them to point to the new filesystem layout.
"""

DESCRIPTION = "Update CLAUDE.md files for new filesystem layout"

from pathlib import Path

HOME = Path.home()

ROOT_CLAUDE = HOME / "CLAUDE.md"
GLOBAL_CLAUDE = HOME / ".claude" / "CLAUDE.md"


def check() -> bool:
    """Applied if CLAUDE.md files don't reference old paths."""
    for f in [ROOT_CLAUDE, GLOBAL_CLAUDE]:
        if f.exists():
            content = f.read_text()
            if "aosv2" in content or ".aos-v2" in content or "~/aos-v1-archive" in content:
                return False
            # Check if root CLAUDE.md has been updated to new structure
            if f == ROOT_CLAUDE and "~/.aos/" not in content and f.exists():
                return False
    return True


def up() -> bool:
    """Update CLAUDE.md files."""

    # Update ~/CLAUDE.md — the root pointer
    if ROOT_CLAUDE.exists():
        ROOT_CLAUDE.write_text("""# AOS — Agentic Operating System

This Mac Mini runs AOS. The operating system lives at `~/aos/`.

## Layout

```
~/
├── aos/                 ← The operating system (framework, git-tracked)
│   ├── core/            ← System code (agents, services, bin, work engine)
│   ├── config/defaults/ ← Shipped defaults
│   ├── .claude/         ← Skills, commands, rules
│   ├── templates/       ← Agent catalog + project scaffold
│   ├── vendor/          ← Third-party tools (Steer, MCP servers)
│   └── specs/           ← Architecture docs
├── .aos/                ← Instance data (never in git)
│   ├── services/        ← Service deployments (.venv, runtime)
│   ├── config/          ← User config overrides
│   ├── work/            ← Tasks, goals, inbox
│   ├── data/            ← Runtime data (health, phoenix)
│   └── logs/            ← All logs
├── vault/               ← Knowledge (Obsidian + QMD indexed)
├── nuchay/              ← Project: Nuchay
├── chief-ios-app/       ← Project: Chief app
└── CLAUDE.md            ← This file
```

## Quick Reference

| What | Where |
|------|-------|
| Update system | `~/aos/core/bin/aos update` |
| Self-test | `~/aos/core/bin/aos self-test` |
| Work CLI | `python3 ~/aos/core/work/cli.py list` |
| Secrets | `~/aos/core/bin/agent-secret get/set` |
| Search vault | `~/.bun/bin/qmd query "<topic>" -n 5` |
| Services | bridge (daemon), dashboard (:4096), listen (:7600) |

## Rules
- Secrets in macOS Keychain only — never in files
- Framework (`~/aos/`) is read-only at runtime
- Instance data (`~/.aos/`) is machine-specific, never committed
- Each project gets its own CLAUDE.md and Telegram bot
""")
        print("       Updated ~/CLAUDE.md")

    # Update ~/.claude/CLAUDE.md — the global kernel
    if GLOBAL_CLAUDE.exists():
        content = GLOBAL_CLAUDE.read_text()
        content = content.replace("~/aosv2/", "~/aos/")
        content = content.replace("aosv2", "aos")
        content = content.replace("~/.aos-v2/", "~/.aos/")
        content = content.replace(".aos-v2", ".aos")
        GLOBAL_CLAUDE.write_text(content)
        print("       Updated ~/.claude/CLAUDE.md")

    return True
