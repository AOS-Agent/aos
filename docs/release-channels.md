# Release Channels

AOS runs on one machine — this Mini — and it tracks `edge`. The two lanes
below are how a release is cut, soaked and tagged; they are not a rollout to
anybody else.

| Channel  | Tracks              | Cadence          |
|----------|---------------------|------------------|
| `edge`   | `origin/main` HEAD  | same-day (4am)   |
| `stable` | the `stable` tag    | promoted only    |

`stable` has no machine on it today. It is kept because the promotion guard is
what makes a release a release — a soak window, an on-main check, ship-check and
self-test, and a tag that records which commit was judged good. Delete the lane
and that record goes with it.

## How a machine picks its lane

`~/.aos/config/channel` holds a single line: `edge` or `stable`. If the file is
absent or holds anything else, the machine resolves to **stable** — the safe
lane. An install that has declared nothing cannot be handed same-day churn.

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
Telegram note.

## Commands

```
aos channel              # show this machine's channel
aos channel edge         # set channel
aos promote              # promote running release to stable (all guards)
aos promote --days 3     # require a 3-day soak
aos promote --force      # skip the soak guard (still confirms)
```

## Freeze

From v0.8.0 this machine is **frozen**: `frozen: true` in
`~/.aos/config/update-policy.yaml` (written by migration 117) means the updater
offers patches and nothing else. "Patch" is decided from the VERSION numbers —
same `major.minor`, higher `patch` — not from trust in the sender, and a
candidate whose version cannot be read is offered *nothing*, because failing
open here would push an unidentified release onto a machine that asked to stop
receiving them.

The freeze sits on top of the channel: the channel decides *which* commit is a
candidate, the freeze decides whether a candidate may be offered at all. Both
live in `core/lib/channels.py` and are unit-tested in `tests/test_freeze.py`.

Reverse it with `frozen: false`, or by deleting the file.

## First-time setup

The `stable` tag must exist before a stable machine can track it (until then it
falls back to main). Create it once at ship time, pointed at the shipped commit.
This machine is flipped to edge with `aos channel edge`.
