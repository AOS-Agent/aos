#!/usr/bin/env bash
# Stop — if Swift/project.yml changed since the last green gate, build before
# the turn ends. Exit 2 with the errors → Claude keeps working. Skips when the
# build slot is busy (never blocks another session), when nothing relevant
# changed, when re-entered (stop_hook_active), or when no app dir is found.
#
# App dir = the directory holding project.yml or a *.xcodeproj (APP_DIR env,
# else repo root, ios/, iOS/, app/, else a depth-3 search). Scheme = SCHEME env,
# else .xcodebuildmcp/config.yaml sessionDefaults.scheme, else the first scheme.
# The build uses this worktree's DerivedData/ (DERIVED env), CODE_SIGNING_ALLOWED=NO,
# and runs through tools/build-slot.sh --try when present.
set -u
INPUT=$(cat)
printf '%s' "$INPUT" | grep -q '"stop_hook_active": *true' && exit 0
TOP=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

find_app_dir() {
  [[ -n "${APP_DIR:-}" ]] && { echo "$APP_DIR"; return; }
  local d
  for d in "$TOP" "$TOP/ios" "$TOP/iOS" "$TOP/app"; do
    [[ -f "$d/project.yml" ]] && { echo "$d"; return; }
    ls -d "$d"/*.xcworkspace "$d"/*.xcodeproj >/dev/null 2>&1 && { echo "$d"; return; }
  done
  d=$(find "$TOP" -maxdepth 3 \( -path '*/DerivedData' -o -path '*/.build' -o -path '*/node_modules' -o -path '*/.claude' -o -path '*/Pods' \) -prune -o \( -name project.yml -o -name '*.xcodeproj' \) -print 2>/dev/null | head -1)
  [[ -n "$d" ]] && dirname "$d"
}
APP=$(find_app_dir); [[ -n "$APP" ]] || exit 0
cd "$APP" 2>/dev/null || exit 0

# Signature = the actual diff content (not --stat: two different one-line
# edits share a stat line and the cache would wrongly skip the second).
SIG=$( { git -C "$TOP" diff HEAD -- '*.swift' '*project.yml'; \
         git -C "$TOP" ls-files --others --exclude-standard -- '*.swift' | while read -r f; do cat "$TOP/$f"; done; } | md5 )
DERIVED="${DERIVED:-DerivedData}"
STAMP="$DERIVED/.gate-ok"
[[ -f "$STAMP" && "$(cat "$STAMP")" == "$SIG" ]] && exit 0
[[ "$SIG" == "$(printf '' | md5)" ]] && exit 0            # no swift/project changes at all

if [[ -f project.yml ]] && git -C "$TOP" diff HEAD --name-only | grep -q "project.yml"; then
  command -v xcodegen >/dev/null 2>&1 && xcodegen generate >/dev/null 2>&1
fi
WS=$(find . -maxdepth 1 -name "*.xcworkspace" | head -1)
PJ=$(find . -maxdepth 1 -name "*.xcodeproj" | head -1)
if [[ -n "$WS" ]]; then CONTAINER=(-workspace "$WS"); elif [[ -n "$PJ" ]]; then CONTAINER=(-project "$PJ"); else exit 0; fi
SCHEME="${SCHEME:-}"
[[ -n "$SCHEME" ]] || SCHEME=$(awk '/^ *scheme:/{print $2; exit}' .xcodebuildmcp/config.yaml 2>/dev/null)
[[ -n "$SCHEME" ]] || SCHEME=$(xcodebuild "${CONTAINER[@]}" -list 2>/dev/null | awk '/Schemes:/{f=1;next} f&&NF{print $1;exit}')
[[ -n "$SCHEME" ]] || exit 0
SLOT="$TOP/tools/build-slot.sh"; [[ -x "$SLOT" ]] && SLOT=("$SLOT" --try) || SLOT=()

LOG=$(mktemp)
${SLOT[@]+"${SLOT[@]}"} xcodebuild "${CONTAINER[@]}" -scheme "$SCHEME" \
    -configuration Debug -derivedDataPath "$DERIVED" -destination 'generic/platform=iOS Simulator' \
    -skipPackagePluginValidation -skipMacroValidation CODE_SIGNING_ALLOWED=NO -quiet build >"$LOG" 2>&1
rc=$?
if (( rc == 75 )); then rm -f "$LOG"; exit 0; fi           # slot busy: don't block
if (( rc != 0 )); then
  echo "Build gate FAILED (rc=$rc). Fix before stopping:" >&2
  grep -E "error:|fatal error" "$LOG" | head -20 >&2
  rm -f "$LOG"; exit 2
fi
mkdir -p "$DERIVED" && printf '%s' "$SIG" > "$STAMP"; rm -f "$LOG"; exit 0
