#!/usr/bin/env bash
# install.sh — Phase 1 of the ios-dev-loop skill: put the loop into a project.
# Idempotent: existing files are KEPT (your routes, your hooks) unless --force;
# settings.json hooks are merged, never overwritten; .gitignore lines appended
# only when missing. Safe to re-run after a skill update to see what drifted.
#
#   ~/.claude/skills/ios-dev-loop/scripts/install.sh [<repo-or-app-dir>] [--force] [--dry-run]
#
# Layout it produces (APP = dir holding project.yml / *.xcodeproj, often ios/):
#   APP/script/snap, APP/script/device            the loop
#   REPO/tools/build-slot.sh provision.sh worktree.sh ship.sh
#   REPO/.claude/hooks/*.sh + hooks merged into REPO/.claude/settings.json
#   APP/.xcodebuildmcp/config.yaml (scheme/project filled in), APP/.swiftlint.yml (only if none)
#   REPO/.gitignore: DerivedData/ .ios-sim .agent/ship-state .claude/worktrees/ build/snaps/
# Warns when .gitattributes routes *.json through LFS (it swallows .claude/settings.json).
set -u
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORCE=0; DRY=0; TARGET=""
for a in "$@"; do case "$a" in --force) FORCE=1 ;; --dry-run) DRY=1 ;; --help|-h) grep '^# ' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1 ;; *) TARGET="$a" ;; esac; done
TARGET="${TARGET:-$PWD}"; TARGET="$(cd "$TARGET" && pwd -P)" || exit 1
REPO=$(git -C "$TARGET" rev-parse --show-toplevel 2>/dev/null || echo "$TARGET")

