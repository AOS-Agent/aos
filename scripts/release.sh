#!/usr/bin/env bash
# Release AOS: signed, notarized, stapled DMG — and published, in one run.
#
# Usage:
#   scripts/release.sh              # release the version already in tauri.conf.json
#   scripts/release.sh 0.2.0        # bump to 0.2.0, then release
#   scripts/release.sh 0.2.0 "Arms detail pages and a faster health probe"
#
# Requires (all already on this machine):
#   - Keychain identity "Developer ID Application: Hisham Al Hadi (KYT5TSBZ8B)"
#     (private key backup: ~/.aos/certs/aos-devid-private.key, 0600)
#   - App Store Connect API key ~/private_keys/AuthKey_PLMH58HXMH.p8
#   - Keychain secret ASC_ISSUER_ID (agent-secret)
#   - Tauri updater signing key ~/.tauri/aos-updater.key
#
# WHY THE PUBLISH STEP LIVES HERE:
# The updater's trust model is a signature over one exact tarball. Copying the
# artifact by hand and hand-writing latest.json are two chances to publish a
# manifest whose signature does not match its file — and when that happens every
# installed app silently refuses the update, with no error the operator ever
# sees. Generating both from the same build output is the only way to make that
# mismatch impossible rather than merely unlikely.
set -euo pipefail
cd "$(dirname "$0")/.."

NEW_VERSION="${1:-}"
NOTES="${2:-}"

IDENTITY="Developer ID Application: Hisham Al Hadi (KYT5TSBZ8B)"
KEY_ID="PLMH58HXMH"
KEY_PATH="$HOME/private_keys/AuthKey_${KEY_ID}.p8"
SIGNING_KEY="$HOME/.tauri/aos-updater.key"
CONF="src-tauri/tauri.conf.json"

# Where aos.hish.am is served from (declared in ~/project/hish.am/sites.yaml,
# port 8096, access: public). Never hand-edit Caddy — this only writes files.
SITE_ROOT="/Volumes/AOS-X/hish.am/sites/aos"
UPDATER_DIR="$SITE_ROOT/updater"
ENDPOINT="https://aos.hish.am/updater/latest.json"

fail() { echo "✗ $*" >&2; exit 1; }

# ── preflight: everything that would waste a 5-minute notarization ──────────
echo "── preflight ──"
[ -f "$SIGNING_KEY" ] || fail "updater signing key missing: $SIGNING_KEY
    Without it no update can be signed, and installed apps will never upgrade."
[ -f "$KEY_PATH" ] || fail "App Store Connect key missing: $KEY_PATH"
[ -d "$SITE_ROOT" ] || fail "publish target not reachable: $SITE_ROOT
    /Volumes/AOS-X is not mounted. Mount it before releasing — otherwise the
    build succeeds and there is nowhere to publish it."
security find-identity -v -p codesigning 2>/dev/null | grep -q "$IDENTITY" \
    || fail "Developer ID identity not in the Keychain: $IDENTITY"
ISSUER="$(bash "$HOME/aos/core/bin/cli/agent-secret" get ASC_ISSUER_ID)" \
    || fail "ASC_ISSUER_ID not in the Keychain"
echo "  ✓ signing key, ASC key, identity, issuer, publish target"

