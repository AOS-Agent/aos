# Release Channels

AOS ships on two lanes so day-to-day churn doesn't land on other people's
machines.

| Channel  | Tracks              | Who        | Cadence          |
|----------|---------------------|------------|------------------|
| `edge`   | `origin/main` HEAD  | operator   | same-day (4am)   |
| `stable` | the `stable` git tag| friends    | promoted only    |

## How a machine picks its lane

`~/.aos/config/channel` holds a single line: `edge` or `stable`. If the file is
absent or holds anything else, the machine resolves to **stable** — the safe
lane. That's the whole backward-compat story: a machine that merely receives
this code lands on stable with no operator action.

All resolution rules live in `core/lib/channels.py` (pure, unit-tested). The
update scripts (`core/bin/crons/check-update`, `core/bin/internal/release-manager`)
shell out to it for the target ref and hash.

- **edge** → deploys `origin/main` HEAD (today's behavior).
- **stable** → deploys the commit the `stable` tag points at.
- **stable, but no `stable` tag yet** → falls back to `origin/main` with a log
  note, so a machine never strands itself before the first promotion.

## Promotion

The operator runs `aos promote` on the edge machine. It promotes the commit that
machine is *currently running*:

1. **Soak** — the running release must have been deployed ≥ N days ago
   (default 2; `--days N` to change, `--force` to override). Soak time is the
   mtime of `~/.aos/data/deployed-hash`, written at activation.
2. **On-main** — the candidate must be an ancestor of `origin/main`.
3. **Quality** — `ship-check` (blocking failures stop it) and `aos self-test`.
4. **Confirm** — prints the plan and requires a typed `yes`.

Then it moves the `stable` tag to that commit and pushes **only the tag**
(`git push origin +refs/tags/stable` — never main, never a branch) and posts a
Telegram note. Friend machines pick it up at their next 4am update.

## Commands

```
aos channel              # show this machine's channel
aos channel edge         # set channel
aos promote              # promote running release to stable (all guards)
aos promote --days 3     # require a 3-day soak
aos promote --force      # skip the soak guard (still confirms)
```

## First-time setup

The `stable` tag must exist before any stable machine can track it (until then
they fall back to main). Create it once at ship time, pointed at the shipped
commit. The operator's machine is flipped to edge with `aos channel edge`.
