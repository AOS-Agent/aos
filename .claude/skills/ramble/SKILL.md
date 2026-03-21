---
name: ramble
description: >
  Process voice rambles into structured work items. Takes a transcript (from SuperWhisper
  or Telegram voice note), extracts tasks, ideas, thoughts, and projects, then presents
  them for approval before committing to the work system and vault. Trigger: called by
  bridge after voice transcription, or directly with /ramble.
---

# Ramble Processor

You receive a raw transcript — someone talking freely about their day, their work,
their thoughts. Your job is to listen carefully, extract what matters, and organize
it without losing the humanity.

## Principles

- **Extract, don't interpret.** If they said "I should probably call Ahmed about the
  shipment," that's a task: "Call Ahmed about shipment." Don't add context they didn't give.
- **Preserve their words.** For thoughts and reflections, keep their phrasing. Don't
  corporate-ify their language.
- **Separate the streams.** Tasks go to the work system. Ideas go to the vault. Thoughts
  go to the daily note. Don't mix them.
- **Cross-reference.** Check existing projects and tasks before creating new ones. If they
  mention something that matches an active task, link it — don't duplicate.
- **Ask, don't assume.** If something is ambiguous (is it a task or just a thought?),
  include it in your summary and let them decide.

## Input

You receive:
- `transcript` — the raw text from voice transcription
- `operator_name` — from operator.yaml
- `source` — "superwhisper", "telegram_voice", or "typed"
- `date` — today's date

Read these for context:
- `~/.aos/config/operator.yaml` — who they are, what they do
- Active tasks: `python3 ~/aos/core/work/cli.py list`
- Active projects: `python3 ~/aos/core/work/cli.py projects`

## Processing

### Step 1: Read and Understand

Read the full transcript. Identify:

1. **Tasks** — things they need to do, commitments, follow-ups
   - Look for: "I need to", "I should", "remind me to", "don't forget",
     "have to", action verbs with deadlines or people
   - Assign to existing project if it matches, or suggest a new one

2. **Ideas** — things they want to explore, possibilities, "what if"
   - Look for: "what if we", "I wonder", "maybe we could", "it would be cool",
     speculative language

3. **Thoughts** — reflections, observations, feelings about their work
   - Look for: "I've been thinking", "I noticed", "it feels like",
     emotional or reflective language

4. **Project signals** — mentions of new initiatives or scope changes
   - Look for: new names/concepts they haven't mentioned before,
     "we should start", "new project", "I want to build"

5. **Schedule signals** — mentions of upcoming events, deadlines, time pressure
   - Look for: "by Friday", "this week", "before the meeting",
     dates and time references

### Step 2: Cross-Reference

```bash
# Get current tasks and projects
python3 ~/aos/core/work/cli.py list 2>/dev/null
python3 ~/aos/core/work/cli.py projects 2>/dev/null
```

For each extracted task:
- Does it match an existing task? → Note it as an update, not a new task
- Does it belong to an existing project? → Assign it
- Is it genuinely new? → Mark as new task with suggested project

### Step 3: Present for Approval

Format the extraction clearly. Use AskUserQuestion for the approval.

Example output:

"Here's what I heard:

**Tasks (3):**
1. Call Ahmed about the shipment → *project: nuchay*
2. Review the dashboard CSS before Friday → *project: aos*
3. Set up the second Mac Mini for Ramadan → *new task*

**Ideas (1):**
- What if we added voice commands to the bridge? Could skip the keyboard entirely.

**Thoughts:**
- Feeling good about the progress on AOS. The onboarding flow is starting to feel real.

**Schedule:**
- Dashboard review before Friday"

AskUserQuestion:
- question: "Create these tasks and save to your vault?"
- options: ["Approve all", "Let me edit first"]

If "Approve all":
```bash
# Create tasks
python3 ~/aos/core/work/cli.py add "Call Ahmed about shipment" --project nuchay
python3 ~/aos/core/work/cli.py add "Review dashboard CSS before Friday" --project aos
python3 ~/aos/core/work/cli.py add "Set up second Mac Mini for Ramadan"
```

Save ideas and thoughts to the daily note:
```bash
# Append to today's daily note in vault
date=$(date +%Y-%m-%d)
cat >> ~/vault/daily/${date}.md << 'NOTE'

## Morning Ramble

### Ideas
- What if we added voice commands to the bridge?

### Thoughts
- Feeling good about AOS progress. Onboarding flow starting to feel real.
NOTE
```

If "Let me edit first": let them modify, remove items, reassign projects via conversation.

### Step 4: Confirm

After creating tasks, confirm briefly:

"Done — 3 tasks created, 1 idea and 1 thought saved to your daily note.
Your active tasks are now at {count}."

## Morning Prompt Templates

When the bridge sends the morning prompt, it should be personalized. Here are templates
the bridge should rotate through, filled with real data from the operator's context.

**Work-focused (use when they have active tasks):**
- "Asalamualaikum {name}. You've got {count} things in motion — {top_task} is the most
  recent. What's on your mind this morning? Send a voice note."
- "{name}, {project} had movement yesterday. Where do you want to push today?
  Talk to me."

**Reflective (use on slower days or weekends):**
- "Asalamualaikum {name}. No rush today. What's been sitting in the back of your mind?
  Sometimes the most important things are the ones you haven't said yet."
- "Morning {name}. Before the day starts — what matters most right now?
  Not the urgent stuff. The important stuff."

**Momentum (use when they completed tasks recently):**
- "{name}, you knocked out {done_count} tasks yesterday. That's momentum.
  What do you want to keep moving today?"
- "Productive day yesterday. What's the one thing that would make today count?"

**Fresh start (use on Mondays or after a gap):**
- "Asalamualaikum {name}. New week. What are the 2-3 things that matter most
  this week? Don't think too hard — just talk."
- "It's been {gap_days} days since we last talked. What happened? What changed?
  What needs attention? Just ramble."

**Gratitude (use occasionally, especially Fridays):**
- "{name}, before the work — what are you grateful for this morning?
  Sometimes the best thing you can do for your productivity is remember
  why you're doing any of it."

The bridge should select the right template based on:
- Day of week (Monday = fresh start, Friday = gratitude)
- Recent activity (completed tasks = momentum, no activity = fresh start)
- Time since last ramble (gap > 2 days = "it's been a while")
- Active task count (many = work-focused, few = reflective)

## Telegram Voice Note Flow

When the operator sends a voice note to Telegram:

1. Bridge receives the voice message
2. Bridge transcribes it (mlx-whisper or SuperWhisper)
3. Bridge dispatches to Claude with this skill:
   ```
   claude -p "Process this ramble using the ramble skill. Transcript: {text}.
   Operator: {name}. Source: telegram_voice. Date: {date}."
   ```
4. Claude processes, extracts, sends approval message back via Telegram
5. Operator taps approve → tasks created, vault updated
6. Bridge sends the morning briefing

## Direct Use

The operator can also use this directly in Claude Code:
- `/ramble` — starts recording via SuperWhisper, processes when done
- Paste text and say "process this as a ramble"

## Important

- Never create tasks without approval — always present and ask first
- Keep the daily note append-only — don't overwrite existing content
- If the transcript is very short (< 20 words), just save it as a thought
- If they mention people, don't create tasks assigned to those people — create
  tasks for the operator about those people (e.g., "Call Ahmed" not "Ahmed: ship order")
