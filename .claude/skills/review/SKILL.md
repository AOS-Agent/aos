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
1. Work tasks: `python3 ~/aosv2/core/work/cli.py list --status done` (completed today)
2. Work tasks: `python3 ~/aosv2/core/work/cli.py list` (remaining active/todo)
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
2. Goal progress: `python3 ~/aosv2/core/work/cli.py goals`
3. Daily logs from the week: `~/vault/log/days/` for health/energy/work patterns
4. Session summaries: `~/vault/ops/sessions/` for this week
5. Friction reports: `~/vault/ops/friction/` for this week
6. Work summary: `python3 ~/aosv2/core/work/cli.py summary`

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
