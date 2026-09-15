#!/usr/bin/env bash
# tools/ship.sh — the TestFlight pipeline as one script, in three explicit stages.
#
#   tools/ship.sh prepare <version> <build> [--changelog]   # guards → preflight → tests → bump → xcodegen → commit
#   tools/ship.sh prepare patch|minor|major|build [--changelog]   # same, numbers derived from the current ones
#   tools/ship.sh archive                     # Release archive → Info.plist check → export IPA (no path overrides)
#   tools/ship.sh upload                      # altool (or fastlane) → attach hook → annotated tag
#   tools/ship.sh status                      # what stage the current checkout is at
#
# Why three invocations and not one: `upload` is the irreversible step. A build
# once shipped past a hold because an async "wait" message raced an agent
# mid-run. Here the GO token is structural — nobody uploads without being told
# to run `upload`. Rules encoded:
#   * clean tree; HEAD on main/master is refused, and so is the canonical checkout
#     of a repo with linked worktrees — SHIP_ALLOW_MAIN=1 lifts both guards
#   * optional project hooks run when present: script/sync-data --check,
#     script/preflight or tools/*preflight*.sh (survey), script/test (SHIP_SKIP_TESTS=1 to skip)
#   * --changelog: CHANGELOG.md [Unreleased] must have content; it is rolled into a
#     versioned entry and exported to build/RELEASE_NOTES.txt (TestFlight "What to Test")
#   * version lives in ONE place: project.yml (CFBundleShortVersionString/CFBundleVersion
#     or MARKETING_VERSION/CURRENT_PROJECT_VERSION), else the .pbxproj
#   * archive with ZERO DerivedData/BUILD_DIR overrides (they break xcodebuild archive)
#   * verify the .xcarchive has an Info.plist before exporting
#   * upload: SHIP_UPLOADER=altool (default) or fastlane (auto when .aos-app.env +
#     fastlane/Fastfile exist — runs `fastlane ios ship`, which re-archives via gym)
#   * post-upload attach hook when present (Scripts/attach-to-testflight.sh <build>)
#   * annotated tag v<version>-b<build> AFTER upload succeeds, never before;
#     the tag is NOT pushed unless SHIP_PUSH_TAG=1 — pushing is the operator's call
#
# Credentials: ~/aos/core/bin/cli/agent-secret get ASC_KEY_ID / ASC_ISSUER_ID /
# APPLE_TEAM_ID (Keychain) and ~/.appstoreconnect/private_keys/AuthKey_<id>.p8.
# Env: APP_DIR, SCHEME, EXPORT_OPTIONS (plist path), SHIP_ALLOW_MAIN=1 (main branch + canonical checkout).
set -euo pipefail

STAGE="${1:-status}"; shift || true
if ! TOP=$(git rev-parse --show-toplevel 2>/dev/null); then
    [[ "$STAGE" == status ]] || { echo "✗ not a git repository — a release is a commit, not a vibe" >&2; exit 1; }
    TOP=$PWD
fi
cd "$TOP"
STATE=".agent/ship-state"          # gitignored; stage artifacts recorded here
SECRET="$HOME/aos/core/bin/cli/agent-secret"

die() { echo "✗ $*" >&2; exit 1; }
say() { printf '\033[1m→ %s\033[0m\n' "$*"; }
need_clean() { [[ -z "$(git status --porcelain)" ]] || die "working tree not clean — commit or stash first"; }
not_canonical() {  # SHIP_ALLOW_MAIN=1 lifts BOTH guards: the main branch and the canonical checkout
    local common linked branch
    [[ "${SHIP_ALLOW_MAIN:-0}" == "1" ]] && return 0
    branch=$(git rev-parse --abbrev-ref HEAD)
    [[ "$branch" != main && "$branch" != master ]] || die "on $branch — release from a release/v<version> branch (SHIP_ALLOW_MAIN=1 to override)"
    common=$(git rev-parse --git-common-dir); linked=$(git worktree list --porcelain | grep -c '^worktree ')
    if [[ "$common" == ".git" && "$linked" -gt 1 ]]; then
        die "this is the canonical checkout of a repo that uses linked worktrees — ship from a release/v<version> worktree (SHIP_ALLOW_MAIN=1 to override)"
    fi
}
secret() { [[ -x "$SECRET" ]] && "$SECRET" get "$1" 2>/dev/null || true; }
# shellcheck disable=SC1090
load_state() { [[ -f "$STATE" ]] && source "$STATE" || true; }
save_state() { mkdir -p .agent; printf 'VERSION=%q\nBUILD=%q\nARCHIVE=%q\nIPA=%q\nNOTES=%q\nSTAGE_DONE=%q\n' "${VERSION:-}" "${BUILD:-}" "${ARCHIVE:-}" "${IPA:-}" "${NOTES_FILE:-}" "$1" > "$STATE"; }

