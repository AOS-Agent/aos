#!/usr/bin/env python3
"""
new-ios-app scaffold generator.

Generates a modern (iOS 26 / Xcode 26 era) SwiftUI app:
  - thin app shell over a local Swift Package (<Target>Core)
  - @Observable state, enum AppState, @Environment DI
  - Swift 6 language mode + MainActor default isolation
  - Swift Testing test target
  - Liquid Glass by default (free via iOS 26 SDK)
  - optional on-device Foundation Models AI layer (--ai)
  - XcodeGen project.yml, fastlane config, AGENTS.md, script/ entry points
  - placeholder 1024 app icon (no alpha) so TestFlight upload validates

Usage:
  scaffold.py --name "Tasbih Counter" --bundle com.you.tasbihcounter \\
      --min-ios 18.0 --dest ~/project [--ai]

Prints the absolute project directory on the last line (PROJECT_DIR=...).
"""
import argparse, os, re, sys, subprocess, zlib, struct, json

# ---------- helpers ----------

def secret(name):
    try:
        out = subprocess.run(
            [os.path.expanduser("~/aos/core/bin/cli/agent-secret"), "get", name],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return ""

def slugify(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())

def pascal(name):
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "App"

def render(text, ctx):
    for k, v in ctx.items():
        text = text.replace("__%s__" % k, str(v))
    return text

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

def write_icon_png(path, size=1024):
    """Pure-python vertical-gradient RGB PNG (no alpha) — valid app icon."""
    top = (79, 70, 229)      # indigo
    bot = (124, 58, 237)     # violet
    def crc_chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    raw = bytearray()
    for y in range(size):
        t = y / (size - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        raw.append(0)                      # filter type none
        raw += bytes((r, g, b)) * size
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(sig + crc_chunk(b"IHDR", ihdr) + crc_chunk(b"IDAT", idat) + crc_chunk(b"IEND", b""))

# ---------- templates (raw strings: keep Swift backslashes intact) ----------

PROJECT_YML = r"""name: __TARGET__
options:
  bundleIdPrefix: __BUNDLE_PREFIX__
  deploymentTarget:
    iOS: "__MIN_IOS__"
  createIntermediateGroups: true
packages:
  __TARGET__Core:
    path: __TARGET__Core
settings:
  base:
    SWIFT_VERSION: "6.0"
    DEVELOPMENT_TEAM: "__TEAM_ID__"
    CODE_SIGN_STYLE: Automatic
    MARKETING_VERSION: "__VERSION__"
    CURRENT_PROJECT_VERSION: "1"
    SWIFT_STRICT_CONCURRENCY: complete
    SWIFT_DEFAULT_ACTOR_ISOLATION: MainActor
    SWIFT_APPROACHABLE_CONCURRENCY: YES
    ENABLE_USER_SCRIPT_SANDBOXING: YES
targets:
  __TARGET__:
    type: application
    platform: iOS
    sources:
      - path: __TARGET__
    dependencies:
      - package: __TARGET__Core
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: __BUNDLE_ID__
        ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon
        ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME: AccentColor
        TARGETED_DEVICE_FAMILY: "1,2"
    info:
      path: __TARGET__/Info.plist
      properties:
        CFBundleDisplayName: __APP_NAME__
        CFBundleShortVersionString: "__VERSION__"
        CFBundleVersion: "1"
        ITSAppUsesNonExemptEncryption: false
        UILaunchScreen: {}
        UIApplicationSceneManifest:
          UIApplicationSupportsMultipleScenes: false
        UISupportedInterfaceOrientations:
          - UIInterfaceOrientationPortrait
          - UIInterfaceOrientationLandscapeLeft
          - UIInterfaceOrientationLandscapeRight
        UISupportedInterfaceOrientations~ipad:
          - UIInterfaceOrientationPortrait
          - UIInterfaceOrientationPortraitUpsideDown
          - UIInterfaceOrientationLandscapeLeft
          - UIInterfaceOrientationLandscapeRight
"""

PACKAGE_SWIFT = r"""// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "__TARGET__Core",
    // macOS is declared so `swift test` (which builds for the host) uses a
    // modern deployment target; the app itself ships iOS-only.
    platforms: [.iOS(__MIN_IOS_ENUM__), .macOS(__MAC_ENUM__)],
    products: [
        .library(name: "__TARGET__Core", targets: ["__TARGET__Core"]),
    ],
    targets: [
        .target(
            name: "__TARGET__Core",
            swiftSettings: [
                .swiftLanguageMode(.v6),
                .defaultIsolation(MainActor.self),
            ]
        ),
        .testTarget(
            name: "__TARGET__CoreTests",
            dependencies: ["__TARGET__Core"],
            swiftSettings: [
                .swiftLanguageMode(.v6),
                .defaultIsolation(MainActor.self),
            ]
        ),
    ]
)
"""

APP_SWIFT = r"""import SwiftUI
import __TARGET__Core

@main
struct __TARGET__App: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}
"""

APP_STATE = r"""import Foundation

/// Top-level application state as an explicit, validated machine.
/// Prefer enum cases over boolean flags. See AGENTS.md.
enum AppState: Equatable {
    case launching
    case ready
    case failed(String)
}
"""

APP_MODEL = r"""import Observation

/// App-wide model + dependency container, injected via `.environment(...)`.
/// MainActor-isolated by default (package default isolation).
@Observable
final class AppModel {
    private(set) var state: AppState = .launching

    func start() async {
        // Wire up services, load initial data, restore session, etc.
        state = .ready
    }
}
"""

ROOT_VIEW = r"""import SwiftUI

/// The app's single public entry point. The app shell renders `RootView()`.
public struct RootView: View {
    @State private var model = AppModel()

    public init() {}

    public var body: some View {
        Group {
            switch model.state {
            case .launching:
                ProgressView().controlSize(.large)
            case .ready:
                HomeView()
            case .failed(let message):
                ContentUnavailableView(
                    "Something went wrong",
                    systemImage: "exclamationmark.triangle",
                    description: Text(message)
                )
            }
        }
        .environment(model)
        .task { await model.start() }
    }
}

#Preview {
    RootView()
}
"""

HOME_STORE = r"""import Observation

/// Presentation state for the Home feature.
@Observable
final class HomeStore {
    var count = 0
    func increment() { count += 1 }
}
"""

HOME_VIEW = r"""import SwiftUI

struct HomeView: View {
    @State private var store = HomeStore()

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Image(systemName: "sparkles")
                    .font(.system(size: 64))
                    .foregroundStyle(.tint)
                Text("Tapped \(store.count) times")
                    .font(.title2)
                Button("Tap me") { store.increment() }
                    .buttonStyle(.borderedProminent)
__AI_SECTION__
            }
            .padding()
            .navigationTitle("__APP_NAME__")
        }
    }
}

#Preview {
    HomeView()
}
"""

AI_SECTION = r"""
                NavigationLink("AI Demo") { AIDemoView() }
                    .buttonStyle(.bordered)"""

EXAMPLE_TEST = r"""import Testing
@testable import __TARGET__Core

@Suite struct HomeStoreTests {
    @Test func incrementRaisesCount() {
        let store = HomeStore()
        #expect(store.count == 0)
        store.increment()
        #expect(store.count == 1)
    }
}
"""

# ---------- AI layer (only when --ai) ----------

AI_SERVICE = r"""import FoundationModels
import Observation

/// Wraps on-device Foundation Models availability + session lifecycle.
@Observable
final class AIService {
    enum Availability: Equatable {
        case available, deviceNotEligible, notEnabled, downloading, unsupported

        var message: String {
            switch self {
            case .available:        "Ready"
            case .deviceNotEligible: "This device doesn't support Apple Intelligence."
            case .notEnabled:        "Turn on Apple Intelligence in Settings to use AI features."
            case .downloading:       "The on-device model is still downloading."
            case .unsupported:       "AI features aren't available right now."
            }
        }
    }

    private(set) var availability: Availability = .unsupported

    init() { refreshAvailability() }

    /// Refresh on `.active` scene phase — the user may toggle Apple Intelligence in Settings.
    func refreshAvailability() {
        switch SystemLanguageModel.default.availability {
        case .available:
            availability = .available
        case .unavailable(.deviceNotEligible):
            availability = .deviceNotEligible
        case .unavailable(.appleIntelligenceNotEnabled):
            availability = .notEnabled
        case .unavailable(.modelNotReady):
            availability = .downloading
        case .unavailable:
            availability = .unsupported
        }
    }

    /// Example structured generation. Never interpolate user text into instructions.
    func summarize(_ text: String) async throws -> KeyPoints {
        let session = LanguageModelSession(
            instructions: "You extract concise, factual key points from the user's text. Be brief."
        )
        let response = try await session.respond(to: text, generating: KeyPoints.self)
        return response.content
    }
}
"""

AI_GENERABLE = r"""import FoundationModels

/// Structured output type — constrained decoding guarantees the shape.
/// Declare the most important properties first (helps streaming).
@Generable
struct KeyPoints: Equatable {
    @Guide(description: "A concise one-sentence summary.")
    var summary: String

    @Guide(description: "Three to five key bullet points.", .count(3...5))
    var points: [String]
}
"""

AI_DEMO_VIEW = r"""import SwiftUI
import FoundationModels

struct AIDemoView: View {
    @State private var service = AIService()
    @State private var input = "Paste or type some text and tap Summarize."
    @State private var result: KeyPoints?
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        Form {
            if service.availability == .available {
                Section("Input") {
                    TextEditor(text: $input).frame(minHeight: 120)
                }
                Section {
                    Button(isWorking ? "Working…" : "Summarize") {
                        Task { await run() }
                    }
                    .disabled(isWorking || input.isEmpty)
                }
                if let result {
                    Section("Summary") { Text(result.summary) }
                    Section("Key points") {
                        ForEach(result.points, id: \.self) { Text($0) }
                    }
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(.red) }
                }
            } else {
                ContentUnavailableView(service.availability.message, systemImage: "sparkles")
            }
        }
        .navigationTitle("AI Demo")
        .onAppear { service.refreshAvailability() }
    }

    private func run() async {
        isWorking = true; errorMessage = nil; result = nil
        defer { isWorking = false }
        do {
            result = try await service.summarize(input)
        } catch let error as LanguageModelSession.GenerationError {
            switch error {
            case .exceededContextWindowSize:
                errorMessage = "That text is too long. Try a shorter passage."
            case .guardrailViolation:
                errorMessage = "The request was blocked by safety guardrails."
            case .unsupportedLanguageOrLocale:
                errorMessage = "This language isn't supported yet."
            default:
                errorMessage = "Generation failed. Please try again."
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

#Preview {
    NavigationStack { AIDemoView() }
}
"""

# ---------- non-Swift project files ----------

PRIVACY = r"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSPrivacyTracking</key>
    <false/>
    <key>NSPrivacyTrackingDomains</key>
    <array/>
    <key>NSPrivacyCollectedDataTypes</key>
    <array/>
    <key>NSPrivacyAccessedAPITypes</key>
    <array>
        <dict>
            <key>NSPrivacyAccessedAPIType</key>
            <string>NSPrivacyAccessedAPICategoryUserDefaults</string>
            <key>NSPrivacyAccessedAPITypeReasons</key>
            <array>
                <string>CA92.1</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
"""

ASSETS_ROOT = json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2)

APPICON_CONTENTS = json.dumps({
    "images": [{"filename": "icon-1024.png", "idiom": "universal",
                "platform": "ios", "size": "1024x1024"}],
    "info": {"author": "xcode", "version": 1},
}, indent=2)

ACCENT_CONTENTS = json.dumps({
    "colors": [{"idiom": "universal", "color": {
        "color-space": "srgb",
        "components": {"red": "0.31", "green": "0.27", "blue": "0.90", "alpha": "1.000"}}}],
    "info": {"author": "xcode", "version": 1},
}, indent=2)

FASTFILE = r"""default_platform(:ios)

platform :ios do
  desc "Register the app on App Store Connect, build, and upload to TestFlight"
  lane :ship do
    key = app_store_connect_api_key(
      key_id:       ENV.fetch("ASC_KEY_ID"),
      issuer_id:    ENV.fetch("ASC_ISSUER_ID"),
      key_filepath: ENV.fetch("ASC_KEY_PATH")
    )

    produce(
      app_identifier: ENV.fetch("APP_BUNDLE_ID"),
      app_name:       ENV.fetch("APP_NAME"),
      language:       "en-US",
      sku:            ENV.fetch("APP_SKU"),
      team_id:        ENV.fetch("APPLE_TEAM_ID"),
      api_key:        key
    )

    build_app(
      scheme:        ENV.fetch("APP_SCHEME"),
      export_method: "app-store",
      xcargs:        "-allowProvisioningUpdates",
      output_directory: "build"
    )

    upload_to_testflight(
      api_key: key,
      skip_waiting_for_build_processing: true,
      distribute_external: false
    )
  end
end
"""

APPFILE = r"""app_identifier(ENV["APP_BUNDLE_ID"])
team_id(ENV["APPLE_TEAM_ID"])
apple_id(ENV["APPLE_ID"]) if ENV["APPLE_ID"]
"""

APP_ENV = r"""# Generated by new-ios-app. Sourced by script/ship and the skill's ship.sh.
APP_NAME="__APP_NAME__"
APP_BUNDLE_ID="__BUNDLE_ID__"
APP_SCHEME="__TARGET__"
APP_SKU="__SLUG__"
APP_VERSION="__VERSION__"
"""

GITIGNORE = r"""## Xcode / generated
__TARGET__.xcodeproj/
*.xcworkspace/
xcuserdata/
DerivedData/
build/
.DS_Store

## SPM
.build/
.swiftpm/

## fastlane
fastlane/report.xml
fastlane/Preview.html
fastlane/screenshots/**/*.png
fastlane/test_output
*.ipa
*.dSYM.zip
"""

AGENTS_MD = r"""# AGENTS.md — __APP_NAME__

Brief for AI coding agents (and humans). `CLAUDE.md` points here.

## Architecture

Thin app shell over a local Swift Package.

- `__TARGET__/` — app shell ONLY: `@main`, assets, Info.plist, entitlements.
  No business logic lives here.
- `__TARGET__Core/` — the real code, as a Swift Package. This is your
  primary workspace. It's SwiftUI-capable but has no app-target coupling,
  so it builds and tests fast.

```
__TARGET__Core/Sources/__TARGET__Core/
  App/        AppState (enum), AppModel (@Observable), RootView (public)
  Features/   one folder per feature (Home/ ...). View + Store per feature.
  Shared/     Models (structs, Sendable), Services (actors/protocols), DesignSystem
__AI_AGENTS_NOTE__
```

## Conventions

- **State:** `@Observable` classes. Never `ObservableObject`/`@StateObject`.
- **DI:** create app-wide models in a parent with `@State`, inject via
  `.environment(...)`, read with `@Environment(Type.self)`.
- **Property wrappers:** `@State` only when the view OWNS the model;
  `@Environment` for app/scene-wide; `@Bindable` when a child needs `$binding`;
  plain `let` to just read a parent-owned `@Observable`.
- **Concurrency:** Swift 6 language mode, MainActor default isolation. Models
  and Stores stay `@MainActor`. Domain models are `Sendable` value types.
  Push async work into the model; keep UI-state changes synchronous.
- **No logic in `body`.** No global singletons. No `@AppStorage` inside an
  `@Observable` (it silently breaks observation — read it in the view).
- **Every view ships a `#Preview`** with mock dependencies.
- **Naming:** `<Feature>View`, `<Feature>Store`, `<Feature>Service`.

## Commands

```
script/gen     # regenerate the .xcodeproj from project.yml (after adding shell files)
script/build   # build the app (no signing)
script/test    # fast: swift test on the package
script/ship    # register on App Store Connect + upload to TestFlight
```

You rarely need `script/gen` — new files in `__TARGET__Core` are picked up by
SPM automatically. Only the app shell needs a regen when its files change.

## Gotchas

- Adding a Swift file to `__TARGET__Core/Sources` needs NO project edit.
- Don't hand-edit the `.xcodeproj` (it's generated + gitignored).
- App Store Connect app names are globally unique.

## Swift 6 isolation & cross-platform (read before adding UI)

The package uses **MainActor default isolation** — every closure/func is
`@MainActor` unless marked otherwise. This crashes at runtime if a framework
calls your code on a background thread.

- **Dynamic `UIColor` providers must be `nonisolated`.** SwiftUI resolves
  dynamic colors on a background render thread → a MainActor provider traps
  (`_dispatch_assert_queue_fail`). See `Shared/PlatformShims.swift`
  (`Color.adaptive`) — it's already `nonisolated`; follow that pattern.
- **Extension callbacks are nonisolated.** Don't apply MainActor default
  isolation to extension targets; don't store non-Sendable statics.
- **`swift test` builds for the macOS host.** Guard iOS-only APIs with
  `#if os(iOS)` (`topBarLeading`, CoreLocation, AVAudioSession, ActivityKit,
  ManagedSettings/FamilyControls, CoreHaptics). Use the shims in
  `PlatformShims.swift` (`.inlineNavTitle()`, `.hiddenNavBarBackground()`)
  instead of the raw iOS-only nav modifiers.
- **Don't run `xcodebuild` and `swift test` at the same time** (build-DB lock).
- **`DateFormatter` AM/PM uses U+202F** — normalize it in test assertions.
"""

AI_AGENTS_NOTE = r"""  AI/         Foundation Models: AIService, @Generable types, AIDemoView"""

CLAUDE_MD = r"""See [AGENTS.md](AGENTS.md).
"""

PLATFORM_SHIMS = r"""import SwiftUI

// Cross-platform shims + isolation-safe helpers.
//
// The package uses MainActor default isolation, so every closure is @MainActor
// unless marked otherwise. Two consequences these helpers handle:
//  1. `swift test` builds for the macOS host — iOS-only nav modifiers break it.
//  2. Frameworks call some closures on background threads — those must be
//     `nonisolated` or they trap at runtime (`_dispatch_assert_queue_fail`).

public extension View {
    /// `.navigationBarTitleDisplayMode(.inline)` on iOS; no-op elsewhere.
    @ViewBuilder func inlineNavTitle() -> some View {
        #if os(iOS)
        self.navigationBarTitleDisplayMode(.inline)
        #else
        self
        #endif
    }

    /// Hides the navigation bar's toolbar background on iOS; no-op elsewhere.
    @ViewBuilder func hiddenNavBarBackground() -> some View {
        #if os(iOS)
        self.toolbarBackground(.hidden, for: .navigationBar)
        #else
        self
        #endif
    }
}

public extension Color {
    /// A light/dark adaptive color.
    ///
    /// `nonisolated` is REQUIRED: UIKit/SwiftUI invoke the dynamic-color
    /// provider on background render threads. Under MainActor default isolation
    /// a MainActor-isolated provider traps (`EXC_BREAKPOINT`) when resolved
    /// off the main thread.
    nonisolated static func adaptive(light: Color, dark: Color) -> Color {
        #if canImport(UIKit)
        return Color(uiColor: UIColor { trait in
            trait.userInterfaceStyle == .light ? UIColor(light) : UIColor(dark)
        })
        #else
        return dark
        #endif
    }
}
"""

README_MD = r"""# __APP_NAME__

A SwiftUI iOS app. Architecture and conventions: [AGENTS.md](AGENTS.md).

## Quick start

```
script/gen      # generate the Xcode project
open __TARGET__.xcodeproj
```

Or from the command line: `script/build`, `script/test`, `script/ship`.

- **Min iOS:** __MIN_IOS__
- **Bundle ID:** __BUNDLE_ID__
- Generated by AOS `new-ios-app`.
"""

SCRIPT_GEN = "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")/..\"\nxcodegen generate\n"
SCRIPT_BUILD = ("#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")/..\"\n"
                "xcodegen generate\nxcodebuild -project __TARGET__.xcodeproj -scheme __TARGET__ "
                "-destination 'generic/platform=iOS' -configuration Debug build CODE_SIGNING_ALLOWED=NO\n")
SCRIPT_TEST = ("#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")/../__TARGET__Core\"\n"
               "swift test\n")
SCRIPT_SHIP = ("#!/usr/bin/env bash\nset -euo pipefail\nHERE=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"\n"
               "exec ~/.claude/skills/new-ios-app/scripts/ship.sh \"$HERE\"\n")

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--min-ios", default="18.0")
    ap.add_argument("--dest", default=os.path.expanduser("~/project"))
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--ai", action="store_true")
    args = ap.parse_args()

    name = args.name.strip()
    slug = slugify(name)
    target = pascal(name)
    if not slug:
        print("ERROR: app name has no usable characters", file=sys.stderr); sys.exit(2)

    min_ios = args.min_ios
    if args.ai and int(min_ios.split(".")[0]) < 26:
        min_ios = "26.0"  # Foundation Models requires iOS 26
    min_major = min_ios.split(".")[0]
    bundle = args.bundle.strip()
    prefix = bundle.rsplit(".", 1)[0] if "." in bundle else bundle
    team = secret("APPLE_TEAM_ID")

    dest = os.path.expanduser(args.dest)
    proj = os.path.join(dest, slug)
    if os.path.exists(proj):
        print("ERROR: %s already exists" % proj, file=sys.stderr); sys.exit(2)

    ctx = {
        "APP_NAME": name, "TARGET": target, "SLUG": slug,
        "BUNDLE_ID": bundle, "BUNDLE_PREFIX": prefix,
        "MIN_IOS": min_ios, "MIN_IOS_ENUM": ".v%s" % min_major,
        "MAC_ENUM": ".v26" if int(min_major) >= 26 else ".v15",
        "TEAM_ID": team, "VERSION": args.version,
        "AI_SECTION": AI_SECTION if args.ai else "",
        "AI_AGENTS_NOTE": AI_AGENTS_NOTE if args.ai else "",
    }

    core = "%sCore" % target
    src = os.path.join(proj, core, "Sources", core)
    files = {
        # app shell
        os.path.join(proj, "project.yml"): PROJECT_YML,
        os.path.join(proj, target, "%sApp.swift" % target): APP_SWIFT,
        os.path.join(proj, target, "Resources", "PrivacyInfo.xcprivacy"): PRIVACY,
        os.path.join(proj, target, "Assets.xcassets", "Contents.json"): ASSETS_ROOT,
        os.path.join(proj, target, "Assets.xcassets", "AppIcon.appiconset", "Contents.json"): APPICON_CONTENTS,
        os.path.join(proj, target, "Assets.xcassets", "AccentColor.colorset", "Contents.json"): ACCENT_CONTENTS,
        # package
        os.path.join(proj, core, "Package.swift"): PACKAGE_SWIFT,
        os.path.join(src, "App", "AppState.swift"): APP_STATE,
        os.path.join(src, "App", "AppModel.swift"): APP_MODEL,
        os.path.join(src, "App", "RootView.swift"): ROOT_VIEW,
        os.path.join(src, "Features", "Home", "HomeStore.swift"): HOME_STORE,
        os.path.join(src, "Features", "Home", "HomeView.swift"): HOME_VIEW,
        os.path.join(src, "Shared", "PlatformShims.swift"): PLATFORM_SHIMS,
        os.path.join(proj, core, "Tests", "%sTests" % core, "HomeStoreTests.swift"): EXAMPLE_TEST,
        # fastlane
        os.path.join(proj, "fastlane", "Fastfile"): FASTFILE,
        os.path.join(proj, "fastlane", "Appfile"): APPFILE,
        # meta
        os.path.join(proj, ".aos-app.env"): APP_ENV,
        os.path.join(proj, ".gitignore"): GITIGNORE,
        os.path.join(proj, "AGENTS.md"): AGENTS_MD,
        os.path.join(proj, "CLAUDE.md"): CLAUDE_MD,
        os.path.join(proj, "README.md"): README_MD,
        os.path.join(proj, "script", "gen"): SCRIPT_GEN,
        os.path.join(proj, "script", "build"): SCRIPT_BUILD,
        os.path.join(proj, "script", "test"): SCRIPT_TEST,
        os.path.join(proj, "script", "ship"): SCRIPT_SHIP,
    }
    if args.ai:
        files[os.path.join(src, "AI", "AIService.swift")] = AI_SERVICE
        files[os.path.join(src, "AI", "KeyPoints.swift")] = AI_GENERABLE
        files[os.path.join(src, "AI", "AIDemoView.swift")] = AI_DEMO_VIEW

    for path, content in files.items():
        write(path, render(content, ctx))

    for s in ("gen", "build", "test", "ship"):
        os.chmod(os.path.join(proj, "script", s), 0o755)

    write_icon_png(os.path.join(proj, target, "Assets.xcassets", "AppIcon.appiconset", "icon-1024.png"))

    # generate the Xcode project
    subprocess.run(["xcodegen", "generate"], cwd=proj, check=True)

    print("PROJECT_DIR=%s" % proj)

if __name__ == "__main__":
    main()
