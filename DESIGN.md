# AOS Design Language

Read this file before building any UI. Follow it exactly.

## Philosophy

The qareen is a presence, not a dashboard. Deep, quiet, restrained.
Content lives on a canvas — not in boxes. Chrome floats and disappears.
The interface feels like a space you inhabit, not a tool you operate.

The palette is **charcoal and black** — neutral-warm near-blacks, never
cold or blue-tinted. There is no loud brand hue. A single restrained
**warm bone** accent carries active states; content and status colors
carry the only real color. The chrome itself stays monochrome.

**Guiding principles:**
- Charcoal, never blue. Neutral-warm near-blacks on every surface — never a cold, blue-tinted black.
- Restraint over decoration. One quiet bone accent, not a color for its own sake.
- Content-first. Generous whitespace, no decorative noise.
- Hierarchy through opacity, not separate color values.
- Minimal elevation. Borders over shadows.
- No permanent chrome. Content gets the screen. Chrome floats and overlays.

## Two Fonts

| Font | Use | Why |
|------|-----|-----|
| **EB Garamond** (serif) | Body text, content, paragraphs, transcripts, notes, briefings | The qareen's voice. Warmth, personality, readability. |
| **Inter** (sans-serif) | UI chrome: nav, buttons, labels, inputs, badges, context bar, timestamps | Interface elements. Clean, functional, stays out of the way. |

```css
--font-serif: "EB Garamond Variable", Georgia, serif;
--font-sans:  "Inter Variable", "SF Pro Text", -apple-system, sans-serif;
--font-mono:  "Berkeley Mono", "SF Mono", ui-monospace, monospace;
```

Body defaults to serif. UI elements (nav, button, input, label, kbd, code, overlines, captions) override to sans.

## Color Tokens — Charcoal

The palette is neutral-warm charcoal. Never cold. Never blue-tinted.
Each background step keeps `R ≥ G ≥ B` by a hair, so the charcoal reads
warm-neutral rather than blue-black.

### Backgrounds

| Token | Value | Usage |
|-------|-------|-------|
| `bg` | `#0B0B0A` | Deepest background — true near-black |
| `bg-panel` | `#121210` | Panels, drawers |
| `bg-secondary` | `#1A1917` | Elevated: inputs, cards |
| `bg-tertiary` | `#24221F` | Hover surfaces, code blocks |
| `bg-quaternary` | `#302E2A` | Highest elevation |

### Text

| Token | Value | Usage |
|-------|-------|-------|
| `text` | `#FFFFFF` | Primary — headings, emphasis |
| `text-secondary` | `#E7E4DE` | Body text, descriptions |
| `text-tertiary` | `#97938C` | Metadata, timestamps |
| `text-quaternary` | `#66625B` | Placeholders, disabled |

### Borders

| Token | Value | Usage |
|-------|-------|-------|
| `border` | `rgba(245, 242, 236, 0.07)` | Default dividers |
| `border-secondary` | `rgba(245, 242, 236, 0.11)` | Input borders, cards |
| `border-tertiary` | `rgba(245, 242, 236, 0.16)` | Strong emphasis |

Borders are warm-neutral white alpha, not pure white. `rgba(245, 242, 236, ...)` not `rgba(255, 255, 255, ...)`.

### Accent & Status

The accent is a **warm bone** — desaturated, near-neutral, unmistakably
not orange. It is chosen over a saturated hue deliberately: on charcoal,
charcoal-and-bone reads as a restrained, editorial monochrome, and the
operator vetoed the previous orange. Because the accent is **light**,
solid accent fills (`bg-accent`) carry **dark** text via `on-accent`,
not white — a light bone button with charcoal type, the way a primary
button works in a monochrome system.

| Token | Value | Usage |
|-------|-------|-------|
| `accent` | `#D6CCB4` | Warm bone — active indicators, links, focus, primary fills |
| `accent-hover` | `#E4DCC8` | Hover state (brighter bone) |
| `accent-muted` | `#1B1915` | Dark accent-tinted surface |
| `accent-subtle` | `rgba(214, 204, 180, 0.14)` | Faint accent wash behind active items |
| `on-accent` | `#14130E` | Charcoal text/icons **on** a solid accent fill |
| `green` | `#30D158` | Success, connected |
| `red` | `#FF453A` | Error, destructive |
| `yellow` | `#FFD60A` | Warning, pending |
| `blue` | `#0A84FF` | Info, links |
| `purple` | `#BF5AF2` | Special, agent |

