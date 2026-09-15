#!/usr/bin/env bash
# PreToolUse (Edit|Write) — block edits to Xcode-managed / generated files.
# Fires in every permission mode. Exit 2 = blocked, message goes to Claude.
#
# XcodeGen projects (a project.yml beside the .xcodeproj): .pbxproj, .xcodeproj/,
# .xcworkspace/, .entitlements, Package.resolved and .xcuserstate are generated —
# the edit belongs in project.yml + `xcodegen generate`. Plain .xcodeproj
# projects: the same edit is allowed with a one-line warning, because there the
# pbxproj IS the source of truth (prefer Xcode 16 synchronized folders so file
# adds need no pbxproj edit at all). Package.resolved is always blocked — SwiftPM
# owns it in every project shape, including pure-SwiftPM repos with no .xcodeproj.
set -u
INPUT=$(cat)
FP=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || echo "")
[[ -n "$FP" ]] || exit 0
# canonicalize: symlinked paths (~/project → /Volumes/…, /tmp → /private/tmp) must strip against the -P toplevel
[[ -e "$FP" ]] && FP="$(cd "$(dirname "$FP")" && pwd -P)/$(basename "$FP")"
case "$FP" in
  *.pbxproj|*.xcodeproj/*|*.xcworkspace/*|*.xcworkspace|*.entitlements|*Package.resolved|*.xcuserstate) ;;
  *) exit 0 ;;
esac
# XcodeGen-managed? look for project.yml from the file's directory up to the repo root
TOP=$(git -C "$(dirname "$FP")" rev-parse --show-toplevel 2>/dev/null || echo /); TOP=$(cd "$TOP" && pwd -P)
d=$(dirname "$FP"); YML=""
while :; do
  [[ -f "$d/project.yml" ]] && { YML="$d/project.yml"; break; }
  [[ "$d" == "$TOP" || "$d" == "/" ]] && break
  d=$(dirname "$d")
done
if [[ "$FP" == *Package.resolved ]]; then
  cat >&2 <<MSG
BLOCKED: '$FP' is written by SwiftPM. Change the dependency in Package.swift
(or project.yml → packages:) and let 'swift package resolve' / xcodebuild
-resolvePackageDependencies rewrite the lockfile; never edit it by hand.
MSG
  exit 2
fi
if [[ -n "$YML" ]]; then
  cat >&2 <<MSG
BLOCKED: '$FP' is generated/Xcode-managed. This project uses XcodeGen —
edit $YML and run 'xcodegen generate' in its directory, never the .xcodeproj.
Entitlements and Package.resolved are owned by project.yml / SwiftPM.
MSG
  exit 2
fi
case "$FP" in
  *.xcuserstate) echo "BLOCKED: $FP is per-user Xcode state, never edit it." >&2; exit 2 ;;
esac
echo "warning: editing Xcode-managed file $FP by hand (no project.yml — plain .xcodeproj). Keep the edit minimal; a broken pbxproj kills every build." >&2
exit 0
