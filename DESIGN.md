# AOS Design Language

Read this file before building any UI — dashboard pages, mobile screens, or web interfaces.

## Philosophy

Notion-inspired. Warm, not cold. Content-first. Minimal chrome.
The interface should feel like a tool you already know how to use.

## Colors

```css
/* Backgrounds */
--bg-page:     #FFFFFF;          /* Main content */
--bg-sidebar:  #F7F6F3;          /* Sidebar, table headers */
--bg-hover:    rgba(55,53,47,0.04);
--bg-active:   rgba(55,53,47,0.08);

/* Text — warm brown-black, NOT pure black */
--text:        #37352F;           /* Primary */
--text-secondary: rgba(55,53,47,0.65);
--text-tertiary:  rgba(55,53,47,0.45);
--text-faint:     rgba(55,53,47,0.3);

/* Borders — barely visible */
--border:       #E9E9E7;
--border-light: rgba(55,53,47,0.09);

/* AOS accent */
--orange:    #D9730D;
--orange-bg: #FAEBDD;

/* Tag pairs (text / background) */
Gray:   #787774 / #F1F1EF    Green:  #448361 / #EDF3EC
Brown:  #9F6B53 / #F4EEEE    Blue:   #337EA9 / #E7F3F8
Orange: #D9730D / #FAEBDD    Purple: #9065B0 / #F6F3F9
Yellow: #CB912F / #FBF3DB    Pink:   #C14C8A / #FAF1F5
Red:    #D44C47 / #FDEBEC
```

## Typography

```css
/* System font stack — no custom web fonts in content */
--font: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
```

| Element | Size | Weight | Line Height |
|---------|------|--------|-------------|
| Page title | 30px | 700 | 1.2 |
| Section title | 20px | 600 | 1.3 |
| Body text | 16px | 400 | 1.5 |
| Nav items | 14px | 500 | 1.3 |
| Small text | 14px | 400 | 1.5 |
| Tags/badges | 12px | 400 | 1.2 |
| Metadata/mono | 12px | 400 | 1.5 |

## Radius & Spacing

- Border radius: `3px` everywhere (buttons, cards, tags, inputs)
- Cards: `1px solid #E9E9E7`, no shadow (hover adds `background: rgba(55,53,47,0.02)`)
- Tags: `padding: 0 6px; height: 20px; border-radius: 3px`
- Buttons: `padding: 4px 12px; height: 28px; border-radius: 3px`
- Page padding: `48px` sides (desktop), `20px` (mobile)
- Content max-width: `900px`

## Sidebar

- Width: `240px`, background: `#F7F6F3`
- Items: `14px`, `500` weight, `5px 12px` padding, `3px` radius
- Hover: `rgba(55,53,47,0.04)` background
- Active: `rgba(55,53,47,0.08)` background
- Icons: `18px`, color `rgba(55,53,47,0.4)`
- Section labels: `12px`, `500` weight, `uppercase`, color `rgba(55,53,47,0.45)`
- Mobile: slides in from left as overlay with `rgba(0,0,0,0.3)` backdrop

## Components

**Status tags** — use the color pairs above:
```html
<span class="tag tag-green">active</span>
<span class="tag tag-yellow">stale</span>
<span class="tag tag-red">failed</span>
```

**Buttons** — minimal, no shadow:
```html
<button class="btn">Secondary</button>
<button class="btn btn-primary">Primary</button>
```

**Cards** — barely-there container:
```html
<div class="card">
    <div class="card-title">SECTION NAME</div>
    <!-- content -->
</div>
```

**Tables** — clean, no zebra striping:
```html
<table class="table">
    <thead><tr><th>Name</th><th>Status</th></tr></thead>
    <tbody><tr><td>Item</td><td><span class="tag tag-green">ok</span></td></tr></tbody>
</table>
```

## Rules

1. **No pure black.** Text is `#37352F`. Backgrounds are white or `#F7F6F3`.
2. **No heavy shadows.** Use borders. If a shadow is needed, use `rgba(15,15,15,0.1) 0 0 0 1px`.
3. **No pill-shaped elements.** Radius is `3px`, never `999px` or `50%`.
4. **No custom fonts in content.** Use the system font stack. Mono for data only.
5. **Opacity for hierarchy.** Use `rgba(55,53,47,X)` at 0.65/0.45/0.3 — not separate gray colors.
6. **Touch targets minimum 34px.** Especially on mobile.
7. **Content-first.** Max-width `900px`, centered, generous whitespace.
8. **Warm over cold.** When in doubt, warmer. Gray-beige over gray-blue.

## File Structure

```
static/
├── style.css          ← Shared CSS (variables, sidebar, cards, tags, tables)
├── app.js             ← Shared JS (SSE, utilities)
└── {page}.css         ← Page-specific styles (optional)

templates/
├── base.html          ← Layout shell (sidebar + content area)
├── dashboard.html     ← Command center
├── work.html          ← Tasks, projects, goals
├── agents.html        ← Agent cards
├── sessions.html      ← Session history
├── crons.html         ← Automations
├── conversations.html ← Message logs
└── logs.html          ← System logs
```

## Mobile

- Sidebar collapses to overlay (hamburger toggle)
- Grids collapse to single column at `768px`
- Font sizes stay the same — trust the viewport
- Side padding drops to `20px`
- Touch targets: `44px` minimum height for buttons/links
