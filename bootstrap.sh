#!/usr/bin/env bash
# AOS bootstrap — the curl one-liner target
# Usage: bash -c "$(curl -fsSL .../bootstrap.sh)"
#
# This clones the repo and runs the real installer with a proper terminal.
# Keeps the actual install logic in install.sh where it belongs.

set -euo pipefail

AOS_DIR="$HOME/aos"
REPO="https://github.com/hishamalhadi/aos.git"

echo ""
echo "  Bootstrapping AOS..."
echo ""

if [[ ! -d "$AOS_DIR/.git" ]]; then
    git clone "$REPO" "$AOS_DIR"
else
    git -C "$AOS_DIR" fetch origin main 2>/dev/null
    git -C "$AOS_DIR" reset --hard origin/main 2>/dev/null
    echo "  Updated to latest."
fi

echo ""
exec bash "$AOS_DIR/install.sh" "$@"
