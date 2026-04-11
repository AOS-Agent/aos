---
name: frontend-craft
description: Opinionated frontend design skill that enforces a design brief, composition thinking, and anti-pattern awareness before writing code. Sister skill to frontend-design — same goal, different method.
---

Build frontend interfaces that feel designed, not assembled. This skill replaces vibes with technique.

**Convergence warning:** You have a strong prior toward clean, card-based SaaS UI with uniform spacing, safe colors, and identical hover states on everything. You will default to this unless you actively resist. Every section of this skill exists to pull you away from that center. Read it as a set of constraints, not suggestions.

## When This Activates

When the user asks to build, design, or enhance any UI — pages, components, layouts, apps.

**AOS context** (`core/qareen/`, `apps/mission-control/`): DESIGN.md is law for tokens (colors, fonts, spacing). This skill governs *composition* — how those tokens are arranged into something with personality.

**External context** (standalone apps, client work, new projects): This skill governs everything — palette, type, layout, composition. You MUST anchor to a concrete aesthetic world (see Step 1).

## Step 1: The Brief (Mandatory)

Before writing ANY code, output a design brief. No exceptions. This is the most important step.

```
## Design Brief

**What this is:** [One sentence. What the user will do here.]
**Who uses it:** [Role + context. "An operator glancing at this between tasks" is different from "an analyst drilling into data."]
**Aesthetic anchor:** [A concrete reference world — not adjectives. Examples: "train station departure board," "field notebook," "recording studio mixer," "newspaper front page," "museum label." This anchors every decision.]
**Density:** [Sparse / Balanced / Dense — how much information per viewport]
**Rhythm:** [How the eye moves. "Top-to-bottom scan" / "Left anchor, right detail" / "Hero then list" / "Tabular scan"]
**Content types:** [List the distinct kinds of content on this page and whether they need different visual treatment. "System alerts break the layout; routine items stay in the flow."]
**Signature move:** [ONE thing that makes this page feel intentional. Not decoration — a layout choice, a type pairing, a data presentation that's unusually good.]
**What I'm NOT doing:** [One default pattern I'm consciously rejecting and why]
```

The brief forces commitment. "Aesthetic anchor" prevents vague adjectives — you must name a real-world object whose visual language you're borrowing. "Content types" prevents treating all data as equal. "Signature move" prevents generic output. "What I'm NOT doing" prevents the default card grid.

## Step 2: Composition (Before Components)

Think about the PAGE before thinking about COMPONENTS. A page is not a stack of components — it's a composition with visual hierarchy.

### Hierarchy Techniques

Not everything is equally important. Use these to create rank:

| Technique | How | Instead of |
|-----------|-----|------------|
| **Scale contrast** | Make the primary element 2-3x larger than secondary | Everything the same size |
| **Density variation** | Dense metadata next to spacious content | Uniform padding everywhere |
| **Type contrast** | Serif headline + mono data + sans labels | Same font/weight throughout |
| **Negative space as emphasis** | Surround the key element with emptiness | Filling every pixel |
| **Color restraint** | One accent color, used sparingly | Rainbow status badges |
| **Progressive disclosure** | Show summary, reveal detail on interaction | Everything visible at once |

### Layout Vocabulary

Stop defaulting to "centered column of cards." Consider:

- **Split pane**: Fixed left context + scrolling right detail
- **Timeline/river**: Vertical flow with time as the spine, items alternating sides or indenting by type
- **Dense table with inline expansion**: Rows that expand to show detail, no separate detail page
- **Narrative scroll**: Single column that tells a story — large type at top, increasing density as you scroll
- **Sidebar + canvas**: Thin nav/filter rail + wide content area
- **Masonry/stagger**: Items of different heights, packed efficiently
- **Single hero + supporting list**: One item gets 60% of the viewport, the rest are compact rows below

Pick one. Commit. Don't mix three layouts on one page.

### Content-Type-Aware Composition

Not all data on a page is the same kind of thing. When your page has mixed content types (e.g., alerts + routine events, or featured items + archive), they should break the layout differently:

- **Interruptions** (errors, alerts, urgent items): Break out of the flow entirely. Full-width banner, different background, or slide-in overlay. They don't belong in the same list as routine items.
- **Featured content** (most recent, most important): Gets disproportionate space — 2-3x the height/width of normal items.
- **Routine items** (homogeneous lists): Compact rows, not cards. Density is a feature here.
- **Metadata** (timestamps, counts, status): Inline, never in its own container. It annotates content, it doesn't stand alone.

If you find yourself rendering all content types with the same component, stop. That's the model defaulting to uniformity.

### The Card Trap

