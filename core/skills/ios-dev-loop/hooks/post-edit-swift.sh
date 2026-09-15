#!/usr/bin/env bash
# PostToolUse (Edit|Write) — swiftlint --fix the edited .swift file with the
# nearest .swiftlint.yml (walking up from the file to the repo root), then
# refuse NEW hard-ban patterns (try! / as! / @unchecked Sendable) relative to
# HEAD. Pre-existing offenders never block; only edits that add more.
# Self-skips the lint when swiftlint or a .swiftlint.yml is missing (the
# default rule set would rewrite unrelated style and bury the real diff).
set -u
INPUT=$(cat)
FP=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || echo "")
[[ "$FP" == *.swift && -f "$FP" ]] || exit 0
top=$(git -C "$(dirname "$FP")" rev-parse --show-toplevel 2>/dev/null || echo "")
if command -v swiftlint >/dev/null 2>&1; then
  d=$(dirname "$FP"); CFG=""
  while :; do
    [[ -f "$d/.swiftlint.yml" ]] && { CFG="$d/.swiftlint.yml"; break; }
    [[ "$d" == "${top:-/}" || "$d" == "/" ]] && break
    d=$(dirname "$d")
  done
  if [[ -n "$CFG" ]]; then
    ( cd "$(dirname "$CFG")" && swiftlint --fix --quiet --config "$CFG" "$FP" >/dev/null 2>&1 ) || true
  fi
fi
BAN='try!|as!|@unchecked Sendable'
now=$(grep -cE "$BAN" "$FP" 2>/dev/null); now=${now:-0}
rel=${FP#"$top"/}
before=$(git -C "${top:-.}" show "HEAD:$rel" 2>/dev/null | grep -cE "$BAN"); before=${before:-0}
if (( now > before )); then
  echo "Hard-ban added in $rel ($before → $now occurrences of try! / as! / @unchecked Sendable):" >&2
  grep -nE "$BAN" "$FP" | head -8 >&2
  echo "Use do/catch or try?, as?/guard-let, and real Sendable conformance (axiom-concurrency)." >&2
  exit 2
fi
exit 0
