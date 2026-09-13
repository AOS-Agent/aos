# Tracking — work CLI and sbs, in order

Read from `SKILL.md` when creating, closing, handing off, or resuming a step-by-step flow. Two tools, one state: the work system holds the tasks and their done-when criteria; `sbs` reads and writes them.

```
WORK="python3 ~/aos/core/engine/work/cli.py"
SBS="python3 ~/.claude/skills/step-by-step/scripts/sbs.py"
```

## At SCOPE (after approval)

```bash
$WORK add "{Task}" --priority 2                 # → aos#15 (auto-assigns the cwd's project)
$WORK subtask aos#15 "Part 1: {name}"           # → aos#15.1
$WORK subtask aos#15 "Part 2: {name}"           # → aos#15.2 …
$WORK start aos#15
$SBS scope aos#15                               # show the structure with IDs
```

Initiative phase → link the parent so the initiative checkbox follows the cascade:

```bash
$WORK add "{Task}" --source-ref "vault/knowledge/initiatives/{slug}.md"
```

Set `--priority 1` or `2` only when the work is genuinely urgent or important.

## At MAP (per part)

```bash
$WORK start aos#15.2
$SBS trail aos#15 --current aos#15.2
$SBS criteria aos#15.2 --size M --set '$ cmd :: expect' --set '$ cmd' --set 'manual gate'
```

`criteria` rewrites the whole done-when block; re-run with the full set to amend. `--size` persists without repeating it.

## At EXECUTE (per part)

```bash
$SBS verify aos#15.2      # this part — exit 1 on any ❌
$SBS verify aos#15        # every done/active part — the backward check
$WORK done aos#15.2
```

`done` on the last subtask cascades: parent auto-completes, a linked initiative's checkbox updates, the dashboard receives the event. Those are never edited by hand.

A part the operator skipped: `$WORK cancel aos#15.3` — the trail renders it ⏭ and `verify` leaves it out.

## At POLISH

```bash
$SBS verify aos#15
$SBS log --task "{Task}" --parts 4 --mode as-we-go --domain code --work-id aos#15 [--skipped 1 --splits 1 --merges 0 --initiative slug --phase 2]
$WORK show aos#15         # status: done, auto_completed
```

`log` accepts only those keys — fields stay comparable across runs.

## Leaving before POLISH

```bash
$WORK handoff aos#15 \
  --state "Parts 1–2 done and verified; Part 3 half-built: adapter written, router untouched" \
  --next "Finish the router in apps/bridge/router.py, then sbs verify aos#15.3" \
  --files "apps/bridge/adapter.py,apps/bridge/router.py" \
  --decisions "Protocol over ABC|WhatsApp adapter deferred to its own task" \
  --blockers "Needs WHATSAPP_TOKEN in Keychain"
```

Written for the next agent: state, the one next step, decisions — never a log.

## Resuming

```bash
$WORK dispatch aos#15     # handoff + subtask status
$SBS scope aos#15         # which parts are ✅ / 🔶 / ⬜
```

Confirm the pickup point with the operator, then open the next part's brief. Criteria stored at MAP are still on the subtasks; `verify` runs them unchanged.
