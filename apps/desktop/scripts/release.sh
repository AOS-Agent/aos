#!/usr/bin/env bash
# Release AOS: signed, notarized, stapled DMG — and published, in one run.
#
# Usage:
#   scripts/release.sh              # release as the repo VERSION (the unified number)
#   scripts/release.sh 0.2.0        # explicit override: bump to 0.2.0, then release
#   scripts/release.sh 0.2.0 "Arms detail pages and a faster health probe"
#
# ONE VERSION NUMBER: with no argument, the app releases as the AOS VERSION at
# the repo root. `release-app` (core/bin/cli) drives this from /ship so the git
# release and the updater manifest always carry the same number. Passing an
# explicit version stays possible for repair work, but the default is unity.
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

# No explicit version → the unified number: the repo-root VERSION, sans "v".
if [ -z "$NEW_VERSION" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [ -n "$REPO_ROOT" ] && [ -f "$REPO_ROOT/VERSION" ]; then
        NEW_VERSION="$(tr -d 'v[:space:]' < "$REPO_ROOT/VERSION")"
        echo "── unified version: $NEW_VERSION (from $REPO_ROOT/VERSION) ──"
    fi
fi

IDENTITY="Developer ID Application: Hisham Al Hadi (KYT5TSBZ8B)"
KEY_ID="PLMH58HXMH"
KEY_PATH="$HOME/private_keys/AuthKey_${KEY_ID}.p8"
SIGNING_KEY="$HOME/.tauri/aos-updater.key"
CONF="src-tauri/tauri.conf.json"

# Where qren.ai is served from. The publish root is a machine-local detail of
# the operator's serving registry (sites.yaml), so it lives in instance config,
# never in this repo: ~/.aos/config/qren-site-root (one line, the absolute
# path), or the QREN_SITE_ROOT env var. Never hand-edit the web server config —
# this script only writes files into the root.
SITE_ROOT="${QREN_SITE_ROOT:-}"
if [ -z "$SITE_ROOT" ] && [ -f "$HOME/.aos/config/qren-site-root" ]; then
    SITE_ROOT="$(tr -d '[:space:]' < "$HOME/.aos/config/qren-site-root")"
fi
UPDATER_DIR="$SITE_ROOT/updater"
ENDPOINT="https://qren.ai/updater/latest.json"

fail() { echo "✗ $*" >&2; exit 1; }

# ── preflight: everything that would waste a 5-minute notarization ──────────
echo "── preflight ──"
[ -f "$SIGNING_KEY" ] || fail "updater signing key missing: $SIGNING_KEY
    Without it no update can be signed, and installed apps will never upgrade."
[ -f "$KEY_PATH" ] || fail "App Store Connect key missing: $KEY_PATH"
[ -n "$SITE_ROOT" ] || fail "publish root not configured.
    Put the site's publish root in ~/.aos/config/qren-site-root (one line,
    absolute path) or set QREN_SITE_ROOT."
[ -d "$SITE_ROOT" ] || fail "publish target not reachable: $SITE_ROOT
    The volume it lives on is not mounted. Mount it before releasing —
    otherwise the build succeeds and there is nowhere to publish it."
security find-identity -v -p codesigning 2>/dev/null | grep -q "$IDENTITY" \
    || fail "Developer ID identity not in the Keychain: $IDENTITY"
ISSUER="$(bash "$HOME/aos/core/bin/cli/agent-secret" get ASC_ISSUER_ID)" \
    || fail "ASC_ISSUER_ID not in the Keychain"
# The bump must be able to land. release.sh publishes an artifact to the world;
# if the version bump that produced it cannot be committed, main ends up
# claiming one version while every installed app runs another. That happened
# with 0.3.2: cut from a detached worktree, artifact pushed, bump stranded.
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "HEAD" ] && fail "detached HEAD — the version bump would have nowhere to land.
    Check out a branch before releasing."
if ! git diff --quiet -- src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock; then
    fail "uncommitted version files. Commit or discard them before releasing, so the
    bump this run makes is the only change in the release commit."
fi
echo "  ✓ signing key, ASC key, identity, issuer, publish target, branch $BRANCH"

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
    # Cargo.toml too. tauri.conf.json wins at build time, so a stale Cargo
    # version is invisible until something reads it and disagrees — exactly the
    # drift class that cost us a night. Keep the two in lockstep.
    #
    # Not sed: BSD sed rejects the `0,/re/` address GNU accepts, and it fails
    # without erroring, which is how the first attempt at this silently did
    # nothing while reporting success.
    "$HOME/.aos/python/bin/python3" - "$NEW_VERSION" <<'PYEOF'
