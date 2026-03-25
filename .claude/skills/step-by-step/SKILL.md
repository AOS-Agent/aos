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

## Structured Choices

Claude Code has a native `AskUserQuestion` tool that presents clean, selectable options. **Use it at every decision point** in this skill instead of listing options as text. The operator taps a choice instead of typing.

Rules:
- Use `question` for the prompt, `options` for the choices
- Keep option labels short (2-5 words). Put detail in the question, not the options.
- Always include a freeform fallback — the tool allows typed responses alongside options
- Don't use AskUserQuestion for simple yes/no — just ask naturally in prose

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

After scope approval, use `AskUserQuestion` to set the rhythm:

```
AskUserQuestion(
  question: "I'd suggest [as-we-go/plan-first] for this. How do you want to flow?",
  options: ["As we go", "Plan first"]
)
```

- **As we go** -- present a part, approve, execute, next
- **Plan first** -- present all parts for approval, then execute

Smart defaults by domain:
- **Infrastructure / system config / setup / migration / code** -> suggest "As we go"
- **Business strategy / planning** -> suggest "Plan first"

## Phase 1.5: 10x RECOMMEND

After scope is approved but before executing anything, pause and think bigger. The operator asked for X — but what's the *best possible* version of X? What would someone who's done this 50 times recommend?

**What to produce:**

A short recommendation block (3-8 lines max) that covers:

1. **The 10x take** — Is there a fundamentally better approach than the obvious one? A different tool, pattern, architecture, or sequence that would make this 10x better?
2. **What most people get wrong** — The common mistake or shortcut that causes pain later
3. **The move** — One concrete recommendation that elevates the whole task

**When to include:**
- Always, unless the task is purely mechanical (e.g., "rename these 5 files")
- The recommendation should be grounded — not theoretical. Reference real tools, patterns, or approaches you know work.

**Format:**

```
### 💡 10x Recommendation

Most people [common approach]. The better move is [10x approach] because [why].

Watch out for [common mistake that causes pain later].

**Recommendation**: [One concrete, actionable suggestion].
```

**Rules:**
- Be opinionated. The operator wants your best judgment, not a menu of options.
- If the operator's approach is already the best one, say so: "Your approach is solid — no 10x upgrade needed here."
- If the 10x move changes the scope, flag it and let the operator decide: "This would add a Part 0 but save you from rebuilding later."
- Don't pad. If you don't have a genuine 10x insight, skip this phase entirely rather than writing filler.

After presenting, use `AskUserQuestion`:

```
AskUserQuestion(
  question: "Want to adopt this into the scope?",
  options: ["Adopt", "Note it", "Skip"]
)
```

Then move to MAP for Part 1.

## Phase 2: MAP (per part)

Before executing each part, present a brief explaining what you'll do and why.

**Header format:**

```
                    ┃ N of Total ┃
  ━━━━━━━━━━━━━━━━━━┛            ┗━━━━━━━━━━━━━━━━━━
  Part Name                                    🟢 S
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✅ Done  →  🔶 Current  →  ⬜ Upcoming  →  ⬜ Upcoming
```

Size dots: `🟢 S` · `🟡 M` · `🔴 L`

These markers match the initiative pipeline: `✅` complete, `🔶` in progress, `⬜` not started.

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
- `~/.aos/logs/bridge.jsonl` contains entries with `"level"` and `"ts"` fields
- `launchctl list | grep com.agent.logwatch` shows running

Then use `AskUserQuestion`:

```
AskUserQuestion(
  question: "Ready for [Part Name]?",
  options: ["Go", "Reorder", "Split", "Skip", "Merge", "Discuss"]
)
```

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
   ✅ Health Endpoints  →  🔶 Watchdog Script  →  ⬜ Alerting  →  ⬜ Dashboard
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

Skipped parts show as: `⏭ Part Name`

**Goal check**: Does the completed work deliver what was originally requested?

**Gaps**: Anything knowingly deferred or out of scope

**Hardening**: Error handling, edge cases, or tests worth adding

**10x Reflection**: Did the 10x recommendation from Phase 1.5 land? If adopted, did it pay off? If skipped, would it have helped? One sentence.

**Dependencies Created**: Anything downstream that needs updating

## Work System Integration

Step-by-step tracks all work through the work system. No separate plan files — the work system IS the tracking, the resume state, and the source of truth.

### At SCOPE (after operator approves parts)

Create a parent task and subtasks:

```bash
# Create parent task for the whole step-by-step flow
python3 ~/aos/core/work/cli.py add "{Task Name}" --project {project}
# Note the returned ID (e.g., aos#15)

# Create subtasks — one per part
python3 ~/aos/core/work/cli.py subtask aos#15 "Part 1: {name}"
python3 ~/aos/core/work/cli.py subtask aos#15 "Part 2: {name}"
python3 ~/aos/core/work/cli.py subtask aos#15 "Part 3: {name}"
```

If this work belongs to an initiative phase, add `source_ref` when creating the parent:
```bash
python3 ~/aos/core/work/cli.py add "{Task Name}" --project {project} --source-ref "vault/knowledge/initiatives/{slug}.md"
```

Show the created structure to the operator:
```
  Scope: {Task Name}                              [aos#15]

  ⬜ 1. {Part 1} (S)                              [aos#15.1]
  ⬜ 2. {Part 2} (M)                              [aos#15.2]
  ⬜ 3. {Part 3} (L)                              [aos#15.3]
```

### At EXECUTE (after each part completes)

Mark the subtask done. This is mandatory — run this command after verifying the part's acceptance criteria:

```bash
python3 ~/aos/core/work/cli.py done aos#15.1
```

The engine handles everything downstream:
- Subtask marked done
- When ALL subtasks done → parent auto-completes (cascade)
- If parent has `source_ref` → initiative checkbox auto-updates
- Dashboard gets a live event

You do NOT need to manually update initiative docs, plan files, or dashboards. The engine does it.

### At POLISH

Verify the parent task cascaded:
```bash
python3 ~/aos/core/work/cli.py show aos#15
```

If it shows `status: done` with `auto_completed: true`, everything synced. If not, mark it done manually:
```bash
python3 ~/aos/core/work/cli.py done aos#15
```

## Resumability

If a session ends mid-flow, the operator can say "resume" or "continue" and you should:
1. Read your injected context — active tasks with subtask status are already there
2. Or run `python3 ~/aos/core/work/cli.py show {parent-id}` to see which subtasks are done
3. Confirm: "Parts 1-3 are done. Picking up at Part 4 — {name}. Sound right?"
4. Continue from MAP for the next uncompleted part

The work system IS the resume state. No files to read, no context to recover.

## When NOT to use this skill

- Simple, single-action requests ("restart the bridge", "check system health")
- Tasks where the operator explicitly wants speed over structure ("just do it", "quick fix")
- Pure research or information gathering

The operator is always in control. If they say "skip the ceremony, just execute" -- respect that.

## Usage Tracking

After completing a step-by-step flow, append a one-line entry to `~/.aos/logs/step-by-step.jsonl`:

```json
{"date":"2026-03-25","task":"Auth system","parts":4,"mode":"as-we-go","domain":"code","skipped":0,"splits":1,"initiative":"nuchay-app","phase":1}
```

The `initiative` and `phase` fields are included when the work is initiative-linked, omitted otherwise.

## Bundled Resources

- `references/domain-examples.md` -- Example decompositions across 5 domains. Read when scoping a task in an unfamiliar domain.
