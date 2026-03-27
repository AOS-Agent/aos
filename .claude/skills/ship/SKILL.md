---
name: ship
description: >
  Ship AOS changes from the dev workspace to main. Trigger on "/ship",
  "ship this", "ship it", "push to main", "deploy this", "release this",
  "send this out", or any request to publish dev workspace changes to
  all AOS machines.
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

# /ship — Ship AOS Changes

Ship changes from `~/project/aos/` (dev workspace) to main.
Every user's machine pulls main at 4am and auto-updates.

## Pre-flight Checks

Before showing the operator anything, run ALL of these silently:

```bash
# 1. Verify dev workspace exists and is a git repo
if [[ ! -d "$HOME/project/aos/.git" ]]; then
    echo "ERROR: Dev workspace not found at ~/project/aos/"
    exit 1
fi
cd ~/project/aos

# 2. Fetch latest main so our diff is accurate
git fetch origin --quiet 2>/dev/null

# 3. Check if there's anything to ship
CHANGES=$(git diff origin/main --name-only | wc -l | tr -d ' ')
UNCOMMITTED=$(git status --short | wc -l | tr -d ' ')
if [[ "$CHANGES" -eq 0 && "$UNCOMMITTED" -eq 0 ]]; then
    echo "Nothing to ship — dev workspace matches main."
    exit 0
fi

# 4. Check if dev is behind main (someone else pushed)
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [[ "$BEHIND" -gt 0 ]]; then
    echo "WARN: Dev workspace is $BEHIND commit(s) behind origin/main."
    echo "Pull first: cd ~/project/aos && git pull origin main"
fi

# 5. Full diff against main
git status --short
git diff origin/main --stat
git diff origin/main --name-only

# 6. Run self-test on current runtime
# Note: this tests ~/aos/ (the running system), not the dev code.
# It confirms the base system is healthy before shipping.
cd ~/aos && python3 core/bin/aos self-test 2>&1

# 7. Dry-run reconcile from the dev workspace
cd ~/project/aos && python3 core/reconcile/runner.py check 2>&1

# 8. Quality gate — code health, docs sync, consistency
cd ~/project/aos && bash core/bin/ship-check 2>&1
# Exit 0 = all pass, 1 = failures (block), 2 = warnings (allow with note)

# 9. Check for runtime data that shouldn't ship
git diff origin/main --name-only | grep -E "execution_log|\.jsonl|\.log$|__pycache__"
```

## Safety Checks

**Reject the ship if:**
- Dev workspace is behind main (must pull first)
- `execution_log/`, `.jsonl`, or `__pycache__/` files are in the diff
- `.env`, `credentials`, `*.key`, `*.pem` files are in the diff
- Self-test fails
- VERSION was bumped but no matching CHANGELOG.md entry exists
- Quality gate has failures (exit 1) — syntax errors, missing docs, unregistered checks, install drift

**Warn but allow if:**
- Reconcile dry-run shows issues (might be expected if adding a new check)
- Large diff (>500 lines changed) — ask operator to confirm
- Untracked files exist that aren't in the diff (mention them)
- Quality gate has warnings (exit 2) — debug artifacts, deprecated dirs, integration gaps

**Install drift checks** (in ship-check section 4):
The quality gate catches things that silently break new installs:
- Hardcoded lists in install.sh (services, skills, LaunchAgents) that should auto-discover
- Onboarding referencing config files or integrations that don't exist
- Missing default templates that install.sh tries to copy
- Absolute paths that should use `~` for portability
When adding a new service, skill, agent, or integration — ship-check catches if install.sh can't see it.

## Ship Flow

### Step 1: Summarize

Show the operator a clean summary:

```
━━━━━━━━━━━━━━━━━━
Ship to main
━━━━━━━━━━━━━━━━━━

Files changed: N
Lines: +X / -Y

Changes:
  ✦ core/reconcile/checks/new_check.py (new)
  ↻ core/bin/check-update (modified)
  ✓ core/work/inject_context.py (modified)

Self-test: ✓ passed
Reconcile: ✓ all checks pass
Quality:   ✓ code health, docs sync, consistency, install drift
Safety:    ✓ no runtime data, no secrets
```

If there are uncommitted changes, list them clearly:
```
Uncommitted (will be staged):
  M core/bin/aos
  ?? .claude/skills/ship/SKILL.md (new, untracked)
```

If VERSION was bumped, also show:
```
Version: v0.2.0 → v0.3.0
Release notes: [summary line from CHANGELOG.md]
```

