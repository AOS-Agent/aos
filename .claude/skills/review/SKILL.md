---
name: review
description: >
  Generate daily, weekly, or monthly reviews of work and progress.
  Trigger on "/review", "review my day", "weekly review", "how did this week go",
  "what got done", "daily summary", "monthly retrospective".
allowed-tools: Bash, Read, Glob, Grep
---

# /review -- Work Reviews & Reflections

Generate structured reviews by reading work data, vault, and session history.

## Usage

```
/review daily     — What happened today, what's tomorrow
/review weekly    — This week's patterns, progress, drift
/review monthly   — Goal scoring, retrospective, next month
```

## Daily Review

**Data sources:**
1. Work tasks: `python3 ~/aos/core/work/cli.py list --status done` (completed today)
2. Work tasks: `python3 ~/aos/core/work/cli.py list` (remaining active/todo)
3. Daily log: `~/vault/log/days/YYYY-MM-DD.md` (health, sessions, work summary)
4. Sessions: `~/vault/ops/sessions/` for recent session summaries

**Output format:**

```
## Daily Review -- [Date]

**Energy**: [from daily note or "not logged"]
**Sessions**: N sessions today

### Done
- [task title] (completed)
- [work accomplished outside task system]

### Carried Over
- [tasks that were active but not completed]

### Blockers
- [anything that slowed progress]

### Tomorrow
- [top 1-3 priorities based on priority + due dates]
```

## Weekly Review

**Data sources:**
1. All tasks completed this week: filter by completed date
2. Goal progress: `python3 ~/aos/core/work/cli.py goals`
3. Daily logs from the week: `~/vault/log/days/` for health/energy/work patterns
4. Session summaries: `~/vault/ops/sessions/` for this week
5. Friction reports: `~/vault/ops/friction/` for this week
6. Work summary: `python3 ~/aos/core/work/cli.py summary`
7. Initiative state: `python3 ~/aos/core/work/cli.py initiatives` for active initiative progress

**Output format:**

```
## Weekly Review -- [Date Range]

### Progress
- [goal]: [specific movement this week]

### Completed (N tasks)
- [list of completed tasks, grouped by project]

### Patterns
- [energy/time observations]
- [recurring friction]

### Drift Check
- Working on what we said? [Yes / Drifted toward X]
- Biggest unplanned time sink: [what]

### Initiative Progress
- [{initiative title}] [{status}]: {one-line summary of movement this week}
  - Phase progress: {completed}/{total} phases
  - Tasks completed this week: {count}

Stale Initiatives:
- [{title}] [{status}, {N} days untouched] — archive or pick up?

### Inbox
- [N items awaiting triage -- list if < 5]

### Next Week
- [1-3 suggested priorities]
```

## Monthly Review

**Data sources:**
All weekly data plus goal key results and trend analysis.

**Output format:**

```
## Monthly Review -- [Month Year]

### Goal Scorecard
| Goal | Progress | On Track? |
|------|----------|-----------|

### Initiative Scorecard
| Initiative | Status | Started | Progress | On Track? |
|-----------|--------|---------|----------|-----------|
| {title} | {status} | {created} | {phase}/{total} | {yes/drift/stale} |

Completed This Month:
- [{title}] — {actual time} vs {appetite} appetite. {one-line retrospective}.

Stale (>7 days):
- [{title}] — recommendation: archive or resurface?

### Wins
- [biggest accomplishments]

### Gaps
- [what didn't move]

### System Health
- [is the work system itself working? friction points?]

### Next Month
- [adjusted priorities]
```

## How to Generate

1. Gather data from all sources listed above
2. Synthesize -- don't just list raw data, identify patterns
3. Be honest about drift and gaps
4. Keep it scannable -- the operator reads this in 2 minutes, not 10
5. Save the review to `~/vault/reviews/` if it's weekly or monthly

## Rules

- Daily reviews are ephemeral -- show them, don't save unless asked
- Weekly reviews get saved: `~/vault/log/weeks/YYYY-wNN.md`
- Monthly reviews get saved: `~/vault/log/months/YYYY-MM.md`
- Always cite evidence -- "3 of 5 tasks completed" not "good progress"
- If data is missing (no daily notes, no sessions), say so -- don't fabricate

## Initiative Review

When an initiative reaches status: review (all phases complete):

**Data sources:**
1. Initiative document: full content including progress log
2. Task completion dates vs estimates
3. Session history linked to the initiative
4. Decision log analysis

**Output format:**

```
## Initiative Review -- {title}

### Summary
{What was built, in 2-3 sentences}

### Timeline
- Started: {created date}
- Completed: {today}
- Appetite: {appetite} | Actual: {calculated from dates}
- Phases: {N} | Sessions: {count from progress log}

### What Worked
- {patterns from smooth phases}

### What Didn't
- {patterns from difficult phases, blockers}

### Decisions Revisited
- {any locked decisions that turned out right/wrong}

### Lessons for Next Initiative
- {concrete takeaways}
```

Save to initiative document's ## Review section and to `~/vault/reviews/`.
Update initiative frontmatter: status → done.
