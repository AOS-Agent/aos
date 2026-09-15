---
name: ios-dev-loop
description: >
  Fast agentic UI iteration for any iOS app: snapshot simulator screens, self-
  judge against the design system, deploy to the operator's physical iPhone — no
  TestFlight. Also installs the project tooling the loop rests on (worktree +
  leased simulator per session, build slot, hooks, three-stage ship). Trigger on
  "iterate on the UI", "snap the screen", "show me the screen", "deploy to my
  phone", "set up the dev loop", "screenshot the app", "new worktree for",
  "ship to TestFlight", "check the iOS toolchain", or whenever making visual
  iOS changes that should be verified by looking. Self-scaffolds script/snap,
  script/device, tools/ and hooks if missing.
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, AskUserQuestion
---

# ios-dev-loop — see what you ship

The loop: **edit → snap → judge → show → device**. The agent carries the
inner loop (build, screenshot, self-verify); the operator enters only for
taste calls and the final feel-check on real hardware. TestFlight is for
distributing to *other people* — never for testing a change to the app being built.

Everything here is project-agnostic and self-discovering: the scripts run with
zero edits on a single-app XcodeGen or plain `.xcodeproj` project, and degrade
gracefully (no submodule → skip, no xcodegen → skip, no swiftlint → skip, no
xcsift → `cat`, no build-slot script → run directly).

## Phase 0 — Detect (always run first)

```bash
git rev-parse --show-toplevel; git rev-parse --abbrev-ref HEAD   # where am I, on what
ls script/snap script/device tools/provision.sh .claude/hooks/stop-build-gate.sh 2>/dev/null
```

`script/` lives in the **app dir** (the directory holding `project.yml` or the
`.xcodeproj` — often `ios/` in a monorepo); `tools/` and `.claude/` live at the
repo root.

- **All present** → Phase 2. The project's `script/snap` always wins over the
  template — read its route block to learn this app's screens, and the
  project's `AGENTS.md` "Dev loop" section.
- **Anything missing** → Phase 1 (the installer is idempotent; it reports
  what it kept and what it added).
- **Committing this session?** If the repo uses linked worktrees and you are
  on the canonical checkout or `main`, move first: `tools/worktree.sh <area> <slug>`.

## Phase 1 — Scaffold

```bash
~/.claude/skills/ios-dev-loop/scripts/install.sh [<repo-or-app-dir>] [--dry-run] [--force]
```

One command installs everything and merges (never overwrites) the hooks into
`.claude/settings.json`:

| Installed | Where | Role |
|---|---|---|
| `script/snap`, `script/device` | app dir | the loop |
| `tools/build-slot.sh` | repo | one heavy xcodebuild at a time, machine-wide |
| `tools/provision.sh` | repo | submodules, `script/sync-data`, xcodegen-if-stale, simulator lease (`.ios-sim`) — runs at SessionStart |
| `tools/worktree.sh` | repo | `<canonical>/.claude/worktrees/<slug>` + branch + provision; `--remove`, `--gc` |
| `tools/ship.sh` | repo | `prepare` → `archive` → `upload` (Phase 3) |
| `.claude/hooks/*.sh` + settings block | repo | guard generated files, lint on edit, build gate on Stop, HANDOFF staleness |
| `.xcodebuildmcp/config.yaml` | app dir | scheme/project filled in; per-worktree DerivedData; CLI-mode defaults |
| `.swiftlint.yml` | app dir | only if none exists — autocorrect-safe rules only |
| `.gitignore` lines | repo | `DerivedData/ .ios-sim .agent/ship-state .claude/worktrees/ build/snaps/` |

Then, the part only a human-or-agent reading the code can do:

1. **Wire routes** (the real multiplier). `grep -rn "ProcessInfo.processInfo.arguments"`
   — if the app has DEBUG launch args, map them in the `# EDIT ME` block of
   `script/snap` (grammar `name[:a[:b]]`; unknown routes pass through as raw
   args). If it has none, offer the operator a minimal `#if DEBUG` hook: a
   seed arg that bypasses onboarding with fixture data, and a screen arg that
   picks the initial tab. Compiled out of release builds.
   Give data-heavy routes a longer settle delay in the second `# EDIT ME` block.
