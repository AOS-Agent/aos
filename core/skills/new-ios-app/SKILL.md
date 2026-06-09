---
name: new-ios-app
description: >
  Scaffold a brand-new, best-practice SwiftUI iOS app and ship it to
  TestFlight from any directory. Trigger on "/new-ios-app", "new iOS app",
  "start an iOS app", "create an iOS app", "scaffold an app", "spin up an
  app", "make a new app and get it on TestFlight", or any request to
  bootstrap a fresh iOS project from zero. Uses the operator's already-
  configured Apple credentials (Team ID + App Store Connect API key) to
  register the bundle ID, create the App Store Connect record, build, and
  upload a first TestFlight build automatically.
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, AskUserQuestion
---

# new-ios-app — zero to TestFlight

Turn "start an app called X" into a modern SwiftUI project that builds
locally and has a first build processing in TestFlight — one flow, from any
directory. The generated app embodies 2026 / iOS 26 best practices (see
"What gets generated" below).

Credentials are account-level and already set up; this skill is app-agnostic.
Run it as many times as you want — each run is a new app.

## Step 0 — Preflight / environment check (always, fail loud)

```bash
~/.claude/skills/new-ios-app/scripts/preflight.sh            # check
~/.claude/skills/new-ios-app/scripts/preflight.sh --install  # check + auto-fix brew/pip items
```

Run this FIRST on any machine — especially the first time on a new device.
It verifies the full environment and prints exact remediation for anything
missing:

- **Toolchain**: Homebrew, full Xcode (+ license accepted), iOS SDK, an iOS
  Simulator runtime, `xcodegen`, `fastlane`.
- **Optional**: `PyJWT`/`cryptography` (enables the live auth test).
- **Credentials**: Keychain secrets (`APPLE_TEAM_ID`, `ASC_KEY_ID`,
  `ASC_ISSUER_ID`), the `.p8` at
  `~/.appstoreconnect/private_keys/AuthKey_<ASC_KEY_ID>.p8`, and a live ASC
  API auth call.

`--install` auto-installs the Homebrew/pip items (xcodegen, fastlane, python
deps); it never installs Xcode (App Store), simulator runtimes (Xcode
component), or Apple keys (operator-approved). If preflight reports NOT READY,
STOP and surface exactly what's missing. Never create Apple API keys here.

## Operator defaults — `~/.aos/config/ios.yaml`

Read it at the start of every run:

```yaml
bundle_prefix: com.example        # operator's reverse-domain prefix for new bundle IDs
min_ios: "18.0"                   # default deployment target (non-AI apps)
dest_root: ~/project              # where new projects are created
```

This file is instance-level (`~/.aos/config/`) and is NEVER shipped. If
missing, ask the operator for `bundle_prefix` (one question), write the file
with these defaults, continue. The skill ships with no personal data — all
identity (Team ID, keys, bundle prefix) lives in Keychain or instance config.

## The flow — ask one question at a time

1. **App name** (display name, e.g. "Tasbih Counter"). Required.
2. **Bundle ID** — propose `<bundle_prefix>.<slug>`, accept override.
3. **On-device AI?** — "Should this app use Apple's on-device AI (Foundation
   Models)?" If yes, the deployment target is forced to iOS 26 and an
   `AIService` + `@Generable` example + AI demo view are included.
4. **Minimum iOS** — propose default (or 26 if AI). Accept override.
5. Confirm summary (name / bundle ID / AI / min iOS / destination), then go.

Then run in order:

```bash
# 1. Scaffold + generate the Xcode project (add --ai if AI was chosen)
~/.claude/skills/new-ios-app/scripts/scaffold.py \
    --name "<APP NAME>" --bundle "<BUNDLE_ID>" --min-ios "<MIN_IOS>" \
    --dest "<DEST_ROOT>" [--ai]
#    -> prints PROJECT_DIR=<path> on the last line. Capture it.

# 2. Verify it compiles locally BEFORE touching Apple
cd "<PROJECT_DIR>" && script/build | tail -8
#    (also: script/test runs the package's Swift Testing suite fast)

# 3. Full auto to TestFlight: register bundle ID + create ASC record +
#    archive + sign + upload.
script/ship
```

On success report: bundle ID, ASC app created, build number uploaded, and
that it's processing (15–30 min before testers install). Internal testers get
it automatically once processed.

## What gets generated (the best-practice scaffold)

- **Thin app shell over a local Swift Package** (`<App>Core`). All logic lives
  in the package → `swift test` is ~60× faster than `xcodebuild test`, and
  agents get a clean, importable, SwiftUI-capable target.
- **`@Observable` state**, enum `AppState`, `@Environment` DI (no TCA).
- **Swift 6 language mode + MainActor default isolation + approachable
  concurrency.**
- **Swift Testing** (`@Test`/`#expect`) test target.
- **Liquid Glass** by default (free via the iOS 26 SDK — not opted out).
- **XcodeGen `project.yml`** (`.xcodeproj` is generated + gitignored).
- **`AGENTS.md` + `CLAUDE.md` pointer**, `script/` entry points, `#Preview`s.
- **`PrivacyInfo.xcprivacy`** prefilled (UserDefaults `CA92.1`), encryption
  declared exempt, and a valid **1024 app icon (no alpha)** so upload passes.
- **Optional AI layer**: availability gating, `LanguageModelSession`,
  `@Generable` structured output, the 3 mandatory `GenerationError` catches,
  graceful degradation when Apple Intelligence is off/unavailable.

## Axiom routing (defer the hard parts)

- **Local build fails** → invoke `axiom-build` first, then retry step 2.
- **Code signing / ITMS upload error** → invoke `axiom-security`
  (code-signing-diag) — config, not transient.
- **Building real AI features** → `axiom-ai` (Foundation Models is the
  authority; the generated AI layer is a correct starting point, not a ceiling).
- **Real SwiftUI features** → `axiom-swiftui`. **Rejections/metadata** →
  `axiom-shipping`.

## Gotchas

- **App name uniqueness**: ASC names are globally unique. If `produce` fails
  on a name conflict, ask for a different display name (bundle ID can stay).
- **First external TestFlight build** needs Beta App Review; internal testers
  (≤100) get it immediately.
- **Placeholder icon** is a solid gradient — replace before public release.
- **AI apps require iOS 26** and an Apple-Intelligence-capable device (A17
  Pro+); the app still builds and runs with AI gracefully disabled elsewhere.
