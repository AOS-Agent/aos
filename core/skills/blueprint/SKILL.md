---
name: blueprint
description: "Extract a website's complete design system into a Stitch-format DESIGN.md file. Captures colors, typography, spacing, shadows, component patterns, responsive behavior, and interaction states. Trigger on: /blueprint, 'extract the design', 'get the design system from', 'what design tokens does this site use'. Provide target URL as argument."
argument-hint: "<url>"
user-invocable: true
allowed-tools: Bash, Read, Write, Glob
---

# Blueprint — Design System Extraction

Extract the complete visual design system from **$ARGUMENTS** and output a Stitch-format DESIGN.md.

## Pre-Flight

1. Validate the URL is accessible
2. Create a dev-browser session: `dev-browser --browser blueprint-$(date +%s)`
3. Navigate to the target URL
4. Create output directory if needed

## Phase 1: Global Extraction

### Screenshots
Take full-page screenshots at three widths:
```bash
dev-browser --browser $SESSION <<'EOF'
const page = await browser.getPage("main");
await page.setViewportSize({ width: 1440, height: 900 });
await saveScreenshot(await page.screenshot({ fullPage: true }), "desktop.png");
await page.setViewportSize({ width: 768, height: 1024 });
await saveScreenshot(await page.screenshot({ fullPage: true }), "tablet.png");
await page.setViewportSize({ width: 390, height: 844 });
await saveScreenshot(await page.screenshot({ fullPage: true }), "mobile.png");
console.log("done");
EOF
```

### Design Token Extraction
Run the Global Design Token Extraction script from the Reverser agent's body via `page.evaluate()`. This captures:
- All colors with usage context (text, bg, border)
- Font families
- Type scale (size/weight/line-height combinations with frequency)
- Box shadows
- Border radii
- Body defaults

### Component Pattern Sampling
For each major UI pattern (buttons, cards, inputs, nav, badges), find representative elements and extract their full computed styles. Note:
- All interactive states (hover, active, focus, disabled)
- Variants (primary button vs secondary vs ghost)
- Size variations

### Font Discovery
```javascript
// Extract font loading strategy
(function() {
  const links = [...document.querySelectorAll('link[href*="fonts"]')].map(l => l.href);
  const fontFaces = [...document.styleSheets].flatMap(ss => {
    try { return [...ss.cssRules].filter(r => r instanceof CSSFontFaceRule).map(r => r.cssText); }
    catch(e) { return []; }
  });
  return JSON.stringify({ links, fontFaces }, null, 2);
})()
```

### Responsive Behavior
At each viewport width (1440, 768, 390), check:
- Does the nav collapse? To what? (hamburger, bottom bar, hidden)
- Do grids change column count?
- Do font sizes change?
- What elements hide or show?
- Touch target sizes at mobile

## Phase 2: Analysis & Writing

Using the extracted data, write the DESIGN.md following the Stitch 9-section format:

### Section 1: Visual Theme & Atmosphere
Write evocative prose — not bullet points. Describe the mood, density, and design philosophy. What does using this site FEEL like? Reference the awesome-design-md examples at `~/project/awesome-design-md/` for quality reference.

### Section 2: Color Palette & Roles
Organize extracted colors into semantic groups:
- **Primary/Brand** — the dominant accent color(s)
- **Interactive** — links, buttons, focus rings
- **Neutral Scale** — backgrounds, text hierarchy, borders
- **Surface & Borders** — card backgrounds, dividers
- **Status** — success, error, warning, info
- **Shadow Colors** — extracted from box-shadow values

For each color: semantic name, hex value, CSS variable (if found via inspecting stylesheets), and functional role.

### Section 3: Typography Rules
Font families with full fallback stacks. Complete hierarchy table:
| Role | Font | Size | Weight | Line Height | Letter Spacing | Notes |

### Section 4: Component Stylings
Document buttons (all variants with exact CSS per state), cards, badges/pills, inputs/forms, navigation. Include hover, active, focus, disabled states with exact values.

### Section 5: Layout Principles
Spacing system (infer base unit from common padding/margin values). Grid/container max-widths. Whitespace philosophy.

### Section 6: Depth & Elevation
Shadow system as table: Level | CSS Shadow | Use Case.

### Section 7: Do's and Don'ts
8-10 each. Be specific and actionable. Derived from observed patterns — "Do use 500 weight for body text" not "Do use appropriate weights."

### Section 8: Responsive Behavior
Breakpoints (inferred from where layouts change). Touch targets. Collapsing strategy per component type.

### Section 9: Agent Prompt Guide
Quick color reference lookup table. 4-5 ready-to-use example prompts with exact values for common components.

## Phase 3: Save & Catalog

Save to `vault/knowledge/references/blueprints/<site-name>.design.md` with frontmatter:
```yaml
---
title: "<Site Name> Design System"
type: reference
date: <today>
tags: [reverse-engineering, design-system, <site-name>]
source_ref: <url>
stage: 3
---
```

Copy screenshots to `vault/knowledge/references/blueprints/<site-name>/` for reference.

Report: site name, number of colors extracted, fonts found, component patterns documented.