2. **Document** — paste `templates/AGENTS.snippet.md` into the app's
   `AGENTS.md` and fill the routes table. The skill carries the method; the
   repo carries the map.
3. **Smoke** — `tools/provision.sh && script/snap`, then READ the png.
4. **`.gitattributes`** — if `*.json filter=lfs` exists, add
   `.claude/**/*.json !filter !diff !merge text` or the settings file becomes an LFS pointer.
5. **Commit** the installed files.

Route maps from real projects, for shape:

```bash
# quran-tools (reader app): page:<N>, study:<N>:<S>:<A>, panel[:<N>], root:<id>
study) ARGS+=(-readingMode study -startPage "${A:?}"); [[ -n "$B" ]] && ARGS+=(-targetAyah "$B:${C:?}") ;;
# deenoverdunya (seeded tabs + sheets): today, record-month, card-accepted, excuse-sheet-begin
ARGS=(-uiTestSeed); …; record-month) ARGS+=(-uiTab record -uiRecordView month) ;;
```

New projects from `new-ios-app` get all of this at birth (its scaffold runs
this installer).

## Phase 2 — The loop

For each UI change requested:

1. **Edit** the code. (Tests first when the change is logic-bearing —
   formatters, layout math — lock the behavior, then snap.)
2. **Snap**: `script/snap <route>` (first run builds; `--no-build` for
   re-snaps, ~15s). It prints two paths: the `-1x.png` (points == pixels,
   cheap to read, tap coordinates read straight off it) and the full-res.
3. **Navigate by the accessibility tree, judge by the screenshot.**
   `axe describe-ui --udid $(cat .ios-sim)` tells you what is on screen and
   where; `axe tap --label …` drives it. Screenshots are for *judgment*, not
   for finding buttons.
4. **Judge it yourself** — Read the png. Compare against the project's design
   system (`DESIGN.md` or equivalent) and the operator's stated intent.
   Iterate until *you* think it's right. **Cap unattended visual iterations
   at ~3** — past that, agents praise their own work and stop seeing it;
   show the operator instead.
5. **Show candidates**: the best 1–3 via SendUserFile; inside cmux
   (`CMUX_SOCKET_PATH` set), also `cmux browser open "file://…"`.
