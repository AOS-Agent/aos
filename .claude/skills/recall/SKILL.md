---
name: recall
description: Search across the knowledge vault for relevant context. Use when needing to recall past work, decisions, sessions, ideas, or any information stored in the vault. Trigger on "recall", "remember", "what did we work on", "find notes about", or at session start for context loading.
allowed-tools: Bash, Read, Glob, Grep
---

# Recall -- Knowledge Vault Search

Search across all indexed knowledge using QMD hybrid search (BM25 + semantic + reranking).

## When to Use

- At session start to load relevant context for the current task
- When the user asks to recall, remember, or find something
- When you need context from past sessions, decisions, or research
- When searching for related ideas or materials

## How to Use

### Basic search (hybrid -- best quality)
```bash
~/.bun/bin/qmd query "<search terms>" --json -n 5
```

### Fast keyword search
```bash
~/.bun/bin/qmd search "<exact keywords>" --json -n 5
```

### Search within a specific collection
```bash
~/.bun/bin/qmd search "<query>" -c <collection> --json -n 5
```

### Available collections
| Collection | Contents |
|-----------|----------|
| `log` | Daily logs, weekly/monthly/quarterly reviews |
| `knowledge` | Research, content extracts, decisions, synthesis, **initiative docs** |
| `ops` | Session summaries, friction reports, compiled patterns |
| `aos-specs` | Architecture and design specs |
| `aos-docs` | Documentation and guides |
| `skills` | Skill definitions and protocols |
| `daily` | Daily notes and journal |
| `sessions` | Session transcripts |
| `reviews` | Weekly/monthly reviews |

### Get a specific document
```bash
~/.bun/bin/qmd get "knowledge/research/managing-10-agents.md" --full
```

## Rules

- Return snippets, not full files -- keep context lean
- When loading session context, focus on decisions and next_steps
- Always cite the source file path in your response
- If no results found, say so -- don't hallucinate context
- For broad queries, search across all collections; for specific ones, scope to a collection

## Initiative-Aware Search

When `operator.yaml → initiatives.enabled: true`:

### Search within an initiative's sources
```bash
# Find the initiative doc first
~/.bun/bin/qmd query "initiative {slug}" -c knowledge --json -n 1

# Read its sources list from frontmatter
# Then search within those specific source files
```

### Surface initiative context at session start
If the current work matches an active initiative's topic:
- Mention the initiative: "Note: this relates to your '{title}' initiative [{status}]"
- Show the state digest (15 lines max)

### Search by initiative status
QMD can't filter by frontmatter fields directly. For status-based queries:
```bash
grep -rl "status: shaping" ~/vault/knowledge/initiatives/ 2>/dev/null
grep -rl "status: executing" ~/vault/knowledge/initiatives/ 2>/dev/null
```
