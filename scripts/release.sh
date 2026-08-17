#!/usr/bin/env bash
# Release AOS: signed, notarized, stapled DMG in one run.
#
# Requires (all already on this machine):
#   - Keychain identity "Developer ID Application: Hisham Al Hadi (KYT5TSBZ8B)"
#     (private key backup: ~/.aos/certs/aos-devid-private.key, 0600)
#   - App Store Connect API key ~/private_keys/AuthKey_PLMH58HXMH.p8
#   - Keychain secret ASC_ISSUER_ID (agent-secret)
#
# Usage: scripts/release.sh
set -euo pipefail
cd "$(dirname "$0")/.."

IDENTITY="Developer ID Application: Hisham Al Hadi (KYT5TSBZ8B)"
KEY_ID="PLMH58HXMH"
KEY_PATH="$HOME/private_keys/AuthKey_${KEY_ID}.p8"
ISSUER="$(bash "$HOME/aos/core/bin/cli/agent-secret" get ASC_ISSUER_ID)"

echo "── build + sign + notarize app ──"
APPLE_SIGNING_IDENTITY="$IDENTITY" \
APPLE_API_ISSUER="$ISSUER" \
APPLE_API_KEY="$KEY_ID" \
APPLE_API_KEY_PATH="$KEY_PATH" \
TAURI_SIGNING_PRIVATE_KEY_PATH="$HOME/.tauri/aos-updater.key" \
TAURI_SIGNING_PRIVATE_KEY_PASSWORD="" \
bunx tauri build

DMG=$(ls -t src-tauri/target/release/bundle/dmg/AOS_*.dmg | head -1)
echo "── notarize + staple DMG container: $DMG ──"
xcrun notarytool submit "$DMG" --key "$KEY_PATH" --key-id "$KEY_ID" --issuer "$ISSUER" --wait
xcrun stapler staple "$DMG"

echo "── verify ──"
spctl --assess --type open --context context:primary-signature -v "$DMG"
echo "Release ready: $DMG"
