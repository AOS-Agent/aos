---
globs:
  - "**/*"
description: Work tracking awareness — guides proactive task and thread management during sessions
---

# Work Awareness

You have access to a work tracking system. The SessionStart hook injects current tasks and threads into your context. Use this information proactively — don't wait for explicit commands.

## When to Act

**Complete a task** — When you finish work that matches an active task (building a feature, fixing a bug, setting up infrastructure), run:
```bash
python3 ~/aosv2/core/work/cli.py done <task_id>
```

**Start a task** — When the user explicitly asks to work on something that matches a todo task:
```bash
python3 ~/aosv2/core/work/cli.py start <task_id>
```

**Link this session** — When working on an active task across multiple sessions, the SessionEnd hook handles this automatically. But if you notice a task is relevant mid-session, you can explicitly link:
```bash
python3 ~/aosv2/core/work/cli.py link <task_id> --session <session_id> --outcome "what was accomplished"
```

**Suggest tracking** — When a conversation evolves into multi-step work that isn't tracked, suggest:
- "This has become a multi-step effort. Want me to track it as a task?"
- Don't auto-create without asking. Suggest once, respect the answer.

**Thread awareness** — If the context shows a current thread, you're continuing prior work. Reference it naturally: "Picking up from the work system buildout thread..."

## When NOT to Act

- Simple questions, chat, explanations — not everything is a task
- Single-action requests ("restart the bridge") — too small to track
- When the user says "just do it" or wants speed over ceremony
- Don't create tasks for tasks (no meta-tracking of tracking)

## Quality Standards

- Task titles should be actionable: "Build session linking" not "Session stuff"
- Use the project field when the work clearly belongs to a project
- Priorities: only set 1 or 2 if genuinely urgent/important. Default is 3.
- Inbox for vague captures, tasks for clear next actions

## Key Commands

```bash
python3 ~/aosv2/core/work/cli.py add "Title" --project X --priority N
python3 ~/aosv2/core/work/cli.py done <id>
python3 ~/aosv2/core/work/cli.py start <id>
python3 ~/aosv2/core/work/cli.py list
python3 ~/aosv2/core/work/cli.py thread "Exploration title"
python3 ~/aosv2/core/work/cli.py inbox "Vague thought to triage later"
```
