# aos-app — Project Rules

Unbranded Tauri 2 + React 19 + Tailwind 4 desktop app for AOS (name TBD — never
hardcode a product name). Rust backend in `src-tauri/src/lib.rs`, one-file React
frontend in `src/App.tsx`, logos in `src/logos.tsx`.

## Design law (operator-set, non-negotiable)

1. **Monochrome only.** Black, white, zinc grays. NO blue, purple, emerald,
   amber, or any hue in UI chrome — status dots, pills, buttons, callouts,
   meters are all zinc. The ONLY color on screen comes from third-party brand
   logos. Destructive actions differentiate by copy + weight, not by red.
2. **Mobile-first.** Every screen is designed at 375px width first, then
   enhanced for desktop. Grids collapse to single column, the sidebar must be
   collapsible, touch targets ≥ 40px. This app's UI is headed for phones and
   the workspace client — never assume a wide window.
3. **Native macOS behavior.** Window is draggable from the top strip on every
   screen (`data-tauri-drag-region` + `core:window:start-dragging` permission).
   Cmd+= / Cmd+- / Cmd+0 zoom the content (persisted). Standard shortcuts work.
4. **Nothing static.** Every row is live-probed or actionable (see
   `~/vault/knowledge/specs/connector-system-v2.md` principle 2). A key on file
   is not "in use" — dormancy is computed and shown.

## Engineering rules

- Every feature has a browser demo path (`IN_TAURI` guard + demo data) so it
  reviews at http://localhost:1420 without the Tauri runtime; `?fresh` forces
  the fresh-machine flow.
- Secrets: Keychain via `agent_secret` helper only; never in argv (curl gets
  headers/bodies via stdin), never logged, never in command output.
- Verify before claiming done: `bunx tsc --noEmit` and
  `cargo check` in `src-tauri/` must both pass.
- Commit style: what + why, wrapped at ~72 cols.
- This app lives INSIDE the framework repo at `apps/desktop/`. A change to the
  manifest and to the Rust that parses it belong in one commit; they can no
  longer drift apart across two repositories.
- `src-tauri/modules.yaml` is still a copy of `config/modules.yaml` because
  `include_str!` needs a real file at compile time. Once the manifest lands on
  main it should become a symlink, which removes the last way these can diverge.
- Releases still ship separately from the framework: `aos update` pulls the
  system, `scripts/release.sh` builds, signs, notarizes and publishes the app to
  aos.hish.am. One source of truth, two distribution channels — a notarized
  macOS bundle cannot be delivered by git pull.
