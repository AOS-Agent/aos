"""Scheduler — decides who speaks next from the chat's addressing tags.

Reads the last message's `addressed_to` field:
  - architect | builder | skeptic | dreamer (or any registered persona): direct
  - all: open the floor — pick the agent who spoke longest ago
  - close: conversation over
  - none / unparseable: default to oldest speaker

Also parses agent replies to extract the addressing tag from the body.
"""
from __future__ import annotations
import re
from collections import defaultdict
from .chat import Chat, Message


ADDRESS_RE = re.compile(r"@(\w+)", re.IGNORECASE)


def parse_addressing(body: str, valid_personas: list[str]) -> str:
    """Extract the LAST @name from a body. Returns the bare name (no @) lowercase,
    or 'none' if no valid addressing tag found.

    Valid targets: any persona id, plus 'all', 'close'.
    """
    valid = set(p.lower() for p in valid_personas) | {"all", "close"}
    matches = ADDRESS_RE.findall(body)
    for tag in reversed(matches):
        if tag.lower() in valid:
            return tag.lower()
    return "none"


class Scheduler:
    """Token-passing scheduler over a Chat."""

    def __init__(self, chat: Chat, personas: list[str]):
        self.chat = chat
        self.personas = [p.lower() for p in personas]

    def last_spoke(self) -> dict[str, int]:
        """Map persona -> turn index of last utterance. Never-spoken = -1."""
        idx = defaultdict(lambda: -1)
        for i, m in enumerate(self.chat.read()):
            if m.speaker in self.personas:
                idx[m.speaker] = i
        return dict(idx)

    def pick_oldest(self, exclude: str | None = None) -> str:
        """Pick the persona who spoke longest ago, excluding the optional one."""
        last = self.last_spoke()
        candidates = [p for p in self.personas if p != exclude]
        # Never-spoken (default -1) sorts first naturally
        return min(candidates, key=lambda p: last.get(p, -1))

    def next_speaker(self) -> str | None:
        """Return the next speaker id, 'close' to terminate, or None if can't decide."""
        last = self.chat.last()
        if last is None:
            return None
        addr = last.addressed_to.lower() if last.addressed_to else "none"
        if addr == "close":
            return "close"
        if addr in self.personas:
            return addr
        if addr == "all":
            return self.pick_oldest(exclude=last.speaker)
        # none / invalid — default to oldest other speaker
        return self.pick_oldest(exclude=last.speaker if last.speaker in self.personas else None)
