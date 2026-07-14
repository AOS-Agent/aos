"""People Intelligence — signal extraction, profile building, classification.

Subsystem A: Source Registry + Adaptive Extraction (this package)
Subsystem B: Profile Compiler + Classifier (planned)
Subsystem C: Living Intelligence Loop (planned)
"""
from .types import (
    SignalType,
    SourceCapability,
    PersonSignals,
    CommunicationSignal,
    VoiceSignal,
    PhysicalPresenceSignal,
    ProfessionalSignal,
    GroupSignal,
    MentionSignal,
    MetadataSignal,
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
