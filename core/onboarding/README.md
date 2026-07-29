# AOS Onboarding

Conversational first-time setup. The flow lives in the **`onboard` skill**
(`core/skills/onboard/SKILL.md`), not here — this file is a map to the moving
parts.

## Trigger

Chief loads the skill when `~/.aos/config/onboarding.yaml` is **missing**.

The installer also drives it directly: `aos start` passes an explicit onboarding
prompt to Claude Code when that file is absent, and `install.sh` ends by
`exec aos start` for both roles.

> Both roles. Onboarding used to be gated on `ROLE == developer`, which meant a
> non-developer install finished by opening a browser tab and promising "Sahib
> will take it from here" — while nothing in Qareen ever started onboarding.

## Where things live

| Piece | Path |
|---|---|
| The flow | `core/skills/onboard/SKILL.md` |
| Persona / agent def | `templates/agents/onboard.md` |
| Live system inventory | `core/bin/cli/aos-snapshot` (`aos snapshot`) |
| Integration registry | `core/infra/integrations/registry.yaml` |
| Per-integration setup | `core/infra/integrations/*/setup.sh` (`--check` = health, no prompts) |
| Install report | `~/.aos/config/install-report.yaml` (written by the health gate) |
| Completion marker | `~/.aos/config/onboarding.yaml` |
| Developer-facing log | `~/.aos/logs/onboarding.md` |

## The rule that keeps it correct

**The skill never states a fact about the system. It reads `aos snapshot`.**

Counts, service names, integration lists, vault folders, which subsystems exist
— all of it comes from whatever declares it (the service registry,
`config/crons.yaml`, the integration manifests, the vault on disk, the instance
databases).

This is not stylistic. The skill previously hardcoded "12+ automated jobs" (a
large undercount), named seven available integrations out of 22 — telling
operators that Linear, Notion, and Slack weren't available when all three
shipped — and gave a vault tour listing four folders that no longer existed.
Every claim was true when written. Prose cannot track a filesystem, so it
stopped trying to.

`ship-check` enforces this — see its Onboarding Drift section.

## Resume

`onboarding.yaml` is written at the end. If a session dies mid-flow, the
onboarding tasks in the work system show how far it got.
