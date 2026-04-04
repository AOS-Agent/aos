---
name: architect
description: >
  Governance framework for deciding whether a new capability should be an agent,
  a skill, or handled by the main agent directly. Use this skill BEFORE creating
  any new agent or skill. Triggers on: "I need the system to do X", "should this
  be an agent?", "add capability for X", "create an agent for X", "we need a new
  agent", "should I make a skill for this?". Also activates when the skill-creator
  skill is about to be used — architect runs first as a gatekeeper to prevent
  agent/skill sprawl. Use proactively whenever you're about to create something
  new in .claude/agents/ or .claude/skills/.
---

# Architect — Capability Design Framework

You're about to add a new capability to AOS. Before writing any code or creating any files, run through this framework to determine the right form factor: **agent**, **skill**, **main agent**, or **extend existing**.

## The Company Model

Think of AOS as a small company:

- **Main agent (Opus)** = the CEO. Makes judgment calls, talks to the user, synthesizes results, handles strategy. Never delegates thinking to someone less capable.
- **Permanent agents** = staff with specific jobs. They exist because the CEO genuinely shouldn't do this work (it's beneath them, they need to be doing something else at the same time, or it requires restricted access).
- **Temporary agent spawns** = contractors. Spun up for a specific task, report back, gone. No persistent identity needed.
- **Skills** = playbooks and reference manuals. Knowledge the CEO loads when they need it. Doesn't spawn anyone — just makes the CEO smarter for the current task.

The default is: **the main agent does it directly.** Everything else needs justification.

## Step 1: Check What Already Exists

Before creating anything new, read the current roster:

```bash
# Current agents
ls .claude/agents/

# Current skills
ls .claude/skills/
```

Current system agents and their domains:
- **engineer** (sonnet) — infrastructure, installation, system configuration, LaunchAgents
- **ops** (haiku) — health monitoring, heartbeat, service status checks
- **technician** (sonnet) — messaging infrastructure (Telegram, WhatsApp, iMessage, bridge)

Project agents (one per project):
- **nuchay** (sonnet) — Nuchay project work in ~/nuchay
- **chief** (opus) — Chief iOS/macOS app in ~/chief-ios-app (XcodeBuildMCP, SwiftUI, TestFlight)

If the new capability fits within an existing agent's domain, **extend that agent** rather than creating something new. An agent's scope grows naturally until its tool permissions or model requirements diverge from its current setup.

**Extension signals:**
- "We need WhatsApp management" → technician already owns messaging. Extend.
- "We need Slack integration" → technician. Extend.
- "We need to monitor Phoenix" → ops already monitors services. Extend.
- "We need to set up a new Docker service" → engineer. Extend.

**Split signals (when to NOT extend):**
- The new work needs a different model (e.g., adding haiku work to a sonnet agent)
- The new work needs fundamentally different tool permissions (e.g., adding write access to a read-only agent)
- The agent's context would become so large it degrades performance

## Step 2: Run the Decision Tree

For each new capability, ask these questions in order. The first YES determines the form factor.

```
1. Does an existing agent or skill already cover this?
   YES → EXTEND it. Stop here.
   NO  → continue

2. Is this JUDGMENT or STRATEGY work?
   (priorities, goal alignment, user-facing decisions, creative work,
    code review, architectural choices)
   YES → MAIN AGENT. Opus is better at judgment than any sub-model.
         Never delegate thinking down.
   NO  → continue

3. Is this AUTONOMOUS work that runs without human oversight?
   (cron jobs, background processing, fire-and-forget tasks)
   YES → Is it mechanical/cheap?
         YES → AGENT on haiku
         NO  → AGENT on sonnet
   NO  → continue

4. Does this need PARALLEL execution alongside other work?
   (health check during /gm, multiple installs simultaneously)
   YES → AGENT (for context isolation during parallel spawns)
   NO  → continue

5. Does this need RESTRICTED tools (security boundary)?
   (read-only access, no network, no write permissions)
   YES → AGENT with limited tool list
   NO  → continue

6. Is this METHODOLOGY or DOMAIN KNOWLEDGE?
   (debugging approach, API reference, deployment checklist,
    "how to do X properly")
   YES → SKILL. Inject it into main agent's context.
   NO  → continue

7. Is this a ONE-OFF complex task?
   YES → Temporary agent spawn (Explore agent, or generic subagent).
         No permanent file needed.
   NO  → MAIN AGENT handles it directly. No new artifact needed.
```

