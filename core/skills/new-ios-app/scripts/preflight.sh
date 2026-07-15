#!/usr/bin/env bash
# Environment + credential check for new-ios-app.
# Run on ANY machine before scaffolding or shipping — verifies this device has
# everything needed and prints exact remediation for whatever's missing.
#
#   preflight.sh             # check only (exit 0 = ready, 1 = missing hard deps)
#   preflight.sh --install   # also `brew install` / `pip install` the fixable ones
#
# Deliberately does NOT use `set -e` — we collect ALL problems in one pass.
set -uo pipefail

INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

SECRET="$HOME/aos/core/bin/cli/agent-secret"

HARD_FAILS=0
SOFT_WARNS=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; HARD_FAILS=$((HARD_FAILS+1)); }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; SOFT_WARNS=$((SOFT_WARNS+1)); }
fix()  { printf "      ↳ %s\n" "$1"; }

echo "new-ios-app preflight  ($(uname -s) $(uname -m))"
echo

# ---- 0. Homebrew (needed to install the rest) ----
echo "Toolchain:"
BREW=""
if command -v brew >/dev/null 2>&1; then
  BREW="$(command -v brew)"; ok "Homebrew"
else
  warn "Homebrew not found — needed to install xcodegen/fastlane"
  fix 'install: /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
fi

brew_install() {  # brew_install <formula>
  if [ "$INSTALL" = "1" ] && [ -n "$BREW" ]; then
    echo "      installing $1 …"; "$BREW" install "$1" >/dev/null 2>&1 && ok "$1 installed" || bad "$1 install failed"
  fi
}

# ---- 1. Xcode + command line tools ----
if xcode-select -p >/dev/null 2>&1; then
  XPATH="$(xcode-select -p)"
  if printf '%s' "$XPATH" | grep -q "Xcode.app"; then
    if XV="$(xcodebuild -version 2>/dev/null | head -1)"; then
      ok "Xcode ($XV)"
    else
      bad "Xcode present but xcodebuild errored — license not accepted?"
      fix "run: sudo xcodebuild -license accept"
    fi
  else
    bad "Only Command Line Tools selected — full Xcode required to build apps"
    fix "install Xcode from the App Store, then: sudo xcode-select -s /Applications/Xcode.app"
  fi
else
  bad "No Xcode / command line tools"
  fix "install Xcode from the App Store (or: xcode-select --install)"
fi

# ---- 2. iOS SDK + simulator runtime ----
if xcodebuild -showsdks 2>/dev/null | grep -qi "iphoneos"; then
  SDK="$(xcodebuild -showsdks 2>/dev/null | grep -oi 'iphoneos[0-9.]*' | tail -1)"
  ok "iOS SDK ($SDK)"
else
  bad "No iOS SDK found"
  fix "open Xcode once and let it install platform support"
fi

if xcrun simctl list runtimes 2>/dev/null | grep -qi "iOS"; then
  RT="$(xcrun simctl list runtimes 2>/dev/null | grep -i 'iOS' | tail -1 | sed 's/ (.*//')"
  ok "iOS Simulator runtime ($RT)"
else
  warn "No iOS Simulator runtime installed (needed to run/test in a simulator)"
  fix "Xcode ▸ Settings ▸ Components — install an iOS runtime (or: xcodebuild -downloadPlatform iOS)"
fi

# ---- 3. CLI packages ----
if command -v xcodegen >/dev/null 2>&1; then ok "xcodegen"; else bad "xcodegen missing"; fix "brew install xcodegen"; brew_install xcodegen; fi
if command -v fastlane  >/dev/null 2>&1; then ok "fastlane";  else bad "fastlane missing";  fix "brew install fastlane";  brew_install fastlane;  fi

# ---- 4. Python deps (optional — only for the live auth test) ----
echo
echo "Optional:"
if python3 -c "import jwt, cryptography" >/dev/null 2>&1; then
  ok "PyJWT + cryptography (enables live ASC auth test)"
else
  warn "PyJWT/cryptography missing — live auth test will be skipped"
  fix "pip3 install pyjwt cryptography"
  if [ "$INSTALL" = "1" ]; then
    echo "      installing pyjwt cryptography …"
    pip3 install --quiet pyjwt cryptography >/dev/null 2>&1 && ok "python deps installed" || warn "pip install failed"
  fi
fi

# ---- 5. Apple credentials (account-level, in Keychain) ----
echo
echo "Apple credentials:"
CRED_OK=1
get() { "$SECRET" get "$1" 2>/dev/null; }
for k in APPLE_TEAM_ID ASC_KEY_ID ASC_ISSUER_ID; do
  if [ -n "$(get "$k")" ]; then ok "$k"; else bad "$k not in Keychain"; fix "$SECRET set $k <value>"; CRED_OK=0; fi
done
KID="$(get ASC_KEY_ID)"
P8="$HOME/.appstoreconnect/private_keys/AuthKey_${KID}.p8"
if [ -n "$KID" ] && [ -f "$P8" ]; then
  ok "App Store Connect .p8 present"
elif [ -n "$KID" ]; then
  bad "App Store Connect .p8 missing at ~/.appstoreconnect/private_keys/AuthKey_${KID}.p8"
  fix "Apple only lets you download a .p8 once. Restore from backup or create a new API key (operator-approved)."
  CRED_OK=0
fi

# ---- 6. Live ASC auth (only if creds + python deps available) ----
if [ "$CRED_OK" = "1" ] && python3 -c "import jwt" >/dev/null 2>&1; then
  if python3 - "$KID" "$(get ASC_ISSUER_ID)" "$P8" <<'PY' 2>/dev/null
import sys, time, urllib.request, urllib.error, jwt
kid, iss, p8 = sys.argv[1], sys.argv[2], sys.argv[3]
now = int(time.time())
tok = jwt.encode({"iss": iss, "iat": now, "exp": now+600, "aud": "appstoreconnect-v1"},
                 open(p8).read(), algorithm="ES256", headers={"kid": kid, "typ": "JWT"})
req = urllib.request.Request("https://api.appstoreconnect.apple.com/v1/apps?limit=1",
                             headers={"Authorization": "Bearer " + tok})
urllib.request.urlopen(req, timeout=20)
PY
  then ok "Live App Store Connect API auth verified"
  else bad "Live ASC auth failed (key revoked, wrong issuer, or no network)"; fi
fi

# ---- verdict ----
echo
if [ "$HARD_FAILS" -eq 0 ]; then
  echo "READY ✅  ($SOFT_WARNS warning(s)) — scaffold + ship will work."
  exit 0
else
  echo "NOT READY ❌  $HARD_FAILS blocker(s), $SOFT_WARNS warning(s)."
  [ "$INSTALL" = "0" ] && echo "Re-run with --install to auto-fix Homebrew/pip items."
  exit 1
fi
