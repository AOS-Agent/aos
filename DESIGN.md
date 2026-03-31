# AOS Design Language

Read this file before building any UI. Follow it exactly unless explicitly told otherwise.

Source of truth is this file. Implementation lives in `apps/mission-control/app/globals.css`.

## Philosophy

Warm, content-first, minimal chrome. Dark mode default (Apple Messages-inspired: neutral near-black, barely warm). Light mode is warm paper (not white). Accent orange for intentional highlights only. The interface should feel like a tool you already know how to use.

**Guiding principles:**
- Warm over cold. When in doubt, warmer. Gray-beige over gray-blue.
- Content-first. Generous whitespace, no decorative noise.
- Hierarchy through opacity, not separate color values.
- Minimal elevation. Borders over shadows. Shadows only for floating layers.
- No pure black text in light mode. No pure white backgrounds in light mode.

## Color Tokens

### Backgrounds

| Token | Dark | Light | Usage |
|-------|------|-------|-------|
| `bg` | `#0A0A0A` | `#F7F4F0` | Page background |
| `bg-panel` | `#141414` | `#EFEBE4` | Sidebar, panels |
| `bg-secondary` | `#1C1C1E` | `#EAE5DD` | Input fields, cards |
| `bg-tertiary` | `#2C2C2E` | `#E3DDD5` | Hover surfaces, code blocks |
| `bg-quaternary` | `#3A3A3C` | `#DDD7CE` | Pressed states, badges |

### Text

| Token | Dark | Light | Usage |
|-------|------|-------|-------|
| `text` | `#FFFFFF` | `#38322B` | Primary text, headings |
| `text-secondary` | `#EBEBF5` | `#5C554C` | Body text, descriptions |
| `text-tertiary` | `#8E8E93` | `#8A827A` | Metadata, timestamps |
| `text-quaternary` | `#636366` | `#AEA69C` | Placeholders, disabled |

### Borders

| Token | Dark | Light | Usage |
|-------|------|-------|-------|
| `border` | `rgba(255,255,255,0.06)` | `rgba(56,50,43,0.07)` | Default dividers |
| `border-secondary` | `rgba(255,255,255,0.10)` | `rgba(56,50,43,0.12)` | Input borders, cards |
| `border-tertiary` | `rgba(255,255,255,0.15)` | `rgba(56,50,43,0.18)` | Strong emphasis |

### Accent

| Token | Value | Usage |
|-------|-------|-------|
| `accent` | `#D9730D` | Brand orange, links, active indicators |
| `accent-hover` | `#E8842A` | Hover state for accent elements |
| `accent-muted` | `#1F1510` | Subtle accent background (dark mode) |
| `accent-subtle` | `rgba(217,115,13,0.15)` | Selection highlight, focus rings |

### Interaction

| Token | Dark | Light | Usage |
|-------|------|-------|-------|
| `hover` | `rgba(255,255,255,0.05)` | `rgba(56,50,43,0.04)` | Hover backgrounds |
| `active` | `rgba(255,255,255,0.08)` | `rgba(56,50,43,0.08)` | Active/pressed states |
| `selected` | `rgba(255,255,255,0.12)` | `rgba(56,50,43,0.12)` | Selected items |

### Status Colors

| Name | Dark | Dark Muted | Light | Light Muted |
|------|------|------------|-------|-------------|
| Green | `#30D158` | `#0D1F12` | `#2D8A50` | `#E8F5EE` |
| Yellow | `#FFD60A` | `#1C1A08` | `#B8860E` | `#FBF5E0` |
| Orange | `#FF9F0A` | `#1C1508` | `#D9730D` | `#FAE8D8` |
| Red | `#FF453A` | `#1F0F0E` | `#B84840` | `#FCE8E7` |
| Blue | `#0A84FF` | `#0A1520` | `#3372A0` | `#E0EEF6` |
| Purple | `#BF5AF2` | `#1A1020` | `#7A58A8` | `#EEE8F5` |
| Teal | `#64D2FF` | `#0A1A20` | `#308898` | `#E0F2F5` |
| Pink | `#FF375F` | `#28101A` | `#A04878` | `#F5E5EE` |

Use status color + muted background for tags: `<span class="bg-tag-green-bg text-tag-green">active</span>`

## Typography

### Font Stacks

