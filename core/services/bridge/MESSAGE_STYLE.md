# Telegram message style

Every message the bridge sends lands on a phone, in a chat, read in a few
seconds between other things. Write for that reader — a person, not a dashboard.
This applies to the briefing, the evening wrap, notifications, and anything a
Claude session sends back through the bridge.

## The rules

1. **Plain human English.** Short sentences. Say "overdue by 3 days", not
   "overdue 3d". Say "top priority", not "P1". Say "in progress", not
   "[executing]".

2. **No internal codes or IDs.** No priority codes (P1/P2), no bracketed status
   tags (`[executing]`, `[shaping]`), no task/initiative IDs (`aos#167`), no
   commit hashes (`56ffdca`), no version-gate or CI jargon ("ship-check",
   "reconcile", "gate"). If the reader can't act on it, leave it out.

3. **Lead with what matters.** Bottom line up front. The most important line is
   the first line. Group the rest under clear sections.

4. **Bold section headers, one emoji each.** One emoji marks a section so the
   eye can find it while scrolling: `🔴 Urgent`, `🟡 Important`, `🌙 Overnight`.
   Don't sprinkle emoji mid-sentence.

5. **Scannable.** One item per line, a few items per section. Prefer four items
   over ten — a phone screen and a tired brain both fill up fast.

6. **Warm, not robotic.** It's a companion, not a ticketing system. "Plate is
   clear." reads better than "0 open tasks."

## Quick swaps

| Don't write        | Write instead              |
|--------------------|----------------------------|
| `— P1 active`      | `— top priority`           |
| `— P2`             | `— worth doing this week`  |
| `[executing]`      | `in progress`              |
| `phase 2/3`        | `phase 2 of 3`             |
| `overdue by 3d`    | `overdue by 3 days`        |
| `aos#167 fixed`    | (drop the ID, or name it)  |
| `v0.6.11 56ffdca`  | `AOS v0.6.11`              |

When adding a status or code to a message, map it to plain words first — see
`_STATUS_WORDS` / `_status_words()` in `daily_briefing.py` for the pattern.
