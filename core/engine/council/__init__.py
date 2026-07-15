"""Council — multi-agent deliberation engine.

Token-passing chat across persona-driven agents. Agents address each other with
`@<name>`, the scheduler routes, the chat lives in an append-only JSONL log.

Built to replace the cmux-pane orchestration pattern, which suffers from
paste-buffer corruption, completion-verb diversity, and idle-detection
fragility — the runtime is wrong for programmatic multi-agent.

Public API:
    from core.engine.council import Council
    c = Council.convene(topic="...", personas=["architect", ...], seed="...")
    c.run(rounds=8)

CLI: core/bin/cli/council
"""
from .chat import Chat
from .engine import Council
from .persona import BUILTIN_PERSONAS, Persona, load_persona
from .scheduler import Scheduler

__all__ = ["Council", "Chat", "Persona", "Scheduler", "load_persona", "BUILTIN_PERSONAS"]