**Accent choice — rationale.** Three candidates were on the table: warm
bone, muted gold, and desaturated green. **Bone won.** Gold was rejected
as too close to the vetoed orange — the operator explicitly warned against
sneaking in orange-adjacent ambers, and gold sits right beside amber.
Desaturated green was rejected because it collides with the semantic
status-green (`connected` / `success`); an accent and a "connected" dot in
the same hue is ambiguous. Bone is the only option that is categorically
not-orange (it sits near the neutral axis, the opposite of saturated
amber), stays warm (honoring "never cold/blue"), and pairs cleanly with
pure-white headings.

Status colors are semantic and kept legible on charcoal. The old `orange`
token (a distinct tag/severity swatch) is repointed to a warm-neutral
**sand** `#A99E88` — there is no orange anywhere in the system.

### Interaction

| Token | Value | Usage |
|-------|-------|-------|
| `hover` | `rgba(255, 245, 235, 0.05)` | Hover backgrounds |
| `active` | `rgba(255, 245, 235, 0.08)` | Active/pressed |
| `selected` | `rgba(255, 245, 235, 0.12)` | Selected items |

## Glass Pill Pattern

Floating UI chrome uses translucent glass pills.

```css
background: rgba(26, 25, 23, 0.62);   /* charcoal bg-secondary at ~62% */
backdrop-filter: blur(12px);
border: 1px solid rgba(245, 242, 236, 0.07);
border-radius: 9999px;
box-shadow: 0 2px 12px rgba(0, 0, 0, 0.3);
height: 32px;
```

Used for: navigation toggle, context bar. Never for content containers.

## Navigation

No permanent sidebar or topbar. Navigation is a floating overlay.

**Collapsed (default):** Small glass pill at top-left. Shows: hamburger icon → current page icon → page name → connection dot. Always visible. `z-index: 320`.

**Expanded:** Drawer slides in from left ON TOP of content. Background gets `backdrop-blur-sm` overlay. Click outside, Escape, or click pill to close. Nav item selection auto-closes.

**Animation:** Slide in/out 180ms ease-out. Backdrop fades in sync.

Content always gets 100% screen width.

## Aurora Background

The companion idle screen has a living atmospheric background.

**Technique:** Canvas-drawn sine-wave ribbons. CSS `filter: blur(45px)`. Animation via `requestAnimationFrame` (not CSS keyframes — unreliable on Safari over Tailscale).

**Colors shift by Islamic prayer period — within the charcoal system.**
The aurora stays monochrome charcoal-and-bone; the shift is a subtle
temperature move, not a burst of color. No orange, ever — sunset reads as
a deep warm graphite, not a glow.

| Period | Palette | Vibe |
|--------|---------|------|
| Fajr / Last Third | Faint indigo-charcoal | Pre-dawn (the one cool cast) |
| Sunrise → Asr | Warm-neutral charcoal → soft bone | Day |
| Maghrib | Deep warm graphite | Sunset (muted, no orange) |
| Isha | Deepest charcoal | Night |

Only on the companion idle screen. Active sessions use solid `bg`.

## Context Bar

Glass pill centered at top of companion screen. Same vertical position as nav pill (`top: 12px`). Uses Inter (sans-serif). `text-[11px]`.

```
11:47 AM · Tue, Apr 1 · Duha · Dhuhr in 1h 28m · 14° Overcast
```

- Time: `text-secondary`, tabular-nums
- Date: `text-tertiary`
- Prayer period: `accent` color
- Countdown + weather: `text-tertiary`

Prayer via `adhan` library (local calculation). Weather via Open-Meteo (free, no key).

## Typography Scale

| Element | Font | Size | Weight | Line Height |
|---------|------|------|--------|-------------|
| Greeting | Garamond | 24px | 600 | 1.3 |
| Body / paragraphs | Garamond | 14px | 400 | 1.6 |
| Nav items | Inter | 12-13px | 450-590 | 1.3 |
| Labels | Inter | 13px | 510 | 1.4 |
| Buttons | Inter | 12-13px | 510 | — |
| Badges / tags | Inter | 11px | 510 | 1.2 |
| Captions | Inter | 11px | 400 | 1.45 |
| Overlines | Inter | 10px | 590 | 1.2 |
| Context bar | Inter | 11px | 450-510 | — |
| Mono / code | Mono | 12px | 400 | 1.5 |

