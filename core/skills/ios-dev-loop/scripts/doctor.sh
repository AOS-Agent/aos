#!/usr/bin/env bash
# doctor.sh — machine-level checks for the iOS dev loop. Prints fixes, applies none.
#
#   ~/.claude/skills/ios-dev-loop/scripts/doctor.sh        # exit 0 = healthy, 1 = something to fix
#
# Checks: Xcode build location (Unique), selected Xcode + version, the tools
# the loop leans on (xcodegen, xcsift, swiftlint, axe, xcodebuildmcp, fastlane)
# with versions and brew-outdated status, the Axiom plugin version, disk free
# on / and the temp volume, Chrome code_sign_clone bloat, the build slot.
set -u
FAILS=0; WARNS=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAILS=$((FAILS+1)); }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; WARNS=$((WARNS+1)); }
fix()  { printf "      ↳ %s\n" "$1"; }

echo "ios-dev-loop doctor  ($(sw_vers -productVersion 2>/dev/null) $(uname -m))"; echo

echo "Xcode:"
STYLE=$(defaults read com.apple.dt.Xcode IDEBuildLocationStyle 2>/dev/null || echo Unique)
if [[ "$STYLE" == "Unique" ]]; then ok "build location: Unique (per-DerivedData products)"
else
  bad "build location: $STYLE — every worktree's products land in ONE shared Build/Products; parallel sessions overwrite each other's .app (stale-binary screenshots, rc=137 kills), and BUILD_DIR workarounds break xcodebuild archive"
  fix "quit Xcode, then: defaults write com.apple.dt.Xcode IDEBuildLocationStyle Unique"
fi
if XP=$(xcode-select -p 2>/dev/null); then
  XV=$(xcodebuild -version 2>/dev/null | head -1)
  case "$XV" in
    *"Xcode 27"*)
      X26=$(ls -d /Applications/Xcode*26*.app 2>/dev/null | head -1)
      warn "$XV is the selected Xcode ($XP) — the Claude Desktop iOS simulator pane targets Xcode 26.x per code.claude.com/docs/en/desktop-ios-simulator; keep 26 selected and 27 side-by-side if you use that pane"
      fix "sudo xcode-select -s ${X26:-/Applications/Xcode-26.app}" ;;
    "") bad "xcodebuild errored at $XP — license not accepted or CLT-only"; fix "sudo xcodebuild -license accept  (or select the full Xcode.app)" ;;
    *) ok "$XV at $XP" ;;
  esac
else
  bad "no Xcode selected"; fix "sudo xcode-select -s /Applications/Xcode.app"
fi
if xcrun simctl list runtimes 2>/dev/null | grep -q '^iOS'; then ok "iOS simulator runtime: $(xcrun simctl list runtimes | awk '/^iOS/{r=$1" "$2} END{print r}')"
else warn "no iOS simulator runtime"; fix "xcodebuild -downloadPlatform iOS"; fi

