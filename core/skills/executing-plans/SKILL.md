---
name: executing-plans
description: Use when you have a written implementation plan to execute in a separate session with review checkpoints
---

# Executing Plans

## Overview

Load plan, review critically, execute all tasks, report when complete.

**Announce at start:** "I'm using the executing-plans skill to implement this plan."

**Note:** Plan execution works significantly better when subagents are available. If your platform supports subagents (e.g., Claude Code), dispatch a fresh subagent per task for better context isolation and review quality.

## The Process

### Step 1: Load and Review Plan
1. Read plan file
2. Review critically - identify any questions or concerns about the plan
3. If concerns: Raise them with your human partner before starting
4. If no concerns: Create TodoWrite and proceed

### Step 2: Execute Tasks

For each task:
1. Mark as in_progress
2. Follow each step exactly (plan has bite-sized steps)
3. Run verifications as specified
4. Mark as completed
5. If the plan has an **Initiative:** reference in its header:
   - Read the initiative document
   - Find the matching checkbox for the completed task
   - Check it: `- [ ]` → `- [x]`
   - Update the initiative's `updated:` date
   - This keeps initiative progress in sync with plan execution

### Step 3: Complete Development

After all tasks complete and verified:
- Announce: "I'm using the finishing-a-development-branch skill to complete this work."
- Run the branch completion workflow: verify all tests pass, present merge/squash options to the operator, and execute their choice
- Ensure no uncommitted changes, no failing tests, and a clean diff before offering to merge

## When to Stop and Ask for Help

**STOP executing immediately when:**
- Hit a blocker (missing dependency, test fails, instruction unclear)
- Plan has critical gaps preventing starting
- You don't understand an instruction
- Verification fails repeatedly

**Ask for clarification rather than guessing.**

## When to Revisit Earlier Steps

**Return to Review (Step 1) when:**
- Partner updates the plan based on your feedback
- Fundamental approach needs rethinking

**Don't force through blockers** - stop and ask.

## Remember
- Review plan critically first
- Follow plan steps exactly
- Don't skip verifications
- Reference skills when plan says to
- Stop when blocked, don't guess
- Never start implementation on main/master branch without explicit user consent

## Integration

**Required workflow steps:**
- **Git worktree isolation** - REQUIRED: Set up an isolated workspace (worktree or branch) before starting
- **The writing-plans skill** - Creates the plan this skill executes
- **Branch completion workflow** - Verify tests, present options, and merge after all tasks complete

## Initiative Integration

When executing a plan that references an initiative (via the `**Initiative:**` header field):

1. **Before starting**: Read the initiative doc to understand the broader context
2. **After each task**: Update the initiative's matching checkbox
3. **After all tasks**: Run a gate check to see if the current phase is complete:
   ```bash
   python3 ~/aos/core/work/cli.py initiatives
   ```
   If the phase is complete, note it for the operator: "Phase {N} is now complete. Ready for gate check before Phase {N+1}?"
4. **On completion**: Append to the initiative's Progress section:
   ```
   - {date}: Plan "{plan name}" executed — {N} tasks completed
   ```
