"""Trigger detection for agentic commitment phrases.

When the operator sends an outbound message containing a trigger phrase
('consider it done', '@aos'), the detector records an `agent_triggers`
row that the Sentinel spawner picks up and acts on.
"""

from .detector import TriggerDetector, TriggerMatch

__all__ = ["TriggerDetector", "TriggerMatch"]
