<!-- ios-dev-loop skill: paste into the app's AGENTS.md (or CLAUDE.md) and fill the routes table. -->

## Dev loop (ios-dev-loop skill)

```bash
script/snap [--no-build] [route[:a[:b]]] [launch args…]   # build → sim → screenshot (paths on the last lines)
script/device [--no-build]                                # build → install → launch on the operator's iPhone
tools/worktree.sh <area|task> <slug>                      # a worktree + branch + leased simulator per session
tools/ship.sh prepare|archive|upload|status               # TestFlight in three explicit stages (upload = the GO token)
```

`snap` uses **this worktree's leased simulator** (`.ios-sim`, written by `tools/provision.sh`),
per-worktree `DerivedData/`, the machine-wide build slot (`tools/build-slot.sh`), `xcsift`
for the build log, and writes a full-res and a `-1x.png` (points = pixels — read the 1x for
taps, the full-res to judge).

### Build / run / test tooling — priority order

| Need | Use | Not |
|---|---|---|
| Build → install → launch → screenshot | `script/snap [route]` (per-worktree DerivedData; takes the build slot) | ad-hoc `xcodebuild` with hand-typed flags |
| Tap / type / read the screen | `axe describe-ui --udid $(cat .ios-sim)` (a11y tree first), then `axe tap --label …`; screenshot only to *judge* | guessing coordinates |
| Structured build/test results, UI automation with element refs | `xcodebuildmcp <workflow> <tool> --output json` — CLI mode, reads `.xcodebuildmcp/config.yaml` | enabling the MCP server by default (80+ tool schemas in context) |
| SwiftUI preview as PNG, Apple docs search | Apple's Xcode MCP, opt-in: `claude mcp add --transport stdio xcode -s project -- xcrun mcpbridge` | — |
| Crash logs / console capture | Axiom `xcsym` / `xclog` (`/axiom:analyze-crash`) | hand-parsing `.ips` |

Never `rm -rf DerivedData` to "fix" a build; never pass `BUILD_DIR`/`SYMROOT`/`OBJROOT` (breaks `xcodebuild archive`).

### Hooks you will hit (`.claude/hooks/`, registered in `.claude/settings.json`)

| Hook | What it does | When it blocks |
|---|---|---|
| PreToolUse `pre-tool-guard.sh` | Refuses edits to `.pbxproj` / `.xcodeproj` / `.xcworkspace` / `.entitlements` / `Package.resolved` | XcodeGen projects: always — edit `project.yml`, run `xcodegen`. Plain projects: warns only |
| PostToolUse `post-edit-swift.sh` | `swiftlint --fix` (whitespace/comma rules only) on the edited file | Only when the edit **adds** `try!` / `as!` / `@unchecked Sendable` vs HEAD |
| Stop `stop-build-gate.sh` | Builds when Swift or `project.yml` changed this turn (own DerivedData, build slot, cached by diff) | On compile errors — fix them, don't argue with it. Skips silently if another session holds the build slot |
| SessionStart `provision.sh` / `handoff-staleness.sh` | Provisions the worktree; warns when `.agent/HANDOFF.md` is >3 days older than the newest commit | Never |

### Routes (`name[:a[:b]]`, all `#if DEBUG` launch args in `<App>App.swift`)

| Route | Lands on | Launch args |
|---|---|---|
| `home` (default) | Home | — |
| <!-- `page:<N>` --> | <!-- Reader at page N --> | <!-- `-startPage N` --> |

Unknown routes pass through as raw launch args. A route proves the screen renders, not
that navigation reaches it — verify the real user path too.
