---
name: report
description: >
  Investigate, diagnose, and report AOS issues to the developer. Trigger on
  "this is broken", "there's a bug", "report this", "something's wrong with",
  "/bug", "/report", "file an issue", "the [service] keeps [failing]", or any
  user frustration with AOS behavior. Also trigger on "I have an idea",
  "it would be nice if", "feature request", "I wish", "/idea" for enhancement
  requests. Activate proactively when you notice AOS errors during normal work
  — but only for issues that are clearly AOS-related (services, skills, config,
  crons, hooks, dashboard, bridge, work system), not external environment
  problems (network, disk, hardware).
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# /report — Investigate, Diagnose, Report

A user told you something is broken, confusing, or missing — or you noticed
it yourself during normal work. Your job: investigate like an engineer,
diagnose the root cause, propose a fix if possible, and file a clean report
to GitHub so the developer can act on it.

The value of this skill is the *diagnosis*, not relaying the complaint. A
GitHub issue that says "bridge crashes when operator.yaml has no schedule
field — null check needed at main.py:47" saves the developer hours compared
to "bridge is broken."

## Step 1: Triage — Is This an AOS Issue?

Before investigating, determine whether this is something AOS can fix:

| AOS issue (investigate) | NOT an AOS issue (help directly) |
|------------------------|----------------------------------|
| Service crashing or not responding | WiFi/internet down |
| Hook failing on session start | Disk full |
| Dashboard showing wrong data | macOS system update broke something |
| Cron job not running | User confused about non-AOS tool |
| Skill not triggering | Hardware failure |
| Config in wrong location | External API down (not ours) |

If it's not an AOS issue, help the user directly and don't file a report.
Say: "This looks like a [network/disk/etc.] issue rather than an AOS bug.
Let me help you fix it directly."

## Step 2: Classify

| Type | Label | Severity | Example |
|------|-------|----------|---------|
| **Bug** | `bug` | medium | "the bridge keeps crashing" |
| **Friction** | `friction` | low | "I can never find my tasks on the dashboard" |
| **Idea** | `enhancement` | low | "I wish the morning briefing showed weather" |
| **Missing** | `missing-feature` | low | "there's no way to snooze a task" |

Severity adjustments:
- User is frustrated or something is actively broken → **high**
- Service down or data loss possible → **critical**
- Minor annoyance, workaround exists → **low**

## Step 3: Investigate

This is the most important step. Do real work before filing anything.

### For bugs — follow the trail:

```bash
# 1. Service status
launchctl list 2>/dev/null | grep com.aos

# 2. Relevant logs (pick the right one)
tail -50 ~/.aos/logs/bridge.log          # bridge issues
tail -50 ~/.aos/logs/bridge.err.log      # bridge crashes
tail -50 ~/.aos/logs/dashboard.log       # dashboard issues
tail -50 ~/.aos/logs/crons/scheduler.log # cron job failures

# 3. System context
python3 ~/aos/core/bin/cli/aos-report --context
```

Then trace: error message → stack trace → source file → root cause.
- Cron failure: check `~/aos/config/crons.yaml` → find the command → read the script
- Service crash: check LaunchAgent plist → find the entrypoint → read the code
- Hook failure: check `~/.claude/settings.json` hooks → read the hook script

### For ideas/friction:

```bash
# Check if something similar exists
cd ~/aos && gh issue list --search "<keywords>" --limit 5 2>/dev/null || true

# Check if a skill or feature partially covers this
ls ~/.claude/skills/ | grep -i <topic>
```

## Step 4: Diagnose

Write a clear assessment. Be explicit about your confidence:

