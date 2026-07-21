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

## System alerts

Everything above is the law for briefings and chat. It is *doubly* the law for
**system alerts** — the messages the machine sends on its own when a service
falls over, a check fails, a drive goes missing, a login expires. These are the
ones most likely to regress into a stack trace wearing a trenchcoat, because
they're assembled from internal fields (`CheckResult.message`, a service slug, a
file path) instead of being written by hand. They still land on a phone. They
still get read by a person who wants to know two things: *what happened* and
*do I need to do anything.*

### Alert anatomy

Every alert is at most four short parts, in this order:

1. **One emoji, leading.** It sets the tone before a word is read — `🔧` I
   fixed something, `⚠️` heads-up, `🚨` urgent, `🧹` tidy-up, `😴` paused,
   `✅` recovered.
2. **One line: what happened, in human terms.** "The transcriber stopped."
   Not "service_loaded: transcriber (health_url 7602 unreachable)."
3. **One line: what I did, or what you should do.** "I've restarted it and it's
   healthy again." / "Two-minute fix: System Settings → Privacy & Security → …"
   If there's nothing to do, say so — "Nothing urgent."
4. **Optional tail: where the detail lives.** "Details are in the log." Never
   inline the detail itself.

### What never goes in a system alert

- **Slugs.** `dead_code`, `volume_access`, `bridge_poll_liveness` — these are
  check IDs, not English. Translate them.
- **Paths and filenames.** `~/.aos/logs/transcriber.err.log`, `bridge-topics.yaml`.
  The operator can't act on a path from their phone. It belongs in the log.
- **Lists of raw items.** Not "orphaned: aos-report, eventd, foo, bar, baz."
  A **count** is fine — "7 old scripts" — the list goes in the log.
- **CI / infra jargon.** "plist", "LaunchAgent", "kickstart", "migration 083",
  "reconcile", "ship-check", "TCC", "watermark", "backfill", "entities".
  Say what the reader experiences: "voice-note text", "background service",
  "your login expired".
- **Version and commit refs**, bracketed codes (`[executing]`, `[0.87]`),
  task IDs (`aos#170`). Same rule as everywhere else.

### Transformations to aim for

| Raw (don't send)                                              | Human (send this) |
|--------------------------------------------------------------|-------------------|
| `⚠️ dead_code: Dead code detected — 7 orphaned bin scripts…` | 🧹 Found 7 old scripts nobody uses anymore. I'll list them for cleanup when you're ready — nothing urgent. |
| `volume_access NOTIFY: AOS-X not accessible (TCC revoked?)`  | ⚠️ I can't read the external drive — usually a Mac permission got reset after an app update. Two-minute fix: System Settings → Privacy & Security → Files and Folders → allow your terminal. |
| `Comms backfill PAUSED — login expired`                      | 😴 Overnight reading paused — my login expired. Run /login next time you're at the Mac and I'll pick up where I left off. |
| `[AOS] transcriber was down — restarted successfully.`       | 🔧 The transcriber stopped — I've restarted it and it's healthy again. |

### Centralize the translation

Don't make every check a copywriter. When a message is assembled from internal
fields, put the check-name → friendly-template map in **one** place with a
fallback that at least strips slugs and paths. For reconcile that's
`core/infra/reconcile/alert_copy.py` (`humanize_finding`, `render_report`); the
runner keeps the raw message for the log and routes only the phone-facing copy
through it. New check? Add a template there — and if you forget, the fallback
still scrubs the raw string so nothing lands verbatim.
