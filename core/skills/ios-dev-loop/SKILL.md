---
name: ios-dev-loop
description: >
  Fast agentic UI iteration for any iOS app: snapshot any screen on the
  simulator, self-judge against the design system, show the operator
  candidates, deploy to their physical iPhone — no TestFlight in the loop.
  Trigger on "iterate on the UI", "snap the screen", "show me the screen",
  "deploy to my phone", "put it on my phone", "set up the dev loop",
  "screenshot the app", or whenever making visual changes to an iOS app
  that should be verified by looking, not guessing. Self-scaffolding:
  if the project has no script/snap or script/device, this skill wires
  them up first — works out of the box on any Xcode project.
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, AskUserQuestion
---

# ios-dev-loop — see what you ship

The loop: **edit → snap → judge → show → device**. The agent carries the
inner loop (build, screenshot, self-verify); the operator enters only for
taste calls and the final feel-check on real hardware. TestFlight is for
distributing to *other people* — never for testing a change to the app being built.

## Phase 0 — Detect (always run first)

```bash
ls script/snap script/device 2>/dev/null
```

- **Both exist** → skip to Phase 2. Project-specific scripts always win
  over the templates — read them to learn this project's routes.
- **Missing** → Phase 1.
- Also read the project's `AGENTS.md`/`CLAUDE.md` for documented routes,
  schemes, or a dedicated screenshot simulator.

## Phase 1 — Scaffold (only when missing)

1. Find the app root: the directory containing `*.xcodeproj`,
   `*.xcworkspace`, or `project.yml` (monorepos often keep it in `ios/`).
2. Copy the templates and make them executable:
   ```bash
   mkdir -p script
   cp ~/.claude/skills/ios-dev-loop/scripts/snap script/snap
   cp ~/.claude/skills/ios-dev-loop/scripts/device script/device
   chmod +x script/snap script/device
   ```
   The templates are **self-discovering** (scheme, bundle id, container,
   simulator, device) — they run with zero edits on a single-app project.
3. Smoke-test: `./script/snap` then READ the png it prints. If the app
   shows onboarding/permission gates, proceed to step 4.
4. **Wire routes** (the real multiplier). Check whether the app already
   has debug launch args (`grep -rn "ProcessInfo.processInfo.arguments"`).
   - If yes: map them in the `case "$ROUTE"` block of `script/snap`.
   - If no: offer the operator a minimal `#if DEBUG` launch-arg hook —
     a seed arg that bypasses onboarding with fixture data, and a screen
     arg that picks the initial tab/screen. Keep it tiny and compiled out
     of release builds.
5. Document the routes in the project's `AGENTS.md` (a "Dev loop" section:
   routes, sim name if dedicated, anything non-default). The skill carries
   the method; the repo carries the map.
6. Commit the scripts.

## Phase 2 — The loop

For each UI change requested:

1. **Edit** the code.
2. **Snap**: `./script/snap <route>` (first run builds; use `--no-build`
   for re-snaps after the incremental build, ~15s).
3. **Judge it yourself** — Read the png. Compare against the project's
   design system (`DESIGN.md` or equivalent) and the operator's stated
   intent. Iterate until *you* think it's right. Do not show the operator
   every intermediate step.
4. **Show candidates**: send the best 1–3 via SendUserFile; if inside
   cmux (`CMUX_SOCKET_PATH` set), also `cmux browser open "file://…"`.
5. **Operator picks / annotates** → refine if needed.
6. **Device**: on approval, `./script/device` puts it on their iPhone for
   the feel-check (haptics, scroll, materials — things the sim can't show).

Tests first when the change is logic-bearing (formatters, layout math):
update the unit tests to lock the new behavior before editing, then snap.

## Gotchas (hard-won — trust these)

| Symptom | Cause / fix |
|---------|-------------|
| Screenshot fails "Operation not permitted" | CoreSimulator can't write to external volumes/TCC paths — templates stage via `/tmp` (keep that). |
| Snap shows the OLD ui / wrong screen | `simctl terminate` is async; racing it relaunches the stale instance. Templates `sleep 1` between terminate and launch. Verify args landed: `ps aux \| grep <AppName>.app`. |
| Device "connected (no DDI)" | Developer disk image mounts only while the phone is **unlocked**. Unlock, wait ~30s, retry. |
| `devicectl` finds no device | One-time USB pairing + Trust + Developer Mode. After that Wi-Fi works (same network). |
| Launch args ignored | Debug hooks are `#if DEBUG` — confirm the build configuration is Debug. |
| Wrong scheme picked | Multi-scheme projects: set `SCHEME=<name>` env or hardcode in the script header. |

## Boundaries

- **Sim is for agents, the phone is for the operator.** Don't drive the
  physical device for iteration; deploy to it at milestones.
- TestFlight enters only when shipping to other humans (see `new-ios-app`
  / project `script/ship` for that path).
- New projects scaffolded by `new-ios-app` should get these scripts at
  birth — if you're in that flow, copy them during scaffold.
