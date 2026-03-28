---
globs:
  - "**/*"
description: Work tracking awareness — guides proactive task and thread management during sessions
---

# Work Awareness

You have access to a work tracking system with project-scoped IDs, fuzzy resolution, subtasks, and handoff context. The SessionStart hook injects current tasks and threads into your context. Use this information proactively.

## Task IDs

Tasks use project-scoped IDs: `aos#3`, `chief#1`, `t#1` (unassigned). Subtasks use dot notation: `aos#3.1`, `aos#3.2`.

You can resolve tasks by:
- Exact ID: `work done aos#3`
- Fuzzy title: `work done "sse push"`
- Legacy ID: `work done t14` (backward compat)

When in a project directory, new tasks auto-assign to that project.

## When to Act

**Complete a task** — When you finish work that matches an active task:
```bash
python3 ~/aos/core/engine/work/cli.py done "fuzzy search or exact id"
```

**Start a task** — When beginning work on a task:
```bash
python3 ~/aos/core/engine/work/cli.py start "fuzzy search or exact id"
```

**Create subtasks** — When you discover a task needs decomposition mid-work:
```bash
python3 ~/aos/core/engine/work/cli.py subtask aos#3 "Subtask title"
python3 ~/aos/core/engine/work/cli.py subtask aos#3 "Already done part" --done
```
Subtasks auto-cascade: when all subtasks of a parent are done, the parent auto-completes.

**Write handoff** — Before ending a session where work is in progress. This is the relay baton for the next session:
```bash
python3 ~/aos/core/engine/work/cli.py handoff aos#4 \
    --state "What was accomplished and where things stand" \
    --next "The specific next step to take" \
    --files "file1.py,file2.py" \
    --decisions "Decision 1|Decision 2" \
    --blockers "Blocker 1|Blocker 2"
```

**Get dispatch context** — When picking up a task that has existing handoff context:
```bash
python3 ~/aos/core/engine/work/cli.py dispatch aos#4
```

**Link this session**:
```bash
python3 ~/aos/core/engine/work/cli.py link aos#4 --session <session_id> --outcome "what was accomplished"
```

**Suggest tracking** — When a conversation evolves into multi-step work that isn't tracked, suggest once. Don't auto-create.

**Thread awareness** — If the context shows a current thread, you're continuing prior work. Reference it naturally.

## When NOT to Act

- Simple questions, chat, explanations
- Single-action requests ("restart the bridge")
- When the user says "just do it" or wants speed
- Don't create tasks for tasks

## Quality Standards

- Task titles should be actionable: "Build session linking" not "Session stuff"
- When in a project dir, tasks auto-assign — no need for `--project`
- Priorities: only set 1 or 2 if genuinely urgent/important. Default is 3.
- Inbox for vague captures, tasks for clear next actions
- Handoffs: write for the NEXT agent, not as a log. State + next step + decisions.

## Key Commands

```bash
python3 ~/aos/core/engine/work/cli.py add "Title" --priority N
python3 ~/aos/core/engine/work/cli.py done "fuzzy title or exact id"
python3 ~/aos/core/engine/work/cli.py start "task"
python3 ~/aos/core/engine/work/cli.py subtask "parent" "Subtask title"
python3 ~/aos/core/engine/work/cli.py handoff "task" --state "..." --next "..."
python3 ~/aos/core/engine/work/cli.py dispatch "task"
python3 ~/aos/core/engine/work/cli.py show "task"
python3 ~/aos/core/engine/work/cli.py list
python3 ~/aos/core/engine/work/cli.py today
python3 ~/aos/core/engine/work/cli.py next
python3 ~/aos/core/engine/work/cli.py search "query"
python3 ~/aos/core/engine/work/cli.py projects
python3 ~/aos/core/engine/work/cli.py thread "Exploration title"
python3 ~/aos/core/engine/work/cli.py inbox "Vague thought to triage later"
```
