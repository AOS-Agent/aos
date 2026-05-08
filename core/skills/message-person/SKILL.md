---
name: message-person
description: "Send a message to a person by name — auto-picks the best channel (WhatsApp, iMessage, Telegram, Slack, email). Resolves the recipient via People DB, previews, confirms with the operator, then sends. TRIGGER when the operator says 'message <name>', 'text <name>', 'whatsapp <name>', 'dm <name>', 'email <name>', 'tell <name> that…', 'send <name> a message', 'let <name> know…', or any instruction whose intent is to deliver a message to another human by name."
---

# Skill: message-person

Operator says "message \<name\> that I'll be late" → you resolve the person via the People DB, auto-pick the best channel, show a preview, get confirmation, and send.

**All the plumbing exists.** This skill is a thin orchestration layer. Do not call adapters or the resolver directly — use the CLI at `core/bin/cli/message-person`.

## Core rule: always confirm before sending

Sending a message to another person is a visible, shared-state action. **You MUST preview and get explicit operator approval before every send.** No exceptions.

The operator can override per-session by saying "just send it" or "don't ask" — in that case, send without confirmation for the immediate request only. Never carry an override across turns.

## Channels

The system supports 5 channels. The CLI auto-picks based on verb hint + person's identifiers + availability:

| Channel | Adapter | Identifier needed | Setup status |
|---------|---------|-------------------|-------------|
| **WhatsApp** | whatsmeow bridge (localhost:7601) | `wa_jid` | Ready (bridge running) |
| **iMessage** | AppleScript → Messages.app | `phones` or `emails` | Ready (macOS native) |
| **Telegram** | Bot API (token in Keychain) | `telegram_id` | Ready (but Bot API can only message users who've messaged the bot first) |
| **Slack** | Web API (user or bot token) | `slack_user_id` | Ready (bot token configured; `users:read` scope needed for member discovery) |
| **Email** | gws CLI primary, SMTP fallback | `emails` | Needs setup (`brew install googleworkspace-cli` or SMTP creds) |

## Verb-to-channel intent map

The operator's phrasing hints at which channel to use:

| Operator says | Hint | Channel |
|---|---|---|
| "whatsapp \<name\>", "wa \<name\>" | whatsapp | WhatsApp |
| "text \<name\>", "itext \<name\>" | text | iMessage |
| "imessage \<name\>" | imessage | iMessage |
| "telegram \<name\>", "tg \<name\>" | telegram | Telegram |
| "dm \<name\>", "slack \<name\>" | dm/slack | Slack |
| "email \<name\>", "mail \<name\>" | email | Email |
| "message \<name\>", "tell \<name\>", "send \<name\>", "let \<name\> know" | (none) | Auto-pick |

**Auto-pick** (when no verb hint): The CLI queries the person's communication history from the signal store and picks the channel with the most messages. "Message Ramadan" → iMessage (25,000 msgs) not WhatsApp (48 msgs). Falls back to static priority (WhatsApp → iMessage → Telegram → Slack → Email) for new contacts with no history.

The person's `preferred_channel` metadata overrides auto-pick when set.

## The CLI

Always invoke by full path. Prefer the runtime copy; fall back to dev:

```
~/aos/core/bin/cli/message-person           # runtime (post-ship)
~/project/aos/core/bin/cli/message-person   # dev workspace (pre-ship)
```

Key commands:

```bash
# Dry-run: resolve + pick channel + preview (no send)
~/aos/core/bin/cli/message-person --to "<name>" --text "<body>" --dry-run --json

# With verb hint (skill passes the operator's verb)
~/aos/core/bin/cli/message-person --to "<name>" --hint text --text "<body>" --dry-run --json

# With enhancement override
~/aos/core/bin/cli/message-person --to "<name>" --text "<body>" --enhance elevated --dry-run --json

# Explicit channel override
~/aos/core/bin/cli/message-person --to "<name>" --channel slack --text "<body>" --dry-run --json

# Bypass resolver (direct addressing)
~/aos/core/bin/cli/message-person --phone "+1 555-0100" --text "<body>"
~/aos/core/bin/cli/message-person --jid "15550100@s.whatsapp.net" --text "<body>"
~/aos/core/bin/cli/message-person --email "alice@example.com" --text "<body>" --subject "Hello"

# Actual send (no --dry-run)
~/aos/core/bin/cli/message-person --to "<name>" --text "<body>"
```

**Exit codes:** 0=sent/preview, 1=send failed, 2=unresolved/ambiguous/no channel, 3=bad args.

**JSON output includes:** `chosen_channel`, `why_chosen`, `draft_id`, `contact`, `recipient`, `confidence`.

## Procedure

### Step 1 — Parse the request

Extract from the operator's message:
- **Recipient reference** — name, alias, or relationship
- **Message body** — what to send (may be absent)
- **Verb hint** — the operator's verb maps to a preferred channel (see table above)

Be conservative with the body. Send what they said, not a rewrite. If they said "tell \<name\> that Y" → the body is "Y" phrased naturally. Don't add greetings or sign-offs unless the operator included them.

- **Enhancement cues** — detect if the operator implies a style level:
  - "raw", "exactly as I'd write it", "just like me" → `--enhance raw`
  - "clean it up", "fix the grammar" → `--enhance clean`
  - "make it formal", "professional", "tighten it up" → `--enhance elevated`
  - No cue → use person's default (usually "clean")

### Step 2 — Resolve + pick channel (dry-run)

Run a `--dry-run --json` first to resolve the person and auto-pick the channel:

```bash
~/aos/core/bin/cli/message-person --to "<name>" --hint "<verb>" --text "" --dry-run --json
```

Parse the JSON output. Handle each status:

| Status | Action |
|--------|--------|
| `preview` | Proceed — person resolved, channel picked |
| `ambiguous` | List candidates, ask the operator which one. Note: first-name references (e.g., "faisal") auto-prefer the highest-importance match, so ambiguity is rare for inner-circle contacts. |
| `unresolved` | Tell the operator, ask for a phone/email/JID to bypass |
| `no_channel` | Person found but no identifier for any channel. Show what's on file, ask how to reach them. |
| `adapter_unavailable` | Channel's service is down. Report and stop. |

### Step 3 — Get the body (if missing)

If no message body was in the original instruction:

> "Found **\<name\>** — will send via \<channel\>. What should I say?"

One question. Don't draft or suggest unless asked.

### Step 4 — Preview + confirm

Show the operator exactly what will be sent:

> → **\<Channel\>** to **\<name\>** (\<identifier\>)
> "\<body\>"
> **Send?**

Include the channel name prominently — the operator should know HOW the message will be delivered. Wait for explicit approval.

### Step 5 — Send

On approval, run without `--dry-run`:

```bash
~/aos/core/bin/cli/message-person --to "<name>" --hint "<verb>" --text "<body>"
```

Report the result:
- Success: "✓ Sent via \<channel\> to \<name\>."
- Failure: Report the error. Do NOT retry automatically — the message may have partially delivered.

### Step 6 — Stop

Unless the operator chains another message, stop. No unsolicited follow-ups.

## Edge cases

**Multiple recipients** — resolve and send in sequence, previewing each separately.

**Operator says a channel name but the person lacks that identifier** — the CLI reports `no_channel`. Tell the operator, suggest what IS available.

**Auto-pick chose a surprising channel** — the `why_chosen` field explains the decision. Show it if the operator seems confused.

**Email needs a subject** — if the operator didn't specify one, the CLI auto-generates from the first line of the body. You can also pass `--subject "..."` explicitly.

**Operator gives a phone/email/JID directly** — use `--phone`, `--email`, or `--jid` to bypass the resolver. Still confirm before sending.

**Group messages** — out of scope. Groups use different identifiers and code paths.

**Operator asks to reply to someone's last message** — this skill does NOT fetch conversation context. Offer to send a fresh message.

## Per-channel limitations

- **Telegram**: Bot API can only message users who've started a conversation with the bot. If the person has never messaged the AOS Telegram bot, the send will fail.
- **Slack**: Requires `slack_user_id` in the People DB. Run the Slack member discovery (`python3 -m core.engine.people.intel.sources.slack --apply`) after adding `users:read` scope to the Slack app.
- **Email**: Requires either gws CLI (`brew install googleworkspace-cli`) or SMTP credentials (`agent-secret set EMAIL_SMTP_PASSWORD <app_password>`).
- **iMessage**: Requires macOS Automation permission for Messages.app. Green bubble (SMS) fallback happens automatically for non-iMessage recipients.

## Draft-first persistence

Every message attempt (preview or send) is logged to `~/.aos/data/comms-drafts.jsonl` with a `draft_id`, status, channel, recipient, and body. This provides a paper trail of all messaging activity. Use `--no-draft` to suppress logging for test runs.

## Style Intelligence

When a person has a computed style profile (≥20 outbound messages analyzed by the nightly `compute-style-intelligence` cron), the system automatically:

- **Detects conversational mode** — banter, transactional, deep, warm, or professional — based on the current thread texture + topic
- **Pulls exemplar messages** that match the detected mode — real messages the operator sent in this mode
- **Applies enhancement dial** — raw (exact replication), clean (fix grammar only), or elevated (tighter/sharper)

The preview JSON includes `style_info.has_style` and `style_info.enhancement` when style intelligence is active. Show it in the preview:

> → **WhatsApp** to **Alice** (style: banter, clean)
> "yalla can you send me the deck?"
> **Send?**

If no style profile exists (new contact or <20 messages), drafting proceeds normally without style intelligence.

## What this skill does NOT do

- Draft message content for the operator (unless they ask)
- Read incoming messages or conversations
- Manage threads or reply chains
- Send to groups
- Auto-respond or monitor for replies
- Switch channels mid-conversation

Scope: **operator says who + what → you pick the channel, confirm, send.** That's the whole skill.
