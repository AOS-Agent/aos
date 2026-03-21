---
name: session-analysis
description: Analyze past Claude Code sessions for friction patterns and suggest CLAUDE.md improvements. Trigger on "analyze sessions", "friction report", "improve CLAUDE.md", or "what mistakes do I keep making".
allowed-tools: Bash, Read, Edit, Glob, Grep
---

# Session Analysis — Self-Improving Agent Instructions

Mine session history for friction patterns and generate actionable CLAUDE.md improvements.

## Step 1: Generate the friction report

Run the analysis script:

```bash
python3 ~/aos/bin/session-analysis --days 7
```

This scans all JSONL session files from the last 7 days and writes a categorized report to `~/vault/reviews/session-friction-{date}.md`.

## Step 2: Read the report

Read the generated report file. Pay attention to:
- Which category has the most instances (correction, retry, frustration, clarification, overreach)
- Repeated patterns — the same type of mistake across multiple sessions
- The user's exact words when correcting — they reveal what the instruction should say

## Step 3: Generate CLAUDE.md improvements

For each recurring pattern, draft a specific rule:

- **Correction patterns** → Add a rule about what NOT to do (e.g., "Never modify files outside the current project without asking")
- **Retry patterns** → Add a verification step (e.g., "Before refactoring, confirm the scope with the user")
- **Frustration patterns** → Add a behavioral constraint (e.g., "Keep responses under 3 paragraphs unless asked for detail")
- **Clarification patterns** → Add a disambiguation step (e.g., "When the request is ambiguous, ask one clarifying question before proceeding")
- **Overreach patterns** → Add a scope limit (e.g., "Only make changes directly requested — no unsolicited improvements")

## Step 4: Present for approval

Show the suggested improvements as a numbered list. Do NOT modify CLAUDE.md directly. Wait for the operator to approve each suggestion before applying.

## Rules

- Never auto-apply changes to CLAUDE.md — always present for review
- Focus on patterns (3+ occurrences), not one-off mistakes
- Keep rules concise — one line per rule, under 200 lines total in CLAUDE.md
- If a friction pattern already has a matching rule in CLAUDE.md, note that the rule isn't working and suggest strengthening it
- Include the friction count and example in your suggestion so the operator understands why
