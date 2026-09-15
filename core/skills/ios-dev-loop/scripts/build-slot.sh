#!/usr/bin/env bash
# tools/build-slot.sh — run a command holding the machine-wide build slot.
#
# One heavy xcodebuild at a time on this Mac. Per-worktree DerivedData isolates
# *products*, but two concurrent cold SwiftPM resolves still wedge syspolicyd
# (Gatekeeper), and parallel heavy builds just starve each other. This used to
# be a mkdir-by-convention in a memory note; now it is a script every build
# path goes through (script/snap, script/device, the Stop hook, tools/ship.sh).
#
# Usage:
#   tools/build-slot.sh <command …>          # wait for the slot, run, release
#   tools/build-slot.sh --try <command …>    # exit 75 immediately if busy
#   tools/build-slot.sh --status             # who holds it
#   BUILD_SLOT=0 <anything>                  # bypass (you know what you're doing)
#   BUILD_SLOT_TIMEOUT=1200                  # max seconds to wait (default 20 min)
#   BUILD_SLOT_LOCK=<dir>                    # lock location (default ${TMPDIR:-/tmp}/ios-build-slot.lock)
#
# Lock: a directory (atomic mkdir) holding a pid + label file. A lock whose pid
# is dead is stale and gets stolen, so a killed session never blocks the fleet.
# The lock is machine-wide by design: every project on this Mac shares one Xcode.

set -u

LOCK_DIR="${BUILD_SLOT_LOCK:-${TMPDIR:-/tmp}/ios-build-slot.lock}"
LOCK_DIR="${LOCK_DIR%/}"
TIMEOUT="${BUILD_SLOT_TIMEOUT:-1200}"
TRY=0

status() {
    if [[ -d "$LOCK_DIR" ]]; then
        local pid; pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "?")
        local label; label=$(cat "$LOCK_DIR/label" 2>/dev/null || echo "?")
        if kill -0 "$pid" 2>/dev/null; then
            echo "build slot HELD by pid $pid — $label (since $(stat -f %Sm "$LOCK_DIR"))"
        else
            echo "build slot STALE (pid $pid dead) — $label"
        fi
    else
        echo "build slot free"
    fi
}

case "${1:-}" in
    --status) status; exit 0 ;;
    --try) TRY=1; shift ;;
    --help|-h|"") grep '^# ' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1 ;;
esac

if [[ "${BUILD_SLOT:-1}" == "0" ]]; then
    exec "$@"
fi

mkdir -p "$(dirname "$LOCK_DIR")"
LABEL="$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)") :: ${*:1:3}"
waited=0
while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    holder=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
    if [[ -n "$holder" ]] && ! kill -0 "$holder" 2>/dev/null; then
        echo "build-slot: stealing stale lock from dead pid $holder" >&2
        rm -rf "$LOCK_DIR"
        continue
    fi
    if [[ $TRY -eq 1 ]]; then
        echo "build-slot: busy — $(status)" >&2
        exit 75
    fi
    if (( waited == 0 )); then echo "build-slot: waiting — $(status)" >&2; fi
    if (( waited >= TIMEOUT )); then
        echo "build-slot: gave up after ${TIMEOUT}s — $(status)" >&2
        exit 124
    fi
    sleep 5; waited=$((waited + 5))
done
echo $$ > "$LOCK_DIR/pid"
echo "$LABEL" > "$LOCK_DIR/label"
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM HUP

"$@"
