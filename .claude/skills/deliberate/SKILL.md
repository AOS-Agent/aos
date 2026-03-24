---
name: deliberate
description: >
  Multi-perspective deliberation for high-stakes decisions. Dispatches parallel
  Advisor agents with different lenses, then synthesizes into a decision memo.
  Triggers on: /deliberate, 'help me decide', 'I need perspectives',
  'let's deliberate', or auto-triggered by gate skill when result is CONCERNS
  on a high-stakes initiative.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# Deliberate — AOS Initiative Pipeline

> Part of the **AOS Initiative Pipeline** — a suite of skills that turn ideas into tracked,
> phased, executed work. Related skills: forge, shape, plan, gate, deliberate.

Dispatch parallel Advisor agents with different perspective lenses to analyze a high-stakes decision, then synthesize their outputs into a structured decision memo for the operator.

## Protocol

### Step 1: FRAME -- Structure the Brief

Before any analysis can happen, the decision must be clearly framed. Gather or construct these four sections:

1. **Situation**: What exists today? What's the context? What led to this decision point?
2. **Stakes**: What happens if we choose wrong? What's at risk? (time, money, reputation, opportunity)
3. **Constraints**: Time available, resources, budget, technical limitations, dealbreakers
4. **Key Questions**: What specific questions must be answered to make this decision?

If the operator's request is vague, push back:

> "I need a clearer brief before we can deliberate effectively. Let me ask a few questions."

Then ask (one at a time):

1. "What's the decision you need to make? Be specific."
2. "What are the options you're considering?"
3. "What happens if you choose wrong? What's at stake?"
4. "What constraints are non-negotiable?"

**Reject vague briefs.** A brief like "should I use React or Vue?" is too thin. Push for: what's the project, what's the team's experience, what are the performance requirements, what's the timeline?

Once the brief is solid, present it back:

> "Here's the structured brief I'll send to the advisory panel:
>
> **Situation**: {situation}
> **Stakes**: {stakes}
> **Constraints**: {constraints}
> **Key Questions**: {questions}
>
> Does this capture the decision accurately?"

Wait for confirmation before proceeding.

### Step 2: SELECT PERSPECTIVE LENSES

Choose 3-5 perspective lenses based on the decision type. Each lens is a distinct analytical frame.

#### Strategic / Business Decisions

| Lens | Focus |
|------|-------|
| **Risk Assessor** | What could go wrong? Probability times impact. Worst-case scenarios. Mitigations. |
| **Scope Guardian** | Is this too big? Can we cut 40% and still win? Where's the minimum viable version? |
| **Value Challenger** | Does this actually matter? What's the ROI? Is there a simpler way to get the same outcome? |
| **Moonshot** | What if we 10x'd this? What's the ambitious version? What would we do with unlimited resources? |

#### Technical / Architecture Decisions

| Lens | Focus |
|------|-------|
| **Feasibility** | Can we actually build this with what we have? Skills, tools, time, infrastructure. |
| **Maintainability** | Will future-us hate past-us? Complexity budget. Operational burden. |
| **Simplicity** | What's the simplest thing that could possibly work? Where are we over-engineering? |
| **Performance** | What are the scaling implications? Where are the bottlenecks? What breaks at 10x load? |

#### Personal / Life Decisions

| Lens | Focus |
|------|-------|
| **Risk** | What's the worst case? Can you recover from it? What's the probability? |
| **Values Alignment** | Does this fit who you want to be? Does it align with your stated priorities? |
| **Opportunity Cost** | What are you NOT doing by choosing this? What doors close? |
| **10-Year View** | Will this matter in a decade? Is this a one-way door or a two-way door? |

#### Mixed / Custom

For decisions that don't fit neatly into one category, pick the most relevant lenses from any category. You can also create custom lenses if the decision warrants it.

Present the selected lenses to the operator:

> "For this decision, I'll dispatch these perspectives:
> 1. {Lens}: {one-line description of what it will focus on}
> 2. {Lens}: {one-line description}
> 3. {Lens}: {one-line description}
>
> Want to add, remove, or swap any perspective?"

Wait for confirmation.

### Step 3: DELIBERATE -- Dispatch Parallel Agents

Dispatch an Advisor subagent for each perspective lens. All run in parallel.

Each agent receives:
- The structured brief from Step 1
- Their specific perspective lens and instructions
- A directive to be specific, quantitative where possible, and to challenge assumptions

Example dispatch pattern (use the Agent tool for each):

