# Comms Pipeline — pointer

Message history: `~/.aos/data/comms.db` (all channels, FTS5); identity: `people.db`. To search past messages ("what did I tell X", topics, timeframes) use `comms-recall search|person|get` — access control is enforced inside the tool; never pass `--include-private` on a contact's behalf.

**Before working on comms internals (bus, adapters, enrichment, trust cascade) or writing SQL against comms.db, read the full reference: `~/aos/docs/reference/comms-pipeline.md`.**
