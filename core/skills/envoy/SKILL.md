---
name: envoy
description: Commission an autonomous outbound conversation — the system texts a real person over iMessage, introduces itself as the operator's AI agent, and goes back and forth until the mission is done, escalating when unsure. Trigger on "reach out to X and…", "message X and get/arrange/ask…", "have a conversation with X", "talk to X for me", "send your agent to X", "envoy", "handle this over text with X", or any request where the operator delegates a goal-directed text conversation with a third party. Works identically when the request arrives via Telegram bridge. NOT for one-off messages the operator dictates verbatim (just send those), and NOT for replying in the operator's own voice (that is Sentinel's domain).
---

# Envoy — Autonomous Outbound Conversations

The operator gives you a mission involving a real person; envoy runs the
conversation to completion. It always **introduces itself as the operator's AI
agent** — it never impersonates the operator (that's Sentinel's reactive,
operator-voice domain; envoy is proactive and transparent).

## Architecture (already built — do not reinvent)

- Engine: `core/engine/comms/envoy/` — store, persona prompts, headless-turn
  runner, CLI. State per conversation in `~/.aos/work/envoy/<id>/`
  (mission.yaml, state.json, transcript.jsonl — all inspectable).
- Daemon: `com.aos.envoy` LaunchAgent, 5-min poll, auto-installed on first
  `envoy start`. Survives session death. No active conversations → instant no-op.
- Detection: inbound replies via `~/.aos/data/comms.db` (Sentinel's ingest).
- Sending: the comms iMessage AppleScript path. Notifications: Telegram.
- Intelligence: each turn is a headless `claude --print` call returning a
  strict JSON action: `reply | wait | complete | escalate`.

## Protocol

### 1. Resolve the mission (don't interrogate)

You need four things. Infer what you can; ask ONE question only if something
critical is genuinely ambiguous:

- **Contact**: phone/iMessage email. If the operator gives a name, resolve it
  from comms.db (`SELECT sender_id FROM messages WHERE ...`) or contacts.
  Never guess a number.
- **Mission**: what the conversation should accomplish, in one paragraph.
  Include any context the envoy needs (links, deadlines, facts).
- **Success criteria**: how the envoy knows it's done. Make it observable —
  "she confirms a date and time", not "she's happy".
- **Constraints** (optional): tone, topics to avoid, offers it may/may not make.

### 2. Confirm before first contact

Outbound messaging to a third party is externally visible. Show the operator a
one-line summary — contact, mission, success — and get a go-ahead **unless the
operator's request was already fully specified and imperative** ("message X
and walk him through installing the build" = already a go).

### 3. Launch

```bash
~/aos/core/bin/cli/envoy start \
  --to "<E.164-number>" --name "Firstname" \
  --mission "Help him get the Quran Garden TestFlight build installed. Context: ..." \
  --success "He confirms the app is on his home screen" \
  --constraints "Don't promise release dates"
```

The kickoff turn runs inline — report the actual intro message that was sent.
Useful flags: `--max-messages N` (default 12), `--expires-days N` (default 5),
`--dry-run` (compose but never send — use for testing).

### 4. Report and step back

Tell the operator: intro sent, daemon polling every 5 min, and that they'll
get Telegram updates on completion/escalation. Do NOT poll from the session —
the daemon owns the conversation now.

### 5. Managing running conversations

```bash
~/aos/core/bin/cli/envoy list            # all conversations + phase
~/aos/core/bin/cli/envoy show <id>       # mission, state, full transcript
~/aos/core/bin/cli/envoy stop <id>       # kill switch
~/aos/core/bin/cli/envoy resume <id>     # un-pause after escalation/cap
~/aos/core/bin/cli/envoy run-once        # force a poll cycle now
```

After an **escalation** (envoy paused, operator notified): discuss with the
operator, optionally append guidance to the mission constraints (edit
`~/.aos/work/envoy/<id>/mission.yaml`), then `envoy resume <id>`.

## Guardrails (baked into the engine — know them)

- Disclosure is mandatory: first message always identifies as the operator's
  AI assistant. Never disable this.
- One contact per conversation; envoy cannot message anyone else.
- Never commits the operator to money, meetings, or promises outside the
  mission — those auto-escalate.
- Caps: max_messages (default 12) then pause; expiry (default 5 days) then
  notify. Three consecutive turn failures → Telegram alert.
- Everything is logged: transcript.jsonl per conversation + comms.db ingest.

## When NOT to use

- Operator dictates the exact message → just send it (osascript), no envoy.
- Replying to threads in the operator's voice → Sentinel.
- Conversation with the operator themselves → you're already having it.
- Bulk/broadcast messaging → refuse; envoy is 1:1 and mission-scoped.
