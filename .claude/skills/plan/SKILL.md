---
name: plan
description: >
  Initiative planning skill. Decomposes a shaped initiative into phased
  execution with work system task linking and readiness checks. Triggers on:
  /plan, 'break this down', 'plan this out', 'create phases', or
  auto-triggered by forge when initiative status is shaping-complete.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Plan — AOS Initiative Pipeline

> Part of the **AOS Initiative Pipeline** — a suite of skills that turn ideas into tracked,
> phased, executed work. Related skills: forge, shape, plan, gate, deliberate.

Decompose a shaped initiative into phased execution with concrete tasks, dependencies, and readiness validation. The output is an initiative ready for execution with all tasks created in the work system.

## Prerequisite

The initiative **must have completed shaping**. Check the initiative document:

- `status` should be `shaping` (meaning shaping is complete)
- Problem, Solution, Non-Goals, and Decisions sections must be populated
- Appetite must be set

If any of these are missing, redirect to the shape skill:

> "This initiative hasn't been fully shaped yet. Let's finish shaping before planning. Loading shape skill."

## Protocol

### Step 1: DECOMPOSE

Read the full initiative document:

```bash
# Read initiative
# Use Read tool on: ~/vault/knowledge/initiatives/{slug}.md
```

Then propose a phased structure:

1. **Identify natural boundaries** in the solution -- what can be built and validated independently?
2. **Group work into phases** -- each phase should deliver something testable or demonstrable.
3. **Size each phase**: S (< 1 day), M (1-3 days), L (3-5 days)
4. **Map dependencies** between phases -- which phases depend on others completing first?
5. **Assign wave numbers** -- independent phases that can run in parallel get the same wave number. Dependent phases get the next wave number.

Present the proposed structure:

```
## Proposed Phases for: {Title}
Appetite: {appetite}

Wave 1:
  Phase 1: {name} [{size}]
    {one-line description}
    Depends on: nothing

  Phase 2: {name} [{size}]
    {one-line description}
    Depends on: nothing

Wave 2:
  Phase 3: {name} [{size}]
    {one-line description}
    Depends on: Phase 1

Total estimated effort: {sum of sizes}
Appetite: {appetite}
Fit: {yes/no -- does total effort fit within appetite?}
```

Ask operator to approve the phase structure before creating any tasks:

> "Does this phase breakdown look right? I won't create any tasks until you approve the structure."

If the total effort exceeds the appetite:

> "This plan estimates {total} but the appetite is {appetite}. We need to cut scope. What can we drop or simplify?"

Work with operator to trim until it fits. The appetite is the constraint -- the plan bends to fit it, not the other way around.

### Step 2: TASK CREATION (per phase)

After operator approves the phase structure, create tasks in the work system.

For each phase:

1. Break into individual tasks, each 30min-3hr of work
2. Each task gets a clear title and acceptance criteria
3. Create the phase as a parent task, with individual items as subtasks

```bash
# Create phase parent task
python3 ~/aos/core/work/cli.py add "Phase 1: {phase name}" --project {project} --tags initiative:{slug},phase:1

# Create subtasks under the phase
python3 ~/aos/core/work/cli.py subtask {phase-task-id} "{task title}"
python3 ~/aos/core/work/cli.py subtask {phase-task-id} "{task title}"
```

After creating tasks, link them back into the initiative document. Update the **Phases** section with checkboxes and task IDs:

```markdown
## Phases

### Phase 1: {name} [S/M/L] -- Wave 1
- [ ] {Task description} -- `{project}#{task-id}`
- [ ] {Task description} -- `{project}#{task-id}`