Cards are the model's security blanket. Before reaching for a card, ask:

1. Does this data have clear boundaries? (If not, don't box it — use spacing and type hierarchy instead)
2. Are all cards the same importance? (If yes, consider a table or list — cards imply equality when you might want rank)
3. Will there be more than 6? (If yes, cards create visual noise — switch to compact rows)
4. Is the card interactive? (If not, the border is just decoration)

**Cards are appropriate for:** Draggable items, selection targets, self-contained previews.
**Cards are NOT appropriate for:** Lists of homogeneous items, settings, sequential data, metadata display.

## Step 3: Anti-Patterns (Know What Generic Looks Like)

These are the patterns that make AI-generated UI look like AI-generated UI. Recognizing them is the first step to avoiding them.

### Banned Defaults

If you catch yourself reaching for any of these without conscious justification, stop:

- **Purple/blue/indigo gradient on white** — the single most recognizable AI aesthetic
- **Three identical feature cards in a row** — "feature 1, feature 2, feature 3" with icons
- **Stat card row** (icon + label + big number + subtitle) — say it in a sentence instead
- **Hero section with centered text + CTA button** — earn the hero or skip it
- **Lucide/Heroicons next to every label** — icons are for scanning, not decoration
- **`rounded-xl` or `rounded-2xl` on everything** — vary radius by element purpose
- **Flat solid background with no atmosphere** — layer a subtle gradient, grain, or tint
- **Evenly-distributed color palette** — pick a dominant + one accent, not a rainbow

### Layout Anti-Patterns

| Pattern | Why it's generic | Alternative |
|---------|-----------------|-------------|
| `max-w-[640px] mx-auto` on every page | Makes everything feel like a blog post | Let content determine width. Dense data can go wider. |
| Grid of 3-4 identical cards | Implies everything is equal importance | Vary card sizes, or use a list with one featured item |
| Stat card row (icon + label + big number) | The #1 most common AI UI pattern | Inline stats in a sentence: "14 tasks completed, 3 blocked" — or use sparklines |
| Empty page + centered illustration + "Nothing here yet" | Zero personality, zero guidance | Context-specific: "No sessions today. Your last session was Tuesday — pick up where you left off?" |

### Interaction Anti-Patterns

| Pattern | Why it's generic | Alternative |
|---------|-----------------|-------------|
| `hover:bg-hover transition-colors` on everything | Makes every element feel the same | Differentiate: primary actions get scale/color change, secondary get subtle opacity, tertiary get underline |
| Identical `translateY(-8px)` enter animation on every element | Motion should convey meaning, not just "I appeared" | Stagger children. Vary direction by position. Or skip animation for low-importance elements. |
| Click → navigate to detail page | Sometimes the data fits inline | Expand-in-place, slide-over panel, or modal for quick views |

### Visual Anti-Patterns

| Pattern | Why it's generic | Alternative |
|---------|-----------------|-------------|
| Same `border-radius` on every element | Monotonous | Sharp corners for data containers, rounded for interactive elements, pill for status |
| Status badge for every item | Rainbow noise | Use position or grouping to convey status. Reserve badges for exceptions. |
| Icon next to every label | Redundant when the label is clear | Icons for scanning (nav, actions). No icons for inline labels the user reads. |
| Uniform text size with weight as the only differentiator | Flat hierarchy | Use actual size contrast. 24px title → 13px body is a real jump. 15px → 13px is nothing. |

## Step 4: Craft Details

These are the small moves that separate "designed" from "assembled."

### Typography That Breathes

- **Pair fonts with purpose.** Serif for content the user reads. Sans for content the user scans. Mono for content the user compares.
- **Size jumps of 2-3x, not 1.2x.** 15px heading → 13px body is invisible hierarchy. 28px → 13px is real. If the jump doesn't feel dramatic, it's not working.
- **Weight contrast means extremes.** 400 vs 500 is nothing. 300 vs 700, or 100 vs 900 — that's contrast. Use weight to create visual anchors, not subtle distinctions.
- **Letter-spacing is a tool.** Tight (-0.02em) for large type. Normal for body. Wide (+0.05em) for tiny uppercase labels.
- **Line-height varies.** Tight (1.2) for headings. Relaxed (1.6) for body paragraphs. Snug (1.3) for lists and UI text.
- **For non-AOS work: ban Inter, Roboto, system defaults.** Reach for fonts with character: Bricolage Grotesque, Fraunces, Cabinet Grotesk, Departure Mono, Space Grotesk, Instrument Serif. Google Fonts has thousands — use them.

### Color With Restraint

- **One accent, used rarely.** If everything is orange, nothing is orange. Accent marks the ONE thing that matters on the page.
- **Hierarchy through opacity, not hue.** `text-text` → `text-secondary` → `text-tertiary` creates hierarchy without adding colors.
- **Status colors are for status.** Green = success, red = error. Don't use them decoratively.
- **Backgrounds create grouping.** Use 2-3 background levels max. More than that is noise.

### Motion With Meaning

- **Stagger > uniform.** If 5 items enter, delay each by 30-50ms. The cascade tells the eye where to start.
- **Direction encodes position.** Items from the left slide in from left. Items from below slide up. Don't animate everything from the same direction.
- **Exit matters.** If something animates in, it must animate out. Instant disappearance feels broken.
- **Most things don't need animation.** Hover state changes, tab switches, content updates — instant is fine. Save motion for meaningful transitions.

### Atmosphere (Backgrounds Are Not Flat)

A flat `bg-black` or `bg-white` background is the absence of design. Every page lives in a space, and the background establishes that space.

- **Subtle radial gradient** — one warm tone fading from center/corner. Not decorative, atmospheric.
- **Grain/noise** — SVG `feTurbulence` at 2-4% opacity adds analog warmth. Everything feels more real.
- **Contextual tinting** — if the content has a mood (success, error, active session), let the background absorb a hint of it.
- **For AOS:** The prayer-period ambient tint already handles this. Don't add more.
- **For external work:** This is your cheapest win. A 30-second CSS gradient change makes the entire page feel intentional.

### The Edges (Where Personality Lives)

The model generates the happy path and stops. But empty states, error messages, loading indicators, and first-run experiences are where users form opinions about quality. These are low-risk, high-impact:

- **Empty states:** Never "No data available." Always context-specific with a next action. "No sessions yet — start one with /companion" beats a sad illustration.
- **Error states:** Brief, human, specific. "Couldn't reach the bridge — it may be restarting" not "An error occurred."
- **Loading:** Match the shape of what's loading. If it's a list, show faint rows. If it's a single block, show its silhouette. Generic spinners are lazy.
- **First-run/onboarding:** The first thing a user sees on a new page defines their expectation. Make it warm, not clinical.

Design these BEFORE the main content. They're the pages users remember.

### Whitespace as Design

- **Unequal spacing is intentional.** Tight spacing between related items, generous spacing between groups. This creates visual paragraphs.
- **Let one element breathe.** Give the most important element 2x the surrounding space. Importance = isolation.
- **Edge-to-edge vs. padded.** Full-bleed elements (dividers, images, headers) feel expansive. Padded elements feel contained. Mix them.

## Step 5: Enhancement Mode

When enhancing existing UI (not building from scratch):

1. **Read the existing code first.** Understand what's there before proposing changes.
2. **Question the information architecture before touching the presentation.** Ask: is the right information visible? Should something be hidden by default? Is the grouping correct? Enhancement that only changes how things look — without questioning what's shown — is surface polish, not craft.
3. **Identify the ONE structural change that would improve it most.** Not 10 cosmetic tweaks. One move that changes how the page feels to use.
4. **Preserve what works.** Enhancement means the user already has something functional. Don't redesign — refine.

**Structural enhancement moves** (question the mental model):
- Should inactive/empty/disabled items be visible at all? Consider progressive disclosure — hide by default, toggle to show.
- Is the page showing the right default view? Maybe "last 7 days" is better than "all time." Maybe "active only" is better than "everything."
- Are there items the user always has to scroll past? Move them, collapse them, or remove them.

**Presentation enhancement moves** (refine the surface):
- Replace a card grid with a more appropriate layout for the data
- Add type hierarchy where everything is the same size
- Reduce color noise (fewer badges, more spacing-based grouping)
- Add one moment of delight (a well-crafted empty state, a satisfying transition, a smart default)
- Increase information density where the user is power-user level

## Rules

1. **Brief before code.** Always. The brief is the skill's core value.
2. **Composition before components.** Think about the page, then the pieces.
3. **Reject one default.** Every page must consciously avoid at least one generic pattern.
4. **Earn every element.** If a border, shadow, badge, or icon doesn't serve hierarchy or interaction, remove it.
5. **Differentiate interaction by importance.** Primary actions: color change or scale. Secondary: subtle opacity shift. Tertiary: underline or no effect. If everything has `hover:bg-hover transition-colors`, you've failed.
6. **Design the edges first.** Empty state, error state, loading state, first-run — write these before the happy path.
7. **AOS = DESIGN.md tokens + this skill's composition.** Never override DESIGN.md colors/fonts for AOS work.
8. **Test with real content.** Don't design for placeholder text. Use realistic data lengths, edge cases, empty states.
