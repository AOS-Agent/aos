# Comms Engine

Communication intelligence layer. Resolves contact names and aliases to channel addresses, then routes outbound messages through the appropriate adapter. Supports 340 aliases across contacts. Adapters are pluggable — adding a new channel is a single file under `channels/`.

## Quick Reference
- **Port**: N/A (library)
- **Restart**: N/A
- **Logs**: `~/.aos/logs/comms.log`
- **Config**: `~/.aos/config/comms.yaml`

## Key Files
- `orchestrator.py` — Entry point: accept a message intent, resolve target, dispatch to adapter
- `resolver.py` — Name and alias resolution, 340 aliases (994 lines)
- `recall.py` — Recall engine: verbatim query facade over comms.db + people.db
- `channels/` — One adapter per channel (Telegram, Slack, email, SMS)

## Recall (Ambient Knowledge, Phase 1)

`recall.py` is the on-demand retrieval facade over the message history — the
answer to "I think I talked to this person about this thing." It queries
`comms.db` by **keywords** (FTS5 `MATCH`), **person** (any handle/name/alias →
the 5-tier resolver → `person_id`), and **timeframe** — combinable — and returns
a bounded, snippet-first list. CLI wrapper: `core/bin/cli/comms-recall`
(`search` / `person` / `get`).

**The contract.** Every result row crossing the interface is exactly:

```
{ "entity": {…message payload…},          # snippet-first for search, full for get()
  "confidence": 1.0,                        # verbatim FTS/SQL hit → certain
  "source_refs": [ {message_id, channel, date} ],   # never empty
  "scope": "open" | "limited" | "private" | "unknown" }   # from privacy_level
```

No field is ever omitted. This is the seam a later derived-summary/vector layer
plugs into (it will hedge confidence below 1.0); the shape does not change.

**Access control lives in the engine, not the caller.** `people.privacy_level`
(1 = full AI, 2 = limited, 3 = no AI analysis) is enforced in SQL: only
`privacy_level = 1` contacts are returned by default; anything `>= 2` requires
the explicit, operator-only `include_private=True`. Messages with no resolved
person carry no privacy signal → scoped `"unknown"`, included by default (an
absent person record is not a private flag). The store is opened **read-only**;
recall never mutates comms.db.

```python
from recall import RecallEngine
eng = RecallEngine()
rows = eng.search(query="ramadan", person="my mom", since="2026-06-01", limit=20)
full = eng.get("im-223330")
```

Bounds: default 20 results, hard cap 100. Tests: `tests/test_recall.py`
(contract shape, privacy filtering, resolver integration, FTS correctness,
timeframe, bounds — all against fake fixtures, never the live DBs).

## Debugging
- Check if running: `pgrep -f "comms/orchestrator.py"`
- Tail logs: `tail -f ~/.aos/logs/comms.log`
