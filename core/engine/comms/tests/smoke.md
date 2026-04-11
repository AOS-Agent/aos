# Comms Pipeline Smoke Tests

Run these in a fresh Claude Code session with zero context. Each test should work without explanation. If any fail, log what happened and file an issue.

## Test 1: Search comms by keyword

```
Search my comms for "ramadan" — show me the top 5 most recent messages across all channels.
```

Expected: Returns messages from comms.db via FTS5. Multiple channels (WhatsApp, iMessage, email). Sub-second.

## Test 2: Person-scoped message search

```
What has Mama sent me in the last 30 days?
```

Expected: Resolves "Mama" via alias → person_id → queries comms.db by person_id. Shows messages with timestamps and channels.

## Test 3: Conversation context pull

```
Show me my recent conversation with Zeeshan Bari.
```

Expected: Pulls last ~10 messages from comms.db for that person. Shows both inbound and outbound. Chronological order.

## Test 4: Send message with context

```
message-person --to "Ahmad" --text "I'll be there at 3" --dry-run --json
```

Expected: JSON output includes `context` field with recent messages from Ahmad. Channel chosen based on active conversation (not just lifetime history). Preview, not sent.

## Test 5: Comms bus health

```
Check if the comms bus is running and healthy.
```

Expected: Hits `localhost:4099/health`. Shows adapters (which available), consumers (4), last poll time, poll count.

## Test 6: Cross-channel person timeline

```
Build me a timeline of all communication with Fahad Khan across every channel.
```

Expected: Queries comms.db WHERE person_id = (resolved). Returns messages from WhatsApp, iMessage, email — unified. May surface duplicate person issue (Fahad Khan / Fahd Khan).

## Test 7: Topic search (post-enrichment)

```
Find conversations tagged with the topic "family".
```

Expected: Queries message_entities WHERE entity_type = 'topic' AND entity_id LIKE '%family%'. Joins to messages for content. If enrichment hasn't run on enough data, should say so.

## Test 8: Outbound message lands in comms.db

```
message-person --to "Zeeshan Bari" --text "test message from smoke test" --dry-run --json
```

Then verify: `sqlite3 ~/.aos/data/comms.db "SELECT COUNT(*) FROM messages WHERE direction = 'outbound' AND content LIKE '%smoke test%';"`

Expected: Dry-run doesn't write. Actual send (if done) writes to comms.db.

## Test 9: FTS5 performance

```
Time a full-text search across all 248K+ messages for an uncommon term.
```

Expected: `time sqlite3 ~/.aos/data/comms.db "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'cryptocurrency';"` — under 10ms.

## Test 10: Enrichment dry run

```
python3 ~/aos/core/bin/crons/enrich-comms --dry-run --verbose --limit 20
```

Expected: Groups unprocessed messages by person+day. Shows person names resolved. Reports batch count. No LLM calls (dry run).

---

## How to Log Results

After running tests, log results to: `~/vault/log/comms-smoke-YYYY-MM-DD.md`

```yaml
---
title: Comms Pipeline Smoke Test
type: log
date: YYYY-MM-DD
tags: [comms, testing, smoke-test]
---
```

For each test: PASS/FAIL, actual output, latency, any errors. Failed tests become tasks.
