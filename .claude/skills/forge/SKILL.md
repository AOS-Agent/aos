---
name: forge
description: >
  Bootstrap skill for initiative pipeline. Routes operator to next action based
  on initiative state, work tasks, and schedule. Triggers on: /forge, /next,
  'what should I work on', 'what's next', 'let's continue', session start when
  initiatives are active. Also intercepts initiative-level requests that don't
  have a tracked initiative.
allowed-tools: Read, Write, Edit, Glob
---

# Forge — AOS Initiative Pipeline

> Part of the **AOS Initiative Pipeline** — a suite of skills that turn ideas into tracked,
> phased, executed work. Related skills: forge, shape, plan, gate, deliberate.

Route the operator to the right next action. This is the entry point for all initiative-driven work.

## How It Works

**You do NOT gather data.** At session start, `inject_context` already injected everything into
your context: tasks, initiatives, inbox, schedule, suggested focus. Just read what's there.

Mid-session, if context is stale (tasks completed, state changed), run ONE command:
```bash
python3 ~/aos/core/work/cli.py briefing
```
That returns everything in a single call. Never run multiple CLI commands to gather state.

If you need more detail on a specific initiative, read its document:
```
~/vault/knowledge/initiatives/{slug}.md
```

## Protocol

### Step 1: READ CONTEXT

**At session start**: Look at the `[Work System]` block already in your context.
**Mid-session**: Run `work briefing` (one command, one call).

It contains:
- Active tasks (by project and overall)
- Due/overdue items
- High-priority todos
- Initiative state digests (status, phase, next action)
- Handoff context from previous sessions
- Inbox preview
- Today's schedule blocks
- Suggested focus

**Do not run bash commands to gather this data. It's already there.**

### Step 2: ROUTE

Present a concise summary:

```
You have N active initiatives:
  1. {title} [{status}] — next: {action}

Plus N tasks. Suggested focus: {suggestion from injected context}.

What do you want to pick up?
```

Then based on operator's choice:

| Initiative status | Route to |
|------------------|----------|
| `research` with material | Load **shape** skill |
| `research` needing more | Research work directly |
| `shaping` in progress | Load **shape** skill |
| Shaping complete | Load **plan** skill |
| `planning` | Load **plan** skill |
| `executing` | Show current phase tasks, proceed |
| Phase boundary | Load **gate** skill |
| `review` | Load **review** skill for initiative review |
| Standalone task | Proceed normally |

If no active initiatives:
- Surface inbox items from injected context
- Offer to create a new initiative if operator has something in mind

### Step 3: ANTI-SKIP CHECK

Before executing any multi-session or complex request, check if an initiative exists.

Signals that a request is initiative-level:
- Multi-session language: "build me", "create a system", "research and then implement"
- Multiple components: more than one subsystem or deliverable
- Research needed: "figure out the best way to", "evaluate options"
- Outcome framing: "I want to be able to", "the goal is to"

If signals present and no matching initiative:

> "This looks like initiative-level work. Want me to track it as an initiative?"

- If **yes**: Create initiative doc at `vault/knowledge/initiatives/{slug}.md` with status: research
- If **no**: Proceed as normal task work

## Rules

- **Never gather data** — read injected context only. One file read max (the initiative doc if needed).
- **One question at a time** — never batch questions
- **Never auto-advance** — present summary, let operator choose
- **Keep it tight** — 5-10 lines for the summary. Details come from individual skills.
- **Stale initiatives get attention** — surface prominently
- **Status flow is one-directional** — research → shaping → planning → executing → review → done
