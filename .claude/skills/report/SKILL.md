---
name: report
description: >
  Investigate, diagnose, and report AOS issues to the developer. Trigger on
  "this is broken", "there's a bug", "report this", "something's wrong with",
  "/bug", "/report", "file an issue", "the [service] keeps [failing]", or any
  user frustration with AOS behavior. Also trigger on "I have an idea",
  "it would be nice if", "feature request", "I wish", "/idea" for enhancement
  requests. Activate proactively when you notice AOS errors during normal work.
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# /report — Investigate, Diagnose, Report

You are Chief. A user just told you something is broken, confusing, or missing —
or you noticed it yourself. Your job: investigate like an engineer, diagnose the
root cause, propose a fix if possible, and file a clean report to GitHub so the
developer can act immediately.

**Do NOT just relay what the user said.** Investigate first. The value is in
the diagnosis, not the complaint.

## Step 1: Classify

Determine what kind of report this is:

| Type | Label | Severity default | Example |
|------|-------|-----------------|---------|
| **Bug** | `bug` | medium | "the bridge keeps crashing" |
| **Friction** | `friction` | low | "I can never find my tasks on the dashboard" |
| **Idea** | `enhancement` | low | "I wish the morning briefing showed weather" |
| **Missing** | `missing-feature` | low | "there's no way to snooze a task" |

If the user is frustrated or something is actively broken → bump severity to high.
If a service is down or data loss is possible → severity is critical.

## Step 2: Investigate

**This is the most important step.** Do real work before filing.

### For bugs:

```bash
# 1. Check service status
launchctl list 2>/dev/null | grep com.aos

# 2. Read relevant logs (pick the right log for the service)
tail -50 ~/.aos/logs/bridge.log          # bridge issues
tail -50 ~/.aos/logs/bridge.err.log      # bridge crashes
tail -50 ~/.aos/logs/dashboard.log       # dashboard issues
tail -50 ~/.aos/logs/crons/scheduler.log # cron job failures

# 3. Get system context
python3 ~/aos/core/bin/aos-report --context

# 4. Read the relevant source code
# Use the error message / stack trace to identify the file and line
```

Follow the trail:
- Error message → stack trace → source file → root cause
- If a cron job fails: check `~/aos/config/crons.yaml` for the command, then check the script
- If a service crashes: check the LaunchAgent plist for the command, then check the service code
- If a hook fails: check `~/.claude/settings.json` hooks, then check the hook script

### For friction/ideas:

```bash
# 1. Check if something similar already exists
gh issue list --search "<keywords>" --limit 5

# 2. Check if a skill or feature partially covers this
ls ~/.claude/skills/ | grep -i <topic>

# 3. Check vault for prior discussions
~/.bun/bin/qmd query "<topic>" -n 3 2>/dev/null || true
```

## Step 3: Diagnose

For bugs, write a clear root cause analysis:

- **What's happening:** [observed behavior]
- **What should happen:** [expected behavior]
- **Root cause:** [specific file, line, logic error]
- **Impact:** [who's affected, how badly]

For ideas/friction, frame it as:
- **Current behavior:** [what happens now]
- **Desired behavior:** [what the user wants]
- **Suggested approach:** [where in the codebase this would live]

## Step 4: Propose a Fix (bugs only)

If you can identify the fix, write it as a code block:

```python
# File: core/services/bridge/main.py
# Line 47: Add null check for operator config

# Before:
schedule = config['schedule'].split(',')

# After:
schedule = config.get('schedule', '').split(',') if config.get('schedule') else []
```

Don't propose fixes for:
- Architecture changes (too complex for a quick fix)
- Things you're not sure about (say "needs investigation" instead)
- Security-sensitive code (just report it)

**Good fixes are small, obvious, and safe.** One null check, one path correction,
one missing import. If the fix is more than ~20 lines, describe it instead of
writing the code.

## Step 5: Check for Duplicates

Before filing, search existing issues:

```bash
cd ~/aos && gh issue list --search "<key terms>" --json number,title,state --limit 5
```

If a matching open issue exists:
- Comment on it instead of creating a new one
- Use `duplicate_of` field when calling aos-report

## Step 6: Show the User

Before filing, show the user a brief summary:

> **What I found:** [1-2 sentence diagnosis]
>
> **What I'll report:** [title]
>
> **Proposed fix:** [yes/no + one line description]
>
> Filing this now — the developer will see it on Telegram.

Don't ask for permission. Just tell them what you're doing and do it.
If they say "don't file" or "wait", stop. Otherwise, file immediately.

## Step 7: File

Build the report JSON and pipe to aos-report:

```bash
echo '{
  "title": "Bridge crashes on empty operator schedule config",
  "body": "## What happened\n\nUser reported bridge disconnecting every few hours.\n\n## Root cause\n\n`bridge/main.py:47` calls `.split()` on `config[\"schedule\"]` which is `None` when operator.yaml has no schedule configured.\n\n## Impact\n\nBridge process crashes and restarts via LaunchAgent, causing ~30s message gaps.",
  "labels": ["bug"],
  "severity": "high",
  "proposed_fix": "```python\n# core/services/bridge/main.py line 47\nschedule = config.get(\"schedule\", \"\").split(\",\") if config.get(\"schedule\") else []\n```",
  "source_file": "core/services/bridge/main.py"
}' | python3 ~/aos/core/bin/aos-report
```

The script handles:
- Filing to GitHub Issues with full system context
- Telegram notification to the developer
- PII scrubbing (home paths, emails, tokens)
- Local logging to `~/.aos/logs/reports.jsonl`
- Commenting on duplicates instead of creating new issues

Parse the JSON output to get the issue URL:
```bash
# Returns: {"action": "created", "issue_url": "https://...", "number": 42}
```

## Step 8: Confirm to User

After filing:

> "Reported to the developer. [Issue title] — they'll see it on Telegram.
> [If proposed fix:] I included a proposed fix. If it's merged, you'll get
> it in the next update."

## Labels Reference

Use these exactly — they're created in GitHub:

| Label | When |
|-------|------|
| `bug` | Something is broken |
| `friction` | Works but confusing/slow |
| `enhancement` | New feature idea |
| `missing-feature` | Expected something that doesn't exist |
| `auto-captured` | Filed automatically by a hook or cron (not user-initiated) |
| `has-fix` | Report includes a proposed fix |

Add `has-fix` alongside the primary label when a fix is proposed.

## Proactive Reporting

Don't wait for the user to say "report this." If during normal work you notice:
- A hook failing silently
- A service not running that should be
- A cron job producing errors
- A config file with wrong paths

Mention it to the user: "I noticed [problem]. Want me to report this to the
developer?" One question, then act on their answer.

## Rules

- ALWAYS investigate before filing — the diagnosis IS the value
- NEVER file without telling the user what you're reporting
- NEVER include secrets, tokens, or full API keys in reports
- ALWAYS propose a fix for obvious bugs (wrong path, null check, missing import)
- ALWAYS search for duplicates first
- Keep titles actionable: "Bridge crashes on empty schedule config" not "Bridge broken"
- Keep the body scannable: headers, code blocks, tables — not walls of text
