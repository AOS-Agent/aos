# AOS Project — .claude/ Reference

This file provides Claude Code with the .claude/ structure for this project.
For system architecture, see the root `CLAUDE.md`. For the global kernel, see `~/.claude/CLAUDE.md`.

## Harness Model

AOS is a configured Claude Code harness. Claude Code is the runtime.

```
~/.claude/CLAUDE.md   = Global kernel (loaded every session, everywhere)
~/aos/CLAUDE.md     = System details (loaded when working on AOS)
.claude/agents/       = Agent definitions (dispatched via Agent tool)
.claude/skills/       = Knowledge modules (loaded on demand)
.claude/rules/        = Conditional policies (loaded by path/context)
.claude/hooks/        = Deterministic handlers (no LLM, no tokens)
```

## Agent Installation

System agent source: `core/agents/` (chief.md, steward.md, advisor.md)
Catalog templates: `templates/agents/` (engineer.md, developer.md, etc.)
Active agents: `~/.claude/agents/` (installed during setup)

Install mechanism: **copy** from source to `~/.claude/agents/`.
Copied (not symlinked) so the system repo can be safely updated without breaking active agents.
Catalog agents track their source via frontmatter: `_source: catalog/engineer@1.0`

## Skills

Skill source: `~/aos/.claude/skills/` (development).
Active skills: `~/.claude/skills/` (installed globally, available in every session).

Installed globally (not project-scoped) because agents are global — Chief needs
skills regardless of which directory the session is in.

| Skill | Trigger | What |
|-------|---------|------|
| `recall` | "recall", "remember", "find notes about" | QMD vault search |
| `step-by-step` | "step by step", "build out X", "set up X properly" | Structured decomposition |
| `work` | "/work", "add task", "show my tasks", "mark X done" | Task/project/goal management |
| `review` | "/review", "daily summary", "weekly review" | Work reviews and reflections |

## Integration Framework

Integration manifests in `core/integrations/`. Each integration directory has a `manifest.yaml`
declaring what it provides, requires, and how to verify health.
Registry at `core/integrations/registry.yaml` lists all known integrations.