find_app_dir() {
    [[ -n "${APP_DIR:-}" ]] && { echo "$APP_DIR"; return; }
    local d
    for d in "$TARGET" "$REPO" "$REPO/ios" "$REPO/iOS" "$REPO/app"; do
        [[ -f "$d/project.yml" ]] && { echo "$d"; return; }
        ls -d "$d"/*.xcworkspace "$d"/*.xcodeproj >/dev/null 2>&1 && { echo "$d"; return; }
    done
    d=$(find "$REPO" -maxdepth 3 \( -path '*/DerivedData' -o -path '*/.build' -o -path '*/node_modules' -o -path '*/.claude' -o -path '*/Pods' \) -prune -o \( -name project.yml -o -name '*.xcodeproj' \) -print 2>/dev/null | head -1)
    [[ -n "$d" ]] && dirname "$d"
}
APP=$(find_app_dir); [[ -n "$APP" ]] || { echo "✗ no project.yml / .xcodeproj under $REPO (set APP_DIR)" >&2; exit 1; }
APP_REL=${APP#"$REPO"/}; [[ "$APP" == "$REPO" ]] && APP_REL="."
echo "ios-dev-loop install → repo $REPO"; echo "                       app  $APP_REL"; (( DRY )) && echo "                       (dry run)"; echo

put() {  # put <src> <dst> [mode]
    local src="$1" dst="$2" mode="${3:-644}" rel="${2#"$REPO"/}"
    if [[ -f "$dst" ]]; then
        if cmp -s "$src" "$dst"; then echo "  = $rel (up to date)"
        elif (( FORCE )); then (( DRY )) || { cp "$src" "$dst"; chmod "$mode" "$dst"; }; echo "  ↻ $rel (overwritten, --force)"
        else echo "  · $rel (exists, kept — differs from the template; diff: diff $src $dst)"; fi
    else
        (( DRY )) || { mkdir -p "$(dirname "$dst")"; cp "$src" "$dst"; chmod "$mode" "$dst"; }; echo "  + $rel"
    fi
}

echo "Scripts:"
put "$SKILL/scripts/snap"   "$APP/script/snap"   755
put "$SKILL/scripts/device" "$APP/script/device" 755
for t in build-slot.sh provision.sh worktree.sh ship.sh; do put "$SKILL/scripts/$t" "$REPO/tools/$t" 755; done

echo "Hooks:"
for h in pre-tool-guard.sh post-edit-swift.sh stop-build-gate.sh handoff-staleness.sh; do put "$SKILL/hooks/$h" "$REPO/.claude/hooks/$h" 755; done
SETTINGS="$REPO/.claude/settings.json"
if (( DRY )); then echo "  ~ .claude/settings.json (hooks would be merged)"; else
mkdir -p "$REPO/.claude"
python3 - "$SETTINGS" "$SKILL/hooks/settings.hooks.json" <<'PY'
import json, os, re, sys
path, snippet_path = sys.argv[1], sys.argv[2]
snippet = json.load(open(snippet_path))["hooks"]
indent = 2
try:
    if os.path.exists(path):
        raw = open(path).read(); data = json.loads(raw)
        m = re.search(r'^( +)"', raw, re.M); indent = len(m.group(1)) if m else 2
    else:
        data = {}
except ValueError:
    print("  ✗ .claude/settings.json is not valid JSON (LFS pointer? see .gitattributes) — hooks NOT merged"); sys.exit(0)
hooks = data.setdefault("hooks", {})
added = 0
for event, groups in snippet.items():
    existing = hooks.setdefault(event, [])
    present = {h.get("command") for g in existing for h in g.get("hooks", [])}
    for g in groups:
        new = [h for h in g.get("hooks", []) if h.get("command") not in present]
        if not new: continue
        entry = {k: v for k, v in g.items() if k != "hooks"}; entry["hooks"] = new
        existing.append(entry); added += len(new)
if added:
    with open(path, "w") as f: json.dump(data, f, indent=indent, ensure_ascii=False); f.write("\n")
print("  %s .claude/settings.json (%d hook(s) %s)" % ("+" if added else "=", added, "merged" if added else "already registered"))
PY
fi

echo "Config:"
if [[ ! -f "$APP/.xcodebuildmcp/config.yaml" || $FORCE -eq 1 ]]; then
    PJ=$(cd "$APP" && { ls -d ./*.xcworkspace 2>/dev/null; ls -d ./*.xcodeproj 2>/dev/null; } | head -1 | sed 's|^\./||')
    if [[ -z "$PJ" && -f "$APP/project.yml" ]]; then PJ="$(awk '/^name:/{print $2; exit}' "$APP/project.yml" | tr -d '"'"'").xcodeproj"; fi
    SCH="${SCHEME:-}"
    [[ -n "$SCH" ]] || SCH=$(cd "$APP" && { [[ "$PJ" == *.xcworkspace ]] && xcodebuild -workspace "$PJ" -list 2>/dev/null || xcodebuild -project "$PJ" -list 2>/dev/null; } | awk '/Schemes:/{f=1;next} f&&NF{print $1;exit}')
    # No .xcodeproj yet (fresh worktree, xcodegen not run): read project.yml. Prefer the
    # first target of `type: application`, then the one named like the project, then the
    # first target — the first key is often a test/extension target (deenoverdunya listed
    # DeenOverDunyaUITests first and the installer wrote that as the scheme).
    [[ -n "$SCH" || ! -f "$APP/project.yml" ]] || SCH=$(awk '
        /^name:/ && !pn { pn=$2; gsub(/["'"'"']/,"",pn) }
        /^targets:/ { f=1; next }
        f && /^[^ ]/ { f=0 }
        f && /^  [A-Za-z0-9_.-]+:/ { t=$1; sub(":","",t); if (!first) first=t }
        f && t && /^ +type: *application/ && !app { app=t }
        END { if (app) print app; else if (pn) print pn; else print first }' "$APP/project.yml")
    [[ -n "$SCH" ]] || SCH="${PJ%.*}"
    SIMN="${IOS_SIM_TYPE:-iPhone 16 Pro}"
    if (( DRY )); then echo "  + $APP_REL/.xcodebuildmcp/config.yaml (project $PJ, scheme $SCH)"; else
        mkdir -p "$APP/.xcodebuildmcp"
        sed -e "s|__PROJECT_FILE__|$PJ|" -e "s|__SCHEME__|$SCH|" -e "s|__SIM_NAME__|$SIMN|" "$SKILL/templates/xcodebuildmcp.config.yaml" > "$APP/.xcodebuildmcp/config.yaml"
        echo "  + $APP_REL/.xcodebuildmcp/config.yaml (project $PJ, scheme $SCH)"
    fi
else echo "  · $APP_REL/.xcodebuildmcp/config.yaml (exists, kept)"; fi
if [[ -f "$APP/.swiftlint.yml" || -f "$REPO/.swiftlint.yml" ]]; then echo "  · .swiftlint.yml (project has one, kept)"
else put "$SKILL/templates/swiftlint.minimal.yml" "$APP/.swiftlint.yml"; fi

echo "Git:"
GI="$REPO/.gitignore"; (( DRY )) || touch "$GI"
for line in "DerivedData/" ".ios-sim" ".agent/ship-state" ".claude/worktrees/" "build/snaps/"; do
    if grep -qxF "$line" "$GI" 2>/dev/null; then :; else (( DRY )) || printf '%s\n' "$line" >> "$GI"; echo "  + .gitignore: $line"; fi
done
echo "  = .gitignore covers DerivedData/ .ios-sim .agent/ship-state .claude/worktrees/ build/snaps/"
GA="$REPO/.gitattributes"
if [[ -f "$GA" ]] && grep -qE '^\*\.json[[:space:]].*filter=lfs' "$GA" && ! grep -qE '^\.claude/\*\*/\*\.json' "$GA"; then
    echo "  ! .gitattributes routes *.json through LFS — .claude/settings.json would be stored as a pointer (invisible in diffs, unresolved without git-lfs). Add:"
    echo "      .claude/**/*.json !filter !diff !merge text"
fi

echo; echo "Next:"
echo "  1. Wire routes: edit the EDIT ME block in $APP_REL/script/snap (grep -rn 'ProcessInfo.processInfo.arguments' for existing DEBUG args)."
echo "  2. Paste $SKILL/templates/AGENTS.snippet.md into the app's AGENTS.md and fill the routes table."
echo "  3. Smoke: tools/provision.sh && $APP_REL/script/snap   → read the png."
echo "  4. Commit: script/, tools/, .claude/hooks, .claude/settings.json, .xcodebuildmcp, .swiftlint.yml, .gitignore"
