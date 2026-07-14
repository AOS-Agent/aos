"""Signal Adapter base class.

Every data source implements this interface. Adapters are self-describing:
they declare what platform they run on, what signal types they provide,
how to detect availability, and how to extract signals.

Key design: extract_all() receives a person_index (person_id → identifiers)
and returns signals for ALL persons in one pass. This is source-first,
not person-first — each database is opened once, scanned once.

Convention note: is_available() mirrors the naming used by
core.engine.comms.connectors.base.SourceConnector and
core.engine.comms.channel.ChannelAdapter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..types import PersonSignals, SignalType


class SignalAdapter(ABC):
    """Base class for all signal source adapters.

    Subclasses must set class attributes and implement is_available() and
    extract_all().

    Class attributes:
        name:         Unique adapter identifier ("apple_messages", "whatsapp", ...)
        display_name: Human-readable name ("Apple Messages")
        platform:     Target platform ("macos", "android", "web", "any")
        signal_types: List of SignalType enums this adapter provides
        description:  Longer description
        requires:     List of requirements (["file:~/Library/Messages/chat.db", ...])
    """

    name: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    platform: ClassVar[str] = "any"
    signal_types: ClassVar[list[SignalType]] = []
    description: ClassVar[str] = ""
    requires: ClassVar[list[str]] = []

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this source is available on this machine.

        Should be fast — check file existence, not full DB connectivity.
        Must NEVER raise — return False on any failure.
        Returns True if extract_all() is likely to succeed.
        """
        ...

    @abstractmethod
    def extract_all(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        """Extract signals for ALL persons in one pass.

        Args:
            person_index: {person_id: {"name": str, "phones": [str],
                                       "emails": [str], "wa_jids": [str]}}
                          Built from people.db by the extractor orchestrator.

        Returns:
            {person_id: PersonSignals} for every person where signals were found.
            Missing persons are simply absent from the dict (not null).

        Implementation notes:
            - Open the source database ONCE at the start.
            - Copy external databases to temp before reading (avoid locking).
            - Build internal lookup structures (phone → person_id, email → person_id).
            - Iterate source records and match to persons.
            - Close/cleanup at the end.
            - Graceful fail: catch per-record errors, skip, continue.
        """
        ...

    def health(self) -> dict:
        """Health check for diagnostics."""
        try:
            available = self.is_available()
        except Exception as e:
            return {
                "name": self.name,
                "available": False,
                "platform": self.platform,
                "signal_types": [s.value for s in self.signal_types],
                "error": str(e),
            }
        return {
            "name": self.name,
            "available": available,
            "platform": self.platform,
            "signal_types": [s.value for s in self.signal_types],
        }
