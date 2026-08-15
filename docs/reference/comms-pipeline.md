---
globs:
  - "core/comms/**"
  - "core/engine/comms/**"
  - "core/services/comms_bus/**"
  - "core/bin/cli/message-person"
  - "core/bin/crons/enrich-comms"
  - "core/qareen/schemas/comms.sql"
description: Comms pipeline architecture — unified message store, bus, trust cascade, messaging
---

# Communications Pipeline

Two databases, one bus, one loop.

```
comms.db (~/.aos/data/comms.db)     — CONTENT: 248K+ messages, full text, FTS5
people.db (~/.aos/data/people.db)   — IDENTITY: 1,148 people, aliases, identifiers
```

**comms.db** is the unified cross-channel message store. Every message (WhatsApp,
iMessage, email, Slack, SMS, Telegram) lives here with full content, resolved
`person_id`, and FTS5 full-text search index. Schema: `core/qareen/schemas/comms.sql`.

**people.db** is the identity layer. Maps any handle (phone, JID, email, Slack ID)
to a canonical person via a 5-tier resolver (alias → exact → frequency → phonetic → fuzzy).
Schema: `core/engine/people/schema.sql`.

## The Loop

```
INBOUND (comms-bus service, every 5 min):
  Channel adapters poll → CommsStoreConsumer writes to comms.db
  → PeopleIntelConsumer logs interactions to people.db
  → CommsOrchestrator runs trust cascade (L0 observe → L3 autonomous)

OUTBOUND (message-person CLI):
  Resolve person → pull context from comms.db → pick channel (active conversation)
  → send via adapter → write outbound to comms.db

ENRICHMENT (nightly cron):
  Unprocessed messages → batch by person+day → Haiku extracts topics/intent/summary
  → message_entities table → messages.processed = 1
```

## Recall — the agent-facing way to search comms

When a conversation references **a person or a topic from the past** — "what did
I tell Faisal about the lease", "did we ever discuss the Berlin trip", "when did
that order ship" — reach for the **recall tool** instead of writing raw SQL. It
is the single query facade over the message history (like `qmd query` is for the
vault), with access control enforced inside it.

```bash
comms-recall search "berlin trip" --limit 10          # by keywords (FTS5)
comms-recall search "lease" --person "Faisal"          # keywords + person
comms-recall person "my mom" --since 2026-06-01         # a person's messages
comms-recall search --since 2026-07-01 --until 2026-07-07  # a timeframe
comms-recall get im-223330                              # expand one message
comms-recall search "iftar" --json                      # agent-consumable rows
```

Every result row is the same contract — `{entity, confidence, source_refs, scope}` —
so you always know where a fact came from (`source_refs`) and how sensitive it is
(`scope`). Results are snippet-first and bounded (default 20, max 100); expand a
single message to full text with `comms-recall get <message_id>`. Confidence is
`1.0` for these verbatim hits (v1 is FTS + person + timeframe, no inference).

**Privacy is enforced in the tool, not by you.** Restricted contacts
(`privacy_level >= 2`) are excluded by default. `--include-private` is an
explicit, operator-only override — never pass it on a contact's behalf.

Engine: `core/engine/comms/recall.py`. CLI: `core/bin/cli/comms-recall`.

## How to Search Comms (raw SQL — prefer `comms-recall` above)

```sql
-- Keyword search (sub-millisecond via FTS5):
SELECT * FROM messages_fts WHERE messages_fts MATCH 'ramadan'

-- Person-scoped search:
SELECT * FROM messages WHERE person_id = 'p_xxx' AND content LIKE '%topic%'

-- Topic search (after enrichment):
SELECT m.* FROM message_entities me JOIN messages m ON me.message_id = m.id
WHERE me.entity_id = 'family'
```

## Key Files

| File | What |
|------|------|
| `core/services/comms_bus/main.py` | Always-on polling daemon (port 4099) |
| `core/comms/consumers/comms_store.py` | Bus → comms.db writer |
| `core/comms/consumers/people_intel.py` | Bus → people.db interactions |
| `core/engine/comms/orchestrator.py` | Trust cascade (L0-L3) |
| `core/engine/comms/channels/*.py` | Channel adapters (6 channels) |
| `core/engine/people/resolver.py` | 5-tier contact resolution |
| `core/engine/comms/recall.py` | Recall engine — verbatim query facade + in-tool access control |
| `core/bin/cli/comms-recall` | Recall CLI (search / person / get) |
| `core/bin/cli/message-person` | Outbound messaging CLI |
| `core/bin/crons/enrich-comms` | Nightly topic/intent extraction |
| `core/comms/tests/smoke.md` | 10 smoke tests for the pipeline |

## Trust Cascade

Per-person trust levels in `~/.aos/config/trust.yaml`:
- **L0 OBSERVE**: Log interaction only
- **L1 SURFACE**: Alert operator about important messages
- **L2 DRAFT**: Generate reply, operator approves
- **L3 AUTONOMOUS**: Auto-send if confidence >= 85%
