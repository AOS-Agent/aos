"""Intelligence Loop sensors — each writes typed, provenance-bearing rows
to the signals store (core/engine/loop/signals.py) and nothing else.

Sensor taxonomy (council 2026-07-21):
- friction_judge   LLM, eval-gated — may only run with a valid gate-pass
                   marker for the CURRENT judge version hash.
- comms_entities   deterministic SQL over comms.db enrichment output.
                   TAINTED: derived from external message content.
- initiative_drift deterministic scan of vault initiative frontmatter.
                   First-party, untainted.
"""
