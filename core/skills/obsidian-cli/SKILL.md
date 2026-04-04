---
name: obsidian-cli
description: >
  Interact with Obsidian vaults using the `obsidian` CLI — read, create, append,
  search, manage tasks, tags, properties, backlinks, bases, and daily notes.
  Also supports plugin dev (screenshots, DOM, eval, errors). Use when the user
  asks anything about their Obsidian vault, notes, daily notes, tasks, tags, or
  vault operations. Prefer this over GUI automation or REST API for most vault work.
---

# Obsidian CLI

Use the `obsidian` CLI to interact with the running Obsidian instance. Requires Obsidian to be open.

Run `obsidian help` for the full command list — it's always up to date.

## Syntax

Parameters take `=`, flags are bare:

```bash
obsidian create name="My Note" content="# Hello" silent
obsidian append file="My Note" content="New line"
obsidian search query="search term" limit=10
```

- `file=<name>` — resolves like a wikilink (name only, no path/extension)
- `path=<path>` — exact path from vault root (e.g. `daily/2026-03-17.md`)
- `vault=<name>` — target specific vault (default: most recently focused)
- `silent` — don't open the file in Obsidian
- `--copy` — copy output to clipboard

## Common Operations

### Notes
```bash
obsidian read file="My Note"              # Read note content
obsidian create name="New" content="..."  # Create note
obsidian append file="My Note" content="..." # Append to note
obsidian delete path="folder/note.md"     # Delete note
obsidian move file="Old" to="new-folder"  # Move/rename
obsidian open file="My Note"              # Open in Obsidian
```

### Daily Notes
```bash
obsidian daily                            # Open today's daily note
obsidian daily:read                       # Read daily note content
obsidian daily:append content="- New item"  # Append to daily note
obsidian daily:prepend content="# Morning" # Prepend to daily note
obsidian daily:path                       # Get daily note file path
```

### Search & Discovery
```bash
obsidian search query="term" limit=10     # Full-text search
obsidian tags sort=count counts           # List tags with counts
obsidian backlinks file="Note"            # Show what links to this note
obsidian links file="Note"                # Show outgoing links
obsidian orphans                          # Notes with no incoming links
obsidian deadends                         # Notes with no outgoing links
obsidian unresolved                       # Broken links
```

### Tasks
```bash
obsidian tasks todo                       # All unchecked tasks
obsidian tasks done                       # All completed tasks
obsidian tasks daily todo                 # Tasks in daily note
obsidian task toggle line=5 file="Note"   # Toggle task checkbox
```

### Properties (YAML frontmatter)
```bash
obsidian property:set name="status" value="done" file="Note"
obsidian property:get name="status" file="Note"
```

### Bases (Obsidian databases)
```bash
obsidian bases                            # List all base files
obsidian base:views                       # List views in active base
obsidian base:query                       # Query active base
```

### Plugin Development
```bash
obsidian plugin:reload id=my-plugin       # Reload after code change
obsidian dev:errors                       # Check for errors
obsidian dev:screenshot path=shot.png     # Take screenshot
obsidian dev:dom selector=".workspace"    # Inspect DOM
obsidian dev:console level=error          # Console errors
obsidian eval code="app.vault.getFiles().length"  # Run JS
```
