# Claude Code Configuration Rule

Claude Code uses **two separate config files** with different purposes. Never confuse them.

## Config Files

| File | Purpose | Managed by |
|------|---------|------------|
| `~/.claude/settings.json` | Harness settings: permissions, hooks, env vars, model, agent, MCP servers, effort level | Direct edit or `update-config` skill |
| `~/.claude.json` | Runtime preferences: UI toggles, OAuth state, feature flags set via `/config` | `/config` UI or careful direct edit |

These are **not interchangeable**. A key that belongs in one will be silently ignored in the other.

## Verified Settings

These have been tested and confirmed working:

| Feature | Key | File | Verified |
|---------|-----|------|----------|
| Chrome always on | `"chrome": true` | `~/.claude/settings.json` | 2026-03-26 |
| Remote Control all sessions | `"remoteControlAtStartup": true` | `~/.claude.json` | 2026-03-26 |
| Default agent | `"agent": "chief"` | `~/.claude/settings.json` | confirmed |
| Bypass permissions | `"permissions.defaultMode": "bypassPermissions"` | `~/.claude/settings.json` | confirmed |

## AOS Defaults

Every AOS installation must have both:

```jsonc
// ~/.claude/settings.json
{
  "chrome": true,
  "agent": "chief"
}

// ~/.claude.json (written during onboarding or via /config)
{
  "remoteControlAtStartup": true
}
```

Onboarding must write to **both** files.

## The Rule: Don't Assume, Verify

Claude Code settings are **not well-documented** in a single table. Many keys exist only in source code or are set by `/config` without being listed in the official settings docs.

When working with Claude Code configuration:

1. **Never guess a key name.** If you don't know the exact key, don't invent one.
2. **Check the docs first.** Fetch `https://code.claude.com/docs/en/settings` or the relevant feature page.
3. **If docs don't have it, test it.** Toggle via `/config`, then diff `~/.claude.json` to find the actual key.
4. **Document what you verify.** Add confirmed keys to the table above so we don't re-discover them.
5. **settings.json vs .claude.json is not obvious.** The `/config` UI writes to `~/.claude.json`. The `settings.json` file is for harness-level config. When in doubt, check both files before and after a change.
