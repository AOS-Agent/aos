# Life OS — Task & Life Management Design

**For:** Hisham Al Hadi
**Problem:** Everything lives in his head. No external system. Overwhelmed by the volume across teaching, businesses, family, faith, health, and projects. Snooze-then-scramble pattern erodes self-trust.
**Goal:** AOS holds everything so his head doesn't have to, and puts the right thing in front of him at the right time.

---

## Design Principles

### 1. The system must be easier than ignoring it
If checking in takes effort, it won't happen. The primary interface is Telegram — something already on his phone, already open. Not an app to download, not a website to visit.

### 2. Show one thing, not everything
Never present a wall of tasks. The daily message shows: one focus item, one thing AOS did overnight, one thing that needs a decision. That's it. More is available if he asks.

### 3. Do the work, don't just track it
The gap isn't awareness — Hisham knows what needs to happen. The gap is activation energy. AOS should do real work (draft emails, research apartments, prepare course notes) and present it for review, not just remind.

### 4. Trust is earned, not assumed
Start at Level 1: everything gets reviewed. As AOS proves reliable on small things, trust grows. The system tracks its own accuracy so trust is data-driven, not emotional.

### 5. Respect the rhythm
Teaching is Mon–Thu 8:30–9:45. Weekend classes Sat–Sun after Fajr. Ramadan changes everything. The system knows the calendar and adapts — no notifications during class, no overwhelming messages during Ramadan.

---

## Life Domains

Everything in Hisham's life falls into these areas. Each domain has its own weight and rhythm:

| Domain | Examples | Rhythm |
|--------|----------|--------|
| **Deen** | Quran teaching, Quran Garden, weekend classes, personal study | Daily anchor — this is the core |
| **Family** | Haydar, Umarrah, parents, siblings | Always present |
| **Nuchay** | Business revival, products, marketing, operations | Needs forcing function — no external deadline |
| **Livelihood** | School income, other income, Elora courses, rental property | Monthly cycle + July deadline |
| **Home** | Moving by May 1st, household | Urgent deadline |
| **Health** | Weight, exercise, diet | No forcing function — needs system support |
| **AOS/Tech** | This system, Qurabic app, infrastructure | Tool-building in service of other domains |

### What this means for the system
- Tasks tagged by domain, not just "project"
- `/gm` groups by domain, not by urgency
- Some domains have natural deadlines (Home: May 1, Elora: July). Others need the system to create gentle pressure (Nuchay, Health)

---

## Where Tasks Live

```
~/vault/tasks/
├── Tasks.base          ← Obsidian database view
├── some-task.md        ← Individual task files
└── archive/            ← Done tasks moved here after 14 days
```

### Task Schema

```yaml
---
title: "Restart Klaviyo email campaigns"
status: backlog | todo | focus | in-progress | waiting | done
domain: nuchay | deen | family | livelihood | home | health | aos
priority: 1-3          # only 3 levels: must, should, could
created: 2026-03-17
due: null               # real deadline, not aspirational
waiting_on: null        # person or event blocking this
created_by: operator | inbox | bridge
---

Optional body with context, links, log entries.
```

### Key design decisions

**`focus` status**: Not just "todo" — explicitly "I am doing this today/this week." Max 3 items in focus at any time. Al-Mala' pushes back if you try to add a 4th.