import re, sys
from pathlib import Path
version = sys.argv[1]
p = Path("src-tauri/Cargo.toml")
text = p.read_text()
new, n = re.subn(r'^version = "[^"]*"', f'version = "{version}"', text, count=1, flags=re.M)
if n != 1:
    raise SystemExit("Cargo.toml: could not find a version line to bump")
p.write_text(new)
print(f"  ✓ Cargo.toml bumped to {version}")
PYEOF
    echo "  ✓ version bumped to $NEW_VERSION (tauri.conf.json + Cargo.toml)"
fi

VERSION="$("$HOME/.aos/python/bin/python3" -c "import json;print(json.load(open('$CONF'))['version'])")"
TARBALL_NAME="Qren_${VERSION}_aarch64.app.tar.gz"

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

DMG=$(ls -t src-tauri/target/release/bundle/dmg/Qren_*.dmg | head -1)
TARBALL="src-tauri/target/release/bundle/macos/Qren.app.tar.gz"
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
cp "$DMG" "$SITE_ROOT/Qren_${VERSION}.dmg"

# Keep the download page honest: point its CTA + version line at the DMG that
# was just published. Before this, the page drifted (still offered 0.1.0 while
# 0.7.6 was live — found 2026-08-18). Anchored so an unmatched page fails the
# release loudly rather than silently staying stale.
INDEX="$SITE_ROOT/index.html"
if [ -f "$INDEX" ]; then
    grep -Eq '(AOS|Qren)_[0-9]+\.[0-9]+\.[0-9]+\.dmg' "$INDEX" \
        || fail "download page has no (AOS|Qren)_<version>.dmg link to update: $INDEX"
    sed -i '' -E \
        -e "s|(AOS|Qren)_[0-9]+\.[0-9]+\.[0-9]+\.dmg|Qren_${VERSION}.dmg|g" \
        -e "s|v[0-9]+\.[0-9]+\.[0-9]+ · Apple Silicon|v${VERSION} · Apple Silicon|" \
        "$INDEX"
    grep -q "Qren_${VERSION}.dmg" "$INDEX" || fail "download page rewrite did not take"
    echo "  download page → v${VERSION}"
else
    fail "download page missing: $INDEX"
fi

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

# The commit this build came from. `release-app` reads it back to decide
# whether apps/desktop changed since the last published release — the field
# that lets one pipeline own both artifacts.
try:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
    ).stdout.strip()
except Exception:
    commit = ""

manifest = {
    "version": version,
    "notes": notes,
    "commit": commit,
    "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "platforms": {
        "darwin-aarch64": {
            # The signature is read from the .sig this build produced, never
            # copied by hand — that pairing is the whole trust model.
            "signature": Path(sigfile).read_text().strip(),
            "url": f"https://qren.ai/updater/{tarball}",
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

CODE=$(/usr/bin/curl -fsS -o /dev/null -w "%{http_code}" -m 30 "https://qren.ai/updater/$TARBALL_NAME") \
    || fail "published artifact is not downloadable"
[ "$CODE" = "200" ] || fail "artifact returned HTTP $CODE"

LOCAL_BYTES=$(wc -c < "$UPDATER_DIR/$TARBALL_NAME" | tr -d ' ')
SERVED_BYTES=$(/usr/bin/curl -fsSI -m 15 "https://qren.ai/updater/$TARBALL_NAME" | awk 'tolower($1)=="content-length:"{print $2+0}')
[ "$LOCAL_BYTES" = "$SERVED_BYTES" ] || fail "served artifact is $SERVED_BYTES bytes, published file is $LOCAL_BYTES"

# Commit the bump NOW, while the artifact that matches it is verified live.
# Not pushed: pushing is the operator's call. But the commit exists, so the
# bump can no longer be lost between publishing and remembering.
if git diff --quiet -- src-tauri/tauri.conf.json src-tauri/Cargo.toml; then
    echo "── version files already committed ──"
else
    git add src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock 2>/dev/null || true
    git commit -q -m "app $VERSION" -m "Published and verified live at $ENDPOINT." \
        && echo "── committed the $VERSION bump on $BRANCH ──"
fi

echo
echo "✓ Released $VERSION"
echo "    manifest : $ENDPOINT"
echo "    update   : https://qren.ai/updater/$TARBALL_NAME  ($LOCAL_BYTES bytes)"
echo "    download : https://qren.ai/Qren_${VERSION}.dmg"
echo
echo "  Installed apps pick this up on their next launch."
echo "  The bump is committed on $BRANCH — push it so main matches what shipped."
