# AOS Design Language

Read this file before building any UI. Follow it exactly.

## Philosophy

The qareen is a presence, not a dashboard. Warm, atmospheric, alive.
Content lives on a canvas — not in boxes. Chrome floats and disappears.
The interface feels like a space you inhabit, not a tool you operate.

**Guiding principles:**
- Warm over cold. Always. Every surface, every border, every shadow.
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

## Color Tokens — Warm Dark

The palette is warm dark brown. Never cold. Never blue-tinted.

### Backgrounds

| Token | Value | Usage |
|-------|-------|-------|
| `bg` | `#0D0B09` | Deepest background |
| `bg-panel` | `#151210` | Panels, drawers |
| `bg-secondary` | `#1E1A16` | Elevated: inputs, cards |
| `bg-tertiary` | `#2A2520` | Hover surfaces, code blocks |
| `bg-quaternary` | `#3A3530` | Highest elevation |

### Text

| Token | Value | Usage |
|-------|-------|-------|
| `text` | `#FFFFFF` | Primary — headings, emphasis |
| `text-secondary` | `#E8E4DF` | Body text, descriptions |
| `text-tertiary` | `#9A9490` | Metadata, timestamps |
| `text-quaternary` | `#6B6560` | Placeholders, disabled |

### Borders

| Token | Value | Usage |
|-------|-------|-------|
| `border` | `rgba(255, 245, 235, 0.06)` | Default dividers |
| `border-secondary` | `rgba(255, 245, 235, 0.10)` | Input borders, cards |
| `border-tertiary` | `rgba(255, 245, 235, 0.15)` | Strong emphasis |

Borders are cream-tinted, not white. `rgba(255, 245, 235, ...)` not `rgba(255, 255, 255, ...)`.

### Accent & Status

| Token | Value | Usage |
|-------|-------|-------|
| `accent` | `#D9730D` | Brand orange, active indicators |
| `accent-hover` | `#E8842A` | Hover state |
| `green` | `#30D158` | Success, connected |
| `red` | `#FF453A` | Error, destructive |
| `yellow` | `#FFD60A` | Warning, pending |
| `blue` | `#0A84FF` | Info, links |
| `purple` | `#BF5AF2` | Special, agent |

### Interaction

| Token | Value | Usage |
|-------|-------|-------|
| `hover` | `rgba(255, 245, 235, 0.05)` | Hover backgrounds |
| `active` | `rgba(255, 245, 235, 0.08)` | Active/pressed |
| `selected` | `rgba(255, 245, 235, 0.12)` | Selected items |

## Glass Pill Pattern

Floating UI chrome uses translucent glass pills.

```css
background: rgba(30, 26, 22, 0.60);   /* bg-secondary at 60% */
backdrop-filter: blur(12px);
border: 1px solid rgba(255, 245, 235, 0.06);
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

**Colors shift by Islamic prayer period:**

| Period | Palette | Vibe |
|--------|---------|------|
| Fajr / Last Third | Deep blue-violet | Pre-dawn |
| Sunrise | Warm amber + rose | Dawn |
| Duha | Warm amber | Morning energy |
| Dhuhr | Golden | Midday |
| Asr | Mellow amber-orange | Afternoon |
| Maghrib | Deep orange-red | Sunset |
| Isha | Deep indigo-violet | Night |

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

## Rules

1. **Warm over cold.** Every surface, every border, every shadow.
2. **No stat dumps.** The qareen speaks in sentences, not metrics.
3. **Sentence case.** "Transcript" not "TRANSCRIPT". No all-caps except tiny overline labels.
4. **No generic empty states.** Context-specific guidance, not "No data available."
5. **Real names.** "Hisham" not "operator." Always resolve against data.
6. **Cursor pointer.** Every clickable element.
7. **Animate open AND close.** No instant unmount.
8. **No permanent chrome.** Chrome floats and overlays.
9. **Prayer-period awareness.** Aurora, context bar, greeting reflect the current Islamic time period.
10. **No hardcoded colors.** Always reference design tokens.
11. **Serif for content, sans for chrome.** EB Garamond speaks. Inter controls.
