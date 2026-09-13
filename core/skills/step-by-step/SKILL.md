---
name: step-by-step
description: >
  Decompose a multi-part task and execute it one part at a time — scope with
  sizes and dependencies, a brief per part with runnable done-when criteria,
  verified execution, goal-backward polish. Trigger on "step by step", "one at
  a time", "do X properly", "build out X", "let's work through X", and
  proactively on any task with 3+ parts (migrations, multi-service setups,
  large refactors, system configs). "Walk me through" / "explain" wants
  understanding only — answer directly.
---

# Step by Step

Scope → brief → execute → verify → next, one part at a time. The operator stays in control, the work system holds the state, `sbs` renders and verifies so every run looks the same.

```
SBS="python3 ~/.claude/skills/step-by-step/scripts/sbs.py"     # scope · trail · criteria · verify · log
WORK="python3 ~/aos/core/engine/work/cli.py"                    # commands: references/tracking.md
```

Decisions use `AskUserQuestion` — at most 4 options, 2–5-word labels, detail in the question. Yes/no goes in prose.

## 1 · SCOPE

1. Decompose into **deliverables** — each part produces something concrete — sequenced by dependency, sized S/M/L. Split every L now; the operator never meets an L at a brief. Unfamiliar domain → `references/domain-examples.md`.
2. Add the **10x take**: what someone who has done this fifty times would build — the better approach, the mistake that hurts later, one concrete move. Opinionated, grounded in real tools. Their approach already best → say so. Nothing genuine → no block.
3. Present both in one message; ambiguous scope → one clarifying question first.

```
## Scope: [Task]
1. **[Part]** (S) — one line
2. **[Part]** (M) — one line · depends on 1

### 💡 10x take
Most people [X]. The better move is [Y] because [Z]. Watch out for [W].
**Move:** [one action] (+ scope impact)

Rhythm: as we go — say *plan first* for every brief up front.
```

`AskUserQuestion`: **Go** · **Go with the 10x move** · **Discuss**.

4. On approval, create the parent task and one subtask per part (`references/tracking.md`); show `$SBS scope <parent>`. Multi-session scope with no initiative → ask once: "Track as an initiative?"

## 2 · MAP — one brief per part

Open with `$SBS trail <parent> --current <part>` and a readiness signal: `⚡ Ready`, or `🔍 Needs research` — then research first, so the brief is written with the unknowns resolved.

**Context** what this part enables · **Problem** what needs solving, specifically · **Approach** the proper solution, its key trade-off, the tools involved · **Recommendation** only when there is a real choice · **Done when** — stored as you present it:

```
$SBS criteria <part> --size M \
  --set '$ curl -s http://127.0.0.1:4098/health :: 200' \
  --set '$ launchctl list | grep com.agent.logwatch' \
  --set 'notification appears on the phone and tapping it opens the thread'
```

A `$` line is a check the machine decides — `:: text` must appear in the output; without `::`, exit 0 passes. A prose line is a manual gate, for when no command can decide. Runnable and exhaustive beats descriptive.

`AskUserQuestion`, exactly these: **Go** · **Discuss** · **Split** · **Skip**. Anything else — a new order, folding two parts — arrives typed; act on it.

## 3 · EXECUTE

1. Build the proper solution; a temporary hack only when the operator agreed to one.
2. Parts with no dependency edge and an approved approach run in parallel: background agents (worktree if code) while the operator reviews the next brief — pattern in `dispatching-parallel-agents`.
3. Evidence is `$SBS verify <part>` output, verbatim. Every ❌ is fixed before the part closes; every 👁 confirmed by looking.
4. From Part 2 on, `$SBS verify <parent>` re-runs every finished part's criteria — the backward check. A regression stops the flow until it is green again.
5. Something breaks → stop, explain, propose the fix; the operator sees every failure before a retry.
6. `$WORK done <part>` — the engine cascades to parent and initiative — show the trail, open the next brief.

## 4 · POLISH

1. `$SBS verify <parent>` — all still green.
2. **Goal-backward:** restate the original request in one sentence, then use the result end-to-end as the operator would. A gap between "parts done" and "goal met" is the first line of the report, in bold.
3. Report **Gaps** (deferred, out of scope) · **Hardening** (edge cases, tests worth adding) · **10x reflection** (did the take land — one sentence) · **Dependencies created** (downstream updates).
4. `$SBS log --task … --parts N --mode as-we-go|plan-first --domain … --work-id <parent>`, then confirm the parent cascaded: `$WORK show <parent>`.

```
✅ Part 1  →  ✅ Part 2  →  ⏭ Part 3  →  ✅ Part 4   🏁 [Task]
```

## Leaving early · resuming

Any exit before POLISH writes the baton: `$WORK handoff <parent> --state … --next … --decisions …`. On "resume": `$WORK dispatch <parent>`, `$SBS scope <parent>`, confirm — "Parts 1–2 done, picking up at 3, [name]?" — open its brief. Criteria stored at MAP survive the gap; `verify` still runs them.

## Not this skill

Single actions · "just do it", "quick fix" — execute directly · pure research · "walk me through" — answer directly. "Skip the ceremony" mid-flow: run the remaining parts straight through, keeping verify and close.

## Resources

- `scripts/sbs.py` — no arguments prints usage
- `references/tracking.md` — work CLI: create, close, handoff, resume, initiative linking
- `references/domain-examples.md` — decompositions and criteria, six domains
