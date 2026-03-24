---
name: work
description: >
  Manage tasks, projects, goals, and threads. Trigger on "/work", "add task",
  "show my tasks", "what's on my plate", "mark X done", "what should I work on",
  "create project", "add goal", "track this", "create a thread", "promote thread",
  or any request to manage tracked work items. Also self-activate when you detect
  multi-step work emerging that should be tracked, or when a session completes
  work matching an active task.
allowed-tools: Bash, Read, Write, Edit, Glob
---

# /work -- Task & Work Management

Manage all work items through the work engine CLI.

## CLI Reference

All commands run via:
```bash
python3 ~/aos/core/work/cli.py <command> [args]
```

### Task Commands

```bash
# Add a task
python3 ~/aos/core/work/cli.py add "Buy groceries"
python3 ~/aos/core/work/cli.py add "Fix login bug" --priority 2 --project website --tags bug,urgent

# List tasks (hides done/cancelled by default)
python3 ~/aos/core/work/cli.py list
python3 ~/aos/core/work/cli.py list --status active
python3 ~/aos/core/work/cli.py list --project aos

# Change status
python3 ~/aos/core/work/cli.py done t1
python3 ~/aos/core/work/cli.py start t2
python3 ~/aos/core/work/cli.py cancel t3

# Show details
python3 ~/aos/core/work/cli.py show t1

# Search
python3 ~/aos/core/work/cli.py search "groceries"
```

### Session & Thread Commands

```bash
# Link current session to a task (for multi-session work)
python3 ~/aos/core/work/cli.py link t5 --session <id> --outcome "what was done"

# Create a thread (for explorations spanning sessions)
python3 ~/aos/core/work/cli.py thread "Researching WebSocket approach"

# List active threads
python3 ~/aos/core/work/cli.py thread

# List all threads (including promoted/abandoned)
python3 ~/aos/core/work/cli.py threads

# Promote a thread to a project
python3 ~/aos/core/work/cli.py promote th1 --title "WebSocket Integration" --goal launch-mvp
```

### Other Commands

```bash
# Inbox (capture now, triage later)
python3 ~/aos/core/work/cli.py inbox "Look into WebSocket approach"
python3 ~/aos/core/work/cli.py inbox   # show inbox

# Projects and goals
python3 ~/aos/core/work/cli.py projects
python3 ~/aos/core/work/cli.py goals

# Overview
python3 ~/aos/core/work/cli.py summary

# Raw data (for programmatic use)
python3 ~/aos/core/work/cli.py json
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
> Run `cli.py add` with the title. Infer priority from urgency words. Ask for project only if ambiguous.

**"What's on my plate?" / "Show tasks"**
> Run `cli.py list`. Present as a clean list. Highlight anything overdue or urgent.

**"Done with X" / "Finished X"**
> Find the task by searching if they give a title instead of an ID. Run `cli.py done`.

**"What should I work on?"**
> If `operator.yaml → initiatives.enabled: true`: load the `forge` skill instead — it reads initiative state + work state together for a complete picture.
> If initiatives not enabled: Run `cli.py list --status todo,active`. Consider priority, energy level, and due dates. Suggest the top 1-3.

**Quick capture / "Remind me to..."**
> Use `cli.py inbox` for things that need triage later.

**"Track this" / Multi-step work detected**
> If work is clearly a task (defined outcome), use `cli.py add`.
> If exploratory or evolving, use `cli.py thread`.
> If vague or needs thinking, use `cli.py inbox`.

**Session completed a task**
> If you just finished work that matches an active task in your context, run `cli.py done <id>`.
> Confirm with the operator: "Marked t5 as done — Build session linking."

## Data Location

Work file: `~/.aos/work/work.yaml`
Schema: `~/aos/core/work/schema.yaml`

## Initiative Integration

When `operator.yaml → initiatives.enabled: true`:

### Source Reference Display
When showing task details (`work show`), if the task has a `source_ref` field, display it:
```bash
python3 ~/aos/core/work/cli.py show {id}
```
The CLI already handles this — `source_ref` is shown as "Initiative: {path}".

### Initiative Command
```bash
python3 ~/aos/core/work/cli.py initiatives       # Show active initiatives with phase progress
python3 ~/aos/core/work/cli.py initiatives --all  # Include done/archived
```

### Task-Initiative Checkbox Sync
When completing a task that has `source_ref` pointing to an initiative:
1. Complete the task: `python3 ~/aos/core/work/cli.py done {id}`
2. Read the initiative doc at the source_ref path
3. Find the matching checkbox (by task title or ID reference)
4. Check it: `- [ ]` → `- [x]`
5. Update the initiative's `updated:` date

This keeps the initiative document's progress tracking in sync with the work system.

## Rules

- Don't edit work.yaml directly -- always use the CLI (preserves format, handles IDs)
- After any mutation, confirm what was done: "Created t5: Buy groceries [todo]"
- If the operator mentions work during a non-work conversation, note it but don't trigger the full skill -- suggest "Want me to track that as a task?"
- Session linking happens automatically via hooks -- you don't need to manually link unless explicitly asked
- Threads auto-accumulate sessions via the SessionEnd hook -- don't manually manage thread sessions
