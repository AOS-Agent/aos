---
name: harvest
description: "Extract a specific UI component or section from a live website — captures exact CSS, content, states, assets, and optionally generates a React component. Trigger on: /harvest, 'extract this component', 'grab that section', 'pull that pricing table', 'save this pattern'. Provide URL and optionally a section name or CSS selector."
argument-hint: "<url> [section-name or CSS selector]"
user-invocable: true
allowed-tools: Bash, Read, Write, Glob
---

# Harvest — Component Extraction

Extract a specific UI component from **$ARGUMENTS** into the component library.

## Pre-Flight

1. Parse arguments — URL is required, section identifier is optional
2. Navigate to the URL via dev-browser
3. If no section specified: show page topology (list major sections) and ask which to harvest

## Phase 1: Identify the Target

### If section name given
Use `page.snapshotForAI()` to find the matching section, then identify its CSS selector.

### If CSS selector given
Verify the element exists via `page.evaluate()`.

### If nothing specified
Map the page topology:
```javascript
(function() {
  const sections = [...document.querySelectorAll('section, [class*="section"], main > div, header, footer, nav')];
  return JSON.stringify(sections.map((s, i) => ({
    index: i,
    tag: s.tagName.toLowerCase(),
    classes: s.className?.toString().split(' ').slice(0,3).join(' '),
    id: s.id,
    text: s.textContent?.trim().slice(0,100),
    rect: s.getBoundingClientRect().toJSON()
  })), null, 2);
})()
```
Present the list and ask the operator which section(s) to harvest.

## Phase 2: Extract

### Screenshot the section
Scroll to it, screenshot the viewport area containing it.

### Extract full CSS tree
Run the Component Tree CSS Extraction script from the Reverser agent. Use the identified selector. This walks 4 levels deep capturing every computed style.

### Extract content
- All text via `element.textContent` for each text node
- All images with `src`, `alt`, dimensions
- All links with `href` and text
- SVGs (inline) — extract as React components
- Background images from CSS

### Check for states
1. **Hover states** — use dev-browser's Playwright to hover each interactive child element, re-extract CSS, note what changed
2. **Click states** — click tabs, buttons, dropdowns within the section. For each state change: screenshot + re-extract CSS + capture new content
3. **Scroll states** — if the section has scroll-driven behavior (sticky headers, parallax, scroll-snap), capture styles at multiple scroll positions

For each state transition, record:
- What triggered it (hover on X, click on Y, scroll to Z)
- What CSS properties changed (before → after values)
- What content changed
- The transition animation (duration, easing)

### Assess complexity
Count distinct sub-components. If >3 with unique designs, suggest breaking the harvest into multiple components.

## Phase 3: Write Component Spec

Write to `vault/knowledge/references/components/<name>-<source>/spec.md`:

```markdown
---
title: "<Component Name> from <Site>"
type: reference
date: <today>
tags: [reverse-engineering, component, <pattern-type>, <site-name>]
source_ref: <url>
stage: 3
---

# <ComponentName> Specification

## Source
- **URL:** <url>
- **Section:** <identifier>
- **Screenshot:** screenshot.png

## Interaction Model
<static | click-driven | scroll-driven | time-driven>

## DOM Structure
<Element hierarchy — what contains what>

## Computed Styles
### Container
- display: ...
- padding: ...
(every relevant property with exact values)

### <Child elements>
...

## States & Behaviors
### <State name>
- **Trigger:** <what causes this state>
- **Before:** <CSS values>
- **After:** <CSS values>  
- **Transition:** <duration, easing>

## Content (verbatim)
<All text, image refs, links>

## Assets
<Images, icons, videos used>

## Responsive
- **Desktop (1440px):** <layout>
- **Tablet (768px):** <changes>
- **Mobile (390px):** <changes>
```

## Phase 4: Generate Component (Optional)

If the operator wants code (or if working inside a project):

1. **Detect output stack** from current working directory:
   - Has `vite.config.ts` → Vite + React + Tailwind
   - Has `next.config.ts` → Next.js + React + Tailwind
   - Neither → output stack-agnostic spec only, note this

2. **Generate React component** matching the spec:
   - Use exact CSS values from extraction (Tailwind utilities where they match, inline styles for precise values)
   - Include all states and transitions
   - Include responsive behavior
   - Export as named export

3. **Save** to `vault/knowledge/references/components/<name>-<source>/component.tsx`

## Phase 5: Catalog

Save screenshot alongside the spec. Ensure frontmatter is complete. Report what was harvested: component name, source, states captured, assets found.
