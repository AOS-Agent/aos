"""Work runner service — the generic agent-driven-kanban runner (Kanban Phase 4).

Turns a task delegated to an agent into a running headless worker, polling
work.db (the board is the queue). Ships OFF (runner.enabled: false); the operator
opts in. Engine lives in core/engine/work/runner.py; this package is just the
service wrapper + manifest.
"""
