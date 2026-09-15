#!/usr/bin/env bash
# tools/worktree.sh — create a git worktree (and branch) for a focused session.
#
# Each session that intends to commit works in its own worktree, not the
# canonical checkout: independent HEADs, so sibling sessions cannot rug-pull
# each other's branch state. Worktrees live INSIDE the project (fleet rule):
#
#   <canonical-checkout>/.claude/worktrees/<branch-with-slashes-flattened>/
#
# Usage:
#   tools/worktree.sh <area|task-id> <slug> [--from <base-branch>]
#   tools/worktree.sh --remove <slug-or-path>   # delete its simulator + worktree
#   tools/worktree.sh --gc                      # delete leased simulators whose worktree is gone
#
# Examples:
#   tools/worktree.sh fix orient-flicker    → branch fix/orient-flicker, dir fix-orient-flicker
#   tools/worktree.sh release v0.2.2        → branch release/v0.2.2
#   tools/worktree.sh 14 persistent-zoom    → branch <TASK_PREFIX>-14-persistent-zoom (TASK_PREFIX default "task")
#
# Areas: feat fix refactor docs chore release perf test. Base branch defaults
# to main (or master when main does not exist). After creating, runs
# tools/provision.sh from the same directory as this script (submodules, data,
# xcodegen, simulator lease into .ios-sim). --remove deletes the leased
# simulator, then the worktree; git refuses to remove a worktree holding an
# initialized submodule even when clean, so it falls back to rm + prune.
# With TASK_PREFIX set and a numeric task id, best-effort `work start <prefix>#<id>`.

set -e

usage() { grep '^# ' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1; }

git rev-parse --git-dir >/dev/null 2>&1 || { echo "Error: not inside a git repository" >&2; exit 1; }
REPO_MAIN="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd -P)"
WORKTREE_BASE="$REPO_MAIN/.claude/worktrees"
PROJECT="$(basename "$REPO_MAIN")"

sim_delete() {  # <udid> — tolerant: an already-deleted simulator is not an error
    xcrun simctl shutdown "$1" >/dev/null 2>&1 || true
    if xcrun simctl delete "$1" >/dev/null 2>&1; then echo "  ✓ simulator $1 deleted"; else echo "  (simulator $1 already gone)"; fi
    return 0
}

case "${1:-}" in
    --remove)
        TARGET="${2:?usage: $0 --remove <slug-or-path>}"
        [[ -d "$TARGET" ]] || TARGET="$WORKTREE_BASE/$TARGET"
        [[ -d "$TARGET" ]] || { echo "Error: no worktree at $TARGET" >&2; exit 1; }
        TARGET="$(cd "$TARGET" && pwd -P)"
        [[ "$TARGET" != "$REPO_MAIN" ]] || { echo "Error: refusing to remove the canonical checkout" >&2; exit 1; }
        # Only a REGISTERED worktree may be removed — the rm fallback below must
        # never touch a plain directory that merely sits under .claude/worktrees.
        git worktree list --porcelain | grep -qx "worktree $TARGET" \
            || { echo "Error: $TARGET is not a registered git worktree (git worktree list) — nothing removed" >&2; exit 1; }
        if [[ -n "$(git -C "$TARGET" status --porcelain 2>/dev/null)" ]]; then
            echo "Error: $TARGET has uncommitted changes — commit or stash first." >&2; exit 1
        fi
        [[ -f "$TARGET/.ios-sim" ]] && sim_delete "$(cat "$TARGET/.ios-sim")"
        if git worktree remove "$TARGET" 2>/dev/null || git worktree remove --force "$TARGET" 2>/dev/null; then
            echo "  ✓ worktree removed: $TARGET"
        else
            # tree verified clean above; rm + prune is equivalent for a submodule checkout
            rm -rf "$TARGET" && git worktree prune && echo "  ✓ worktree removed (rm + prune; submodule checkout): $TARGET"
        fi
        exit 0 ;;
    --gc)
        # Simulators named <project>-<slug> whose worktree directory no longer exists.
        xcrun simctl list devices | grep -oE "^ *${PROJECT}-[A-Za-z0-9._-]+ \([0-9A-F-]{36}\)" | sed 's/^ *//' | while read -r line; do
            name="${line%% *}"; udid="${line#*(}"; udid="${udid%)}"
            [[ -d "$WORKTREE_BASE/${name#"$PROJECT"-}" ]] && continue
            echo "→ orphan simulator $name"; sim_delete "$udid"
        done
        exit 0 ;;
    --help|-h|"") usage ;;
