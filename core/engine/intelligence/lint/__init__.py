"""intelligence.lint — Nightly vault maintenance (Pass 3, Sonnet).

The overnight lint pass runs once a day via the `vault-maintenance` cron
and produces a daily maintenance report. It:

    1. Refreshes the vault inventory (via the existing scanner)
    2. Detects orphans and stale docs (SQL only, no LLM)
    3. Refreshes topic index orientation paragraphs (Sonnet)
    4. Drafts synthesis suggestions for topics with >=3 captures (Sonnet)
    5. Writes a daily report to ~/vault/log/YYYY-MM-DD-maintenance.md

Design:
    - Heuristic checks (orphans, stale) are cheap and deterministic;
      they run every pass.
    - Sonnet calls are the expensive bits; they're batched, optional via
      env var, and skipped entirely if the vault is empty.
    - Everything is idempotent — the report is overwritten on each run.
    - Reports live in vault/log/ because they're time-series data, not
      permanent knowledge.

Cost budget: ~$0.15-$0.25/night at nominal scale (25 topic refreshes +
5 synthesis drafts via Sonnet).
"""

from .runner import MaintenanceReport, run_maintenance_pass
from .orphans import find_orphans
from .stale import find_stale

__all__ = [
    "MaintenanceReport",
    "run_maintenance_pass",
    "find_orphans",
    "find_stale",
]