# ─── app dir / project / scheme / version file ─────────────────────────────
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
APP=$(find_app_dir); [[ -n "$APP" ]] || die "no project.yml / .xcodeproj found (set APP_DIR)"
APP_REL=${APP#"$TOP"/}; [[ "$APP" == "$TOP" ]] && APP_REL="."
container() {  # sets CONTAINER=(-workspace X | -project Y), run inside $APP
    local ws pj
    ws=$(find . -maxdepth 1 -name "*.xcworkspace" | head -1); pj=$(find . -maxdepth 1 -name "*.xcodeproj" | head -1)
    if [[ -n "$ws" ]]; then CONTAINER=(-workspace "$ws"); else CONTAINER=(-project "${pj:?no .xcodeproj in $APP}"); fi
}
scheme() {
    [[ -n "${SCHEME:-}" ]] && { echo "$SCHEME"; return; }
    local s; s=$(awk '/^ *scheme:/{print $2; exit}' "$APP/.xcodebuildmcp/config.yaml" 2>/dev/null)
    [[ -n "$s" ]] && { echo "$s"; return; }
    ( cd "$APP" && container && xcodebuild "${CONTAINER[@]}" -list 2>/dev/null | awk '/Schemes:/{f=1;next} f&&NF{print $1;exit}' )
}
ver_file() {
    if [[ -f "$APP/project.yml" ]]; then echo "$APP/project.yml"; else ls "$APP"/*.xcodeproj/project.pbxproj 2>/dev/null | head -1; fi
}
read_key() {  # read_key <file> version|build
    local f="$1" v=""
    if [[ "$f" == *.yml ]]; then
        if [[ "$2" == version ]]; then
            v=$(sed -nE 's/^[[:space:]]*CFBundleShortVersionString:[[:space:]]*"?([^"$[:space:]#]+)"?.*/\1/p' "$f" | head -1)
            [[ -n "$v" ]] || v=$(sed -nE 's/^[[:space:]]*MARKETING_VERSION:[[:space:]]*"?([^"$[:space:]#]+)"?.*/\1/p' "$f" | head -1)
        else
            v=$(sed -nE 's/^[[:space:]]*CFBundleVersion:[[:space:]]*"?([^"$[:space:]#]+)"?.*/\1/p' "$f" | head -1)
            [[ -n "$v" ]] || v=$(sed -nE 's/^[[:space:]]*CURRENT_PROJECT_VERSION:[[:space:]]*"?([^"$[:space:]#]+)"?.*/\1/p' "$f" | head -1)
        fi
    else
        if [[ "$2" == version ]]; then v=$(sed -nE 's/^[[:space:]]*MARKETING_VERSION = "?([^";]+)"?;.*/\1/p' "$f" | head -1)
        else v=$(sed -nE 's/^[[:space:]]*CURRENT_PROJECT_VERSION = "?([^";]+)"?;.*/\1/p' "$f" | head -1); fi
    fi
    echo "$v"
}
write_keys() {  # write_keys <file> <version> <build> — every occurrence, both key styles
    local f="$1" ver="$2" bld="$3"
    if [[ "$f" == *.yml ]]; then
        sed -i '' -E \
            -e "s/^([[:space:]]*CFBundleShortVersionString:[[:space:]]*)\"?[^\"\$[:space:]#]+\"?/\1\"$ver\"/" \
            -e "s/^([[:space:]]*MARKETING_VERSION:[[:space:]]*)\"?[^\"\$[:space:]#]+\"?/\1\"$ver\"/" \
            -e "s/^([[:space:]]*CFBundleVersion:[[:space:]]*)\"?[^\"\$[:space:]#]+\"?/\1\"$bld\"/" \
            -e "s/^([[:space:]]*CURRENT_PROJECT_VERSION:[[:space:]]*)\"?[^\"\$[:space:]#]+\"?/\1\"$bld\"/" "$f"
    else
        sed -i '' -E \
            -e "s/^([[:space:]]*MARKETING_VERSION = )\"?[^\";]+\"?;/\1$ver;/" \
            -e "s/^([[:space:]]*CURRENT_PROJECT_VERSION = )\"?[^\";]+\"?;/\1$bld;/" "$f"
    fi
}
first_existing() { local c; for c in "$@"; do [[ -x "$c" ]] && { echo "$c"; return; }; done; return 0; }

case "$STAGE" in
# ───────────────────────────────────────────────────────────────────────────
prepare)
    CHANGELOG=0; ARGS=()
    for a in "$@"; do case "$a" in --changelog) CHANGELOG=1 ;; *) ARGS+=("$a") ;; esac; done
    VF=$(ver_file); [[ -n "$VF" && -f "$VF" ]] || die "no version file (project.yml or .pbxproj) under $APP"
    CUR_V=$(read_key "$VF" version); CUR_B=$(read_key "$VF" build)
    [[ -n "$CUR_V" && -n "$CUR_B" ]] || die "could not read version/build from $VF (expects CFBundleShortVersionString/CFBundleVersion or MARKETING_VERSION/CURRENT_PROJECT_VERSION)"
    case "${ARGS[0]:-}" in
        patch|minor|major|build)
            IFS=. read -r MAJ MIN PAT <<<"$CUR_V"; MIN=${MIN:-0}; PAT=${PAT:-0}
            case "${ARGS[0]}" in
                major) VERSION="$((MAJ + 1)).0.0" ;; minor) VERSION="$MAJ.$((MIN + 1)).0" ;;
                patch) VERSION="$MAJ.$MIN.$((PAT + 1))" ;; build) VERSION="$CUR_V" ;;
            esac
            BUILD=$((CUR_B + 1)) ;;
        "") die "usage: ship.sh prepare <version> <build> | patch|minor|major|build  [--changelog]" ;;
        *) VERSION="${ARGS[0]}"; BUILD="${ARGS[1]:?build number}" ;;
    esac
    not_canonical; need_clean
    say "0. checkout: $(git rev-parse --abbrev-ref HEAD) @ $TOP  (app: $APP_REL)"
    SYNC=$(first_existing "$TOP/script/sync-data" "$APP/script/sync-data")
    [[ -n "$SYNC" ]] && { say "0. project data present"; "$SYNC" --check; }
    PRE=$(first_existing "$TOP/script/preflight" "$APP/script/preflight" "$TOP"/tools/*preflight*.sh)
    [[ -n "$PRE" ]] && { say "1. preflight survey (read this with the operator): $PRE"; "$PRE" || true; }
    (( BUILD > CUR_B )) || die "build $BUILD is not > current $CUR_B (origin/main may be ahead — see the preflight survey)"
    TAG="v$VERSION-b$BUILD"; git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists"
    TEST=$(first_existing "$TOP/script/test" "$APP/script/test")
    if [[ -n "$TEST" && "${SHIP_SKIP_TESTS:-0}" != "1" ]]; then
        say "2. tests ($TEST)"; TLOG=$(mktemp); "$TEST" >"$TLOG" 2>&1 || { tail -20 "$TLOG"; rm -f "$TLOG"; die "tests failing"; }; rm -f "$TLOG"; echo "   tests green"
    fi
    NOTES_FILE=""
    if (( CHANGELOG )); then
        [[ -f CHANGELOG.md ]] || die "--changelog: no CHANGELOG.md at $TOP"
        NOTES=$(awk '/^## \[Unreleased\]/{flag=1; next} /^## \[/{flag=0} flag' CHANGELOG.md | sed -e '/./,$!d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')
        [[ -n "$NOTES" ]] || die "CHANGELOG.md [Unreleased] is empty — write what changed before shipping"
        say "3. rolling CHANGELOG [Unreleased] → [$VERSION ($BUILD)]"
        python3 - "$VERSION" "$BUILD" "$(date +%Y-%m-%d)" <<'PY'
import sys
version, build, today = sys.argv[1:4]
p = "CHANGELOG.md"; t = open(p).read()
open(p, "w").write(t.replace("## [Unreleased]", "## [Unreleased]\n\n## [%s (%s)] — %s" % (version, build, today), 1))
PY
        mkdir -p build; NOTES_FILE="$TOP/build/RELEASE_NOTES.txt"
        printf '%s\n' "$NOTES" | head -c 3900 > "$NOTES_FILE"; echo "   notes → build/RELEASE_NOTES.txt"
    fi
    say "4. bump $VF → v$VERSION build $BUILD (was v$CUR_V build $CUR_B)"
    write_keys "$VF" "$VERSION" "$BUILD"
    [[ "$(read_key "$VF" version)" == "$VERSION" && "$(read_key "$VF" build)" == "$BUILD" ]] || die "bump did not land in $VF"
    if [[ -f "$APP/project.yml" ]]; then
        say "5. xcodegen"; ( cd "$APP" && xcodegen generate >/dev/null )
    fi
    git add -A "$VF"; git add -A "$APP"/*.xcodeproj 2>/dev/null || true; (( CHANGELOG )) && git add CHANGELOG.md
    git commit -q -m "release: v$VERSION build $BUILD" && say "committed bump: $(git log -1 --format=%h)"
    save_state prepare
    echo; echo "Next: tools/ship.sh archive" ;;
# ───────────────────────────────────────────────────────────────────────────
archive)
    load_state; [[ "${STAGE_DONE:-}" == prepare ]] || die "run 'prepare' first (no ship-state)"
    need_clean
    SCH=$(scheme); [[ -n "$SCH" ]] || die "no scheme found (set SCHEME)"
    KEY_ID=$(secret ASC_KEY_ID); ISS=$(secret ASC_ISSUER_ID); TEAM=$(secret APPLE_TEAM_ID)
    KEY_FILE="$HOME/.appstoreconnect/private_keys/AuthKey_${KEY_ID}.p8"
    AUTH=(); SIGN=()
    if [[ -n "$KEY_ID" && -n "$ISS" && -f "$KEY_FILE" ]]; then
        AUTH=(-authenticationKeyPath "$KEY_FILE" -authenticationKeyID "$KEY_ID" -authenticationKeyIssuerID "$ISS")
    else
        echo "  (no ASC API key in Keychain/.p8 — relying on Xcode's signed-in account for provisioning)" >&2
    fi
    [[ -n "$TEAM" ]] && SIGN=(CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM="$TEAM")
    ARCHIVE="${TMPDIR:-/tmp}/${SCH}-$VERSION-b$BUILD.xcarchive"; EXPORT="${TMPDIR:-/tmp}/${SCH}-$VERSION-b$BUILD-export"
    rm -rf "$ARCHIVE" "$EXPORT"
    SLOT="$TOP/tools/build-slot.sh"; [[ -x "$SLOT" ]] || SLOT=""
    say "archive Release: $SCH (no DerivedData/BUILD_DIR overrides — by design)"
    ( cd "$APP" && container && $SLOT xcodebuild "${CONTAINER[@]}" -scheme "$SCH" \
        -configuration Release -destination 'generic/platform=iOS' -archivePath "$ARCHIVE" archive \
        ${SIGN[@]+"${SIGN[@]}"} -allowProvisioningUpdates ${AUTH[@]+"${AUTH[@]}"} \
        -quiet 2>&1 | { command -v xcsift >/dev/null && xcsift -w || cat; } | tail -30 )
    [[ -f "$ARCHIVE/Info.plist" ]] || die "archive incomplete — no Info.plist in $ARCHIVE (a DerivedData/BUILD_DIR override splits the archive; do not pass any)"
    OPTS="${EXPORT_OPTIONS:-$APP/ExportOptions.plist}"
    if [[ ! -f "$OPTS" ]]; then
        mkdir -p "$EXPORT"; OPTS="$EXPORT/ExportOptions.plist"
        [[ -n "$TEAM" ]] || TEAM=$(cd "$APP" && container && xcodebuild "${CONTAINER[@]}" -scheme "$SCH" -showBuildSettings 2>/dev/null | awk -F' = ' '/ DEVELOPMENT_TEAM/{print $2; exit}')
        cat > "$OPTS" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>export</string>
  <key>signingStyle</key><string>automatic</string>
  <key>uploadSymbols</key><true/>
  ${TEAM:+<key>teamID</key><string>$TEAM</string>}
</dict></plist>
PLIST
        echo "  (no ExportOptions.plist in $APP_REL — generated a minimal app-store-connect one at $OPTS)"
    fi
    say "export IPA"
    ( cd "$APP" && xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportPath "$EXPORT" -exportOptionsPlist "$OPTS" \
        ${AUTH[@]+"${AUTH[@]}"} -allowProvisioningUpdates -quiet 2>&1 | tail -5 )
    IPA=$(ls "$EXPORT"/*.ipa 2>/dev/null | head -1); [[ -n "$IPA" ]] || die "no .ipa in $EXPORT"
    say "IPA: $IPA ($(du -h "$IPA" | cut -f1))"
    save_state archive
    echo; echo "STOP. Show the operator the IPA size + the preflight decision, then on explicit GO: tools/ship.sh upload" ;;
# ───────────────────────────────────────────────────────────────────────────
upload)
    load_state; [[ "${STAGE_DONE:-}" == archive && -f "${IPA:-/nonexistent}" ]] || die "run 'archive' first"
    KEY_ID=$(secret ASC_KEY_ID); ISS=$(secret ASC_ISSUER_ID)
    UPLOADER="${SHIP_UPLOADER:-}"
    if [[ -z "$UPLOADER" ]]; then
        UPLOADER=altool
        for d in "$APP" "$TOP"; do [[ -f "$d/.aos-app.env" && -f "$d/fastlane/Fastfile" ]] && { UPLOADER=fastlane; FL_DIR="$d"; }; done
    fi
    case "$UPLOADER" in
        altool)
            [[ -n "$KEY_ID" && -n "$ISS" ]] || die "ASC_KEY_ID / ASC_ISSUER_ID missing in Keychain ($SECRET set …)"
            say "upload $IPA (altool)"
            xcrun altool --upload-app -f "$IPA" -t ios --apiKey "$KEY_ID" --apiIssuer "$ISS" 2>&1 | tail -3
            [[ ${PIPESTATUS[0]} -eq 0 ]] || die "altool upload failed" ;;
        fastlane)
            FL_DIR="${FL_DIR:-$APP}"
            say "upload via fastlane ios ship in $FL_DIR (re-archives with gym; SHIP_UPLOADER=altool uploads the rehearsal IPA instead)"
            [[ -n "$KEY_ID" && -n "$ISS" ]] || die "ASC_KEY_ID / ASC_ISSUER_ID missing in Keychain"
            export ASC_KEY_ID="$KEY_ID" ASC_ISSUER_ID="$ISS" ASC_KEY_PATH="$HOME/.appstoreconnect/private_keys/AuthKey_${KEY_ID}.p8"
            APPLE_TEAM_ID=$(secret APPLE_TEAM_ID); export APPLE_TEAM_ID
            export FASTLANE_DISABLE_COLORS=1 SPACESHIP_SKIP_2FA_UPGRADE=1
            [[ -n "${NOTES:-}" && -f "$NOTES" ]] && export RELEASE_NOTES_PATH="$NOTES"
            ( cd "$FL_DIR" && set -a && source ./.aos-app.env && set +a && fastlane ios ship ) || die "fastlane ios ship failed" ;;
        *) die "unknown SHIP_UPLOADER=$UPLOADER" ;;
    esac
    ATTACH=$(first_existing "$APP/Scripts/attach-to-testflight.sh" "$TOP/ios/Scripts/attach-to-testflight.sh" "$APP/script/attach-to-testflight" "$TOP/script/attach-to-testflight")
    if [[ -n "$ATTACH" ]]; then
        say "attach build $BUILD to the TestFlight group ($ATTACH, loops until VALID)"
        ok=0; for i in 1 2 3; do "$ATTACH" "$BUILD" && { ok=1; break; }; echo "attach attempt $i failed — retrying in 30s"; sleep 30; done
        (( ok )) || die "upload succeeded but attach failed — run $ATTACH $BUILD by hand (testers see nothing until then)"
    fi
    TAG="v$VERSION-b$BUILD"
    git tag -a "$TAG" -m "release: v$VERSION build $BUILD" && say "tagged $TAG (after upload, never before)"
    if [[ "${SHIP_PUSH_TAG:-0}" == "1" ]]; then git push origin "$TAG" && say "pushed $TAG"; else echo "   tag NOT pushed — with operator approval: git push origin $TAG"; fi
    save_state upload
    echo; echo "Done. Merge the release branch with operator approval; then tools/worktree.sh --remove <slug>." ;;
# ───────────────────────────────────────────────────────────────────────────
status)
    load_state; VF=$(ver_file)
    echo "checkout: $TOP"; echo "branch:   $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(not a git repo)")"; echo "app dir:  $APP_REL"
    [[ -n "$VF" && -f "$VF" ]] && echo "project:  v$(read_key "$VF" version) build $(read_key "$VF" build)  ($VF)"
    echo "stage:    ${STAGE_DONE:-none}  ${VERSION:+(v$VERSION b$BUILD)}"; [[ -n "${IPA:-}" ]] && echo "ipa:      $IPA"
    UP=altool; for d in "$APP" "$TOP"; do [[ -f "$d/.aos-app.env" && -f "$d/fastlane/Fastfile" ]] && UP=fastlane; done
    echo "uploader: ${SHIP_UPLOADER:-$UP}" ;;
*) grep '^# ' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1 ;;
esac
