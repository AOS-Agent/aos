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

**Claude profile lanes** (`core/infra/lib/claude_lanes.py`, aos#244.3): every headless `claude` spawn in this pipeline — the Sentinel spawner above, the bridge's `persistent_session.py`/`session_manager.py`, and the `memory-curate` cron — resolves its `CLAUDE_CONFIG_DIR` through this one module instead of calling `subprocess.run`/`asyncio.create_subprocess_exec` directly. A "lane" is `default` (`~/.claude`) or a Claude profile name (`core/bin/cli/claude-profile`, aos#244.1/2); the rotation is `~/.aos/config/claude-lanes.yaml`'s `lanes: [default, cld2, cld3]` list — operator-created, never auto-generated (missing file = one lane, today's behavior unchanged). When a lane's output reports a usage-limit hit, the module records `exhausted_until` in `~/.aos/state/claude-lanes.json`, drops one deduped `source: lanes` inbox note, and the next attempt (or the next spawn, for the bridge's long-lived session) uses the next lane in rotation. `claude-profile status` shows every lane's login state and exhaustion.