```
Sans:  "Inter Variable", "SF Pro Text", -apple-system, BlinkMacSystemFont, sans-serif
Mono:  "Berkeley Mono", "SF Mono", ui-monospace, Menlo, monospace
```

Inter Variable is loaded via `@fontsource-variable/inter`. Use mono only for code, data, and metadata values.

### Type Scale

| Name | Size | Weight | Letter Spacing | Line Height | Usage |
|------|------|--------|----------------|-------------|-------|
| `type-title` | 22px | 680 | -0.025em | 1.15 | Page headings |
| `type-heading` | 15px | 600 | -0.01em | 1.35 | Section headings, card titles |
| `type-body` | 13px | 400 | -0.008em | 1.5 | Body text (default) |
| `type-label` | 13px | 510 | -0.008em | 1.4 | Labels, nav items |
| `type-caption` | 11px | 400 | 0 | 1.45 | Secondary info, help text |
| `type-overline` | 10px | 590 | 0.06em | 1.2 | Section labels (uppercase) |
| `type-tiny` | 10px | 510 | 0.04em | 1.2 | Timestamps, costs, badges |

Base body: 13px, weight 400, line-height 1.5, letter-spacing -0.008em.

## Spacing & Layout

Base unit: **4px**. Use multiples: 4, 8, 12, 16, 20, 24, 32, 48.

| Element | Value |
|---------|-------|
| Page padding (desktop) | `32px` (px-8) |
| Page padding (mobile) | `20px` (px-5) |
| Content area vertical padding | `24px` (py-6) |
| Sidebar width (expanded) | `224px` |
| Sidebar width (collapsed) | `52px` |
| Topbar height | `48px` (h-12) |
| Card gap | `12px` or `16px` |
| Section gap | `24px` or `32px` |

## Border Radius

| Token | Value | Usage |
|-------|-------|-------|
| `radius-xs` | `3px` | Tags, inline code, small badges |
| `radius-sm` | `5px` | Buttons, sidebar items, inputs |
| `radius` | `7px` | Cards, panels, dropdowns |
| `radius-lg` | `10px` | Modals, popovers, code blocks |
| `radius-xl` | `14px` | Chat bubbles, input composer |
| `radius-full` | `9999px` | Avatars, status dots, pills (sparingly) |

Use `radius-xs` through `radius` for most UI. Reserve `radius-xl` for chat-style elements. `radius-full` for circular indicators only.

## Elevation & Shadows

| Level | Dark | Light | Usage |
|-------|------|-------|-------|
| Low | `none` | `0 1px 2px rgba(38,28,18,0.06), 0 0 0 1px rgba(38,28,18,0.04)` | Cards, subtle lift |
| Medium | `0 4px 24px rgba(0,0,0,0.4), inset 0 0 0 1px rgba(255,255,255,0.06)` | `0 2px 8px rgba(38,28,18,0.09), 0 0 0 1px rgba(38,28,18,0.04)` | Dropdowns, popovers |
| High | `0 8px 40px rgba(0,0,0,0.6), inset 0 0 0 1px rgba(255,255,255,0.08)` | `0 6px 20px rgba(38,28,18,0.12)` | Modals, command palette |

Prefer borders over shadows for separation. Use shadows only on floating/overlay elements.

## Motion

| Token | Duration | Usage |
|-------|----------|-------|
| `duration-instant` | 80ms | Hover states, toggles |
| `duration-fast` | 150ms | Focus rings, buttons, tabs |
| `duration-normal` | 220ms | Sidebar expand/collapse, panels |
| `duration-slow` | 350ms | Page transitions, modals |

Easing curves:
- `ease-out`: `cubic-bezier(0.25, 0.46, 0.45, 0.94)` -- most UI transitions
- `ease-out-back`: `cubic-bezier(0.34, 1.56, 0.64, 1)` -- playful bounces
- `ease-in-out`: `cubic-bezier(0.4, 0, 0.2, 1)` -- sidebar, layout shifts

## Z-Index Scale

| Token | Value | Usage |
|-------|-------|-------|
| `z-header` | 100 | Topbar |
| `z-overlay` | 500 | Sidebar overlay (mobile) |
| `z-popover` | 600 | Dropdowns, tooltips |
| `z-command` | 650 | Command palette |
| `z-dialog` | 700 | Modals, confirmations |
| `z-toast` | 800 | Notifications |
| `z-tooltip` | 1100 | Tooltips (above everything) |

