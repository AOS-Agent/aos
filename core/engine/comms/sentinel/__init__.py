"""Sentinel — autonomous commitment agent.

Spawner pipeline:

    agent_triggers (status=detected)
        │
        ▼
    ContextBuilder — convo, contact, voice samples
        │
        ▼
    Spawner.run_trigger() — launch `claude --print --agent Sentinel`
        │
        ▼
    Draft file at ~/.aos/work/sentinel/drafts/{trigger_id}.md
        │
        ▼
    ConfidenceGate.evaluate()
        │
        ├─ high  → SoftWindow.start() → Dispatcher.send()
        └─ low/med → move to pending/, macOS notification
"""

from .confidence_gate import ConfidenceGate, GateResult
from .context_builder import ContextBuilder, ContextBundle

__all__ = ["ContextBuilder", "ContextBundle", "ConfidenceGate", "GateResult"]
