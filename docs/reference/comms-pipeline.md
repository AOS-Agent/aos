---
globs:
  - "core/comms/**"
  - "core/engine/comms/**"
  - "core/services/comms_bus/**"
  - "core/bin/cli/message-person"
  - "core/bin/crons/enrich-comms"
description: Comms pipeline architecture — unified message store, bus, trust cascade, messaging
---

# Communications Pipeline

What actually exists — not the bus-daemon architecture this doc used to describe (`core/services/comms_bus`, `:4099`, `CommsStoreConsumer`, `message-person` CLI). That architecture never existed in this repo.

**Databases**: `~/.aos/data/comms.db` (cross-channel messages, FTS5) and `~/.aos/data/people.db` (identity, via `core/engine/people/resolver.py`).

**Extraction (real, scheduled)**: `core/engine/comms/extract/pipeline.py` resolves senders to person_ids and writes people.db rows. Runs daily 05:00 via the `comms-extract` cron, through `extract/lifecycle.py`.

**comms.db consumers (real code, not wired to a live process)**: `core/engine/comms/consumers/pattern_update.py` and `people_intel.py` are working `Consumer` subclasses for `core/engine/comms/bus.py`'s pub/sub bus — nothing calls `register_consumer()` in production, so they exist and are correct but do not run.

**Sentinel spawner** (`core/engine/comms/sentinel/spawner.py`): the commitment research/draft/send pipeline. Off by default since v0.8.0 (migration 112) — operator opt-in, not ambient.

**Query**: `comms-recall` CLI (`core/engine/comms/recall.py`), the access-controlled search facade over comms.db — prefer it over raw SQL.