### Phase 2: {name} [S/M/L] -- Wave 1
- [ ] {Task description} -- `{project}#{task-id}`
```

### Step 3: READINESS CHECK

Run a formal readiness check before marking the initiative as ready for execution.

Checklist:

| Check | How to verify |
|-------|--------------|
| Every phase has at least one task | Count tasks per phase |
| Every task has acceptance criteria | Review task descriptions -- each should have a clear "done when" |
| Dependencies are mapped | Check for circular dependencies (A needs B needs A) |
| Total estimated effort fits within appetite | Sum phase sizes vs appetite |
| No open questions marked as blocking | Search initiative doc for "blocking" or "blocker" |
| Locked decisions cover all architectural choices | Review decisions section -- any solution aspects without a decision? |

Present the readiness check:

```
## Readiness Check: {Title}

[PASS] Every phase has at least one task
[PASS] Dependencies mapped (no circular deps)
[CONCERN] Task "X" lacks clear acceptance criteria
[FAIL] Total effort (3 weeks) exceeds appetite (2 weeks)

Result: {PASS / CONCERNS / FAIL}
```

**PASS**: All checks pass. Proceed to update status.

**CONCERNS**: Some non-critical issues found. Present them to operator:

> "The plan has some concerns. These won't block execution but you should be aware:
> {list concerns}
> Want to address these now or proceed anyway?"

Operator decides whether to fix or accept.

**FAIL**: Critical issues found. Do NOT proceed.

> "The plan isn't ready for execution:
> {list failures}
> Let's fix these before moving forward."

Work with operator to resolve failures, then re-run the readiness check.

### Step 4: UPDATE DOCUMENT

After readiness check passes (or CONCERNS accepted):

Update the initiative document frontmatter:
```yaml
status: executing
phase: 1
total_phases: {count}
updated: {today's date}
```

Write the full phase structure into the Phases section (done in Step 2).

Write the readiness check results into the Progress section:

```markdown
## Progress
- {today}: Planning complete. {N} phases, {M} total tasks. Readiness: {PASS/CONCERNS}.
  {If concerns: "Accepted concerns: {list}"}
```

Announce completion:

> "Planning complete. Initiative '{title}' is now in executing status, starting at Phase 1. When you're ready to work on it, forge will route you to the current phase tasks."

## Task Granularity Guide

Tasks should be **30 minutes to 3 hours** each. This is the right granularity for:
- Completing in a single focused session
- Giving a sense of progress
- Being specific enough to act on without re-reading the whole initiative

**Too big** (split it):
- "Build the API" -- what endpoints? what validation?
- "Set up the frontend" -- which pages? what components?

**Too small** (merge it):
- "Create the file" -- that's a step within a task, not a task
- "Write the import statement" -- combine with related setup work

**Just right**:
- "Create user authentication endpoint with JWT token generation"
- "Build the dashboard layout with navigation sidebar and content area"
- "Write integration tests for the payment processing flow"

## Wave Numbering

Waves enable parallel execution. Phases in the same wave have no dependencies on each other and can be worked on in any order (or simultaneously if multiple agents were involved).

```
Wave 1: Foundation work (no dependencies)
  Phase 1: Data model [S]
  Phase 2: API scaffold [S]

Wave 2: Depends on Wave 1
  Phase 3: API endpoints [M] -- needs Phase 1 + 2
  Phase 4: CLI interface [S] -- needs Phase 1

Wave 3: Depends on Wave 2
  Phase 5: Integration tests [M] -- needs Phase 3 + 4
```

## Rules

- **Never create tasks without operator approval of phase structure** -- always present and confirm first
- **Appetite is the constraint** -- if the plan doesn't fit, cut scope. Never expand appetite during planning.
- **Tasks are 30min-3hr** -- enforce this granularity. Split big tasks, merge trivial ones.
- **Readiness check is a formal gate** -- FAIL means stop and fix. No exceptions.
- **Wave numbers are informational** -- they help the operator understand execution order, but the operator can choose to work phases in any order they want.
- **Link everything** -- every task in the work system gets tagged with `initiative:{slug}` and `phase:{N}`. Every task in the initiative document gets a work system ID reference.
- **One question at a time** -- present the phase structure for approval, then create tasks. Don't batch.