esac

[[ $# -ge 2 ]] || usage
FIRST="$1"; SLUG="$2"; shift 2

BASE_BRANCH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) BASE_BRANCH="${2:?--from needs a branch}"; shift 2 ;;
        --help|-h) usage ;;
        *) echo "Unknown arg: $1" >&2; usage ;;
    esac
done
if [[ -z "$BASE_BRANCH" ]]; then
    if git show-ref --verify --quiet refs/heads/main; then BASE_BRANCH=main
    elif git show-ref --verify --quiet refs/heads/master; then BASE_BRANCH=master
    else echo "Error: no main/master branch — pass --from <branch>" >&2; exit 1; fi
fi

SLUG_CLEAN=$(echo "$SLUG" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9.-' | sed 's/--*/-/g; s/^-//; s/-$//')
[[ -n "$SLUG_CLEAN" ]] || { echo "Error: slug is empty after sanitization" >&2; exit 1; }

LINK_TASK=false
if [[ "$FIRST" =~ ^[0-9]+$ ]]; then
    TASK_ID="$FIRST"; PREFIX="${TASK_PREFIX:-task}"
    BRANCH="${PREFIX}-${TASK_ID}-${SLUG_CLEAN}"
    [[ -n "${TASK_PREFIX:-}" ]] && LINK_TASK=true
elif [[ "$FIRST" =~ ^(feat|fix|refactor|docs|chore|release|perf|test)$ ]]; then
    BRANCH="${FIRST}/${SLUG_CLEAN}"
else
    echo "Error: first arg must be a task ID (number) or area (feat/fix/refactor/docs/chore/release/perf/test)" >&2
    exit 1
fi

WORKTREE_SLUG=$(echo "$BRANCH" | tr '/' '-')
WORKTREE_PATH="${WORKTREE_BASE}/${WORKTREE_SLUG}"

if [[ -e "$WORKTREE_PATH" ]]; then
    echo "Error: worktree path '$WORKTREE_PATH' already exists." >&2
    echo "  → cd $WORKTREE_PATH                         (to use it)" >&2
    echo "  → tools/worktree.sh --remove $WORKTREE_SLUG   (to remove if stale)" >&2
    exit 1
fi

mkdir -p "$WORKTREE_BASE"
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    echo "Note: branch '$BRANCH' already exists. Attaching worktree to it." >&2
    git worktree add "$WORKTREE_PATH" "$BRANCH"
else
    git worktree add "$WORKTREE_PATH" -b "$BRANCH" "$BASE_BRANCH"
fi

echo ""; echo "→ Provisioning worktree..."
PROVISION="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/provision.sh"   # the copy next to this script
if [[ -x "$PROVISION" ]]; then
    ( cd "$WORKTREE_PATH" && "$PROVISION" ) || echo "  ! provisioning failed — run tools/provision.sh inside the worktree" >&2
else
    echo "  (no provision.sh next to this script — skipped)"
fi

if [[ "$LINK_TASK" == "true" ]] && command -v python3 >/dev/null 2>&1 && [[ -f "$HOME/aos/core/engine/work/cli.py" ]]; then
    echo ""; echo "→ Linking to work task ${PREFIX}#${TASK_ID}..."
    python3 "$HOME/aos/core/engine/work/cli.py" start "${PREFIX}#${TASK_ID}" 2>&1 || echo "  (couldn't start ${PREFIX}#${TASK_ID} — may not exist yet)"
fi

echo ""
echo "✓ Worktree created:"
echo "    Path:   $WORKTREE_PATH"
echo "    Branch: $BRANCH"
echo ""
echo "Next:"
echo "    cd $WORKTREE_PATH"
echo ""
echo "When done (after merge):"
echo "    tools/worktree.sh --remove $WORKTREE_SLUG    # deletes the leased simulator too"
