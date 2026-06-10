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

## Battle-tested lessons (codified from shipping real apps)

### Swift 6 MainActor default isolation — the #1 source of bugs
The package ships with `.defaultIsolation(MainActor.self)`. That makes EVERY
closure/func MainActor-isolated unless marked otherwise — which crashes at
runtime when a framework calls your code on a background thread.
- **Dynamic `UIColor` providers MUST be `nonisolated`.** A `Color.adaptive`
  helper using `UIColor { trait in … }` will trap (`EXC_BREAKPOINT /
  _dispatch_assert_queue_fail`) because SwiftUI resolves dynamic colors on a
  background render thread. Mark the helper `nonisolated`. (The scaffold's
  DesignSystem already does this — keep it.)
- **Extension callbacks are nonisolated** (DeviceActivityMonitor, ShieldAction).
  Don't apply MainActor default isolation to extension targets, and don't store
  non-Sendable statics (e.g. `ManagedSettingsStore.Name` → make it a computed
  `var`, not a `static let`).
- When something runs off-main (Core Location delegate, audio callbacks),
  extract primitives BEFORE hopping to `@MainActor`.

### Cross-platform test host (macOS) — guard iOS-only APIs
`swift test` builds the package for the **macOS host** (fast), so any iOS-only
API breaks it. The scaffold bundles `PlatformShims.swift` (`inlineNavTitle()`,
`hiddenNavBarBackground()`); USE THEM instead of `.navigationBarTitleDisplayMode`
/ `.toolbarBackground(.hidden, for: .navigationBar)`. Guard with `#if os(iOS)`:
`ToolbarItemPlacement.topBarLeading`, `CoreLocation` auth/heading, `AVAudioSession`,
`ActivityKit`/`ActivityAttributes`, `ManagedSettings`/`FamilyControls`, `CHHapticEngine`.
(Alternative if this tax gets heavy: drop `.macOS` from `Package.swift` platforms
and run tests on the iOS Simulator — slower but no guards. Default keeps macOS.)

### Other gotchas that cost real time
- **`DateFormatter` AM/PM uses U+202F** (narrow no-break space), so test
  assertions like `== "4:42 AM"` fail invisibly. Normalize `\u{202F}`→space in
  test helpers, or use `dateFormat`.
- **Don't run `xcodebuild` and `swift test` concurrently** — they fight over the
  build DB ("database is locked"). Run sequentially.
- **App icon**: AI-generated icons often come with rounded corners + white
  margins. Apple needs **full-bleed, square, NO alpha**. Crop to the content,
  flatten alpha onto an opaque background, verify under a rounded mask. For
  iOS 26, the premium path is a layered icon via **Icon Composer** (no baked
  corners/shadows).
- **Custom fonts**: register in `project.yml` `info.properties.UIAppFonts`
  (filenames). Reference by the **PostScript name**, not the filename. Verify
  glyph/tashkīl coverage with fonttools before trusting an Arabic font.
- **App name uniqueness**: ASC names are globally unique. On conflict, add
  keywords ("App: Salah Focus") — the home-screen name (CFBundleDisplayName)
  can stay short.
- **AI apps require iOS 26** + an Apple-Intelligence-capable device (A17 Pro+);
  the app still builds/runs with AI gracefully disabled elsewhere.

## Adding app extensions (widgets, Screen Time, etc.)
The scaffold generates app + Core package only. For extensions:
- Add each as a `type: app-extension` target in `project.yml` with its own
  `PRODUCT_BUNDLE_IDENTIFIER` (strict child of the app's), `entitlements`, and
  `info.NSExtension` (point identifier + principal class). Add it to the app
  target's `dependencies` (auto-embeds).
- **Do NOT set MainActor default isolation on extensions** (callbacks are
  nonisolated). Keep them tiny — DeviceActivityMonitor has a ~6 MB budget.
- Share state via an **App Group** (register once in the portal; add the
  capability + identical `application-groups` entitlement to ALL targets).
- A **lean shared module** (App-Group + helpers, no SwiftUI/GRDB) linked by the
  extensions, while the app links the full Core, keeps extensions small.
- Build features fast with **parallel agents**, each owning one module folder —
  the package-centric architecture makes this conflict-free.

## Shipping realities (App Store Connect / fastlane)
- **Bundle IDs** can be created via the ASC API (`POST /v1/bundleIds`) with the
  `.p8` — fast, no clicking.
- **Creating the App Store Connect app record needs interactive Apple-ID auth**
  — `fastlane produce` with only the API key asks for a username. Either set
  `SPACESHIP_CONNECT_API_KEY_ID/ISSUER_ID/KEY_FILEPATH` env vars, or create the
  record in the ASC web UI (the API-key alone can't create a brand-new app).
- **Two account-holder legal gates block NEW-app submission** (not internal
  TestFlight builds, but eventual release): the updated **Apple Developer
  Program License Agreement** must be accepted, and **EU trader status (DSA)**
  provided. These are the operator's to do — never accept legal terms for them.
- **Family Controls / Screen Time**: the distribution entitlement is requested
  via an **account-level web form** (developer.apple.com/contact/request/
  family-controls-distribution) — in 2026 it's a single "Get Entitlement"
  acknowledgment, then you enable the capability per App ID. **TestFlight
  (even internal) requires the distribution entitlement** (dev entitlement only
  works from Xcode on your own device). Internal testers skip Beta App Review.
- **App Group identifier field auto-prefixes `group.`** in the portal — type
  only the rest, or you get `group.group.…`.

## Optional: AI-generated visual identity (OpenRouter)
For a premium look, generate a coherent asset set at build time:
- Endpoint: `POST https://openrouter.ai/api/v1/chat/completions` with
  `"modalities":["image","text"]`; image returns as a base64 data URL.
- **Always set `max_tokens`** (e.g. 1500 for 1K, ~8000 for 2K) — the default
  reserves the model max and trips a 402 when the key's daily credit is low.
- **Coherence**: generate a master "style frame" first, then condition every
  other asset on it (reference image) + a verbatim locked style-prefix.
- **Icon prompt must say full-bleed** ("fill the entire square edge to edge, no
  border, no rounded corners") or the model draws an icon-of-an-icon.
- Read `OPENROUTER_API_KEY` from Keychain; never hardcode.
- **Authenticity for Islamic/cultural apps**: no figurative imagery, and NEVER
  AI-generate Arabic/scripture (models mangle letterforms) — use real fonts.

## Verifying screens headlessly
Bake a debug launch-arg convention into the app (`-uiTestSeed` to skip
permission-gated onboarding with seeded data; `-uiTab home|history|…` to boot
into a screen), then capture with `xcrun simctl launch <udid> <bundle> -uiTestSeed
-uiTab home` + `xcrun simctl io <udid> screenshot`. Lets agents screenshot every
screen without tapping (GUI automation of the Simulator is often blocked by
missing Accessibility permission).
