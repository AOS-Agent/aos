# Initiative Pipeline — Full Protocol

Loaded on demand by the `work` skill (and Chief) whenever an initiative is being shaped, planned, gated, or executed. This is the single source of truth for the pipeline — chief.md only routes here.


Gate: only active when `operator.yaml → initiatives.enabled: true`. Skip entirely if absent.

`inject_context` pre-computes your full briefing at session start: tasks, initiatives, inbox, schedule, suggested focus. You never gather data — you read what's already in your context. Mid-session refresh: `python3 ~/aos/core/engine/work/cli.py briefing` (one command).

### Routing

When an initiative appears in your injected context, route based on its status:

| Status | Action |
|--------|--------|
| `research` | Ask if ready to shape. If yes, run **Shaping** below. |
| `shaping` | Continue shaping from where it left off. |
| Ready to plan | Dispatch Advisor for **Planning** below. |
| `executing` + `[interactive]` | Run step-by-step with operator in the loop. |
| `executing` + `[autonomous]` | Dispatch agent (worktree if code). Review result when done. |
| `executing` (no mode) | Ask operator: "Walk through this together, or should I handle it?" |
| Phase boundary | Dispatch Advisor for **Gate Check** below. |
| `review` | Dispatch Advisor for retrospective. |

**Execution modes** are set per-phase in the initiative doc:
```
### Phase 1: Schema Design [interactive]
### Phase 2: Build Components [autonomous]
```
If no mode specified, ask the operator. For autonomous dispatch, check trust level — only dispatch if agent's capability trust ≥ 2. Otherwise, fall back to interactive.

"What's next" / "what should I work on" → read injected context, present summary, let operator pick.

### Anti-Skip

Before any multi-session request without a tracked initiative — signals: multi-session scope, multiple components, research needed, outcome framing — ask: "This looks like initiative-level work. Track as an initiative?" If yes, create doc at `vault/knowledge/initiatives/{slug}.md` with status: research.

### Shaping (you run this — conversational)

One question at a time. Do NOT create tasks or code during shaping.

1. "What problem does this solve?"
2. "How much of your time is this worth?" (2-days / 1-week / 2-weeks / 6-weeks)
3. "What does done look like?"
4. "What's the rough solution?"
5. "What's explicitly out of scope?"
6. "What could blow up?"

Lock each answer in the initiative doc under Locked Decisions. After all 6: status → planning.

### Planning

Two options depending on complexity:

**Simple initiatives (3 or fewer phases):** Decompose directly using step-by-step SCOPE logic — propose phases with tasks, present for approval, create in work system.

**Complex initiatives:** Dispatch Advisor: "Read the initiative at {path}. Propose phases with tasks (30min-3hr each). Map dependencies. Assign wave numbers for parallelism. Return the structure."

On approval: create phase tasks via work CLI, update initiative doc, status → executing. Step-by-step handles execution of each phase — it creates a plan file, tracks parts, and syncs progress back to the initiative doc.

### Gate Check (dispatch to Advisor)

Dispatch Advisor with the transition-specific checklist:

| Transition | Checklist |
|-----------|-----------|
| research → shaping | Sources linked? Enough material to shape? |
| shaping → planning | Problem clear? Appetite set? Non-goals defined? Locked decisions present? |
| planning → executing | Every phase has tasks? Tasks have acceptance criteria? Fits appetite? No blocking questions? |
| phase N → phase N+1 | All phase N tasks done? No unresolved blockers? No scope creep? |
| executing → review | All phases complete? |

Dispatch: "Read the initiative at {path}. Run the gate check for {transition}. Validate each item in the checklist. Return PASS / CONCERNS / FAIL with specifics."

PASS → advance. CONCERNS → **auto-convene council in background** (see Council Auto-Dispatch below) — don't ask the operator to run anything. FAIL → fix first.

### Council Auto-Dispatch

High-stakes moments (gate CONCERNS, 2-week+ shaping, 4+ phase planning, mid-execution architecture changes, pre-ship review) auto-convene a council. Full trigger table, command, and interjection protocol: load the `council` skill (see its "Auto-Dispatch" section).

### Session Boundaries

**Session start:** Present active initiatives briefly. Stale (>3 days) → "Pick up or archive?"
**Session end:** `session_close.py` auto-updates initiative timestamps. Update checkboxes for completed tasks.

### Deviation Rules

- Scope additions → always ask operator
- Architecture changes → **auto-convene council** (see Council Auto-Dispatch); do not ask the operator to run it
- Task taking 2x estimate → pause and report
- 3 failed attempts → stop, document, move on
