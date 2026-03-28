# Work Engine

Task tracking engine. Manages tasks and subtasks with project-scoped IDs, cascade completion (all subtasks done → parent auto-completes), priority, handoff context, and session linking. Exposes a CLI and two Claude Code hooks for injecting context at session boundaries.

## Quick Reference
- **Port**: N/A (library + CLI)
- **Restart**: N/A
- **Logs**: `~/.aos/logs/work.log`
- **Config**: `~/.aos/config/work.yaml`

## Key Files
- `engine.py` — Core CRUD, cascade logic, handoff storage
- `cli.py` — Command-line interface (`work add`, `work done`, `work list`, etc.)
- `inject_context.py` — SessionStart hook: injects active tasks and threads into agent context
- `session_close.py` — SessionEnd hook: prompts for handoff if tasks are in-progress

## Debugging
- Check if running: `pgrep -f "work/cli.py"`
- Tail logs: `tail -f ~/.aos/logs/work.log`
