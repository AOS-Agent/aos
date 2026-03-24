---
name: forge
description: >
  Bootstrap skill for initiative pipeline. Routes operator to next action based
  on initiative state, work tasks, and schedule. Triggers on: /forge, /next,
  'what should I work on', 'what's next', 'let's continue', session start when
  initiatives are active. Also intercepts initiative-level requests that don't
  have a tracked initiative.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Forge — AOS Initiative Pipeline

> Part of the **AOS Initiative Pipeline** — a suite of skills that turn ideas into tracked,
> phased, executed work. Related skills: forge, shape, plan, gate, deliberate.

Route the operator to the right next action based on initiative state, work tasks, and schedule. This is the entry point for all initiative-driven work.

## Prerequisites

Gate on `operator.yaml -> initiatives.enabled: true`. If not set or false, skip all initiative behavior and fall through to normal work routing.

Read operator config:
```bash
cat ~/.aos/config/operator.yaml
```

Configuration keys (with defaults):
- `initiatives.enabled` -- must be true to activate (no default, must be explicit)
- `initiatives.max_active` -- maximum concurrent initiatives (default: 3)
- `initiatives.stale_threshold_days` -- days before flagging stale (default: 3)

## Protocol

### Step 1: READ STATE

Gather all context before making any routing decision.

```bash
# All initiative documents
ls ~/vault/knowledge/initiatives/*.md 2>/dev/null

# Read each initiative's frontmatter for status, updated date, phase
# Use Read tool on each file found

# Current work state
python3 ~/aos/core/work/cli.py list
python3 ~/aos/core/work/cli.py summary

# Today's focus
python3 ~/aos/core/work/cli.py today 2>/dev/null

# Operator schedule from operator.yaml
# Already read above -- check schedule blocks and current time
```

### Step 2: ASSESS

For each active initiative (status not `done` or `abandoned`):

1. **Status**: What phase is it in? (research / shaping / planning / executing / review)
2. **Staleness**: When was `updated` in frontmatter last set? If more than `stale_threshold_days` ago, flag as stale.
3. **Next action**: Based on status, what's the logical next step?
   - `research` -- needs more research, or ready for shaping?
   - `shaping` -- needs more shaping questions answered?
   - `planning` -- needs decomposition into phases/tasks?
   - `executing` -- what's the current phase? What tasks remain?
   - `review` -- needs final review and close-out?
4. **Blocked?**: Are there open questions or blockers noted in the document?

For standalone tasks (not linked to an initiative):
- What's priority and due date?
- Does it relate to an initiative? (check tags for `initiative:*`)

### Step 3: ROUTE

Present a concise summary to the operator:

```
You have N active initiatives:
  1. {title} [{status}] -- next: {action description}
  2. {title} [{status}] -- next: {action description}
  {stale items get a STALE flag}

Plus N standalone tasks ({urgent count} urgent).

What do you want to pick up?
```

Then based on operator's choice, route to the appropriate skill:

| Initiative status | Route to |
|------------------|----------|
| `research` with sufficient material | Load **shape** skill |
| `research` needing more research | Proceed with research work directly |
| `shaping` (in progress or complete) | Load **shape** skill (if incomplete) or **plan** skill (if shaping complete) |
| `planning` | Load **plan** skill |
| `executing` | Show current phase tasks, proceed with execution |
| Needs gate check (phase boundary) | Load **gate** skill |
| Standalone task | Proceed with normal task work |

If no active initiatives exist:
- Surface inbox items: `python3 ~/aos/core/work/cli.py inbox`
- Suggest shaping candidates from threads or recurring patterns
- Offer to create a new initiative if operator has something in mind

### Step 4: ANTI-SKIP CHECK

**Before executing any multi-session or complex request**, check if an initiative exists for it.

Signals that a request is initiative-level work:
- Multi-session language: "build me", "create a system", "research and then implement"
- Multiple components: involves more than one subsystem or deliverable
- Research needed: "figure out the best way to", "evaluate options for"
- Outcome framing: "I want to be able to", "the goal is to"
- Estimated effort exceeds a single session

If any of these signals are present and no matching initiative exists:

> "This looks like initiative-level work. Want me to track it as an initiative?"

- If **yes**: Create a new initiative document at `vault/knowledge/initiatives/{slug}.md` with:
  ```yaml
  ---
  title: {title}
  slug: {slug}
  status: research
  created: {today}
  updated: {today}
  appetite: null
  sources: []
  phase: 0
  total_phases: 0
  ---

  # {Title}

  ## Problem
  {Operator's initial description}

  ## Research
  {To be filled}

  ## Solution
  {To be filled}

  ## Non-Goals
  {To be filled}

  ## Decisions
  {To be filled}

  ## Phases
  {To be filled}

  ## Progress
  - {today}: Initiative created from operator request
  ```
- If **no**: Proceed as normal task work, no initiative tracking.

## The /next Command

When operator says `/next`, `/forge`, or "what's next":

1. Run the full READ STATE step
2. Present the summary from ROUTE step
3. Let operator pick
4. Route to the chosen action

This is the primary session-start workflow when initiatives are active.

## Rules

- **One question at a time** -- never batch multiple questions to the operator
- **Never auto-advance** -- always present the summary and let operator choose
- **Respect max_active** -- if operator tries to create a new initiative when at max, warn them and suggest completing or abandoning one first
- **Stale initiatives get attention** -- always surface stale items prominently, they represent forgotten commitments
- **Don't over-explain** -- keep the summary tight. Details come from the individual skills.
- **Initiative document is source of truth** -- always read the actual document, never cache or assume state
- **Status flow is one-directional** -- research -> shaping -> planning -> executing -> review -> done. Never skip stages (that's what gate enforces).
