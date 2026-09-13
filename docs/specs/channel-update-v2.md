# channel-update v2 — spec stub

**Status:** not built. v1 deleted 2026-09-13 (aos#235). This is the one
paragraph worth keeping from 500 lines.

**Why v1 died.** Disabled in `crons.yaml` for months; its bridge check probed
`apps/bridge/main.py`, a path that has not existed since the service moved, so
every hourly run reported the bridge DOWN; and it posted raw percentages, file
paths and `<code>` tracebacks — the opposite of `MESSAGE_STYLE.md`.

**The open question.** Under the digest model (quiet hours + one morning
roll-up) an hourly "everything is fine" post is noise by construction: the
heartbeat already speaks on real problems only, and silence is the signal.

**If v2 happens, it is one line, on change only.** Not hourly. Not a dashboard
in a chat. It says what *changed* since the last post ("🟢 Back to normal" /
"🟡 Disk crossed 85%"), reads its facts from the service registry and the
reconcile log rather than re-implementing the checks, and goes through
`humanize_notice` like every other sender. Health detail stays in the log.

**Decide first:** does anything the operator needs fail to reach them today via
heartbeat, briefing, and the quiet-hours digest? If not, v2 is a no.
