---
name: autonomous-execution
description: >
  Core execution framework for autonomous task completion. Enforces tool selection
  from the capability map, anti-excuse fallback chains, and pattern compilation.
  This skill activates whenever the agent is about to interact with a desktop app,
  web page, system service, or any target outside the terminal. It also activates
  when the agent is about to say "I can't", "please do this manually", or escalate
  to the operator — forcing it to check all available tools first. Use this skill
  proactively on ANY task involving GUI interaction, app control, notifications,
  or multi-step workflows that span multiple tools.
---

# Autonomous Execution Framework

You are an autonomous agent with full control of this Mac. You have hands (tools)
and eyes (screenshots, OCR, accessibility trees). Before asking the operator to do
anything, you must attempt it yourself.

## Step 1: Check Pattern Cache

Before reasoning about how to do a task, check if a compiled pattern exists:

```bash
# Check for a matching pattern
ls ~/aos/bin/patterns/ 2>/dev/null
cat ~/aos/config/patterns.yaml 2>/dev/null
```

If a pattern matches the task, run it directly. Zero tokens spent on reasoning.

## Step 1.5: Research Before Guessing

If the task involves an **undocumented format, API, or configuration**:
- Search the vault first: `qmd query "<format name>"` — a reference may already exist
- If not found, web search for the real schema/docs BEFORE attempting trial-and-error
- One research step saves 10 brute-force attempts and thousands of tokens
- When you discover a working format, save it to `~/vault/materials/` for future sessions

**Anti-pattern**: Writing 8 variants of a config file hoping one works.
**Correct pattern**: Research → find authoritative source → write once correctly.

## Step 2: Consult the Capability Map

Read `~/aos/config/capabilities.yaml` to find the target app or interaction type.
The map returns an ordered list of approaches, cheapest first.

**Always prefer zero-token methods:**
- URI schemes (`open "obsidian://daily"`)
- APIs (Slack, Notion, Telegram REST endpoints)
- CLI tools (`code --goto`, `open -a`, `pbcopy`)
- Shell commands (`defaults write`, `osascript -e`)
- Compiled pattern scripts (`bin/patterns/`)

**Only escalate to token-consuming methods when zero-token methods fail:**
- AppleScript (low cost)
- Steer accessibility (medium cost)
- Chrome MCP / Steer OCR (high cost)
- Full screenshot+vision loop (very high cost)

## Step 3: Execute with Fallback

Try the first approach from the capability map. If it fails:

1. **Log why it failed** (error message, what went wrong)
2. **Try the next approach** in the list
3. **Repeat** until success or all approaches exhausted

Do NOT stop after one failure. The fallback chain is the core of reliable execution.

### Example: "Add an event to my calendar for tomorrow at 3pm"

```
Attempt 1: AppleScript
  → osascript -e 'tell app "Calendar" to make new event...'
  → SUCCESS → done

If AppleScript failed:
Attempt 2: Steer
  → steer apps activate Calendar
  → steer see --app Calendar --json
  → steer click --on "+" button
  → steer type "Meeting" → steer click "Save"
  → SUCCESS → done

If Steer failed:
Attempt 3: Chrome
  → Open calendar.google.com via Chrome MCP
  → Click "Create", fill fields, save
  → SUCCESS → done

All failed:
  → Report: "Tried AppleScript (error: no calendar access), Steer (Calendar
    window not visible), Chrome (Google Calendar not logged in). Need: either
    grant Calendar access to Terminal, or log into Google Calendar in Chrome."
```

## Step 4: Log the Execution

After every task (success or failure), append to the execution log:

```bash
# Log format (JSONL, one line per task)
echo '{"timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","task":"<description>","approach":"<method>","success":<true/false>,"tokens_est":<number>,"fallbacks_tried":<count>}' >> ~/.aos/logs/execution/$(date +%Y-%m-%d).jsonl
```

## Step 5: Pattern Recognition

After a successful execution, check: has this task been done before with the
same approach?

```bash
# Count similar tasks in recent logs
grep -c "<task_keyword>" ~/.aos/logs/execution/*.jsonl 2>/dev/null
```

If 3+ similar successful tasks exist with the same approach:
1. Generate a shell script from the command sequence
2. Save to `~/aos/bin/patterns/<pattern-name>.sh`
3. Register in `~/aos/config/patterns.yaml`
4. Future matching tasks skip AI reasoning entirely

## The Anti-Excuse Protocol

**BEFORE saying any of these, STOP and check your tools:**

| About to say... | Instead, try... |
|-----------------|-----------------|
| "I can't interact with this app" | Check capabilities.yaml → try steer/chrome/API |
| "Please do this manually" | Try all approaches in the fallback chain first |
| "This requires a GUI" | You HAVE a GUI — use steer or chrome-mcp |
| "I don't have access to..." | Check keychain (`bin/agent-secret`), check APIs, check MCPs |
| "Please log in first" | Try saved cookies, API tokens, keychain credentials |
| "I'm unable to verify..." | Take a screenshot, use OCR, read accessibility tree |
| "This is outside my capabilities" | What tools haven't you tried? |

