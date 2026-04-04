---
name: diagram
description: Generate professional architecture diagrams using D2 language. Produces high-quality SVG/PNG with AOS brand styling. Use when the user asks for system diagrams, architecture visualizations, flow charts, or any visual representation of how things connect.
---

# Diagram Skill

You generate professional-grade architecture diagrams using the D2 diagramming language. Your output should look like it belongs in a Stripe or Vercel engineering blog — clean, structured, visually distinctive.

## Tools

- **D2 CLI**: `d2 input.d2 output.svg` — installed at `/opt/homebrew/bin/d2`
- **Rendering**: SVG (primary), PNG (when requested)
- **Theme**: Dark Mauve (200) for dark mode, or custom AOS styling

## Rendering Command

```bash
# Architecture diagrams (nested containers) — use ELK layout
d2 --layout=elk --pad 60 input.d2 output.svg

# Simple flow diagrams — use dagre (default)
d2 --pad 60 input.d2 output.svg

# Hi-res PNG (2x scale)
d2 --layout=elk --pad 60 --scale 2 input.d2 output.png

# Sketch/hand-drawn mode
d2 --layout=elk --sketch --pad 60 input.d2 output.svg
```

**Layout engines**: Use `--layout=elk` for nested architecture diagrams (better container handling). Use default dagre for simple flows. TALA (best) is not available (paid license).

## D2 Preamble (copy into every diagram)

Every AOS diagram MUST start with this block for consistent branding:

```d2
vars: {
  d2-config: {
    theme-id: 200
    theme-overrides: {
      N1: "#0f1117"
      N2: "#1a1d27"
      N3: "#232733"
      N4: "#2e3340"
      N5: "#5c6070"
      N6: "#8b8f9a"
      N7: "#e8e9ed"
      B1: "#e8723a"
      B2: "#f0854f"
      B4: "#60a5fa"
      B5: "#34d399"
      B6: "#fbbf24"
    }
  }
}

classes: {
  svc: {
    style.fill: "#1a1d27"
    style.stroke: "#e8723a"
    style.border-radius: 8
    style.font-color: "#e8e9ed"
  }
  db: {
    shape: cylinder
    style.fill: "#232733"
    style.stroke: "#22d3ee"
    style.font-color: "#8b8f9a"
  }
  ext: {
    style.fill: "#232733"
    style.stroke: "#f472b6"
    style.font-color: "#f472b6"
  }
  agt: {
    style.fill: "#2e3340"
    style.border-radius: 20
    style.font-color: "#e8e9ed"
  }
  grp: {
    style.fill: "#1a1d27"
    style.stroke: "#2e3340"
    style.font-color: "#8b8f9a"
    style.border-radius: 8
  }
  dim: {
    style.stroke: "#2e3340"
    style.font-color: "#5c6070"
  }
}
```

Use classes: `node: Name {class: svc}` — service, `{class: db}` — database, `{class: ext}` — external, `{class: agt}` — agent, `{class: grp}` — group container, `{class: dim}` — dim connection.

## AOS Brand Theme

Apply these colors consistently. DO NOT use D2 defaults — always set explicit styles matching the AOS brand.

```
# Background/Container colors
Background:     #0f1117 (deep charcoal)
Surface:        #1a1d27 (cards/groups)
Surface Alt:    #232733 (nested containers)
Border:         #2e3340 (subtle dividers)

# Text
Primary text:   #e8e9ed (labels, titles)
Secondary text: #8b8f9a (descriptions)

# Accent
Orange:         #e8723a (primary services, CTAs)
Orange hover:   #f0854f (highlights)

# Status / Category colors
Info/API:       #60a5fa (sky blue)
Success/Up:     #34d399 (emerald green)
Warning:        #fbbf24 (amber)
Error/Down:     #f87171 (soft red)
Purple:         #a78bfa (agents, AI)
Pink:           #f472b6 (external services)
Cyan:           #22d3ee (data/storage)
```

## Agent Colors (from .claude/agents/ frontmatter)

Use these when depicting specific AOS agents:
- engineer: `#34d399`
- ops: `#60a5fa`
- technician: `#f43f5e`
- nuchay: `#38bdf8`

## D2 Syntax Reference

### Containers (groups)
```d2
group_name: Label {
  style.fill: "#1a1d27"
  style.stroke: "#2e3340"
  style.font-color: "#e8e9ed"
  style.border-radius: 8

  child: Child Node {
    style.fill: "#232733"
    style.stroke: "#e8723a"
    style.font-color: "#e8e9ed"
  }
}
```

### Connections
```d2
a -> b: Label {
  style.stroke: "#8b8f9a"
  style.font-color: "#8b8f9a"
}
# Bidirectional
a <-> b: Sync
```

### Icons (from icons.terrastruct.com)
```d2
telegram: Telegram {
  icon: https://icons.terrastruct.com/essentials%2F213-chat.svg
}
docker: Docker {
  icon: https://icons.terrastruct.com/dev%2Fdocker.svg
}
```

### Node Shapes
```d2
db: Database {shape: cylinder}
queue: Queue {shape: queue}
cloud: Cloud {shape: cloud}
user: User {shape: person}
```

### Multiple Boards (layers)
```d2
layers: {
  overview: { ... }
  detail: { ... }
}
```

## Output Convention

Save diagrams to:
- D2 source: `~/aos/docs/diagrams/{name}.d2`
- SVG output: `~/aos/docs/diagrams/{name}.svg`
- PNG output: `~/aos/docs/diagrams/{name}.png` (only when requested)

Always save both the `.d2` source (version-controllable) and the rendered output.

## Quality Guidelines

1. **Use nested containers** to show containment (Mac Mini > services > subcomponents)
2. **Color-code by category**: services=orange, data=cyan, external=pink, agents=purple
3. **Label all connections** with the protocol/method (HTTP, Telegram API, MCP, SSH, etc.)
4. **Include ports** on services (`:4096`, `:8880`, `:7600`)
5. **Show the operator** as a person shape connecting via Tailscale
6. **Group related services** visually
7. **Use direction: right** for horizontal layouts, **direction: down** for vertical
8. **Add tooltips** for complex nodes: `node.tooltip: "Detailed description"`
9. **Never use default D2 colors** — always apply the AOS brand palette
10. **Keep text concise** — labels should be 1-3 words, details go in tooltips

## Example: AOS System Overview

When asked for the AOS architecture, generate a comprehensive diagram showing:

**External Layer:**
- Operator (MacBook Pro / Phone) via Tailscale
- Telegram API (cloud)

**Mac Mini Layer:**
- Bridge (daemon) — connects Telegram ↔ Claude CLI
- Dashboard (:4096) — web UI
- Listen (:7600) — job server
- Memory (MCP) — ChromaDB semantic search

**Agent Layer:**
- Agent assembly (engineer, ops, technician + project agents)
- Claude Code as execution engine

**Data Layer:**
- macOS Keychain (secrets)
- SQLite (activity.db)
- ChromaDB (memory vectors)
- Git (config versioning)

After generating the D2 file, render it and show the user the output path. If in a context where you can open files, open the SVG for visual review.