6. **Operator picks / annotates** → refine.
7. **Device**: on approval, `script/device` puts it on their iPhone for the
   feel-check (haptics, scroll, materials — things the sim can't show).

The Stop hook builds when Swift or `project.yml` changed this turn and feeds
compile errors back — fix them, don't argue with it. A route proves the
screen renders, not that navigation reaches it: verify the real user path too.

## Phase 3 — Ship

```bash
tools/ship.sh prepare <version> <build>      # or: prepare patch|minor|major|build  [--changelog]
tools/ship.sh archive                        # STOP here: show the operator IPA size + preflight
tools/ship.sh upload                         # only on the operator's explicit GO
```

`upload` is its own invocation on purpose — the GO token is structural.
Details, hooks the project can provide (preflight survey, `script/test`,
attach-to-TestFlight), uploader selection (altool vs `fastlane ios ship`) and
the tag-after-upload rule are in the script header: `grep '^# ' tools/ship.sh`.
The tag is never pushed by the script.

## Doctor

```bash
~/.claude/skills/ios-dev-loop/scripts/doctor.sh
```

Run when builds behave strangely across projects or after an Xcode / tool
update. Checks the machine, prints fixes, applies none: Xcode build location
must be `Unique` (otherwise every worktree's products land in one shared
`Build/Products` and sessions overwrite each other's `.app`), selected Xcode
(26.x for the Claude Desktop simulator pane), tool presence + versions vs
`brew outdated`, Axiom plugin version, disk free, Chrome `code_sign_clone`
bloat, build-slot state, stuck D-state build processes (usually `syspolicyd`).

## Rollout / migration (applying to a project that already has an older loop)

`quran-tools` shipped the first copy of these scripts under project-specific
names. When you apply the installer there, retire the old copies in the same
change or the two will fight:

- **Build slot.** The old `tools/build-slot.sh` locked
  `/Volumes/AOS-X/tmp-build/build-slot.lock`; this one locks
  `${TMPDIR:-/tmp}/ios-build-slot.lock`. Two different lock paths mean builds
  **stop serializing across projects**. Re-run `install.sh --force` (or set
  `BUILD_SLOT_LOCK` to one shared path everywhere) so every project shares one slot.
- **Provision + worktree.** `tools/qt-provision.sh` / `tools/qt-worktree.sh`
  lease into `.qt-sim`; `provision.sh` / `worktree.sh` lease into `.ios-sim`.
  Leaving both, plus the old `qt-provision.sh` SessionStart line, **leases two
  simulators per worktree**. Delete the `qt-*` scripts and their settings line
  when the installer's versions land.
- **Sanity check after:** `tools/build-slot.sh --status` and `ls .qt-sim .ios-sim`
  — exactly one lease file, one lock path.

## Gotchas (hard-won — trust these)

| Symptom | Cause / fix |
|---------|-------------|
| Screenshot fails "Operation not permitted" | CoreSimulator can't write to external volumes/TCC paths — `snap` stages via `/tmp` (keep that). |
| Screenshot is blank / white | Settle delay too short for that screen (corpus build, font registration). Raise it in `snap`'s second `# EDIT ME` block or `SNAP_DELAY=9`. |
| Snap shows the OLD ui / wrong screen | `simctl terminate` is async and a plain relaunch silently no-ops. `snap` sleeps 1s and launches with `--terminate-running-process`. Verify args landed: `ps aux \| grep <App>.app`. |
| `unbound variable` on launch under bash 3.2 | `"${ARGS[@]}"` on an empty array trips `set -u`. Use `${ARGS[@]+"${ARGS[@]}"}` (the template does). |
| Two sessions clobber each other's `.app` | Xcode build location was `Custom` (shared products). `doctor.sh` catches it; fix is `defaults write com.apple.dt.Xcode IDEBuildLocationStyle Unique`. Never pass `BUILD_DIR`/`SYMROOT` — it breaks `xcodebuild archive`. |
| Build hangs at SwiftPM resolve, machine-wide | Concurrent cold resolves wedge `syspolicyd`. Every build path goes through `tools/build-slot.sh`; `doctor.sh` flags D-state builds. |
| `git worktree remove` refuses a clean worktree | It holds an initialized submodule. `tools/worktree.sh --remove` falls back to `rm` + `git worktree prune`. |
| `.claude/settings.json` shows as an LFS pointer | `*.json filter=lfs` in `.gitattributes`. Exempt config JSON: `.claude/**/*.json !filter !diff !merge text`. |
| Two simulators leased per worktree, builds not serializing | An older `qt-*` loop is still installed alongside this one — see Rollout / migration. |
| Fresh worktree builds green but the app has no data / stale project | Empty submodule or `project.yml` newer than the tracked pbxproj. `tools/provision.sh` (SessionStart) handles both. |
| Device "connected (no DDI)" | Developer disk image mounts only while the phone is **unlocked**. Unlock, wait ~30s, retry. |
| `devicectl` finds no device | One-time USB pairing + Trust + Developer Mode. After that Wi-Fi works (same network). |
| Launch args ignored | Debug hooks are `#if DEBUG` — confirm the build configuration is Debug. |
| Wrong scheme picked | Multi-scheme projects: `SCHEME=<name>` env, or set it in `.xcodebuildmcp/config.yaml` (the Stop hook reads it too). |
| PreToolUse blocks a pbxproj edit | XcodeGen project: edit `project.yml`, run `xcodegen`. Plain projects only get a warning. |

## Boundaries

- **Sim is for agents, the phone is for the operator.** Don't drive the
  physical device for iteration; deploy to it at milestones.
- **One session, one worktree, one simulator.** Never build in the canonical
  checkout of a worktree-model repo; `tools/provision.sh` refuses to provision it.
- TestFlight enters only when shipping to other humans (Phase 3); `upload`
  only on explicit GO; pushing tags and merging to `main` are the operator's calls.
- Never `rm -rf DerivedData` to "fix" a build — reach for `axiom-build` first.
