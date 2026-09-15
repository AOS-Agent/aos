#!/usr/bin/env bash
# SessionStart — warn when .agent/HANDOFF.md is >3 days older than the newest
# commit. Output is injected into the session context. Never blocks.
set -u
TOP=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
H="$TOP/.agent/HANDOFF.md"; [[ -f "$H" ]] || exit 0
hmt=$(stat -f %m "$H"); cmt=$(git -C "$TOP" log -1 --format=%ct 2>/dev/null || echo 0)
gap=$(( (cmt - hmt) / 86400 ))
if (( gap > 3 )); then
  echo "⚠️  .agent/HANDOFF.md is ${gap} days older than the newest commit ($(git -C "$TOP" log -1 --format='%h %s' | cut -c1-60)). Read it with suspicion; rewrite it before this session ends (relay baton)."
fi
exit 0
