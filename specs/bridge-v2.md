# Bridge v2 — Telegram Experience Design

The bridge is AOS's mobile command center. Not a chat wrapper — the primary interface
for operating your system from your phone.

Supersedes: `telegram-upgrades.md` (tactical items folded into this spec).

**Status: APPROVED — 2026-03-24**

Implementation notes (from spec review):
1. Morning briefing + prompt → merge into one message with [What's on your mind?] button
2. Voice transcript → expandable blockquote (collapsed by default), not prominent at top
3. Overnight loop → Phase 4+ work, not Phase 1 (most complex new feature)
4. Shared decision store → button-triggered recording (not LLM judgment) + 30-day TTL
5. Multi-message batching → don't delay single messages; start immediately, append if more arrive within 5s

## Design Principles

1. **BLUF** — Bottom line up front. Most important thing first.
2. **Delta, not status** — Surface what changed, not what's the same.
3. **4 items max** per decision batch (cognitive load research, Cowan 2001).
4. **Tier by response time** — P0 interrupts, P1 is today, P2 is this week, P3 is pull.
5. **One notification per exchange** — Reactions and edits don't buzz. Minimize `sendMessage`.
6. **Decision layer, not execution layer** — Telegram is for decisions, approvals, direction. Terminal is for deep work.
7. **Voice-first** — On phone, voice is the primary input. Treat it as first-class.
8. **Buttons only on decision points** — No decorative buttons. Only when genuine choice exists.

## The Six Moments

Everything the operator does on Telegram falls into one of these:

| Moment | What | Speed | Path |
|--------|------|-------|------|
| "What's going on?" | Briefings, status, reviews | Push 2x/day | Daily topic |
| "Do this quick thing" | Add task, mark done, search | Instant (<500ms) | CLI shortcut |
| "Something's wrong" | Alerts, system ops, fixes | Immediate | Alerts topic |
| "Let me capture this" | Voice, links, thoughts | 5-30s via Claude | DM → vault |
| "Work with me on this" | Conversations, planning | Ongoing | DM / project topic |
| "What did we do?" | Observability, progress | On demand | DM query |

## Architecture

**One bot. Two spaces.**

### Space 1: Private DM — Personal Line

You text your assistant. No proactive notifications. Ever.

- Conversations, questions, voice notes, rambles
- Quick commands ("add task", "search vault", "mark X done")
- Skill invocation ("step by step through this", "extract this video")
- Content extraction (paste a link)

UX contract: When you open DM, it's because YOU initiated.

### Space 2: Forum Group — Organized Dashboard

Each topic is a stream with independent notification controls.
**Topics are created progressively** — not all at once. New users start with `daily` only.

| Topic | What Goes Here | Notification | Tier | Created |
|-------|---------------|-------------|------|---------|
| `daily` | Morning briefing, evening wrap | Normal | P1 | Always |
| `alerts` | Service down, disk critical, security | Loud | P0 | On first alert |
| `work` | Task completions, reminders, overdue | Silent | P2 | On first task action via Telegram |
| `knowledge` | Extractions, vault saves, research | Silent | P3 | On first capture |
| `system` | Agent activity, sessions, health | Muted | P3 | When user asks about system health |

Project topics are additional (one per project, created when project agent is activated):

| Topic | What Goes Here | Notification |
|-------|---------------|-------------|
| `nuchay` | Nuchay project agent chat | Normal |
| `chief-app` | Chief iOS app agent chat | Normal |

When a topic is created for the first time, a pinned welcome message explains its purpose.
Non-technical users may never see more than `daily` + one project topic.

### Notification Budget

| Content | Type | Where | When |
|---------|------|-------|------|
| Morning briefing | Push | daily | Once, morning |
| Morning prompt | Push | daily | After briefing |
| Evening wrap | Push | daily | Once, evening |
| P0 alerts | Push (loud) | alerts | Immediate |
| Task completions | Push (silent) | work | As they happen |
| Overdue reminders | Push (silent) | work | Morning batch |
| Extraction results | Push (silent) | knowledge | When done |
| Weekly digest | Push | daily | Sunday evening |
| System health | Pull | system | On demand |
| Agent activity | Pull | system | On demand |
| Session summaries | Pull | system | On demand |

**Daily buzz count: 2-3** (morning, evening, maybe one alert).

## The Daily Cycle

All in the `daily` topic.

### Morning Sequence

**Message 1: Briefing** — scannable in 10 seconds.

```
[sun] [Day of week], [Date]

[red] URGENT
  . [max 2 items — things that need action TODAY]

[yellow] IMPORTANT
  . [max 2 items — things to move forward this week]

[thought] THINK ABOUT
  . [open threads, unresolved decisions, brewing ideas]

[people] PEOPLE
  . [follow-ups owed, replies waiting, meetings today]

[overnight] OVERNIGHT (if applicable)
  . [results of overnight work requests from yesterday's evening wrap]
```

Rules:
- No system metrics (disk, RAM, service count). That's `system` topic.
- No trust scores. No session counts.
- Delta only: what needs attention, what changed, what was asked for.
- Use expandable block quotes for details on any item.
- If nothing is urgent: say "Nothing urgent today." Don't omit the section.

**Message 2: Prompt** — 15 minutes after briefing.

```
What's on your mind this morning? [mic]

Voice note or text — I'll organize it.
```

The briefing loaded context. The prompt catches what surfaces.
This triggers the ramble skill if they respond.

### Evening Sequence

**One message, warm and invitational:**

```
[moon] Wrapping up [Day]

[check] Done today:
  . [completed tasks, shipped work]

[clipboard] Still open:
  . [moved to tomorrow, unresolved]

Anything you want me to remember?
Anything I should work on overnight?
Anything on your mind before tomorrow?
```

Rules:
- Not a form. Not "rate your energy 1-5."
- Celebratory first, then open items, then invitation.
- If they respond with a request: action it or queue for morning.
- If they don't respond: fine. No follow-up.

### The Overnight Loop

Evening request -> system works autonomously -> morning briefing includes results.

Example flow:
1. Evening: "Draft the Nuchay pricing page"
2. Overnight: Agent works on it, saves draft
3. Morning: "[overnight] Completed: Nuchay pricing page draft [link]"

## Quick Commands — Instant Path

Structured commands bypass Claude and hit CLI tools directly for <500ms response.

| Pattern | CLI Target | Response |
|---------|-----------|----------|
| "add task: X" | `work add "X"` | "Added: X" |
| "mark X done" / "done: X" | `work done "X"` | "Done: X (N tasks remaining)" |
| "what's on my plate" / "tasks" | `work list` | Formatted task list with [Done] buttons |
| "search vault for X" | `qmd query "X"` | Top 3-5 results with links |

Everything else falls through to Claude. The intent matcher is simple pattern matching
on a small set of known commands — not a classifier. If ambiguous, goes to Claude.

**Rate-limit fallback:** When Claude is rate-limited, quick commands still work instantly.
For non-quick messages during rate limit, show: "Rate limited (~N min). Quick commands
still work — try 'tasks' or 'add task: X'."

## Real-Time Chat UX (DM + Project Topics)

### Message Flow

1. User sends message
2. Bot reacts with eyes (instant ack, no notification)
3. Typing indicator starts (every 4s)
4. Response streams:
   - DM: `sendMessageDraft` (progressive, no notification)
   - Forum: `sendMessage` then `editMessageText` (one notification)
5. Bot reacts with thumbs-up (success) or broken-heart (error)

### What's Removed

- **"Thinking..." bubble** — causes ghost notification, redundant with typing indicator
- **Tool status messages** — causes ghost notifications, developer info not user info
- **Triple reaction** (eyes -> lightning -> thumbs) — simplified to eyes -> thumbs/broken-heart
- **"Select an option:" separate message** — moved to inline keyboard on response
- **"Transcribing..." separate message** — typing indicator covers it
- **Separate transcript message** — folded into response as blockquote

### What's Added

- **Rate limit handling**: If >30s wait, edit the response message itself to show
  "Processing... rate limited, ~2 min wait." Then edit to real response when ready.
  One message, no ghosts.
- **Inline buttons on response**: Attach `reply_markup` to the last message chunk.
  Only when genuine choice exists — no decorative buttons.
- **Expandable block quotes**: For long content sections within responses.

### Button Rule

Buttons only when the response genuinely needs a decision. Not on every message.

```
GOOD: "Three pricing approaches..." [A] [B] [C]
BAD:  "Task marked as done" [OK] [Undo] [See tasks]
GOOD: "Task marked as done"  ← just the confirmation
```

Exception: [Undo] offered only for significant or irreversible actions.

### Voice Note Flow

1. User sends voice note
2. Bot reacts with eyes
3. Typing indicator starts (covers transcription time)
4. Response streams as one message (transcript as blockquote at top)
5. Bot reacts with thumbs-up

One exchange = one notification.

### Error Messages

Specific, not generic. Adapted to operator technical level.

Advanced: "Error: rate limit exceeded (resets in 5 min)"
Basic: "I'm temporarily busy. Try again in about 5 minutes, or use a quick command."

## Conversations on Telegram — The Decision Layer

Telegram is not a terminal. It's for decisions, direction, and async handoff.

### Design Rules

1. **Decisions, not execution.** Present options as buttons. Every response that needs
   input should end with a clear next action.

2. **Never block for >30 seconds.** If a task will take longer, go async:
   "Working on this. I'll post results to [topic] when done."

3. **Progress on long tasks.** Any async task >2 min gets a midpoint update (as edit,
   not new message). If it fails, immediate notification.

4. **Checkpoint long conversations.** Every 5-8 exchanges, offer an exit ramp:
   "Good progress. Continue, or save and pick up at your desk?"

5. **Terminal pickup.** When work needs the terminal, say so plainly:
   - Advanced: "This needs the terminal. Resume with `claude --resume [id]`"
   - Basic: "This needs your computer. I'll save everything — just open the project
     when you're at your desk and I'll pick up where we left off."

6. **Context-aware responses.** The system knows:
   - Schedule (operator.yaml) — during Teaching block, extra concise
   - Device (Telegram = mobile) — shorter responses, more buttons
   - Time of day — morning = action, evening = wind-down

### Async Pattern

```
< 30s estimated: Do it inline.
> 30s estimated: "Working on this. ~N min. I'll post to [topic]."
> 2 min elapsed:  Edit with progress: "Found 2 of 3. ~2 more min."
> 5 min elapsed:  Progress every 3 min.
Failure:          Immediate notification in original thread.
```

## Capture — Where Things Land and When They Surface

### Destinations

| Capture Type | Immediate Destination | Also Appears In |
|-------------|----------------------|-----------------|
| Voice ramble → tasks | Work system (`work add`) | Morning briefing next day |
| Voice ramble → ideas | `vault/inbox/ideas/` | Daily note, surfaces when relevant project active |
| Voice ramble → thoughts | Today's daily note | Evening wrap |
| Link → media | `vault/knowledge/media/` | Knowledge topic (silent) |
| Link → research | `vault/knowledge/research/` | Knowledge topic (silent) |
| Quick thought | Today's daily note + project tag | Evening wrap |
| "Remember X about Y" | `vault/projects/Y/notes/` + daily note | Surfaces when working on Y |

### Surfacing Rules

| What Was Captured | When It Surfaces |
|------------------|-----------------|
| A task | Next morning briefing |
| An idea tagged to a project | When that project is active (context injection) |
| A research extraction | When searching related topics, weekly digest |
| A thought about a person | When that person comes up in tasks or conversation |
| An overnight work request | Next morning briefing under OVERNIGHT |
| A "remember this" note | When the related project is active (QMD search) |

### Confirmation Pattern

Every capture confirms WHERE it landed and WHEN it'll surface:

```
Captured
  → Saved to Nuchay project notes
  → Tagged: pricing, schools
  → Will surface next time you work on Nuchay
```

Not just "noted" — explicit about destination and resurfacing.

## Alert System (alerts topic)

### Tiers by Risk

| Risk | Example | Action | Language (basic user) |
|------|---------|--------|---------------------|
| Safe to auto-fix | Service crashed | Fix + report | "Your dashboard went offline. I restarted it — all good." |
| Needs approval | Disk 92% full | Suggest + ask | "Storage is getting full. I can clean up old files to free ~5GB. Want me to?" [Clean up] [Show what] |
| Escalate always | Unknown error | Report only | "Something unexpected happened. Here's what I know: [details]. Want me to investigate?" |

### Alert Format

```
[warning] Your storage is almost full (92%).

I can clean up old logs and temporary files to
free about 5GB. Nothing important gets deleted.

  [Clean up] [Show me what you'd remove] [Ignore]

  > Technical details (expandable)
```

When resolved, the alert is EDITED (not a new message):

```
[check] Storage cleaned up — freed 5.2GB (now 73%)
Removed: old logs (2.1GB), build caches (3.1GB)
```

## Observability — "What Did We Do?"

### Quick Check (DM)

```
You: What happened today?

Claude: Today so far:
  . 2 terminal sessions (aos, nuchay) — 45 min total
  . 3 tasks completed, 1 created
  . Overnight: competitor research delivered
  . System: all healthy, no alerts

  [Details] [Show sessions] [Show tasks]
```

### Project Review (DM or project topic)

```
You: How's Nuchay going this week?

Claude:
Nuchay — Week of March 23

Work: 5 done, 2 added, 3 active
  . Deploy shipped Monday
  . Pricing research complete

Decisions made:
  . Target districts, not individual schools
  . JWT + refresh for auth

Knowledge added:
  . 2 research notes (SaaS pricing, competitor analysis)
  . 1 ramble organized (go-to-market direction)

Next:
  . District procurement research (in progress)
  . Free tier decision pending

  > Full activity log (expandable)
```

## Cross-Session Context

### The Problem

DM and project topics are separate Claude sessions. A decision in DM doesn't
automatically transfer to the Nuchay topic.

### The Solution: Shared Decision Store

When a significant decision or fact is established in any session, it's written
to `~/.aos/data/bridge/shared-context.json`:

```json
{
  "decisions": [
    {
      "project": "nuchay",
      "decision": "Target districts, not individual schools",
      "session": "telegram:6679471412",
      "timestamp": "2026-03-24T10:30:00Z"
    }
  ]
}
```

All sessions load shared context at start. Not full conversation sharing
(which would be confusing) — just decisions and facts.

## Operator Technical Level

The system adapts language based on `operator.yaml`:

```yaml
technical_level: advanced  # advanced | intermediate | basic
```

| Situation | Advanced | Basic |
|-----------|----------|-------|
| Terminal handoff | "Resume with `claude --resume abc`" | "I'll save this for when you're at your desk" |
| Alert detail | "Connection refused on :4096" | "Your dashboard stopped responding" |
| Error message | "Rate limit exceeded (resets in 5 min)" | "I'm temporarily busy. Try again in ~5 min" |

Default for new users: `basic`. Adjustable in settings.

## What Gets Killed

| Current Feature | Disposition |
|----------------|------------|
| "Thinking..." bubble | Removed — ghost notification |
| Tool status messages | Removed — ghost notification |
| Lightning reaction | Removed — redundant |
| "Select an option:" separate message | Moved to inline keyboard on response |
| "Transcribing..." message | Removed — typing indicator covers it |
| Separate transcript message | Folded into response as blockquote |
| Heartbeat every 30 min | Moved to system topic (silent) |
| Channel update hourly | Moved to system topic (silent) |
| Learning drips 3x/day | Removed (or opt-in) |
| Pattern compiler notification | Moved to system topic (silent) |
| Update checker notification | Moved to system topic (silent) |
| Reboot alert in DM | Moved to alerts topic |
| Energy/sleep form | Replaced by conversational evening prompt |

## What Gets Added

| Feature | Description |
|---------|------------|
| Progressive topic creation | Topics created on-demand, not all at once |
| BLUF morning briefing | Classified, scannable, 4-item-max |
| Conversational evening wrap | Celebratory + invitational, not a form |
| Overnight loop | Evening request → work → morning result |
| Quick command shortcuts | <500ms for structured commands, bypass Claude |
| Rate-limit fallback | Quick commands work even when Claude is limited |
| Inline action buttons on alerts | [Fix] [Acknowledge] [Silence] |
| Inline buttons on work items | [Done] [Snooze] |
| Expandable block quotes | Long content within any message |
| `disable_notification` | Silent for P2/P3, normal for P1 |
| Edit-in-place | Alerts resolve via edit, async tasks show progress via edit |
| Async with progress | Long tasks get midpoint updates, failure notifications |
| Checkpoint long conversations | Exit ramp every 5-8 exchanges |
| Terminal pickup | Clear handoff from phone to desk |
| Shared decision store | Cross-session context for decisions/facts |
| Capture confirmation | Explicit "where it landed" + "when it'll surface" |
| Technical level adaptation | Language adapts to user's technical comfort |

## Shipping Strategy

### What's Framework (auto-updates)

All Python code in `core/services/bridge/`:
- `telegram_channel.py` — routing, handlers, UX changes
- `message_renderer.py` — single rendering path (consolidate)
- `daily_briefing.py` — new briefing format
- `evening_checkin.py` — new evening format
- `heartbeat.py` — threshold changes, routing to topics
- `main.py` — forum topic setup, progressive creation
- `intent_classifier.py` — quick command shortcuts
- `shared_context.py` — cross-session decision store (new)

Ships via `git push` → 4am auto-update → bridge restart.

### What's User Config (untouched by updates)

- `~/.aos/config/operator.yaml` — schedule, preferences, technical_level
- `~/.aos/config/bridge-topics.yaml` — forum topic IDs (created by migration)
- Notification settings — user controls per-topic muting in Telegram
- Keychain secrets — bot token, chat IDs

### Migration Plan

A migration script handles first-time setup:
1. Create `daily` topic if it doesn't exist (bot needs admin)
2. Store topic thread IDs in `~/.aos/config/bridge-topics.yaml`
3. Pin welcome message in `daily` explaining the morning/evening cycle
4. Other topics created progressively by the bridge code itself

Runs once via `core/migrations/`. Existing forum group preserved.
Existing project topics kept, system topics added progressively.

### Rollout

| Phase | What | Risk |
|-------|------|------|
| 1 | UX fixes (kill ghost messages, simplify reactions) | Low |
| 2 | Quick command shortcuts (instant path) | Low |
| 3 | Forum topic structure + progressive creation | Medium |
| 4 | New daily cycle (briefing + evening format) | Low |
| 5 | Alert system with risk tiers + inline buttons | Low |
| 6 | Capture confirmation + surfacing rules | Low |
| 7 | Consolidate rendering paths | Medium |
| 8 | Shared decision store | Medium |
| 9 | Async pattern + progress updates | Low |
| 10 | Technical level adaptation | Low |

## Multi-Message Handling

Messages within **5 seconds** of each other are batched into one Claude call.

```
[0.0s] "hey"                              → 👀, start 5s timer
[1.2s] "check on the nuchay deploy"       → 👀, reset timer
[2.1s] "also the dashboard seems slow"    → 👀, reset timer
[7.1s] Timer expires → batch → one Claude call
```

Messages >5 seconds apart are separate interactions.

If Claude is already processing when a new message arrives:
- Acknowledge with 👀 immediately
- Queue the message
- When current response finishes, append queued message to context
- Respond to both in sequence

Quick command messages bypass the queue and execute instantly even while Claude is working.

## Offline Behavior

When the bridge is down and comes back up:

| Message Age | Action |
|-------------|--------|
| < 5 min | Process normally |
| 5-30 min | Process + note: "I was briefly offline. Handling your message from [time]." |
| > 30 min | Ask: "You sent this [time ago]: '[preview]'. Still relevant?" [Process] [Skip] |

Multiple stale messages: batch-summarize rather than processing one by one.

Missed scheduled messages:
- < 1 hour late: send it (briefing at 6:45 is still useful)
- > 1 hour late: skip (stale). Log the miss to system topic.

## Multi-Business Scaling

Auto-managed topic lifecycle:

| Project State | Topic State |
|--------------|-------------|
| Active (worked on in last 2 weeks) | Open (visible) |
| Dormant (no activity > 2 weeks) | Closed (hidden, searchable) |
| Activity resumes | Reopened automatically |

Uses Telegram API `closeForumTopic` / `reopenForumTopic`.
At any time, operator sees only 3-6 active topics.

Topic naming for multi-project businesses:
"Nuchay · API", "Nuchay · Marketing" — or flat if one project per business.
Operator controls granularity in `projects.yaml`.

The system manages visibility, not hierarchy.

## Research Sources

- Cognitive load: Cowan (2001) — 4-chunk working memory limit
- Notification research: Mark, Gudith, Klocke — "The Cost of Interrupted Work" (23 min recovery)
- Batch processing: Kushlev & Dunn (2015) — 3x/day email optimal
- Military briefings: BLUF format, CCIR tiering, INTSUM structure
- Executive briefings: Traffic light dashboard, chiefs of staff BLUF format
- Shutdown ritual: Cal Newport — Deep Work
- GTD daily review: David Allen — Getting Things Done
- BASB cycle: Tiago Forte — Capture/Organize/Distill/Express
- Eisenhower matrix: Urgent/important classification for notification tiers
- Alert tiering: SRE/hospital/military universal 4-tier model
- Dashboard design: Few (2006) — 5-second glance test
- Sleep consolidation: Wagner et al. (2004) — 2x insight after sleep
- Telegram API: Forum topics, expandable quotes, silent messages, inline keyboards
- Information architecture: Radiators vs refrigerators pattern
- Maker/manager schedule: Paul Graham — notification mode awareness
