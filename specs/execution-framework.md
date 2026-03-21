# Autonomous Execution Framework

## Purpose

Define how AOS agents select tools, execute tasks, recover from failures,
and improve over time — with the goal of removing the human from the loop
for all digital tasks.

## Principles

1. **Never excuse, always attempt.** Before escalating to the operator, exhaust all available approaches.
2. **Cheapest first.** Prefer zero-token methods (scripts, APIs, URI schemes) over token-consuming methods (OCR, vision, screenshots).
3. **Compile, don't repeat.** When the same task succeeds 3+ times with the same approach, compile it into a deterministic script.
4. **Fail forward.** When one approach fails, log why and try the next. The log itself becomes training data.
5. **Trust tracks reality.** Success raises trust. Failure lowers it. The system self-calibrates.

---

## Architecture

```
TASK (from user, bridge, cron, or agent)
        │
   ┌────▼─────┐
   │  PATTERN  │ ← Check bin/patterns/ for a compiled script
   │  CACHE    │   HIT  → run script (0 tokens) → done
   └────┬──────┘   MISS → continue ↓
        │
   ┌────▼─────┐
   │ CAPABILITY│ ← Look up config/capabilities.yaml
   │ MAP       │   Returns ordered list of approaches
   └────┬──────┘   e.g. [api, applescript, cli, steer, chrome]
        │
   ┌────▼─────┐
   │ EXECUTION │ ← Try each approach in order
   │ LOOP      │   On success → log + check pattern threshold
   └────┬──────┘   On failure → log reason, try next
        │
   ┌────▼─────┐
   │ ESCALATION│ ← All approaches exhausted?
   │ CHECK     │   Report what was tried + why each failed
   └────┬──────┘   Suggest what capability would fix it
        │
   ┌────▼─────┐
   │ PATTERN   │ ← Successful task logged to execution_log/
   │ LEARNER   │   3+ similar → compile to script
   └────┬──────┘   Script goes in bin/patterns/
        │
   ┌────▼─────┐
   │ TRUST     │ ← Update trust.yaml based on outcome
   │ UPDATE    │   Success in domain → trust++
   └──────────┘   Failure → trust-- (if was autonomous)
```

---

## Capability Map

File: `config/capabilities.yaml`

The capability map is structured data that tells the agent which tools apply
to which targets, in priority order. Each approach has a cost tier:

| Tier | Method | Tokens | Example |
|------|--------|--------|---------|
| zero | script, API, URI, CLI | 0 | `open "obsidian://daily"` |
| low | AppleScript, bash pipe | ~100 | `osascript -e '...'` |
| medium | Steer accessibility | ~500-1k | `steer see + steer click` |
| high | Steer OCR, Chrome MCP | ~1-2k | Screenshot + vision |
| very-high | Computer Use (full vision loop) | ~10-30k | Screenshot loop |

The agent always tries the cheapest approach first.

### Adding to the map

When a new app or interaction type is encountered:
1. The agent attempts available tools and records what works
2. On success, it adds the approach to capabilities.yaml
3. Future tasks benefit from the discovery

---

## Anti-Excuse Protocol

Before saying "I can't", "please do this manually", or "this requires
human intervention", the agent MUST:

1. **Check the capability map** for the target app/interaction
2. **Try at least 2 different approaches** from the map
3. **If no map entry exists**, try the generic fallback chain:
   - bash/CLI (is there a command-line tool?)
   - AppleScript (is it a native macOS app?)
   - Steer accessibility (can we read the UI tree?)
   - Steer OCR (can we see and click?)
   - Chrome web version (does a web alternative exist?)
4. **Log what was tried and why it failed**
5. **Only then escalate**, with a clear report

### Valid escalation reasons (exhaustive list)

- Physical action required (hardware button, cable, USB device)
- Biometric authentication (Face ID, fingerprint, hardware key)
- Legal/financial authorization (signing, payments, contracts)
- Operator explicitly restricted this action in trust config
- Action would violate security rules (secrets, destructive ops)
- All tool approaches genuinely exhausted (with evidence)