**3 priority levels, not 5**: Must (drop everything), Should (this week), Could (when there's space). Simpler = actually used.

**`domain` not `project`**: "Find a new apartment" isn't a project — it's a life domain task. Nuchay tasks and AOS tasks are projects, but teaching prep and health goals are domains. Everything fits.

**`waiting_on` field**: Half of overwhelm is things you can't act on right now but can't forget. "Waiting for landlord to confirm move-out date" is not a task — it's a wait. The system tracks it and pings you when it's been too long.

**No subtasks**: If a task needs subtasks, it's actually a project. Write the steps in the body. Keep the frontmatter clean.

---

## Daily Flow

### Morning (automatic — Telegram /gm)

Sent at 8:00 AM, before class:

```
Salaam Hisham — Tuesday, March 18

FOCUS (1 of 3)
→ Review Nuchay email drafts (AOS prepared 3 last night)

TODAY'S ANCHORS
  8:30 Quran Studies — Al-Kahf continues
  7:00 PM Quran Garden — Tafsir + grammar

WAITING ON (2)
  ⏳ Landlord — move-out confirmation (5 days)
  ⏳ Elora — course access credentials

NEEDS A DECISION
  📋 Nuchay: clear 36 TINGLY lip scrub units at 50% off? (Y/N)

AOS OVERNIGHT
  ✅ Drafted 3 Klaviyo welcome-back emails
  ✅ Found 4 apartment listings in Mississauga under $2,200
```

Short. Scannable. One focus item, not ten. He reads it over coffee, replies "Y" to the decision, and goes to class.

### During the day (Telegram — on demand)

```
/tasks              → show focus + today items only (not backlog)
/tasks all          → show everything grouped by domain
/tasks add <name>   → quick capture → backlog
/tasks focus <name> → move to focus (Al-Mala' warns if >3)
/tasks done <name>  → mark done
/tasks wait <name> <who> → mark waiting
```

### Evening (/close-day — automatic at 9 PM)

```
Today:
  ✅ Reviewed Nuchay emails → sent 1, edited 2
  🔄 Apartment search still in progress
  ⬜ Elora course — didn't start (3rd day)

Tomorrow's suggestion:
  → 30 min on Elora Module 1 (July deadline = 106 days)

Anything to capture before bed? (reply or skip)
```

The Elora nudge isn't guilt — it's math. 106 days, X modules, here's the pace needed. The system does the calculation so he doesn't have to.

---

## Proactive Work — What AOS Actually Does

This is the trust-building layer. Not reminders — real output.

### Level 1 (now — everything reviewed)
- Draft email campaigns for Nuchay → present for approval
- Research apartment listings → summarize top picks
- Prepare weekly Quran Studies notes → organize ayah references
- Reconcile Wave accounting → flag discrepancies

### Level 2 (earned — after proving reliable)
- Send approved email campaigns on schedule
- Auto-reply to routine Nuchay customer emails
- Update inventory counts after Shopify sales
- Post to Nuchay social media (pre-approved content)

### Level 3 (autonomous — significant trust)
- Handle Nuchay customer service end-to-end
- Manage email marketing calendar
- Adjust Shopify pricing based on inventory/margins
- Reorder supplies when inventory drops below threshold

Each completed task at Level 1 builds evidence. After 20 successful reviews with zero reverts, Al-Mala' suggests: "You've approved all 20 email drafts without changes. Want to let me send these automatically?"

---

## Smart Capture

### From /inbox (WhatsApp, iMessage, Email)
```
/inbox processes messages → extracts actionable items →
creates task file with:
  status: backlog
  created_by: inbox
  domain: auto-detected from content
  body: source message + context
```

### From Telegram /capture
```
/capture Find halal beard oil supplier in Mississauga
  → creates task, domain: nuchay, status: backlog
```

### From agent sessions
When an agent discovers work (e.g., ops finds a service is down), it creates a task with `created_by: ops` rather than just alerting. The task persists even if the alert is dismissed.

### Triage
Untriaged items (status: backlog, no priority) surface in `/gm` as a count:
"4 items need triage — reply /triage to review"

`/triage` sends them one at a time:
```
"Find halal beard oil supplier in Mississauga"
[from: /capture, 2 days ago]

Domain: nuchay (auto-detected)
Priority suggestion: 3 (could)

[✅ Accept] [✏️ Edit] [❌ Dismiss]
```

One tap. Next item. No app switching.

---

## What Doesn't Bloat

### Auto-archive
- `status: done` tasks move to `archive/` after 14 days
- Archive is QMD-indexed (searchable) but hidden from Base view
- `/close-day` and a weekly cron handle the move

### Soft limits
- Max 3 focus items → enforced by Al-Mala'
- Max 15 todo items → warning in /gm: "15 todos — consider moving some to backlog"
- Max 5 agent-created tasks per day → after that, agents append to a single "suggested work" note
- Waiting items auto-escalate after 14 days: "Still waiting on X — should we follow up?"

### What gets deleted vs archived
- Dismissed triage items → deleted (never needed)
- Done tasks → archived (context for /drift and reviews)
- Recurring tasks → regenerate, don't accumulate

---

## Recurring Tasks

Some things repeat. Instead of creating N copies, a `recurring.yaml` defines them:

```yaml
recurring:
  - title: "Weekly Quran Garden prep"
    domain: deen
    frequency: weekly
    day: monday
    priority: 1

  - title: "Review Nuchay orders and inventory"
    domain: nuchay
    frequency: weekly
    day: wednesday
    priority: 2

  - title: "Elora course — 1 lesson"
    domain: livelihood
    frequency: daily
    until: 2026-07-31
    priority: 2

  - title: "30 min walk or exercise"
    domain: health
    frequency: daily
    priority: 3
```

A cron checks daily and creates task files for today's recurring items if they don't exist. Simple. No task explosion — only today's items materialize.

---

## Integration Map

```
Telegram /gm        → reads focus + waiting + untriaged count
Telegram /tasks      → CRUD on task files
Telegram /triage     → one-at-a-time backlog review
Telegram /close-day  → evening review + archive
Telegram /inbox      → messages → task files
Telegram /capture    → quick text → task file
Obsidian mobile      → browse Tasks.base, edit frontmatter
Obsidian desktop     → full board view, wiki-links between tasks and notes
Dashboard :4096      → HTML render of active tasks
/gm briefing         → focus + anchors + waiting + overnight work
/drift               → compare task domains vs goals
Hudhud heartbeat     → stale task detection, deadline warnings
Al-Mala'             → priority advice, focus cap enforcement
QMD                  → semantic search across active + archived tasks
```

---

## What This Replaces

| Before | After |
|--------|-------|
| Plane (11 containers, 1.6GB RAM) | Markdown files in vault |
| config/tasks.yaml (empty) | ~/vault/tasks/*.md |
| Everything in Hisham's head | Captured in task files |
| Snooze-then-scramble | System surfaces deadlines with math, not guilt |
| "I should do X" with no follow-through | AOS does the first draft, Hisham reviews |

---

## Chief — The Command Center (iOS + macOS)

Chief is the primary interface to AOS. Thin client, all intelligence server-side.

### Architecture

```
iPhone/Mac (Chief app)
    ↕ Tailscale
Mac Mini (AOS)
    ├── Listen :7600 → /chief/* API endpoints
    ├── Vault ~/vault/ → source of truth (markdown)
    ├── Context Engine → computes what to show
    └── Agents → do real work in background
```

### Context Engine (`GET /chief/context`)

The brain of the home screen. Returns an ordered list of cards based on:
- Current time + day of week
- Teaching schedule (Mon–Thu 8:30–9:45, Tue Quran Garden, Sat–Sun Fajr)
- Upcoming deadlines (May 1 move, July Elora, Eid curriculum switch)
- Task status (focus, overdue, waiting, stale)
- Health data (energy level, sleep quality)
- Overnight agent work (what was completed)
- Untriaged inbox items
- Waiting items that are getting stale

Chief just renders whatever comes back. No logic in the app.

### Screens

1. **Home** — dynamic context cards that change throughout the day
2. **Capture** — text, voice, task, photo → all route to vault
3. **Domains** — tap into any life area (Deen, Nuchay, Family, etc.)
4. **Profile** — AOS status, trust level, settings

### Widgets (iOS home screen)
- Focus widget — 1–3 items, checkable
- Energy widget — tap to log
- Next anchor widget — "Quran Studies in 45 min"
- Nuchay widget — today's orders + revenue

### Push Notifications (via APNs)
Only for: deadline warnings, AOS work ready for review, stale waits, morning briefing

### Offline Mode
Cache last context response. Captures queue locally, sync on reconnect.

---

## Voice Ramble

The most natural input for stream-of-consciousness thinkers.

### How it works
1. Hisham taps "Ramble" in Chief (or sends a voice note via Telegram)
2. Audio transcribed (Whisper on-device or server-side)
3. AOS parses the transcript into structured data:
   - **Tasks** → created as vault task files with auto-detected domain
   - **Ideas** → saved to `~/vault/ideas/`
   - **Reflections** → appended to today's daily note
   - **Decisions** → surfaced as decision cards in next Chief refresh
   - **Follow-ups** → created as waiting tasks with person tagged
4. Full transcript saved to daily note under `## Ramble`
5. Parsed items shown back for quick review: "I extracted 3 tasks, 1 idea, 1 follow-up — correct?"

### Example
Voice: "So I need to follow up with the landlord about the move-out date, also had an idea for a KING beard oil bundle with SHAY butter, and I didn't eat well today, I had McDonald's again..."

Parsed:
- Task: "Follow up with landlord re: move-out date" → domain: home, status: waiting, waiting_on: landlord
- Idea: "KING + SHAY bundle product" → saved to vault/ideas/
- Health log: low diet quality → appended to daily note

---

## Daily Note as Black Box Recorder

The daily note at `~/vault/daily/YYYY-MM-DD.md` becomes the complete record of each day.

### What flows into it (automatically)

```yaml
---
date: 2026-03-19
day: Wednesday
energy: 3
sleep: 6.2
mood: null  # logged via Chief or evening check-in
---

## Anchors
- 8:30–9:45 Quran Studies (Al-Kahf)
- 7:00 PM Quran Garden

## Focus
- [ ] Review Nuchay email drafts
- [x] Reply to landlord
- [ ] Elora Module 1, Lesson 1

## Captures
- **09:12** — Voice ramble (3 tasks, 1 idea extracted)
- **14:30** — /capture "check if SQUAL inventory is accurate"

## Ramble
> Full transcript of morning voice dump...

## Communications
- **Email**: 3 received (1 Nuchay order, 1 Al Huda, 1 personal)
- **WhatsApp**: 12 messages (2 actionable)
- **iMessage**: 4 messages

## Sessions
- 10:15–11:30 — AOS: Chief feature design (45 min, 12 tool uses)
- 14:00–14:20 — Nuchay: inventory check

## Health
- Steps: 4,200 | Calories: 1,800 | Sleep: 6.2h
- Diet note: "McDonald's — low quality"

## Evening Check-in
- Energy: 3/5
- Accomplishments: replied to landlord, reviewed email drafts
- Tomorrow's intention: Elora Module 1
```

### Data sources
| Source | How it feeds in |
|--------|----------------|
| HealthKit | Via HealthSync iOS app → `data/health/` → daily note |
| Email | `/inbox` cron scans Gmail accounts → summary line |
| WhatsApp | whatsmeow bridge → message counts + actionable items |
| iMessage | AppleScript reader → message counts |
| Claude sessions | session-export cron → session summaries |
| Voice rambles | Chief/Telegram → transcribe → parse → daily note |
| Captures | /capture → appended with timestamp |
| Browser history | (future) Chrome history export → topics/domains |
| Task completions | /tasks done → checked off in Focus section |

### Obsidian compatibility
- Fully valid markdown with YAML frontmatter
- Daily Notes Base view already works
- Wiki-links to tasks, ideas, session notes
- Obsidian mobile shows the full picture of any day

---

## Self-Improving App (Autonomous Development)

Chief improves itself based on usage patterns and operator feedback.

### How it works

1. **Usage telemetry** — Chief reports anonymized interaction data to AOS:
   which screens opened, which cards tapped, how long spent, what was ignored
2. **Voice ramble parsing** — when Hisham mentions UI issues ("the font is too small",
   "I keep missing the capture button"), AOS extracts these as Chief improvement tasks
3. **Research** — AOS proactively researches iOS design patterns, SwiftUI best practices,
   accessibility guidelines, and applies relevant improvements
4. **Implementation** — engineer agent modifies SwiftUI code, builds, runs tests
5. **Deployment** — TestFlight upload → auto-installs on Hisham's phone (OTA)
6. **Feedback loop** — next usage telemetry shows if the change helped

### Technician Agent (self-healing)

A dedicated agent monitors Chief health:
- **Crash detection** — if Chief crashes, technician reads crash log, diagnoses, patches, rebuilds, deploys
- **API failures** — if `/chief/context` returns errors, technician fixes the endpoint
- **Build failures** — if Xcode build breaks after a code change, technician reverts or fixes
- **Self-triggering** — runs via Listen job server, no operator intervention needed
- **Escalation** — if the fix requires a design decision, creates a decision card in Chief instead of guessing

### OTA Deployment Pipeline

```
Code change (by engineer agent or technician)
    → XcodeBuildMCP builds (xcodebuild via MCP tools)
    → xcsift filters errors (only actionable output)
    → Screenshot at 1x (ImageMagick resize) → visual verify
    → fastlane beta (match + gym + pilot)
    → TestFlight auto-distributes to Hisham's phone
    → Push notification: "Chief updated — [what changed]"
```

No cable. No manual steps. AOS builds and ships.

### Installed Toolchain
- **XcodeBuildMCP** v2.3.0 — 59 structured tools for build/test/simulator
- **xcsift** — filters xcodebuild output to errors/warnings only
- **ImageMagick** — screenshot resize to 1x for token efficiency
- **Fastlane** — match (signing), gym (build), pilot (TestFlight upload)
- **SwiftUI Agent Skill** — Paul Hudson's SwiftUI best practices
- **iOS Simulator Skill** — 21 scripts for semantic UI automation

### macOS App

Same codebase, SwiftUI with platform conditionals:
- Menu bar widget (always visible) — focus items + energy
- Full window — same as iOS but with more space
- Shares all API endpoints with iOS
- Built and deployed alongside iOS via Xcode

---

## What This Replaces

| Before | After |
|--------|-------|
| Plane (11 containers, 1.6GB RAM) | Markdown files in vault |
| config/tasks.yaml (empty) | ~/vault/tasks/*.md |
| Everything in Hisham's head | Captured in task files + voice rambles |
| Snooze-then-scramble | System surfaces deadlines with math, not guilt |
| "I should do X" with no follow-through | AOS does the first draft, Hisham reviews |
| Multiple apps per life area | Chief — one app, all domains |
| Manual app updates | OTA via TestFlight, autonomous deployment |
| Static app that never changes | Self-improving based on usage + feedback |

---

## Implementation Order

### Phase 1: Foundation (this week)
1. Task schema + Base view finalized
2. Bridge `/tasks` rewrite — vault files instead of Plane
3. `/gm` rewrite — new morning format
4. Context engine API (`/chief/context`)
5. Chief HomeView rewrite — render context cards

### Phase 2: Daily Flow
6. `/triage` command — one-at-a-time backlog review
7. Recurring tasks cron
8. `/close-day` rewrite — evening sweep + archive + daily note
9. Daily note auto-aggregation (email, WhatsApp, sessions)
10. Voice ramble — record + transcribe + parse

### Phase 3: Autonomous
11. TestFlight deployment pipeline (requires Developer account activation)
12. Technician agent — crash detection + self-healing
13. Usage telemetry + self-improvement loop
14. Push notifications via APNs
15. macOS app (menu bar + full window)

### Phase 4: Trust Building
16. Proactive work engine — Nuchay email drafts (Level 1)
17. Trust tracker — log approvals/reverts
18. Level-up suggestions
19. Domain dashboards in Chief (Nuchay revenue, health trends, etc.)
20. iOS widgets