For bugs:
- **What's happening:** [observed behavior]
- **What should happen:** [expected behavior]
- **Root cause:** [specific file, line, logic error]
- **Confidence:** high (traced to specific line) / medium (pattern match from logs) / low (educated guess)
- **Impact:** [who's affected, how badly]

For ideas/friction:
- **Current behavior:** [what happens now]
- **Desired behavior:** [what the user wants]
- **Where this would live:** [which part of the codebase]

The confidence field matters — it tells the developer whether to trust the
diagnosis or investigate further themselves. Don't present guesses as facts.

## Step 5: Propose a Fix (bugs only, when confident)

If you traced the bug to a specific file and line, write the fix:

```python
# File: core/services/bridge/main.py
# Line 47: config['schedule'] is None when operator.yaml has no schedule

# Before:
schedule = config['schedule'].split(',')

# After:
schedule = config.get('schedule', '').split(',') if config.get('schedule') else []
```

When NOT to propose a fix:
- Confidence is low — say "needs further investigation" instead
- The fix is more than ~20 lines — describe the approach instead of writing code
- Architecture change needed — just describe what should change
- Security-sensitive code — report only, don't write fixes

## Step 6: Check for Duplicates

```bash
cd ~/aos && gh issue list --search "<key terms>" --json number,title,url,state --limit 5 2>/dev/null
```

If a matching open issue exists, you'll comment on it instead of creating a
new one. Use the `duplicate_of` field in the report JSON.

If `gh` is not available or not authenticated, skip this step and note it.

## Step 7: Ask the User

Show a brief summary and ask before filing:

> **What I found:** The bridge crashes because `main.py:47` tries to split
> a None value when operator.yaml has no schedule field.
>
> **Proposed fix:** Add a null check (high confidence).
>
> Want me to send this to the developer as a bug report? They'll see it
> on Telegram and GitHub.

**Wait for their answer.** If they say yes, file it. If they say no or want
changes, adjust. If they want to add context, incorporate it.

The reason to ask: sometimes people are venting, not requesting action.
Sometimes they said something they don't want in a GitHub issue. One quick
question respects that.

## Step 8: File

Build the report JSON and pipe it to the report script. Use Python to avoid
JSON escaping issues in bash:

```bash
python3 -c "
import json, subprocess
data = {
    'title': 'Bridge crashes on empty operator schedule config',
    'body': '## What happened\n\nBridge disconnects every few hours...\n\n## Root cause\n\nmain.py:47 calls .split() on None...\n\n## Confidence: high\n\nTraced to specific line via stack trace in bridge.err.log.',
    'labels': ['bug', 'has-fix'],
    'severity': 'high',
    'proposed_fix': 'Add null check: config.get(\"schedule\", \"\").split(\",\") if config.get(\"schedule\") else []',
    'source_file': 'core/services/bridge/main.py'
}
result = subprocess.run(
    ['python3', '$HOME/aos/core/bin/cli/aos-report'],
    input=json.dumps(data),
    capture_output=True, text=True, timeout=30
)
print(result.stdout)
"
```

### Handling the response

Parse the JSON output:

| `action` | What happened | Tell the user |
|----------|--------------|---------------|
| `created` | Issue filed, Telegram sent | "Reported — [issue URL]. Developer will see it on Telegram." |
| `queued` | GitHub unavailable, saved locally | "Saved locally — GitHub isn't available right now. It'll be filed when connectivity is restored." |
| `error` | Script crashed | "I couldn't file this right now. Here's what I found: [show diagnosis]. You can share this with the developer directly." |

**Never tell the user it was filed if the action was `queued` or `error`.**

## Step 9: Confirm

After successful filing:

> "Reported to the developer: *[title]*
> [issue URL]
>
> They'll see it on Telegram. [If fix proposed:] I included a proposed fix —
> if it's good, you'll get it in the next update."

## Proactive Reporting

During normal work, if you notice something AOS-related that's broken:
- A service not running that should be
- A hook producing errors
- A cron job failing in the scheduler log
- A config file with wrong paths

Mention it briefly: "I noticed the scheduler service isn't running — want me
to report this?" One question, act on their answer.

Don't report things the user is actively working on fixing.

## Rules

- Investigate before filing — the diagnosis is the value
- Always ask before filing — one question, respect the answer
- Never include secrets, tokens, or API keys
- Be honest about confidence — don't present guesses as facts
- Keep titles actionable: "Bridge crashes on empty schedule config" not "Bridge broken"
- Use Python subprocess to call aos-report (not echo with JSON — escaping breaks)
- Handle all three response types (created, queued, error) — don't assume success
