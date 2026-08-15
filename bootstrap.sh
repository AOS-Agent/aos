#!/usr/bin/env bash
# AOS bootstrap — the curl one-liner target
# Usage: bash -c "$(curl -fsSL .../bootstrap.sh)"
#
# HARD RULE: this script must not depend on git.
#
# On a fresh Mac /usr/bin/git is a stub that pops the Xcode Command Line Tools
# installer and exits non-zero. Under `set -e` that killed the bootstrap, so the
# operator had to sit through the CLT install and then re-paste the one-liner to
# start over. The irony was that install.sh already ships a three-tier CLT
# installer (prereq_xcode_clt) — it just never got to run, because fetching it
# required the very tool it exists to install.
#
# curl and tar ship with macOS and need no toolchain. So: download a tarball to a
# scratch dir, hand off to install.sh, and let it install CLT properly and clone
# the real ~/aos repo with git once git exists. One paste, start to finish.

set -euo pipefail

REPO_TARBALL="https://codeload.github.com/hishamalhadi/aos/tar.gz/refs/heads/main"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/aos-bootstrap.XXXXXX")"
# Cleaned on failure only. On success we `exec` into install.sh, which replaces
# this process — the trap never fires and the staged files stay readable for the
# life of the installer.
trap 'rm -rf "$STAGE"' EXIT

printf '\n  \xef\xb7\xbd\n\n'
printf '  Bootstrapping AOS...\n\n'

if ! curl -fsSL "$REPO_TARBALL" | tar -xz -C "$STAGE" --strip-components=1; then
    printf '  Could not download AOS.\n' >&2
    printf '  Check your internet connection and run the command again.\n\n' >&2
    exit 1
fi

if [[ ! -f "$STAGE/install.sh" ]]; then
    printf '  Download was incomplete — install.sh is missing.\n' >&2
    printf '  Run the command again.\n\n' >&2
    exit 1
fi

trap - EXIT
exec bash "$STAGE/install.sh" "$@"
