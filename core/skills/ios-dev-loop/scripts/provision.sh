#!/usr/bin/env bash
# tools/provision.sh — make the current checkout/worktree buildable. Idempotent,
# cheap when nothing is missing (<1s), so it runs from tools/worktree.sh AND
# from the SessionStart hook: a worktree created by Claude Desktop or a bare
# `git worktree add` gets the same treatment as one from worktree.sh.
#
#   1. git submodules initialized when .gitmodules lists one that is empty
#      (an empty submodule builds green with no data in the app)
#   2. project data materialized: `script/sync-data --check` else `script/sync-data`
#      (skipped when the project has no such script)
#   3. xcodegen when project.yml is newer than the .xcodeproj (stale tracked pbxproj)
#   4. a leased simulator in .ios-sim, named <project>-<worktree>
#      (IOS_SIM_TYPE, default "iPhone 16 Pro"; falls back to the newest iPhone type)
#
# Usage: tools/provision.sh [--quiet] [--no-sim]
#
# Canonical-checkout guard: when this repo uses linked worktrees (the
# worktree-per-session model), the main checkout is the read-only reference
# and is NOT provisioned. A repo with no linked worktrees IS provisioned in
# place. IOS_PROVISION_MAIN=1 overrides. APP_DIR=<dir> overrides discovery
# of the directory holding project.yml / the .xcodeproj (often ios/).
set -u
QUIET=0; SIM=1
for a in "$@"; do case "$a" in --quiet) QUIET=1 ;; --no-sim) SIM=0 ;; --help|-h) grep '^# ' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1 ;; esac; done
log() { (( QUIET )) || echo "  $*"; }
TOP=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$TOP" || exit 0

COMMON=$(git rev-parse --git-common-dir 2>/dev/null || echo .git)
MAIN_TOP=$(cd "$(dirname "$COMMON")" 2>/dev/null && pwd -P || echo "$TOP")
LINKED=$(git worktree list --porcelain 2>/dev/null | grep -c '^worktree ')
if [[ "$COMMON" == ".git" && "$LINKED" -gt 1 && "${IOS_PROVISION_MAIN:-0}" != "1" ]]; then
    log "(main checkout with linked worktrees — read-only reference, not provisioned; IOS_PROVISION_MAIN=1 to force)"
    exit 0
fi

find_app_dir() {  # dir holding project.yml / *.xcodeproj / *.xcworkspace
    [[ -n "${APP_DIR:-}" ]] && { echo "$APP_DIR"; return; }
    local d
    for d in "$TOP" "$TOP/ios" "$TOP/iOS" "$TOP/app"; do
        [[ -f "$d/project.yml" ]] && { echo "$d"; return; }
        ls -d "$d"/*.xcworkspace "$d"/*.xcodeproj >/dev/null 2>&1 && { echo "$d"; return; }
    done
    d=$(find "$TOP" -maxdepth 3 \( -path '*/DerivedData' -o -path '*/.build' -o -path '*/node_modules' -o -path '*/.claude' -o -path '*/Pods' \) -prune -o \( -name project.yml -o -name '*.xcodeproj' \) -print 2>/dev/null | head -1)
    [[ -n "$d" ]] && dirname "$d"
}
APP=$(find_app_dir)

# 1. submodules
if [[ -f .gitmodules ]]; then
    git config -f .gitmodules --get-regexp 'submodule\..*\.path' 2>/dev/null | awk '{print $2}' | while read -r p; do
        if [[ -z "$(ls -A "$p" 2>/dev/null)" ]]; then
            log "→ submodule $p empty — initializing"
            git submodule update --init --recursive -- "$p" >/dev/null 2>&1 || echo "  ! submodule init failed for $p" >&2
        fi
    done
fi

# 2. project data hook
SYNC=""; for c in "$TOP/script/sync-data" "$APP/script/sync-data"; do [[ -x "$c" ]] && { SYNC="$c"; break; }; done
if [[ -n "$SYNC" ]] && ! "$SYNC" --check >/dev/null 2>&1; then
    log "→ materializing project data ($SYNC)"
    "$SYNC" >/dev/null 2>&1 && log "✓ data materialized" || echo "  ! sync-data failed — run it by hand before xcodegen" >&2
fi

# 3. xcodegen when project.yml is newer than the generated project
if [[ -n "$APP" && -f "$APP/project.yml" ]]; then
    if command -v xcodegen >/dev/null 2>&1; then
        NAME=$(awk '/^name:/{print $2; exit}' "$APP/project.yml" | tr -d '"'"'")
        PBX="$APP/${NAME}.xcodeproj/project.pbxproj"
        [[ -f "$PBX" ]] || PBX=$(ls "$APP"/*.xcodeproj/project.pbxproj 2>/dev/null | head -1)
        if [[ -z "$PBX" || ! -f "$PBX" || "$APP/project.yml" -nt "$PBX" ]]; then
            log "→ xcodegen (project.yml newer than .xcodeproj)"
            ( cd "$APP" && xcodegen generate >/dev/null 2>&1 ) && log "✓ xcodegen" || echo "  ! xcodegen failed" >&2
        fi
    else
        log "(project.yml present but xcodegen not installed — brew install xcodegen)"
    fi
fi

# 4. simulator lease
if (( SIM )) && command -v xcrun >/dev/null 2>&1; then
    if [[ ! -f .ios-sim ]] || ! xcrun simctl list devices available 2>/dev/null | grep -q "$(cat .ios-sim)"; then
        PROJECT=$(basename "$MAIN_TOP"); WT=$(basename "$TOP")
        NAME="$PROJECT"; [[ "$WT" != "$PROJECT" ]] && NAME="$PROJECT-$WT"
        # reuse a same-named simulator if the lease file was lost
        UDID=$(xcrun simctl list devices available 2>/dev/null | awk -F '[()]' -v n="$NAME" '$0 ~ "^ *"n" \\(" {print $2; exit}')
        if [[ -z "$UDID" ]]; then
            TYPE="${IOS_SIM_TYPE:-iPhone 16 Pro}"
            xcrun simctl list devicetypes 2>/dev/null | grep -q "^$TYPE (" || TYPE=$(xcrun simctl list devicetypes 2>/dev/null | awk -F' \\(' '/^iPhone/{t=$1} END{print t}')
            RUNTIME=$(xcrun simctl list runtimes 2>/dev/null | awk '/^iOS/{r=$NF} END{print r}')
            UDID=$(xcrun simctl create "$NAME" "$TYPE" "$RUNTIME" 2>/dev/null || true)
        fi
        if [[ -n "$UDID" ]]; then echo "$UDID" > .ios-sim; log "✓ simulator leased: $NAME ($UDID)"; else echo "  ! could not lease a simulator — snap will use the booted one" >&2; fi
    fi
fi
exit 0