## Spacing & Radius

- Border radius: `3-7px` for cards/inputs. `9999px` for pills only.
- Touch targets: `32px` min, `44px` on mobile.
- Base unit: `4px`. Multiples: 4, 8, 12, 16, 20, 24, 32, 48.

## Motion

| Duration | Usage |
|----------|-------|
| 80ms | Hover states, toggles |
| 150ms | Focus, buttons, tabs |
| 180ms | Sidebar open/close, overlays |
| 220ms | Panels, layout shifts |
| 350ms | Page transitions |

Easing: `cubic-bezier(0.25, 0.46, 0.45, 0.94)` for most transitions.

**Rule:** `requestAnimationFrame` for canvas/motion effects. CSS transitions for hover/state changes. If something animates in, it must animate out.

## Composition

**Centered.** Companion idle screen centers content vertically and horizontally on the aurora canvas. Text stays left-aligned within the centered block.

**Vertical flow.** Greeting → briefing → suggestion chips → recent sessions. Single centered column. No multi-column grids on the main screen.

## Loading & Empty States

Every data fetch has three outcomes — loading, empty, and error. Design all three. An undefined loading or empty state is how you ship a stuck skeleton or a blank screen.

### Skeleton pattern

While content is loading, show a skeleton — not a spinner-only screen, and never nothing.

- **Surface:** `bg-tertiary`, with a subtle opacity pulse (`animate-pulse`).
- **Shape:** the skeleton block matches the border-radius and rough dimensions of the content it will become. A card skeleton is card-shaped; a row skeleton is row-shaped.
- **Hard timeout — the load-bearing rule.** A skeleton must never persist indefinitely. If real content hasn't replaced it within ~8s, resolve to something else. Gate the skeleton on a timeout (see `hooks/useLoadingTimeout.ts`), and gate it on "no data yet" rather than the fetch library's loading flag — a paused/hung query keeps that flag from ever flipping, which is how a skeleton hangs forever. Give the underlying fetch its own timeout (`AbortSignal.timeout`) too, so a hung endpoint rejects instead of stalling.
- **What it resolves to depends on the surface.** A **primary data surface** (the thing the page exists to show) falls back to an explicit error/retry state: a short honest line ("Couldn't load your tasks"), one sentence of why, and a `Retry` button wired to refetch. **Optional chrome** (a secondary banner, a nice-to-have summary) degrades to hidden — never show a standing "failed / Retry" banner for a surface the operator didn't come for, especially one whose endpoint may not be wired yet. Warm palette, sentence case either way.

### Empty-state pattern

When a fetch succeeds but returns nothing, never render "No data available." Follow the Calendar page's canonical pattern:

- A **bold headline** naming the state ("No schedule configured").
- **One sentence of specific guidance** telling the operator exactly what to do next ("Define your daily rhythm in Settings…") — not a generic apology.
- An optional action (button/link) to that next step.

The shared `EmptyState` primitive (`components/primitives/EmptyState.tsx`) encodes this: `icon` + `title` + specific `description` + optional `action`. Use it rather than hand-rolling.

## Rules

1. **Charcoal, never blue.** Neutral-warm near-blacks on every surface, border, and shadow — never a cold, blue-tinted black. No orange anywhere; the one accent is warm bone.
2. **No stat dumps.** The qareen speaks in sentences, not metrics.
3. **Sentence case.** "Transcript" not "TRANSCRIPT". No all-caps except tiny overline labels.
4. **No generic empty states.** Context-specific guidance, not "No data available."
5. **Real names.** "Hisham" not "operator." Always resolve against data.
6. **Cursor pointer.** Every clickable element.
7. **Animate open AND close.** No instant unmount.
8. **No permanent chrome.** Chrome floats and overlays.
9. **Prayer-period awareness.** Aurora, context bar, greeting reflect the current Islamic time period.
10. **No hardcoded colors.** Always reference design tokens. Canvas/SVG contexts that can't use CSS vars must mirror the exact token hex.
11. **Serif for content, sans for chrome.** EB Garamond speaks. Inter controls.
12. **Dark text on accent fills.** The accent is light bone — solid `bg-accent` surfaces use `on-accent` (charcoal) text, never white.
