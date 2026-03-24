---
name: gate
description: >
  Phase transition readiness check for initiatives. Validates that prerequisites
  are met before advancing to the next phase or status. Triggers on: /gate,
  'is this ready', 'readiness check', 'can we move to next phase', or
  auto-triggered at initiative phase boundaries.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# Gate — AOS Initiative Pipeline

> Part of the **AOS Initiative Pipeline** — a suite of skills that turn ideas into tracked,
> phased, executed work. Related skills: forge, shape, plan, gate, deliberate.

Validate that prerequisites are met before advancing an initiative to the next phase or status. This is the quality gate that prevents premature advancement.

## Protocol

### Step 1: IDENTIFY TRANSITION

Read the initiative document to determine the current state and which transition is being checked.

```bash
# Read the initiative document
# Use Read tool on: ~/vault/knowledge/initiatives/{slug}.md

# Check work system for task states
python3 ~/aos/core/work/cli.py list --tags initiative:{slug}
```

Determine the transition:
- If operator specified a transition (e.g., "can we move to planning?"), check that specific one
- If no specific transition requested, check the **next natural transition** based on current status

### Step 2: RUN CHECKS

Each transition has a specific checklist. Run every check and record the result.

#### research -> shaping

| Check | How to verify | Critical? |
|-------|--------------|-----------|
| Sufficient research gathered | Research section in initiative doc is non-empty, has substantive content | Yes |
| Sources linked | `sources:` field in frontmatter has at least one entry | Yes |
| At least one source file exists | Read the source paths -- do the files exist? | Yes |
| Research covers the problem space | Research addresses the core question, not just tangential topics | No |

#### shaping -> planning

| Check | How to verify | Critical? |
|-------|--------------|-----------|
| Problem statement clear | Problem section exists and is non-empty | Yes |
| Appetite set | `appetite:` field in frontmatter is not null | Yes |
| Definition of done written | Definition of Done section exists | Yes |
| Solution direction defined | Solution section exists and is non-empty | Yes |
| Non-goals defined | Non-Goals section exists and has at least one item | No |
| Locked decisions present | Decisions section has at least one locked decision | No |
| No unresolved blocking questions | No items marked "blocking" or "blocker" in open questions | Yes |

#### planning -> executing

| Check | How to verify | Critical? |
|-------|--------------|-----------|
| Phases defined | Phases section has at least one phase | Yes |
| All phases have tasks | Each phase heading has at least one checkbox/task | Yes |
| Tasks have acceptance criteria | Task descriptions are specific enough to verify completion | No |
| Dependencies mapped | Phase descriptions mention dependencies or wave numbers are assigned | No |
| Total effort fits appetite | Sum of phase sizes does not exceed appetite | Yes |
| No open blocking questions | No items marked "blocking" in the document | Yes |
| All tasks exist in work system | Task IDs referenced in document exist in work CLI | Yes |

#### phase N -> phase N+1

| Check | How to verify | Critical? |
|-------|--------------|-----------|
| All phase N tasks marked done | Check work system: all subtasks of the phase parent are done | Yes |
| No unresolved blockers for phase N | No open blockers noted in progress section | Yes |
| Phase N deliverables exist | Acceptance criteria for phase tasks are verifiable | No |
| Scope creep check | Compare current phase tasks to original plan -- were tasks added? | No |

```bash
# Check phase task completion
python3 ~/aos/core/work/cli.py list --tags initiative:{slug},phase:{N}
```

#### executing -> review

| Check | How to verify | Critical? |
|-------|--------------|-----------|
| All phases complete | All phase parent tasks are done | Yes |
| Definition of done met | Re-read definition of done, verify each criterion | Yes |
| No abandoned phases | No phases left in todo/active status | No |

### Step 3: AGGREGATE

Classify the overall result:

- **PASS**: All checks pass (including all critical checks)
- **CONCERNS**: All critical checks pass, but some non-critical checks fail
- **FAIL**: One or more critical checks fail

### Step 4: PRESENT

Format the gate check result clearly:

```
Gate check: {from_status} -> {to_status}
Initiative: {title}

[PASS] Problem statement clear
[PASS] Appetite set (2-weeks)
[CONCERN] Non-goals section empty -- consider adding explicit out-of-scope items
[FAIL] No locked decisions found

Result: {PASS / CONCERNS / FAIL}
{Summary of what this means}
```

Use these markers:
- `[PASS]` -- check passed
- `[CONCERN]` -- non-critical issue
- `[FAIL]` -- critical issue, blocks advancement

### Step 5: ACT ON RESULT

**PASS**:
- Update initiative frontmatter with the new status
- Update `phase` number if this is a phase transition
- Update `updated` date
- Add progress entry: `{today}: Gate check PASS. Transitioned {from} -> {to}.`
- Announce: "Gate passed. Initiative advanced to {new status}."

**CONCERNS**:
- Present the concerns clearly to the operator
- Ask: "The initiative can proceed despite these concerns. Want to address them first or move forward?"
- If operator wants to address: help fix the concerns, then re-run the gate
- If operator accepts: proceed as PASS, but note accepted concerns in progress log

For high-stakes initiatives (appetite >= 2-weeks), suggest deliberation on CONCERNS:

> "This is a significant initiative. Want to run a deliberation on these concerns before proceeding?"

If yes, load the deliberate skill.

**FAIL**:
- Do NOT advance the initiative status
- Present each failure clearly with what needs to be fixed
- Suggest specific actions to resolve each failure
- After fixes, the operator must run the gate check again

```
This gate check failed. The following must be fixed before proceeding:

1. {failure}: {what needs to happen}
2. {failure}: {what needs to happen}

Fix these and run /gate again when ready.
```

## Scope Creep Detection (Phase Transitions)

At phase boundaries (phase N -> phase N+1), perform a scope creep check:

1. Read the original phase plan from the initiative document
2. Read current tasks from the work system for that phase
3. Compare: were any tasks added that weren't in the original plan?

If scope creep is detected:

> "Scope creep detected in Phase {N}:
> - Original plan had {X} tasks
> - Current state has {Y} tasks
> - Added tasks: {list}
>
> This isn't necessarily bad, but worth noting. These additions {do/don't} affect the appetite fit.
> Want to update the plan to reflect these additions?"

## Progress Logging

Every gate check result gets logged in the initiative's Progress section, regardless of outcome:

```markdown
- {today}: Gate check {from} -> {to}: {PASS/CONCERNS/FAIL}
  {If CONCERNS: "Concerns: {list}. Operator decided: {proceed/fix}"}
  {If FAIL: "Failures: {list}. Blocking advancement."}
```

## Rules

- **Never auto-advance on FAIL** -- always present the failures and wait. No exceptions.
- **CONCERNS are the operator's call** -- present them clearly and let the operator decide. Don't pressure in either direction.
- **Scope creep is informational, not blocking** -- note it but don't prevent advancement just because tasks were added. Extra work is only a problem if it breaks the appetite.
- **Every gate check is logged** -- even PASS results get a progress entry. This creates an audit trail.
- **Re-run after fixes** -- if a gate fails and the operator fixes the issues, they must explicitly run the gate again. Don't auto-re-run.
- **One transition at a time** -- don't check multiple transitions at once. Check the immediate next transition only.
- **Read fresh data every time** -- never cache initiative state between gate checks. Always re-read the document and work system.
