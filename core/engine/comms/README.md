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

## Scope (iMessage allowlist)

`scope.py` is the one place that decides which iMessage threads AOS may see.
macOS Full Disk Access is all-or-nothing — once a process can open
`~/Library/Messages/chat.db` it can read every conversation on the Mac — so
the narrowing has to happen here, and only here.

**Config** — `~/.aos/config/comms.yaml` (operator-written; template in
`config/defaults/comms.yaml`). A missing file means `access: all`, today's
behaviour on every machine that never wrote one:

```yaml
imessage:
  access: allowlist        # allowlist | all
  include_groups: false    # groups with an allowlisted member become visible
  allowed:
    - name: Hisham
      handles: ["+14165550123", "hisham@example.com"]
```

Phones match on digits (`+1 416 555 0123` == `4165550123`); emails match
case-insensitively.

**The gate** — every chat.db connection the comms engine opens (adapter,
desktop ingest, Converse, Sentinel) calls `scope.apply(conn, source=...)`
right after opening, or uses `scope.open_chat_db(source=...)` which does both.
On an allowlist, `apply()` installs SQLite **temp views** that shadow
`message`, `chat`, `handle`, `chat_handle_join`, `chat_message_join`,
`attachment` and `message_attachment_join`. Unqualified table names resolve to
the `temp` schema first, so every query on that connection — existing or
future — sees only the allowlisted 1:1 chats (groups only with
`include_groups`), their messages and attachments. Works on `?mode=ro`
connections; never copies the database.

**Outbound** — `scope.send_allowed(recipient)` / `assert_send_allowed()`
(raises `ScopeDenied`) gate every send path with the same handle rules.

**Audit line** — each `apply()` appends one line to `~/.aos/logs/comms.log`:

```
2026-09-15T19:20:01 scope=allowlist source=adapter chats=1 handles=2
```

**The one bypass** — `SELECT … FROM main.message` names the schema and skips
the view. No comms code may qualify a chat.db table with `main.`; a guard test
fails on any hit.

`python3 scope.py status` prints the policy and, if chat.db is readable, how
many chats/handles it exposes. Tests: `tests/test_comms_scope.py` (fake
chat.db in tmp_path, config + log redirected via `AOS_COMMS_CONFIG` /
`AOS_COMMS_LOG`; never the live files).

## Debugging
- Check if running: `pgrep -f "comms/orchestrator.py"`
- Tail logs: `tail -f ~/.aos/logs/comms.log`