echo; echo "Tools:"
OUTDATED=$(brew outdated --quiet 2>/dev/null || true)
tool() {  # tool <name> <brew-formula> <version-cmd> <role> <required 0|1>
  local name="$1" formula="$2" vcmd="$3" role="$4" req="$5" v
  if command -v "$name" >/dev/null 2>&1; then
    v=$(eval "$vcmd" 2>/dev/null | head -1 | tr -d '\n')
    if printf '%s\n' "$OUTDATED" | grep -qx "$formula"; then warn "$name ${v:+($v)} — outdated"; fix "brew upgrade $formula"
    else ok "$name ${v:+($v)} — $role"; fi
  elif (( req )); then bad "$name missing — $role"; fix "brew install $formula"
  else warn "$name missing — $role (the loop degrades gracefully without it)"; fix "brew install $formula"; fi
}
tool xcodegen     xcodegen     'xcodegen --version'                         "project.yml → .xcodeproj"                       0
tool xcsift       xcsift       'xcsift --version'                           "build-log filter (errors/warnings only)"        0
tool swiftlint    swiftlint    'swiftlint version'                          "PostToolUse autocorrect"                        0
tool axe          axe          'axe --version'                              "a11y tree + taps on the simulator"              0
tool xcodebuildmcp xcodebuildmcp 'xcodebuildmcp --version'                  "structured build/test/UI automation (CLI mode)" 0
tool fastlane     fastlane     'fastlane --version 2>/dev/null | grep -E "^fastlane [0-9]" | awk "{print \$2}"'               "ASC writer for new-ios-app projects"            0
tool shellcheck   shellcheck   'shellcheck --version | awk "/^version:/{print \$2}"' "script linting"                       0
AX=$(find "$HOME/.claude/plugins/cache/axiom-marketplace/axiom" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | sort -V | tail -1)
if [[ -n "$AX" ]]; then
  N=$(ls -d "$HOME"/.claude/plugins/cache/axiom-marketplace/axiom/*/ 2>/dev/null | wc -l | tr -d ' ')
  ok "Axiom plugin $AX cached${N:+ ($N version(s) on disk)}"; (( N > 1 )) && fix "older cached versions can go: ls ~/.claude/plugins/cache/axiom-marketplace/axiom/"
else warn "Axiom plugin not installed (axiom-build, axiom-swiftui, xcsym, xclog)"; fix "/plugin marketplace add axiom  → /plugin install axiom"; fi

echo; echo "Disk:"
free_gb() { df -g "$1" 2>/dev/null | awk 'NR==2{print $4}'; }
R=$(free_gb /); TV=$(free_gb "${TMPDIR:-/tmp}")
if [[ -n "$R" ]]; then
  if (( R < 10 )); then bad "/ has ${R}G free — builds, simulators and tool output all fail with ENOSPC below ~1G"
  elif (( R < 25 )); then warn "/ has ${R}G free"; else ok "/ has ${R}G free"; fi
fi
[[ -n "$TV" && "$TV" != "$R" ]] && ok "temp volume has ${TV}G free"
CLONE_DIR="$(dirname "$(getconf DARWIN_USER_TEMP_DIR 2>/dev/null || echo /tmp/x)")/X"
if [[ -d "$CLONE_DIR" ]]; then
  N=$(find "$CLONE_DIR" -maxdepth 2 -type d -name 'com.google.Chrome.code_sign_clone*' 2>/dev/null | wc -l | tr -d ' ')
  INNER=$(find "$CLONE_DIR"/com.google.Chrome.code_sign_clone* -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
  if (( INNER > 20 )); then warn "Chrome code_sign_clone: $INNER clone dirs (each ~14 MB; 593 once filled the disk)"
    fix "find $CLONE_DIR/com.google.Chrome.code_sign_clone* -mindepth 1 -maxdepth 1 -type d -mmin +15 -exec rm -rf {} +"
  else ok "Chrome code_sign_clone: $INNER clone dir(s)"; fi
fi
DD_STD="$HOME/Library/Developer/Xcode/DerivedData"
if [[ -d "$DD_STD" ]]; then SZ=$(du -sg "$DD_STD" 2>/dev/null | cut -f1); (( ${SZ:-0} > 30 )) && { warn "global DerivedData is ${SZ}G"; fix "xcodebuildmcp purge --report   (audit first, then delete per project)"; } || ok "global DerivedData ${SZ:-0}G"; fi

echo; echo "Build slot:"
TMP="${TMPDIR:-/tmp}"; LOCK="${BUILD_SLOT_LOCK:-${TMP%/}/ios-build-slot.lock}"
if [[ -d "$LOCK" ]]; then
  pid=$(cat "$LOCK/pid" 2>/dev/null || echo ?)
  if kill -0 "$pid" 2>/dev/null; then ok "held by pid $pid — $(cat "$LOCK/label" 2>/dev/null)"
  else warn "stale lock (pid $pid dead) — the next build-slot.sh call steals it"; fi
else ok "free ($LOCK)"; fi
DS=$(ps -axo stat,comm 2>/dev/null | awk '$1 ~ /^D/ && /xcodebuild|swift-frontend|SourceKit/ {n++} END{print n+0}')
(( DS > 0 )) && { warn "$DS build process(es) in uninterruptible wait — usually a stuck syspolicyd (Gatekeeper), not the build"; fix "sudo killall -9 syspolicyd; then kill the D-state builds and rebuild one at a time"; }

echo
if (( FAILS == 0 )); then echo "HEALTHY ✅  ($WARNS warning(s))"; exit 0; else echo "FIX NEEDED ❌  $FAILS blocker(s), $WARNS warning(s) — fixes printed above, none applied."; exit 1; fi