## Step 3: Validate Your Verdict

Before proceeding, sanity-check against these anti-patterns:

**Red flags that you're creating an unnecessary agent:**
- The agent would use the same model and tools as the main agent
- The agent's work requires back-and-forth with the user (that's the main agent's job)
- The agent would be used less than once a week (make it a skill instead)
- The agent's entire job is "search for something and return it" (that's a skill + tool call)
- You're creating it because the domain feels important, not because delegation is justified

**Red flags that you're creating an unnecessary skill:**
- The skill would contain fewer than 20 lines of useful instruction
- The knowledge is already in CLAUDE.md or a spec file
- The skill would only be used by one specific command (put the knowledge in the command instead)

## Step 4: Output Your Verdict

State clearly:

```
VERDICT: [agent / skill / main-agent / extend-existing]
TARGET: [which agent/skill to extend, or new name]
REASON: [one sentence — which decision tree question triggered this]
NEXT STEP: [what to do now]
```

**If AGENT:** Create the `.md` file in `.claude/agents/` with proper frontmatter (name, description, role, color, scope, tools, model). Update `config/trust.yaml`. Update `CLAUDE.md` agent table.

**If SKILL:** Hand off to the `skill-creator` skill to build it properly with testing.

**If EXTEND:** Read the existing agent/skill file, identify what to add, make the edit.

**If MAIN-AGENT:** No artifact needed. If the user needs guidance, add a note to CLAUDE.md or the relevant command.

## Spawning Agent Teams

When the main agent faces 2+ independent tasks, it can spawn multiple agents in parallel. This doesn't require creating new permanent agents — it uses existing ones or temporary spawns.

**Pattern: Parallel data gathering (e.g., /gm)**
```
Main agent (Opus):
  → spawn ops (haiku): "check system health"        ── parallel
  → spawn engineer (sonnet): "verify LaunchAgents"   ── parallel
  → main reads goals.yaml, daily notes inline         ── parallel
  → wait for results → synthesize briefing
```

**Pattern: Multiple infrastructure tasks**
```
Main agent:
  → spawn engineer: "install Redis"         ── parallel
  → spawn engineer: "configure LaunchAgent"  ── parallel
  → spawn engineer: "set up uv environment"  ── parallel
  → wait for all → report results
```

Same agent definition, multiple instances. Each spawn is a fresh subprocess.

**Pattern: Research + build**
```
Main agent:
  → spawn Explore agent: "find all references to X"  ── parallel
  → spawn engineer: "set up the new service"          ── parallel
  → wait → integrate findings into the build
```

**When NOT to parallelize:**
- Tasks depend on each other (install A before configuring B)
- The work requires user interaction (that's the main agent's job)
- There's only one task (subprocess overhead isn't worth it)
- The task is judgment-heavy (Opus should do it, not delegate down)

## Principles

1. **The main agent is the strategist.** It reads goals, makes priority calls, advises the user. This is Opus-level work — never delegate judgment to Sonnet or Haiku.

2. **Agents are workers, skills are knowledge.** If you're sending someone away to do work → agent. If you're learning how to do it yourself → skill.

3. **Three is probably enough.** Most systems need: someone to build (engineer), someone to monitor (ops), and someone to fix the specialized stuff (technician). Add more only with strong justification.

4. **Skills prevent agent sprawl.** When in doubt, make it a skill. Skills are lighter, require no trust configuration, and don't fragment the system. An agent you rarely use is worse than a skill you load when needed.

5. **Extend before creating.** The technician started as "Telegram doctor" and now owns all messaging. That's healthy growth. Creating a new agent per messaging platform would be fragmentation.

6. **Chain skills, don't merge them.** When a workflow involves multiple steps (e.g., morning brief → meeting prep → research), keep each step as its own skill and chain them via references (`**REQUIRED SUB-SKILL:**` or `**Related skills:**`). Don't combine unrelated processes into a monolithic skill just because they run together.
