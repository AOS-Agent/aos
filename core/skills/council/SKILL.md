---
name: council
description: >
  Convene a multi-agent council to deliberate on a high-stakes question via
  token-passing chat — agents address each other directly with @name, the chat
  lives in an append-only JSONL log, and the operator can inject at any time.
  Use this skill when a question deserves multiple sharp lenses, when you want
  cross-engagement between perspectives (not just parallel opinions), or when
  you want to stress-test a decision before locking it. Trigger on: /council,
  "convene a council", "deliberate with personas", "stress-test this decision",
  "get multiple lenses on X", or any high-stakes architectural / strategic
  question that would benefit from a structured cross-engaged debate.

  Distinct from `deliberate`: that skill dispatches parallel Advisor agents in
  one shot and synthesizes. This skill runs an ongoing chat across multiple
  turns where agents engage each other directly — closer to a real cabinet
  meeting. Use `deliberate` for quick multi-lens scans, `council` when the
  cross-engagement is the point.
allowed-tools: Bash, Read, Write
---

# Council — Multi-agent Deliberation via Token-Passing Chat

## When to use this skill

Use `council` when:
- The question is **architectural, strategic, or high-stakes** — not a quick lookup
- You want **multiple distinct lenses** (interfaces vs shipping vs safety vs vision)
- You want **cross-engagement** — agents addressing each other, building on or attacking the previous point — not just parallel one-shot opinions
- You want to **stress-test** a verdict by having the council attack its own work
- The decision deserves a **persistent transcript** for later review

Use `deliberate` instead when you just want a fast parallel scan of perspectives without the back-and-forth.

## What this replaces

Earlier councils ran in cmux panes with each persona as an interactive `cld` session. That pattern suffers from:
- Paste-buffer corruption (stacked pastes pile up in input)
- Completion-verb diversity (~15 different "thinking" verbs to detect)
- Phantom autosuggest (gray text that looks like typed input)
- Idle-state detection fragility

`council` eliminates all of these by getting agents out of cmux panes entirely. Each turn is a fresh `claude -p` invocation with persona + chat history. cmux becomes the viewer (`council tail -f`), not the runtime.

## Built-in personas

Four ship by default. Each has a sharp lens.

| Persona | Lens |
|---------|------|
| **architect** | Interfaces, contracts, layer boundaries — what each piece owns and what breaks if a layer is replaced |
| **builder** | What actually gets built, scope honesty, smallest end-to-end loop |
| **skeptic** | Failure modes, attack surface, trust drift, audit before action |
| **dreamer** | Texture, sovereignty, what the operator actually feels when the system works |

Add your own at `~/.aos/personas/<id>.md` — first line is the lens, rest is the body.

## Protocol

- Each turn ends with **one addressing tag**:
  - `@<persona>` — hand the token to a specific peer
  - `@all` — open the floor (oldest non-speaker takes it)
  - `@close` — call the question, adjourn
- Turn body is **2-5 sentences, prose** — keeps council velocity high
- Agents may **address each other inline** ("Builder, your X is wrong because...")
- Last message ends with `=== END ===` marker, stripped before storage

## CLI

### Convene
```bash
council convene "Your topic / question" \
  --personas architect,builder,skeptic,dreamer \
  --seed "Operator's opening — include the question verbatim" \
  --first architect \
  --rounds 8
```

The operator's `--seed` is the first message in the chat, addressed to `--first`. If you omit `--seed`, a generic opening is used. If you omit `--first`, the first persona in the list takes the token.

### Watch live
```bash
council tail -f                 # follow the most recent council
council tail <id> -f            # follow a specific one
```

### Operator interjection
```bash
council say "@skeptic, push harder on the dissent-flattening point"
```

Operator messages can land at any time — including while a council is running. The scheduler routes the operator's @name to the named persona on the next turn.

### Browse / review
```bash
council list                    # all past councils
council show                    # full transcript of the most recent
council show <id>               # specific one
council close                   # adjourn the current council
```

## Patterns that work

### Four-round structure
1. **Round 1 — open**: each persona answers an opening question from their lens
2. **Round 2 — cross-engage**: each responds to the others' Round 1 positions
3. **Round 3 — converge**: each gives one concrete recommendation
4. **Round 4 — stress-test**: each finds the single weakest assumption in the verdict, from their own lens

Round 4 routinely produces the sharpest insights of the whole council. Build it into every convening.

### Operator-in-the-loop
The operator stays involved. `council say` lets you push back, redirect, or close at any time. The system treats your interjections as signal, not friction.

### One council per question
Don't try to make one council debate multiple unrelated questions. Convene a new one when the topic shifts — past transcripts stay browseable.

## Data location

Each council lives at:
```
~/.aos/data/councils/<slug>/
├── chat.jsonl    # append-only message log
├── topic.txt     # the convening question
└── personas.txt  # ordered persona list
```

## Footguns to avoid

- **Don't conflate `council` with `deliberate`.** Deliberate is parallel; council is sequential cross-engaged chat. Different patterns.
- **Don't seed without the actual question in the body.** If your seed says "answer the question above" but the question lives in the topic metadata only, the agents will (correctly) refuse to fake an answer. Inline the question.
- **Don't run more than ~12 turns without an operator check-in.** Conversations drift. Use `council tail -f` to watch, `council say` to redirect.
- **Don't expect the agents to forget character.** They will hold their lens even when it's inconvenient. That's the feature.

## Implementation

Engine: `core/engine/council/`
- `chat.py` — append-only JSONL with fcntl lock
- `persona.py` — Persona loading, built-in + user-defined
- `scheduler.py` — token-passing + @all + @close routing
- `engine.py` — main loop using `claude -p` per turn

CLI: `core/bin/cli/council`

Runtime: `claude -p` (operator's existing Claude auth — no API key needed)
