# Component Lifecycle Rule

When building, reviewing, or planning any new AOS component, always think in three layers:

1. **Framework (`~/aos/`)** — What ships. Code, templates, defaults. Pulled by `aos update`.
2. **Instance (`~/.aos/`)** — What's configured per machine. Operator settings, secrets (Keychain), feature flags. Written during onboarding or first activation.
3. **Runtime** — How it loads. Reads instance config → loads only active parts → runs. Missing config = graceful skip, not crash.

Before shipping anything new, verify: what ships, what configures, what happens at runtime, and what happens on a fresh install with no config.

## Atomic Migration Rule

**Any commit that moves, renames, removes, or restructures something the instance layer depends on MUST include the migration in the same commit.** No exceptions. No "migration coming later."

Instance-impacting changes include:
- Moving files/databases that `~/.aos/` or `~/.claude.json` reference
- Swapping integrations, MCP servers, or service wrappers
- Changing `sys.path` assumptions or import locations
- Renaming config keys or restructuring `accounts.yaml`, `operator.yaml`, etc.
- Adding new Keychain secrets or LaunchAgents

The migration goes in `core/infra/migrations/` in the **same diff**. The reconcile check (if needed) goes in the **same diff**. Ship-check will block if it detects instance-impacting paths without a new migration.

**Why:** v0.6.0 shipped framework changes that assumed instance state would magically follow. It didn't. Two subsystems broke silently. The agent writing the refactor must also write the bridge.

Full spec: `~/vault/knowledge/specs/component-lifecycle.md`