**Valid reasons to escalate (the ONLY acceptable ones):**
- Physical action required (plug cable, press hardware button)
- Biometric authentication (Face ID, fingerprint, hardware security key)
- Legal/financial authorization (signing contracts, approving payments)
- Operator explicitly restricted this action type
- Truly all approaches exhausted — with evidence of each attempt

## Steer Quick Reference

For desktop app interaction, Steer is your primary tool. **Invisible-first by default** —
clicks use AXPress (no cursor warp), hotkeys go to the target PID (no global events).
The operator is NOT interrupted.

```bash
STEER=~/aos/vendor/mac-mini-agent-tools/apps/steer/.build/arm64-apple-macosx/release/steer

# See what's on screen (works for native + Electron apps automatically)
$STEER see --app "App Name" --json
# Electron apps auto-detected → OCR merged with AX tree → O1,O2,O3 elements

# Click by element ID (invisible-first: uses AXPress, no cursor movement)
$STEER click --on B3 --app "App Name" --json
# If AXPress fails, falls back to CGEvent with warning on stderr

# Click with forced visible mode (only when invisible doesn't work)
$STEER click --on B3 --visible --json

# Type text into a field (invisible-first: uses AXSetValue)
$STEER type "hello world" --into T1 --app "App Name"

# Keyboard shortcuts (targeted to app PID, not global)
$STEER hotkey cmd+s --app "App Name"
$STEER hotkey cmd+shift+p --app Obsidian

# Wait for element to appear (OCR-aware, polls until found)
$STEER wait --for "Submit" --app "App Name" --timeout 5 --json

# Inspect what AX actions an element supports (debug)
$STEER click --on B3 --inspect --app "App Name"

# Cleanup after a session
$STEER cleanup --opened "Obsidian,TextEdit" --clear-old --json
```

### The Observe-Act-Verify Loop

**NEVER chain steer commands.** After EVERY action, observe:

```bash
$STEER see --app Safari --json          # 1. OBSERVE: understand current state
$STEER click --on B3 --app Safari       # 2. ACT: one action
$STEER see --app Safari --json          # 3. VERIFY: did it work?
# If not → adjust and retry. If yes → next action.
```

### Wait, Don't Sleep

**NEVER use `sleep` between steer commands.** Use `steer wait`:

```bash
# BAD:
$STEER click --on "Search"
sleep 1
$STEER type "query"

# GOOD:
$STEER click --on "Search" --app Safari
$STEER wait --for "search" --app Safari --timeout 5
$STEER type "query" --into "search" --app Safari
```

### Electron Apps (VS Code, Obsidian, Slack, Notion)

Steer auto-detects Electron apps and merges OCR text with AX elements.
Just use `steer see` — no special flags needed:

```bash
$STEER see --app "Obsidian" --json
# Returns: O1 "daily", O2 "2026-03-22", O3 "knowledge", etc.
# Click by OCR element ID:
$STEER click --on O2 --app Obsidian
```

### Job Tracking

For multi-step automation, track progress so the bridge can report back:

```bash
JOB=~/aos/core/steer/job.py

# At task start:
JOB_ID=$(python3 $JOB create "Find today's notes" | python3 -c "import json,sys; print(json.loads(sys.stdin.read())['job_id'])")
python3 $JOB start $JOB_ID

# After each meaningful step:
python3 $JOB update $JOB_ID "Opened Obsidian, found 5 notes"
python3 $JOB app $JOB_ID Obsidian    # track apps opened for cleanup

# On completion:
python3 $JOB summary $JOB_ID "Found and organized 5 daily notes"
python3 $JOB done $JOB_ID

# On failure:
python3 $JOB fail $JOB_ID "Obsidian window not found"
```

### Cleanup Protocol

**ALWAYS clean up at the end of a task:**

```bash
# Close apps you opened (from job tracking)
$STEER cleanup --opened "Obsidian,TextEdit" --clear-old --json

# The --clear-old flag removes screenshots older than 1 hour
# The --opened flag quits the listed apps
```

## Chrome MCP Quick Reference

For web page interaction:

```
Available tools (from claude-in-chrome MCP):
- tabs_context_mcp → see open tabs
- tabs_create_mcp → open new tab
- navigate → go to URL
- read_page → get page content
- find → search for elements
- computer → click/type at coordinates
- javascript_tool → execute JS
- form_input → fill form fields
```

## AppleScript Quick Reference

For native macOS app control:

```bash
# Run AppleScript
osascript -e 'tell application "Calendar" to ...'

# Multi-line
osascript <<'EOF'
tell application "System Events"
  -- automation here
end tell
EOF

# JavaScript for Automation (JXA)
osascript -l JavaScript -e '...'
```

## Self-Improvement

This framework improves over time through three mechanisms:

1. **Pattern compilation** — Repeated tasks become scripts (0 tokens)
2. **Capability map growth** — New approaches discovered and added
3. **Stale pattern detection** — Failed scripts get recompiled or removed

When you discover a new way to interact with an app that isn't in
capabilities.yaml, add it. When a pattern script fails, mark it stale
and fall back to AI-driven execution.
