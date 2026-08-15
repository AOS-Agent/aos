---
paths:
  - "**/core/engine/people/**"
  - "**/core/qareen/ontology/**"
  - "**/people.db*"
---

# People System — pointer

Identity layer: `~/.aos/data/people.db` (contacts, aliases, relationships, interaction history) with a 5-tier contact resolver (`core/engine/people/resolver.py` — resolves "my mom", nicknames, fuzzy/phonetic names).

**Before ANY work involving people, contacts, or relationships, read the full reference: `~/aos/docs/reference/people-system.md`.**
