---
globs:
  - "core/engine/people/**"
  - "core/qareen/ontology/**"
description: People DB and ontology — contact resolution, identity, relationships, social graph
---

# People System

## People DB (`~/.aos/data/people.db`)

14-table SQLite database. The single source of truth for every person.

**Key tables:**
- `people` — 1,148 contacts (canonical_name, importance 1-4, privacy_level)
- `person_identifiers` — Multi-channel handles (phone, email, wa_jid, telegram_id, slack_user_id)
- `aliases` — 340+ nicknames, relationships ("my mom", "baba", short names)
- `relationships` — Family/friend graph with strength scores
- `relationship_state` — Last interaction, drift detection, trajectory
- `interactions` — Who/when/channel/direction (metadata, not content — content is in comms.db)
- `communication_patterns` — Response times, preferred hours, style
- `groups` — WhatsApp/Slack groups with member lists
- `signal_store` — Aggregated communication signals per person

## 5-Tier Contact Resolver (`core/engine/people/resolver.py`)

```
resolve_contact("my mom")
  Tier 0: Alias lookup (aliases table, priority order)
  Tier 1: Exact name match (first_name, last_name, canonical_name)
  Tier 2: Frequency rank (who you talk to most with that name)
  Tier 3: Phonetic match (Arabic transliteration variants)
  Tier 4: Fuzzy/substring LIKE match
```

Returns: `{resolved: bool, person_id, contact: dict, confidence, tier}`

## Ontology (`core/qareen/ontology/`)

Semantic layer connecting all entities. Types defined in `types.py`:
- ObjectType: PERSON, TASK, PROJECT, GOAL, MESSAGE, CONVERSATION, etc.
- LinkType: SENT_BY, SENT_TO, MEMBER_OF, KNOWS, ASSIGNED_TO, etc.
- TrustLevel: OBSERVE(0) → AUTONOMOUS(5)

Storage is heterogeneous — the ontology doesn't own storage, it connects SQLite (people, comms), YAML (goals, config), and markdown (vault).

## Key Files

| File | What |
|------|------|
| `core/engine/people/db.py` | CRUD operations on people.db |
| `core/engine/people/schema.sql` | 14-table schema |
| `core/engine/people/resolver.py` | 5-tier contact resolution (994 lines) |
| `core/engine/people/profile.py` | Profile compilation (SQL → markdown) |
| `core/engine/people/graph.py` | Social graph + Louvain community detection |
| `core/qareen/ontology/types.py` | All data types and enums |
| `core/qareen/ontology/model.py` | Ontology adapter/action registry |
