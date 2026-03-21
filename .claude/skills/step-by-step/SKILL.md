---
name: step-by-step
description: >
  Structured decomposition and execution workflow that prevents overwhelm by
  presenting one part at a time -- one context, one decision, one execution, then
  next. Use this skill whenever the user says "step by step", "one by one",
  "one at a time", "do X properly", "build out X", "set up X the right way",
  "let's work through X", or any multi-part task where dumping everything at
  once would overwhelm. Also trigger proactively when a task clearly has 3+
  parts even if the user doesn't explicitly ask -- infrastructure migrations,
  multi-service setups, large refactors, business strategy rollouts, system
  configurations. Do NOT trigger for "walk me through" or "explain" requests
  where the user just wants to understand something, not make decisions and
  execute.
---

# Step by Step -- Structured Decomposition & Execution

When a task has multiple parts, don't rush to execute. Decompose, explain, get buy-in, then execute one part at a time with verification. The goal is proper solutions with the operator in control.

## Phase 1: SCOPE

Analyze the request and decompose it into parts.

**What to produce:**
- An ordered list of parts, sequenced by dependency (not arbitrary order)
- Each part gets a **t-shirt size** (S/M/L) so the operator knows the weight
- Note dependencies between parts explicitly ("Part 3 needs Part 1's output")
- If the scope is genuinely ambiguous, ask **one** clarifying question -- not a list

**How to detect parts:**
- Read the domain. Infrastructure -> services/config/verification. Code -> architecture/implementation/testing. Business -> research/strategy/execution.
- Think in deliverables, not activities. Each part should produce something concrete.
- If a part is L-sized, flag it -- it may need to be split further.

**Format:**

```
## Scope: [Task Name]

1. **[Part Name]** (S) -- one-line description
2. **[Part Name]** (M) -- one-line description
   depends on: Part 1
3. **[Part Name]** (L) -- one-line description -- may need splitting
   depends on: Parts 1, 2
```

Wait for the operator to approve, adjust, reorder, or split before proceeding.

After scope approval, ask **one** question to set the rhythm:

> **Execute as we go, or plan everything first?**
> - **As we go** -- I present a part, you approve, I execute, then next part
> - **Plan first** -- I present all parts one by one for approval, then execute them all

Suggest a smart default based on domain:
- **Infrastructure / system config** -> suggest "as we go" (results from Part 1 often shape Part 2)
- **Business strategy / planning** -> suggest "plan first" (decisions are interconnected, better to see the full picture)
- **Code / refactoring** -> suggest "as we go" (each part needs verification before building on it)
- **Setup / migration** -> suggest "as we go" (sequential dependencies, things break)

Present the suggestion, not the reasoning: "I'd suggest as-we-go for this -- want that, or plan-first?"

## Phase 2: MAP (per part)

Before executing each part, present a brief explaining what you'll do and why.

**Header format:**

```
                    ┃ N of Total ┃
  ━━━━━━━━━━━━━━━━━━┛            ┗━━━━━━━━━━━━━━━━━━
  Part Name                                    🟢 S
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✅ Done  →  ● Current  →  ○ Upcoming  →  ○ Upcoming
```

Size dots: `🟢 S` · `🟡 M` · `🔴 L`

**Readiness signal** (right after header):
- `⚡ Ready` -- approach is clear, no unknowns, can execute immediately
- `🔍 Needs research` -- unknowns exist, need to investigate before committing

**Sections:**

**Context**: Why this part matters / what it enables

**Problem**: What needs solving (be specific)

**Approach**: What you'll do (proper solution, not bandaid)
- Key decision or trade-off, if any
- Tools/services involved

**Recommendation**: Your top pick if there's a meaningful choice to make
(skip this if there's only one reasonable approach)

**Done when**: Concrete, verifiable acceptance criteria. Not vibes, not "it works." Specific conditions that can be checked.

Examples of good criteria:
- `curl http://127.0.0.1:4096/health` returns 200
- `~/.aos-v2/logs/bridge.jsonl` contains entries with `"level"` and `"ts"` fields
- `launchctl list | grep com.agent.logwatch` shows running

Then ask:
- ✅ **Go** -- execute this part
- 🔀 **Reorder** -- do a different part first
- ✂️ **Split** -- this part is too big, break it down
- ⏭️ **Skip** -- drop this part
- 🔗 **Merge** -- combine with another part
- 💬 **Discuss** -- need to talk through the approach

## Phase 3: EXECUTE

After approval, do the work. Key rules:

1. **Proper solutions only.** No temporary hacks unless explicitly agreed.

2. **Verify against acceptance criteria.** Show evidence with blockquotes:

   ```
   > ✅ `curl :4096/health` → 200
   > ✅ `logs/bridge.jsonl` has valid JSON with "level" and "ts"
   > ❌ `launchctl list | grep logwatch` → not found
   ```

   If any criterion fails, stop and fix before moving on.

3. **Progress indicator.** Every message during execution starts with:
   ```
   ● Part Name                                    [N/Total]
   ───────────────────────────────────────────────────────────
   ```

4. **Backward verification (Parts 2+).** Re-run acceptance criteria from completed parts that could be affected. If a prior part breaks, stop immediately.

5. **If something goes wrong:** Stop. Explain. Propose a fix. Don't silently retry.

6. **Transition.** After completing a part, show the progress trail, then present next part's MAP:
   ```
   ✅ Health Endpoints  →  ● Watchdog Script  →  ○ Alerting  →  ○ Dashboard
   ```

## Phase 4: POLISH

After all parts are complete, do a final pass.

### Goal-Backward Check

Verify that the *original request* was actually achieved -- not just that tasks were completed.

1. Restate the original request in one sentence
2. For each part completed, check: does this contribute to the original goal?
3. Try to use the result end-to-end as the operator would
4. If there's a gap between "tasks completed" and "goal achieved" -- flag it clearly

### Final Review

```
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✅ Part 1  →  ✅ Part 2  →  ✅ Part 3  →  ✅ Part 4

  🏁 COMPLETE ─── [Task Name]
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Skipped parts show as: `⏭️ Part Name`

**Goal check**: Does the completed work deliver what was originally requested?

**Gaps**: Anything knowingly deferred or out of scope

**Hardening**: Error handling, edge cases, or tests worth adding

**10x Moves**: What would take this from done to exceptional

**Dependencies Created**: Anything downstream that needs updating

## Resumability

If a session ends mid-flow, the operator can say "resume step by step" and you should:
1. Read context to figure out which parts are done
2. Confirm: "Looks like Parts 1-3 are done. Picking up at Part 4 -- [Name]. Sound right?"
3. Continue from there

## When NOT to use this skill

- Simple, single-action requests ("restart the bridge", "check system health")
- Tasks where the operator explicitly wants speed over structure ("just do it", "quick fix")
- Pure research or information gathering

The operator is always in control. If they say "skip the ceremony, just execute" -- respect that.

## Usage Tracking

After completing a step-by-step flow, append a one-line entry to `~/.aos-v2/logs/step-by-step.jsonl`:

```json
{"date":"2026-03-21","task":"Redis setup","parts":4,"mode":"as-we-go","domain":"infrastructure","skipped":0,"splits":1}
```

## Bundled Resources

- `references/domain-examples.md` -- Example decompositions across 5 domains. Read when scoping a task in an unfamiliar domain.