### Invalid escalations (anti-patterns)

- "I don't have access to..." → Check: do you? Try steer/chrome/API
- "This requires a GUI..." → You have Steer and Chrome
- "I can't interact with this app..." → Try accessibility, OCR, API, web version
- "Please log in manually..." → Try saved cookies, API tokens, keychain
- "I'm not able to..." → What tools did you try?

---

## Pattern Compiler

### How patterns are recognized

Every successful task execution is logged:

```yaml
# execution_log/2026-03-17.jsonl (one entry per task)
{
  "timestamp": "2026-03-17T14:30:00Z",
  "task": "open today's daily note in Obsidian",
  "task_hash": "sha256-of-normalized-task-description",
  "approach": "uri",
  "commands": ["open 'obsidian://daily'"],
  "tokens_used": 0,
  "success": true,
  "duration_ms": 1200
}
```

### Compilation threshold

When the same `task_hash` (or semantically similar tasks, clustered by
the agent) succeeds 3+ times with the same approach:

1. Extract the command sequence
2. Generate a shell script in `bin/patterns/`
3. Register it in `config/patterns.yaml` with the trigger description
4. Future matching tasks run the script directly (0 tokens)

### Pattern file format

```bash
#!/bin/bash
# Pattern: open-obsidian-daily
# Trigger: "open today's daily note" / "open daily note in obsidian"
# Compiled: 2026-03-17 from 3 successful executions
# Token savings: ~1500 tokens per invocation

open "obsidian://daily"
```

### Staleness detection

If a compiled pattern fails:
1. Mark it `stale` in patterns.yaml
2. Fall back to AI-driven execution
3. If AI succeeds with a new approach, recompile
4. If AI fails too, the pattern is deleted

---

## Trust Integration

The execution framework feeds into the Sulaimanic trust ladder:

### How trust changes

```yaml
# Automatic trust adjustments
trust_events:
  success:
    weight: +0.1
    cap: 3.0  # max trust level
  failure_with_fallback:
    weight: 0   # tried and recovered = neutral
  failure_escalated:
    weight: -0.2  # had to ask human
  failure_silent:
    weight: -0.5  # failed without noticing
```

### Domain-specific trust

Trust is tracked per domain, not globally:

```yaml
agents:
  main:
    trust_level: 2  # overall
    domains:
      web_automation: 3     # proven reliable
      desktop_native: 2     # mostly reliable
      electron_apps: 1      # still learning
      file_operations: 3    # proven
      api_integrations: 2   # mostly reliable
```

---

## Self-Improvement Mechanisms

### 1. Execution log analysis (weekly cron)

A cron job analyzes `execution_log/` weekly:
- Which tasks are repeated? → Compile candidates
- Which approaches fail most? → Capability map adjustments
- Which domains have lowest success rate? → Focus improvement
- Token spend by category → Optimization targets

### 2. Capability map growth

The map starts incomplete and grows organically:
- Agent discovers a new approach → adds it
- Approach stops working → marks it deprecated
- Better approach found → reorders priorities

### 3. Eval-driven skill improvement

Using the autoresearch pattern from Karpathy:
- Skills with <70% success rate trigger automatic optimization
- The system mutates the skill, runs evals, keeps improvements
- Binary eval criteria (did it work? yes/no) over subjective scoring

---

## File Layout

```
config/
├── capabilities.yaml      # Tool selection map
├── patterns.yaml           # Compiled pattern registry
└── trust.yaml              # Agent trust levels (existing)

bin/
└── patterns/               # Compiled scripts (0-token execution)
    ├── open-obsidian-daily.sh
    ├── check-bridge-health.sh
    └── ...

execution_log/              # Task execution history
├── 2026-03-17.jsonl
├── 2026-03-18.jsonl
└── ...

specs/
└── execution-framework.md  # This document
```
