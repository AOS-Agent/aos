#!/bin/bash
# protect-runtime.sh — PreToolUse hook for Bash tool
# Blocks shell commands that write to ~/aos/ (runtime).
# All framework changes must go through ~/project/aos/ (dev workspace).
#
# Installed in ~/.claude/settings.json under hooks.PreToolUse
# Exit 0 = allow, Exit 2 = block (stderr shown to Claude)

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

# Nothing to check
[[ -z "$COMMAND" ]] && exit 0

AOS_RUNTIME="$HOME/aos"
AOS_RUNTIME_ABS="/Users/agentalhadi/aos"

# --- Block: direct file writes to ~/aos/ ---
# Catches: cp, mv, tee, redirect (>), cat heredoc
if echo "$COMMAND" | grep -qE "(cp|mv|tee|install)\s.*($AOS_RUNTIME_ABS/|~/aos/|\\\$HOME/aos/)"; then
    echo "Blocked: this command writes to ~/aos/ (runtime). Use ~/project/aos/ instead." >&2
    exit 2
fi

# Catches: echo/cat > ~/aos/...
if echo "$COMMAND" | grep -qE ">\s*($AOS_RUNTIME_ABS/|~/aos/)"; then
    echo "Blocked: this command writes to ~/aos/ (runtime). Use ~/project/aos/ instead." >&2
    exit 2
fi

# --- Block: git commit/push/add from ~/aos/ ---
# Allow: git pull, git checkout, git status, git diff, git log, git fetch (read ops)
if echo "$COMMAND" | grep -qE "cd\s+(~/aos|\"~/aos\"|\\\$HOME/aos|$AOS_RUNTIME_ABS)" | head -1; then
    if echo "$COMMAND" | grep -qE "git\s+(commit|push|add|stash)"; then
        echo "Blocked: do not commit from ~/aos/. Use ~/project/aos/ for all git operations." >&2
        exit 2
    fi
fi

# --- Block: mkdir in ~/aos/ (creating new directories) ---
if echo "$COMMAND" | grep -qE "mkdir.*($AOS_RUNTIME_ABS/|~/aos/)"; then
    echo "Blocked: do not create directories in ~/aos/ (runtime). Use ~/project/aos/ instead." >&2
    exit 2
fi

# --- Block: rm in ~/aos/ framework paths (protect framework code) ---
# Allow: rm in ~/aos/vendor/ (cleanup is ok)
if echo "$COMMAND" | grep -qE "rm\s.*($AOS_RUNTIME_ABS/(core|config|templates|specs|\.claude)/|~/aos/(core|config|templates|specs|\.claude)/)"; then
    echo "Blocked: do not delete framework files in ~/aos/. Use ~/project/aos/ instead." >&2
    exit 2
fi

# All other commands pass through
exit 0
