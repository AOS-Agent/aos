---
name: shape
description: >
  Socratic shaping skill for initiatives. Guides operator through problem
  definition, appetite setting, scope constraining, and decision locking.
  Produces a shaped initiative ready for planning. Triggers on: /shape,
  'let's shape this', 'scope this out', 'what are we actually building',
  'define the initiative', or auto-triggered by forge when initiative is in
  research status with sufficient material.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Shape — AOS Initiative Pipeline

> Part of the **AOS Initiative Pipeline** — a suite of skills that turn ideas into tracked,
> phased, executed work. Related skills: forge, shape, plan, gate, deliberate.

Guide the operator through structured problem definition, appetite setting, scope constraining, and decision locking. The output is a shaped initiative document ready for planning.

## HARD GATE

Do NOT create tasks, write code, or invoke any execution skill until shaping is complete and operator has approved the shape. Shaping is a thinking phase -- no building.

## Prerequisites

The initiative must exist as a document at `vault/knowledge/initiatives/{slug}.md`. If it doesn't exist yet, redirect to forge to create it first.

If the initiative has no research yet (no sources linked, research section empty), redirect the operator to do research first before shaping:

> "This initiative has no research yet. Let's gather some information first before shaping. What do you want to explore?"

## Protocol

### Step 1: ORIENT

Read the full initiative document and all linked sources.

```bash
# Read the initiative document
# Use Read tool on: ~/vault/knowledge/initiatives/{slug}.md

# Read all linked sources from frontmatter sources: field
# Each source is a path relative to vault -- read each one

# Also search for related vault content
~/.bun/bin/qmd query "{initiative topic}" -n 5
```

Distill the research into key findings. This is **lossless compression** -- capture every meaningful insight, don't summarize away nuance.

Present to operator:

> "Here's what I understand from the research:
>
> **Key findings:**
> 1. {finding}
> 2. {finding}
> 3. {finding}
>
> **Open questions from research:**
> - {question}
>
> Correct me if I'm missing something or have something wrong."

Wait for operator to confirm or correct before proceeding.

### Step 2: SHAPE (Socratic, one question at a time)

Ask these questions **in order, one at a time**. After each answer, lock the decision in the initiative document before asking the next question.

**a. Problem Definition**

> "What problem does this solve? Who has this problem, and what happens if we don't solve it?"

After answer: Write the **Problem** section in the initiative document.

**b. Appetite**

> "How much of your time is this worth? Pick one:
> - **2-day** -- small bet, quick experiment
> - **1-week** -- meaningful but bounded
> - **2-week** -- significant investment
> - **6-week** -- major initiative, high conviction required"

After answer: Set `appetite` in frontmatter and record as locked decision.

**c. Definition of Done**

> "What does done look like? When would you say 'this is complete, I can stop'?"

After answer: Write the **Definition of Done** section.

**d. Solution Direction**

> "What's the rough solution? Not the detailed design -- just the general approach. What are you going to build or do?"

After answer: Write the **Solution** section.

**e. Non-Goals (Scope Fence)**

> "What's explicitly out of scope? What might someone assume is included that you're deliberately excluding?"

After answer: Write the **Non-Goals** section.

**f. Risks and Open Questions**

> "What are the unknowns that could change everything? What assumptions are you making that might be wrong?"

After answer: Write the **Risks** section.

### Step 3: REVIEW

Present the complete shape as a structured summary:

```
## Shape Summary: {Title}

**Problem**: {one-line summary}
**Appetite**: {appetite}
**Done means**: {definition of done}
**Solution**: {one-line summary of approach}
**Non-goals**: {list}
**Risks**: {list}
**Locked decisions**: {count} decisions locked

Ready to review? I'll show the full document.
```

Then show the full initiative document content. Ask:

> "Does this look right? Anything to change before we lock the shape?"

Operator approves or requests specific changes. Apply any changes.

### Step 4: READINESS ASSESSMENT

After approval:

> "Is this ready to plan, or does it need more research?"

- **More research needed**: Keep status as `research`. Note what needs investigating. The operator will come back to shape after research.
- **Ready to plan**: Update status to `shaping` with a note that shaping is complete. Set routing to `plan`.

### Step 5: UPDATE DOCUMENT

Write all shaped content to the initiative document using the Edit tool.

Update frontmatter:
```yaml
status: shaping  # or stays research if more research needed
appetite: {chosen appetite}
updated: {today's date}
```

Add a progress entry:
```
- {today}: Shaping complete. Problem defined, appetite set to {appetite}, {N} decisions locked.
```

## Locked Decisions Format

Every decision made during shaping is appended to the **Decisions** section using this format:

```markdown
### {date}: {Decision Title}
**Decided**: What was decided
**Why**: The reasoning behind the decision
**Locked by**: operator
**Alternatives rejected**: What else was considered and why it was rejected
```

Locked decisions are **append-only**. The operator can unlock and revise a decision, but it requires explicit intent -- they must say something like "I want to revisit the decision about X" or "unlock the {decision} decision."

When a decision is revised, don't delete the original. Instead, add a new entry:

```markdown
### {date}: {Decision Title} (revised)
**Decided**: New decision
**Why**: Why we changed course
**Previous decision**: {date} -- {what was decided before}
**Locked by**: operator
```

## Rules

- **NEVER skip to planning or execution during shaping** -- this is the hard gate. No tasks, no code, no architecture decisions that belong in planning.
- **One question at a time** -- never batch multiple shaping questions. Wait for each answer before proceeding.
- **Preserve operator's words** -- when writing sections in the initiative document, use the operator's language. Don't over-formalize or rewrite their words into corporate-speak.
- **Don't lead the witness** -- ask open questions. Don't suggest answers unless the operator is stuck.
- **If operator is stuck**, offer examples from similar initiatives or common patterns, but always frame them as "some people do X" not "you should do X."
- **Appetite is a commitment** -- once set, it constrains everything downstream. Make sure the operator understands this. If the solution doesn't fit the appetite during planning, the solution gets cut, not the appetite expanded.
- **Research before shaping** -- if there's no research, don't try to shape. The operator needs input before they can make good decisions.
- **The initiative document is the artifact** -- everything from shaping goes into the document. If it's not in the document, it didn't happen.
