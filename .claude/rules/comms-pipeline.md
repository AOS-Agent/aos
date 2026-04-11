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

## How to Search Comms

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
| `core/bin/cli/message-person` | Outbound messaging CLI |
| `core/bin/crons/enrich-comms` | Nightly topic/intent extraction |
| `core/comms/tests/smoke.md` | 10 smoke tests for the pipeline |

## Trust Cascade

Per-person trust levels in `~/.aos/config/trust.yaml`:
- **L0 OBSERVE**: Log interaction only
- **L1 SURFACE**: Alert operator about important messages
- **L2 DRAFT**: Generate reply, operator approves
- **L3 AUTONOMOUS**: Auto-send if confidence >= 85%
