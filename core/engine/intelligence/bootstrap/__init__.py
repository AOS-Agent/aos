"""intelligence.bootstrap — Sweep existing vault docs through compilation.

The bootstrap is the one-shot operator action that takes a vault with
drift (missing frontmatter, no topics, no backlinks) and runs it through
the same compilation engine the live save path uses, producing a coherent
knowledge base with minimal operator intervention.

Strict safety rules:
    - Git commit snapshot BEFORE any file mutation (rollback path)
    - Doc bodies are NEVER touched — only frontmatter
    - Operator-set frontmatter fields are PRESERVED — merge, not overwrite
    - Confidence below SHADOW_ACCEPT_THRESHOLD stays pending (not auto-applied)
    - Pause + resume + cancel are honored at every checkpoint

Callers:
    - /api/knowledge/bootstrap/preview — dry-run, no LLM, no mutation
    - /api/knowledge/bootstrap/start — kicks off a background worker

State:
    - bootstrap_runs table tracks execution
    - compilation_proposals stores per-doc results (same as live save)
"""

from .engine import (
    BootstrapRun,
    BootstrapPreview,
    build_preview,
    start_run,
    get_run,
    list_runs,
    pause_run,
    resume_run,
    cancel_run,
)
from .git_snapshot import take_snapshot

__all__ = [
    "BootstrapRun",
    "BootstrapPreview",
    "build_preview",
    "start_run",
    "get_run",
    "list_runs",
    "pause_run",
    "resume_run",
    "cancel_run",
    "take_snapshot",
]