**Agent 1: Risk Assessor**
```
You are the Risk Assessor on an advisory board deliberating a decision.

BRIEF:
{full structured brief}

YOUR PERSPECTIVE: Risk Assessment
Analyze this decision purely from a risk perspective:
- What could go wrong with each option? Be specific.
- For each risk: estimate probability (low/medium/high) and impact (low/medium/high/catastrophic).
- What's the absolute worst case for each option?
- What mitigations exist for the top risks?
- Which option has the better risk profile overall?

Be direct. Challenge assumptions. Be specific, not generic.
Limit your analysis to 300-500 words.
```

**Agent 2: {Lens}**
```
You are the {Lens} on an advisory board deliberating a decision.

BRIEF:
{full structured brief}

YOUR PERSPECTIVE: {Lens description}
{Specific questions and focus areas for this lens}

Be direct. Challenge assumptions. Be specific, not generic.
Limit your analysis to 300-500 words.
```

Dispatch all agents with `run_in_background: true` so they execute in parallel. Wait for all to complete before proceeding to synthesis.

### Step 4: SYNTHESIZE -- Create the Decision Memo

After all perspectives return, synthesize into a structured decision memo.

The memo structure:

```markdown
# Decision Memo: {Decision Title}
Date: {today}
Initiative: {initiative name, if applicable}

## Brief
{The structured brief from Step 1}

## Perspectives

### {Lens 1}
{Key points from this perspective, 3-5 bullets}

### {Lens 2}
{Key points from this perspective, 3-5 bullets}

### {Lens 3}
{Key points from this perspective, 3-5 bullets}

## Agreement Zones
Where all perspectives align:
- {point of agreement}
- {point of agreement}

## Tensions
Where perspectives conflict:
- {Lens A} says {X} but {Lens B} says {Y} because {reason for disagreement}

## Tradeoffs
| Option | Gains | Loses |
|--------|-------|-------|
| {Option A} | {what you gain} | {what you give up} |
| {Option B} | {what you gain} | {what you give up} |

## Recommendation
**Option**: {recommended option}
**Confidence**: {high / medium / low}
**Reasoning**: {why this option, given the tensions and tradeoffs}
**Key assumption**: {the assumption that, if wrong, would change this recommendation}

## Next Actions
Regardless of which option is chosen:
1. {action}
2. {action}
```

### Step 5: DECIDE -- Present to Operator

Present the decision memo to the operator. Keep it readable -- the operator should be able to absorb it in 2-3 minutes.

> "Here's the decision memo from the advisory panel. The recommendation is {option} with {confidence} confidence, primarily because {one-line reasoning}.
>
> {Full memo}
>
> What's your call?"

After the operator decides:

**If an initiative exists:**
- Append the decision to the **Decisions** section as a locked decision:
  ```markdown
  ### {date}: {Decision Title}
  **Decided**: {what was decided}
  **Why**: {operator's reasoning}
  **Locked by**: operator
  **Alternatives rejected**: {other options and why}
  **Deliberation**: {confidence} confidence recommendation was {option}. Operator chose {same/different}.
  ```

**If no initiative exists:**
- Save the decision memo to `vault/knowledge/decisions/{slug}-{date}.md` for future reference

**Pattern detection:**
After deliberation, check if any patterns emerged that should be captured as expertise:
- Recurring risk patterns
- Architectural preferences
- Decision-making heuristics the operator uses

If patterns are detected, suggest updating `vault/knowledge/expertise/` files:

> "I noticed a pattern: you consistently prioritize {X} over {Y} in {context}. Want me to note this as a decision-making preference?"

## Rules

- **Never skip the FRAME step** -- a vague brief produces garbage perspectives. Push back on vague requests.
- **Always dispatch at least 3 perspectives, maximum 5** -- fewer than 3 doesn't give enough coverage, more than 5 creates noise.
- **The operator always makes the final call** -- this is advisory, not automated. The recommendation is a suggestion, not a directive.
- **Keep the synthesis concise** -- the operator should read the memo in 2-3 minutes. Don't pad with filler.
- **Be honest about confidence** -- if the perspectives are split and there's no clear winner, say so. Low confidence is a valid and useful signal.
- **Dispatch agents in parallel** -- use `run_in_background: true` for all perspective agents. Don't run them sequentially.
- **Don't over-weight any single perspective** -- the synthesis should fairly represent all views, even if they conflict.
- **Log everything** -- if connected to an initiative, the decision and its context go into the initiative document. If standalone, it goes into the vault.
- **Pattern detection is optional** -- only suggest expertise updates if the pattern is clear and useful. Don't force it.
