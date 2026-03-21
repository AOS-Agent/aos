---
name: work
description: >
  Manage tasks, projects, goals, and threads. Trigger on "/work", "add task",
  "show my tasks", "what's on my plate", "mark X done", "what should I work on",
  "create project", "add goal", or any request to manage tracked work items.
allowed-tools: Bash, Read, Write, Edit, Glob
---

# /work -- Task & Work Management

Manage all work items through the work engine CLI.

## CLI Reference

All commands run via:
```bash
python3 ~/aosv2/core/work/cli.py <command> [args]
```

### Task Commands

```bash
# Add a task
python3 ~/aosv2/core/work/cli.py add "Buy groceries"
python3 ~/aosv2/core/work/cli.py add "Fix login bug" --priority 2 --project website --tags bug,urgent

# List tasks (hides done/cancelled by default)
python3 ~/aosv2/core/work/cli.py list
python3 ~/aosv2/core/work/cli.py list --status active
python3 ~/aosv2/core/work/cli.py list --project aos-v2
python3 ~/aosv2/core/work/cli.py list --status todo,active

# Change status
python3 ~/aosv2/core/work/cli.py done t1
python3 ~/aosv2/core/work/cli.py start t2
python3 ~/aosv2/core/work/cli.py cancel t3

# Show details
python3 ~/aosv2/core/work/cli.py show t1

# Search
python3 ~/aosv2/core/work/cli.py search "groceries"
```

### Other Commands

```bash
# Inbox (capture now, triage later)
python3 ~/aosv2/core/work/cli.py inbox "Look into WebSocket approach"
python3 ~/aosv2/core/work/cli.py inbox   # show inbox

# Projects and goals
python3 ~/aosv2/core/work/cli.py projects
python3 ~/aosv2/core/work/cli.py goals

# Overview
python3 ~/aosv2/core/work/cli.py summary

# Raw data (for programmatic use)
python3 ~/aosv2/core/work/cli.py json
```

### Priority Markers

```
!! = Urgent (1)    — drop everything
!  = High (2)      — this week
   = Normal (3)    — standard
~  = Low (4)       — when capacity allows
?  = Unset (0)     — not triaged
```

## How to Respond

**"Add a task" / "I need to..."**
→ Run `cli.py add` with the title. Infer priority from urgency words. Ask for project only if ambiguous.

**"What's on my plate?" / "Show tasks"**
→ Run `cli.py list`. Present as a clean list. Highlight anything overdue or urgent.

**"Done with X" / "Finished X"**
→ Find the task by searching if they give a title instead of an ID. Run `cli.py done`.

**"What should I work on?"**
→ Run `cli.py list --status todo,active`. Consider priority, energy level (if known from daily note), and due dates. Suggest the top 1-3.

**Quick capture / "Remind me to..."**
→ Use `cli.py inbox` for things that need triage later.

## Data Location

Work file: `~/.aos-v2/work/work.yaml`
Schema: `~/aosv2/core/work/schema.yaml`

## Rules

- Don't edit work.yaml directly -- always use the CLI (preserves format, handles IDs)
- After any mutation, confirm what was done: "Created t5: Buy groceries [todo]"
- If the operator mentions work during a non-work conversation, note it but don't trigger the full skill -- suggest "Want me to track that as a task?"
