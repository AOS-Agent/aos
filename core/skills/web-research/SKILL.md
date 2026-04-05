---
name: web-research
description: "Research any topic from the web — search, crawl, evaluate, synthesize, and optionally save to vault. Trigger on: 'research this', 'find out about', 'what's the latest on', 'deep dive into', '/research', or any request requiring current web information beyond what's in the vault. NOT for social media URLs (use extract skill). NOT for simple factual lookups answerable in one WebSearch."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch
---

# Web Research Skill

Autonomous web research pipeline. Searches, crawls, evaluates sources, synthesizes findings, and saves vault-native artifacts.

## When to Use

- User asks to research a topic, technology, or question
- User needs current information not in the vault
- User says "deep dive", "find out about", "what's the latest on"
- Any research task that requires multiple web sources

## When NOT to Use

- Social media URLs → use `/extract` skill
- Simple "what is X?" → just use WebSearch directly
- Content already in vault → use `/recall` skill

## Procedure

### Step 1: Classify the request

| Type | Signals | Depth | Save to vault? |
|------|---------|-------|----------------|
| **Quick lookup** | "what's the latest", "current price of", "who won" | WebSearch + 1-2 pages | No — answer inline |
| **Focused research** | "research X", "find out about X", bare topic | Search + 3-5 pages | Yes — stage 2 capture |
| **Deep research** | "deep dive", "comprehensive analysis", "thorough research" | Search + crawl + 10+ pages | Yes — stage 3 research |

### Step 2: Search phase

Use multiple search strategies for coverage:

**Primary — WebSearch (always available, free):**
```
WebSearch("CRDT algorithms distributed systems")
```

**Semantic — Exa MCP (if configured):**
```
mcp__exa__search("conflict-free replicated data types implementation patterns")
```

**Multi-perspective queries (STORM pattern):**
Generate 2-3 query variations from different angles:
- Technical: "CRDT implementation algorithms"
- Practical: "CRDT real world use cases production"
- Comparative: "CRDT vs OT operational transform tradeoffs"

Collect all candidate URLs from results. Remove obvious junk (SEO farms, content mills, paywalled sites with no preview).

### Step 3: Triage URLs

Before crawling anything:

1. **Check vault for duplicates** — Search QMD to see if this topic is already researched:
   ```bash
   ~/.bun/bin/qmd query "CRDT algorithms" -c knowledge --json -n 3
   ```
   If good existing research exists, tell the user and ask if they want fresh research anyway.

2. **Score candidates:**
   - Primary sources (official docs, papers, author blogs) > aggregators > forums
   - Recent content > old content (for tech topics)
   - Max 2-3 pages per domain unless it's THE authoritative source

3. **Select 3-5 URLs for focused, 8-12 for deep research.**

### Step 4: Fetch content (escalation chain)

For each selected URL, use the cheapest tool that works:

**Level 1 — WebFetch (free, instant, always try first):**
```
WebFetch(url)
```
Works for most static pages. Returns AI-processed content.

**Level 2 — crawl4ai CLI (when WebFetch fails or returns garbage):**
Use when: JS-rendered pages, WebFetch returned truncated/garbled content, need raw markdown.
```bash
~/.aos/services/crawler/.venv/bin/python ~/aos/core/services/crawler/crawl_cli.py "URL" --format markdown
```

**Level 3 — crawl4ai structured extraction (for recurring data patterns):**
If extracting structured data (prices, specs, listings), check for existing schemas:
```bash
~/.aos/services/crawler/.venv/bin/python ~/aos/core/services/crawler/crawl_cli.py --schemas
```
If a matching schema exists, use it:
```bash
~/.aos/services/crawler/.venv/bin/python ~/aos/core/services/crawler/crawl_cli.py "URL" --schema schema-name --format json
```

**One-shot schema generation (10x pattern):**
If you need to extract the same structure from many pages (e.g., all products on a site), generate a CSS schema ONCE:
1. Crawl one example page with `--format html`
2. Analyze the HTML structure
3. Write a schema YAML and save to `~/.aos/data/crawler/schemas/`
4. Apply to all remaining pages at zero LLM cost

**Level 4 — Chrome MCP (last resort, for interactive/authenticated pages):**
Only if the page requires login, clicking through modals, or interaction.

### Step 5: Evaluate sources (CRAG pattern)

For each retrieved page, classify:

- **Correct** — Directly answers the question with evidence. Authoritative source.
- **Ambiguous** — Partially relevant, tangential, or needs corroboration from another source.
- **Incorrect** — Off-topic, outdated, contradicts reliable sources. Discard.

**Quality check:** If >50% of sources are Ambiguous or Incorrect:
- Refine search queries based on what you learned
- Search again with more specific terms
- One re-search cycle max — don't loop indefinitely

### Step 6: Synthesize

**Quick lookup:** Summarize directly in conversation. Cite the source URL.

**Focused research:** Produce a structured summary:
- Key findings (3-5 bullet points)
- Notable details
- Sources with quality assessment
- Open questions / areas for deeper research

**Deep research:** Produce a full research note:
- Executive summary
- Detailed findings organized by theme
- Contradictions between sources (flag explicitly)
- Confidence level per finding (high/medium/low based on source agreement)
- Source quality table
- Recommendations / next steps

### Step 7: Save to vault (focused and deep only)

**For focused research** — Save as capture (stage 2):
```
~/vault/knowledge/captures/YYYYMMDD-web-{topic-slug}.md
```

**For deep research** — Save as research (stage 3):
```
~/vault/knowledge/research/{topic-slug}.md
```

**Frontmatter template:**
```yaml
---
title: "Research: {Topic}"
type: research
date: "YYYY-MM-DD"
tags: [web-research, {topic-tags}]
stage: 3
source_ref:
  - url: "https://example.com/article"
    title: "Article Title"
    quality: correct
    accessed: "YYYY-MM-DD"
  - url: "https://example.com/other"
    title: "Other Source"
    quality: ambiguous
    accessed: "YYYY-MM-DD"
---
```

**After saving, trigger QMD reindex:**
```bash
~/.bun/bin/qmd update && ~/.bun/bin/qmd embed
```

## Advanced Patterns

### Site-wide research (two-phase crawl)

When researching an entire documentation site or wiki:

1. **Map first** — Discover all URLs quickly:
   ```bash
   ~/.aos/services/crawler/.venv/bin/python ~/aos/core/services/crawler/crawl_cli.py "https://docs.example.com" --map --max-pages 50
   ```

2. **Filter** — Select only relevant URLs from the map output.

3. **Extract selectively** — Crawl only the relevant pages:
   ```bash
   ~/.aos/services/crawler/.venv/bin/python ~/aos/core/services/crawler/crawl_cli.py "URL" --format markdown
   ```

### Deep crawl for comprehensive coverage

When the user wants everything on a topic from one site:
```bash
~/.aos/services/crawler/.venv/bin/python ~/aos/core/services/crawler/crawl_cli.py "URL" --deep bfs --max-pages 10
```

### Adaptive stopping

For deep research, check after every batch of 3-5 sources:
- Are the new sources saying the same things as previous ones? → Stop, you have saturation.
- Are new sources revealing major new themes? → Continue searching.
- Have all sub-questions from Step 2 been addressed? → Stop.

Don't crawl for the sake of crawling. Quality > quantity.
