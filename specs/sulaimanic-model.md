# The Sulaimanic Model — Agent Delegation Framework

## Source
Derived from the Quranic narrative of Prophet Sulaymān (عليه السلام) in Sūrah An-Naml (27).
This is the foundational delegation and orchestration model for the Mac Mini Agent system.

## Core Principle
The operator (principal) has direct authority over a self-organizing assembly of agents.
There is no single router or bottleneck. The assembly serves the principal.

---

## Two Dispatch Patterns

### Pattern 1 — أَيُّكُمْ (Open Assembly Call)

> يَـٰٓأَيُّهَا ٱلْمَلَؤُا۟ أَيُّكُمْ يَأْتِينِى بِعَرْشِهَا
> "O assembly, which of you will bring me her throne?" — An-Naml:38

The operator broadcasts a task to the assembly. Agents self-evaluate and self-nominate.
The best-fit agent takes the task. The operator doesn't need to know which agent is best —
the assembly sorts itself.

**When to use**: When you don't know (or don't care) which agent should handle it.
Describe the task → agents score themselves → highest-confidence agent steps forward.

**Quranic example**: The ʿIfrīt offers to bring the throne before Sulaymān stands.
The one with knowledge of the Book does it in the blink of an eye. The assembly
self-organized from brute force to elegance.

### Pattern 2 — ٱذْهَب (Direct Dispatch)

> ٱذْهَب بِّكِتَـٰبِى هَـٰذَا فَأَلْقِهْ إِلَيْهِمْ
> "Go with this letter of mine and deliver it to them." — An-Naml:28

The operator knows exactly which agent is needed and dispatches directly.
No assembly, no negotiation, no intermediary.

**When to use**: When you know the right agent for the job.
Name the agent → it executes → you verify based on trust level.

**Quranic example**: Sulaymān sends Hudhud directly with the letter to Sheba.
No committee, no routing — direct command.

---

## The Assembly — الْمَلَأُ

The assembly is not a single agent. It is the **mechanism** by which agents are addressed collectively.
The main agent (Opus) serves as the assembly coordinator — it receives tasks, evaluates which agent
should handle them, and delegates accordingly.

The assembly can:
- Receive broadcast tasks and route to the best-fit agent
- Be consulted for advice (main agent handles strategy/judgment directly)
- Surface which agents are available and their trust levels

---

## Agent Roles

### Engineer
**File**: `.claude/agents/engineer.md`
**Model**: sonnet
**Role**: Infrastructure, construction, installation
**Capabilities**: Homebrew packages, LaunchAgents, system config, Python/Node environments, service setup, Docker
**Trust principle**: Builds what is asked. Every build is inspected.

Autonomous worker for infrastructure tasks. Fire-and-forget: "install Redis",
"configure this LaunchAgent", "set up the Python environment."

### Ops
**File**: `.claude/agents/ops.md`
**Model**: haiku (cheap — cost optimization is the point)
**Role**: Health monitoring, service status, heartbeat reports
**Capabilities**: RAM/disk/CPU checks, LaunchAgent verification, log scanning, service health
**Trust principle**: Reports must always be verified before acting on them.

Runs on the cheapest model because health checks are mechanical.
Spawned in parallel during `/gm` for system health. The Python heartbeat
in `apps/bridge/heartbeat.py` handles cron-style checks without an LLM.

### Technician
**File**: `.claude/agents/technician.md`
**Model**: sonnet
**Role**: Messaging infrastructure — Telegram, WhatsApp, iMessage, bridge
**Capabilities**: Bot creation, topic management, bridge diagnostics, service repair, agent onboarding
**Trust principle**: Diagnoses before fixing. All changes logged.

Owns all messaging systems. When scope expands (new channels), this agent
absorbs them. Split only if tool permissions diverge.

### ʿIfrīt (عِفْرِيت) — The Trusted Autonomous
**Not a role — a trust level.**

Any agent that has proven itself قَوِىٌّ أَمِينٌ (strong and trustworthy) can reach
this status. At this level, the agent can execute within its domain without
asking for confirmation. Earned, never assigned at creation.

---

## The Trust Ladder — سُلَّمُ الثِّقَة

Trust is earned through consistent, verified performance.

### Level 1: سَنَنظُرُ (We Shall See)
- Default for all new agents and all background work
- Every output is reviewed before acting on it
- Agent must explain its reasoning

### Level 2: مُؤْتَمَن (Entrusted)
- Agent has a track record of correct outputs
- Can execute routine tasks; results are spot-checked
- May act within narrow, pre-approved boundaries

### Level 3: عِفْرِيت (Autonomous)
- Proven قَوِىٌّ أَمِينٌ — strong AND trustworthy
- Can act without confirmation within its domain
- Still accountable — actions are logged for review
- Can be demoted if trust is broken

### Promotion criteria
An agent advances when:
- It has completed N tasks without error (threshold TBD)
- Its domain-specific outputs have been verified correct
- The operator explicitly promotes it

### Demotion criteria
An agent is demoted when:
- It produces incorrect output that wasn't caught
- It acts outside its domain without authorization
- The operator explicitly demotes it

---

## Current State

System agents in `.claude/agents/`:
- **engineer** — Infrastructure, installation, configuration (sonnet)
- **ops** — Health monitoring, heartbeat (haiku)
- **technician** — Messaging infrastructure, bridge fixes (sonnet)
- **nuchay** — Nuchay project agent (sonnet)

Eliminated agents (absorbed into skills/main agent):
- Strategy/priorities → Main agent (Opus) handles directly
- Security scanning → `skill-scanner` skill
- Knowledge retrieval → `recall` skill

Trust levels tracked in `config/trust.yaml`. All agents currently at Level 1 (سَنَنظُرُ).

Both dispatch patterns are available via Claude Code's subagent system:
- **أَيُّكُمْ** — operator describes a task, main agent routes to best-fit agent
- **ٱذْهَب** — operator names a specific agent via `ask <agent> to...`

---

## Design Principles

1. **The operator is the principal.** The system serves, never overrides.
2. **No single point of failure.** No router bottleneck. Assembly self-organizes.
3. **Trust is earned.** Every agent starts at سَنَنظُرُ. Prove yourself to advance.
4. **Verify before trust.** سَنَنظُرُ أَصَدَقْتَ أَمْ كُنتَ مِنَ ٱلْكَـٰذِبِينَ
5. **Direct dispatch when you know.** Don't broadcast when a direct command is faster.
6. **The assembly advises, not commands.** أَفْتُونِى — "advise me", not "decide for me."