### Step 2: Ask Once

Ask the operator exactly once:

> "Ship this to main? Every machine updates at 4am."

If they say yes, proceed. If no, stop.

### Step 3: Commit & Push

```bash
cd ~/project/aos

# Stage specific files from the diff — NOT git add -A
# List the files explicitly so nothing unexpected gets staged.
# If there are many files, group them logically.
git add <file1> <file2> ...

# Commit with a descriptive message:
# - If VERSION was bumped: "vX.Y.Z: <Summary line from CHANGELOG>"
# - If not: single imperative line summarizing the change (72 chars max)
#   Examples: "fix: SessionStart hook never crashes on Python 3.9"
#             "feat: add reconcile system for automatic drift repair"
git commit -m "<message>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

# Push to main
git push origin HEAD:main
```

### Step 4: Sync Runtime

After pushing, update the local runtime so the operator's own machine
matches what they just shipped:

```bash
cd ~/aos && git pull origin main --ff-only
```

### Step 5: Close Resolved Issues

After pushing, check for open GitHub issues that were resolved by the shipped changes:

```bash
cd ~/project/aos
gh issue list --state open
```

For each open issue:
1. Read the issue title and body
2. Check if the shipped diff includes changes that fix it (match file paths, keywords, commit messages)
3. If resolved, close it with a comment referencing the commit:
   ```bash
   gh issue close <number> --comment "Shipped in <short hash>. <one-line summary of what fixed it>."
   ```
4. If not resolved, leave it open

Report what was closed:
```
Issues closed: #4, #7, #8 (resolved by this ship)
Issues still open: #12 (unrelated)
```

### Step 6: Confirm

```
━━━━━━━━━━━━━━━━━━
Shipped ✓
━━━━━━━━━━━━━━━━━━

Pushed to main: <short hash>
Local runtime synced: ~/aos/ is up to date
Issues closed: N resolved
Next auto-update: 4am — all machines will receive this
```

## Version Bumps

If changes are significant enough to warrant a version bump:

1. Check if VERSION was already bumped — if so, verify CHANGELOG.md has a matching entry
2. If not bumped, decide based on the diff:
   - **Patch** (v0.3.0 → v0.3.1): bug fixes, small tweaks
   - **Minor** (v0.3.0 → v0.4.0): new features, new reconcile checks, new skills
   - Recommend a specific version with reasoning, e.g.:
     "You added a new skill and changed the update flow — that's a minor bump, v0.3.0 → v0.4.0."
3. Ask: "Want me to bump to vX.Y.Z and add a changelog entry?"
4. Write the CHANGELOG entry at the top of CHANGELOG.md (below the header), following this format:

```markdown
## vX.Y.Z — YYYY-MM-DD

One-line summary of the release theme.

- Added foo — short description of what it does and why it matters
- Added `bar` command for doing X
- Changed baz to use `new_approach` instead of `old_approach`
- Fixed `thing` crashing when Y happens
- Removed deprecated Z
```

**Changelog style rules:**
- Each bullet is a natural sentence starting with Added/Changed/Fixed/Removed
- Use backticks liberally for commands, file paths, settings, code references
- Each bullet should be self-contained — scannable without reading other bullets
- No category headers (### Added, etc.) — the verb prefix is enough
- Keep the one-line summary before the bullets (used for Telegram release notes)
- Aim for specificity: "`SessionStart` hook crash on Python 3.9" not "fixed a crash"

## Rollback

If the operator says "roll back" or "undo the last ship":

```bash
# Show recent commits so operator can confirm which one
cd ~/aos && git log --oneline -5

# Revert the last commit (creates a new commit that undoes it)
git revert HEAD --no-edit
git push origin main

# Sync the dev workspace so it reflects the rollback
cd ~/project/aos && git pull origin main --ff-only
```

This creates a new commit that undoes the change. Safe — no force push.
Dev workspace is synced so the next /ship doesn't re-push reverted code.

## Rules

- NEVER force push to main
- NEVER ship without showing the summary first
- NEVER ship runtime data (logs, execution_log, .jsonl)
- NEVER use `git add -A` — stage files explicitly
- ALWAYS fetch origin before diffing (ensures accurate comparison)
- ALWAYS sync ~/aos/ after pushing so the operator runs what they shipped
- ALWAYS sync ~/project/aos/ after a rollback
- If dev is behind main, pull first before shipping
- If in doubt about a change, suggest the operator test it first
