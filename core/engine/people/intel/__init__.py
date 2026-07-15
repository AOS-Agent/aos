"""People Intelligence — signal extraction, profile building, classification.

Subsystem A: Source Registry + Adaptive Extraction (this package)
Subsystem B: Profile Compiler + Classifier (planned)
Subsystem C: Living Intelligence Loop (planned)
"""
from .types import (
    CommunicationSignal,
    GroupSignal,
    MentionSignal,
    MetadataSignal,
    PersonSignals,
    PhysicalPresenceSignal,
    ProfessionalSignal,
    SignalType,
    SourceCapability,
    VoiceSignal,
)

__all__ = [
    "SignalType",
    "SourceCapability",
    "PersonSignals",
    "CommunicationSignal",
    "VoiceSignal",
    "PhysicalPresenceSignal",
    "ProfessionalSignal",
    "GroupSignal",
    "MentionSignal",
    "MetadataSignal",
]
