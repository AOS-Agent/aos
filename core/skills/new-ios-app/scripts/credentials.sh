#!/usr/bin/env bash
# Loads the operator's account-level Apple credentials from Keychain into the
# environment. Sourced by ship.sh and preflight.sh. Fails loud if anything's
# missing — never invents or creates keys.
set -euo pipefail

SECRET="$HOME/aos/core/bin/cli/agent-secret"

require() {
  local name val
  name="$1"
  val="$("$SECRET" get "$name" 2>/dev/null || true)"
  if [ -z "$val" ]; then
    echo "ERROR: Keychain secret '$name' is missing." >&2
    echo "  Set it with: $SECRET set $name <value>" >&2
    exit 1
  fi
  export "$name"="$val"
}

require APPLE_TEAM_ID
require ASC_KEY_ID
require ASC_ISSUER_ID

export ASC_KEY_PATH="$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8"
if [ ! -f "$ASC_KEY_PATH" ]; then
  echo "ERROR: App Store Connect private key not found at:" >&2
  echo "  $ASC_KEY_PATH" >&2
  echo "  (Apple only lets you download a .p8 once, at creation.)" >&2
  exit 1
fi

# Optional Apple ID (API key usually suffices)
APPLE_ID_VAL="$("$SECRET" get APPLE_ID 2>/dev/null || true)"
[ -n "$APPLE_ID_VAL" ] && export APPLE_ID="$APPLE_ID_VAL"

# fastlane reads these
export FASTLANE_DISABLE_COLORS=1
export SPACESHIP_SKIP_2FA_UPGRADE=1