# ── version ────────────────────────────────────────────────────────────────
if [ -n "$NEW_VERSION" ]; then
    [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version must be X.Y.Z, got: $NEW_VERSION"
    "$HOME/.aos/python/bin/python3" - "$CONF" "$NEW_VERSION" <<'PYEOF'
import json, sys
path, version = sys.argv[1], sys.argv[2]
with open(path) as f:
    conf = json.load(f)
conf["version"] = version
with open(path, "w") as f:
    json.dump(conf, f, indent=2)
    f.write("\n")
PYEOF
    echo "  ✓ version bumped to $NEW_VERSION"
fi

VERSION="$("$HOME/.aos/python/bin/python3" -c "import json;print(json.load(open('$CONF'))['version'])")"
TARBALL_NAME="AOS_${VERSION}_aarch64.app.tar.gz"

# Publishing over an existing version leaves installs that already took it
# stranded on a build that no longer matches its signature. Refuse.
[ -f "$UPDATER_DIR/$TARBALL_NAME" ] && fail "version $VERSION is already published.
    Bump the version: scripts/release.sh <next-version>"

echo "── releasing $VERSION ──"

# ── build + sign + notarize ────────────────────────────────────────────────
APPLE_SIGNING_IDENTITY="$IDENTITY" \
APPLE_API_ISSUER="$ISSUER" \
APPLE_API_KEY="$KEY_ID" \
APPLE_API_KEY_PATH="$KEY_PATH" \
TAURI_SIGNING_PRIVATE_KEY="$(cat "$SIGNING_KEY")" \
TAURI_SIGNING_PRIVATE_KEY_PASSWORD="" \
bunx tauri build

DMG=$(ls -t src-tauri/target/release/bundle/dmg/AOS_*.dmg | head -1)
TARBALL="src-tauri/target/release/bundle/macos/AOS.app.tar.gz"
SIGFILE="$TARBALL.sig"

[ -f "$TARBALL" ] || fail "no update bundle produced — is createUpdaterArtifacts enabled?"
[ -f "$SIGFILE" ] || fail "no signature produced — TAURI_SIGNING_PRIVATE_KEY did not take effect"

echo "── notarize + staple DMG: $DMG ──"
xcrun notarytool submit "$DMG" --key "$KEY_PATH" --key-id "$KEY_ID" --issuer "$ISSUER" --wait
xcrun stapler staple "$DMG"
spctl --assess --type open --context context:primary-signature -v "$DMG"

# ── publish ────────────────────────────────────────────────────────────────
# Artifact first, manifest second: a manifest pointing at a file that is not
# there yet is a broken update for anyone who launches in between.
echo "── publish → $UPDATER_DIR ──"
mkdir -p "$UPDATER_DIR"
cp "$TARBALL" "$UPDATER_DIR/$TARBALL_NAME"
cp "$DMG" "$SITE_ROOT/AOS_${VERSION}.dmg"

"$HOME/.aos/python/bin/python3" - "$UPDATER_DIR" "$VERSION" "$TARBALL_NAME" "$SIGFILE" "$NOTES" <<'PYEOF'
import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

updater_dir, version, tarball, sigfile, notes = sys.argv[1:6]

if not notes:
    # Fall back to the subject lines since the last release commit.
    try:
        notes = subprocess.run(
            ["git", "log", "-5", "--format=%s"], capture_output=True, text=True, timeout=10
        ).stdout.strip().replace("\n", " · ")[:300] or f"AOS {version}"
    except Exception:
        notes = f"AOS {version}"

manifest = {
    "version": version,
    "notes": notes,
    "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "platforms": {
        "darwin-aarch64": {
            # The signature is read from the .sig this build produced, never
            # copied by hand — that pairing is the whole trust model.
            "signature": Path(sigfile).read_text().strip(),
            "url": f"https://aos.hish.am/updater/{tarball}",
        }
    },
}
Path(updater_dir, "latest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"  ✓ latest.json written for {version}")
PYEOF

# ── verify what the world actually sees ────────────────────────────────────
echo "── verify ──"
sleep 2
SERVED=$(/usr/bin/curl -fsS -m 15 "$ENDPOINT" | "$HOME/.aos/python/bin/python3" -c "import json,sys;print(json.load(sys.stdin)['version'])") \
    || fail "updater endpoint did not serve valid JSON: $ENDPOINT"
[ "$SERVED" = "$VERSION" ] || fail "endpoint serves $SERVED but we published $VERSION"

CODE=$(/usr/bin/curl -fsS -o /dev/null -w "%{http_code}" -m 30 "https://aos.hish.am/updater/$TARBALL_NAME") \
    || fail "published artifact is not downloadable"
[ "$CODE" = "200" ] || fail "artifact returned HTTP $CODE"

LOCAL_BYTES=$(wc -c < "$UPDATER_DIR/$TARBALL_NAME" | tr -d ' ')
SERVED_BYTES=$(/usr/bin/curl -fsSI -m 15 "https://aos.hish.am/updater/$TARBALL_NAME" | awk 'tolower($1)=="content-length:"{print $2+0}')
[ "$LOCAL_BYTES" = "$SERVED_BYTES" ] || fail "served artifact is $SERVED_BYTES bytes, published file is $LOCAL_BYTES"

echo
echo "✓ Released $VERSION"
echo "    manifest : $ENDPOINT"
echo "    update   : https://aos.hish.am/updater/$TARBALL_NAME  ($LOCAL_BYTES bytes)"
echo "    download : https://aos.hish.am/AOS_${VERSION}.dmg"
echo
echo "  Installed apps pick this up on their next launch."