## Component Patterns

### Sidebar

- Background: `bg-panel`. Border-right: `border`.
- Nav items: `type-label` weight, `h-7`, `radius-sm`, icon `14px` (w-3.5).
- Active: `bg-active`, `font-[590]`. Hover: `bg-hover`.
- Section labels: `type-overline`, `text-quaternary`.
- Collapsed mode: icons only, `52px` wide.

### Topbar

- Height: `48px`. Background: `bg`. Border-bottom: `border`.
- Title: `text-sm font-semibold`. Status dot with `animate-ping` when connected.
- Actions: `w-8 h-8` icon buttons, `rounded-sm`.

### Cards

- Border: `border-secondary`, `radius` (7px). No shadow by default.
- Hover: `bg-hover` or `bg-tertiary` at `0.02` opacity.
- Title: `type-overline` or `type-heading`.

### Tags / Badges

- Use status color pairs: `text-tag-{color}` on `bg-tag-{color}-bg`.
- Padding: `0 6px`, height: `20px`, `radius-xs` (3px).
- Font: `type-tiny` or 11px/12px.

### Buttons

- Primary: `bg-accent`, `text-white`, `radius-sm`, `h-7` (28px).
- Secondary: `bg-bg-secondary`, `border-secondary`, `text-text-secondary`.
- Ghost: transparent, `text-text-tertiary`, `hover:bg-hover`.
- Icon button: `w-7 h-7` or `w-8 h-8`, `rounded-sm`.

### Input Fields

- Background: `bg-secondary`. Border: `border-secondary`. `radius-sm`.
- Focus: `border-accent/30`, `ring-1 ring-accent/10`.
- Placeholder: `text-quaternary`.

### Chat Messages (Chief)

- User: right-aligned, `radius-xl` bubble, `bg-accent-muted`, left-border `accent`.
- Assistant: left-aligned, no bubble, text flows on `bg`. Label with lightning icon + "CHIEF".
- Tool calls: collapsible, `radius` (7px), `bg-tertiary/50`, `type-caption`.
- Metadata: `type-tiny`, `text-quaternary` (timestamp, duration, cost).
- Streaming cursor: `w-[2px] h-[13px] bg-accent`, blink animation.

### Tables

- Headers: `type-overline`, `text-quaternary`, `border-b border-tertiary`.
- Cells: 12-13px, `text-secondary`, `border-b border`.
- No zebra striping. No heavy borders.

## Scrollbars

- Width: `6px`. Track: transparent.
- Thumb: `rgba(255,255,255,0.08)` dark, `rgba(56,50,43,0.10)` light.
- Hover: double the opacity.
- `border-radius: full`.

## Focus & Accessibility

- Focus ring: `2px solid accent`, `outline-offset: -2px`.
- Touch targets: minimum `34px` (prefer `44px` on mobile).
- All interactive elements: `-webkit-tap-highlight-color: transparent`.
- Selection: `bg-accent-subtle`, `color-accent`.
- Respect `prefers-reduced-motion` for animations.

## Theme Implementation

Dark is default. Light activates via `data-theme="light"` on `<html>`.
Toggle persisted in `localStorage` key `mc-theme`.

```html
<html lang="en" class="dark">           <!-- default -->
<html lang="en" data-theme="light">     <!-- light mode -->
```

All semantic tokens remap automatically. Components should never hardcode hex values -- always use token references (`bg-bg`, `text-text-secondary`, `border-border`, etc.).

## Rules

1. **No hardcoded colors.** Always reference design tokens. Never inline hex in components.
2. **Warm over cold.** Brown-tinted grays, never blue-tinted.
3. **Borders over shadows.** Use shadows only for floating layers (modals, popovers, command palette).
4. **Opacity for hierarchy.** Text levels via `text`/`secondary`/`tertiary`/`quaternary`, not separate grays.
5. **Content-first.** Generous whitespace. No decorative chrome.
6. **Touch targets 34px+.** 44px on mobile.
7. **No empty decorative states.** If nothing to show, say why. Guide the user.
8. **Token names, not values.** Write `bg-bg-secondary`, not `#1C1C1E`. The theme layer handles the rest.
