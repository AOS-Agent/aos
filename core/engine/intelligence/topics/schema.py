"""Dataclasses for topic index files.

A TopicIndex is the in-memory representation of a single
vault/knowledge/indexes/<slug>.md file. TopicEntry is one row pointing
at a vault document.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TopicEntry:
    """One entry in a topic index — points at a vault doc."""

    path: str  # relative to vault root, posix-style
    title: str
    type: str  # capture, research, synthesis, decision, expertise
    stage: int  # 1-6
    date: str  # YYYY-MM-DD
    summary: str = ""


@dataclass
class TopicIndex:
    """A whole topic index file."""

    slug: str
    title: str
    orientation: str = ""
    captures: list[TopicEntry] = field(default_factory=list)
    research: list[TopicEntry] = field(default_factory=list)
    synthesis: list[TopicEntry] = field(default_factory=list)
    decisions: list[TopicEntry] = field(default_factory=list)
    expertise: list[TopicEntry] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    updated: str = ""
    _operator_appendix: str = ""

    @property
    def doc_count(self) -> int:
        return (
            len(self.captures)
            + len(self.research)
            + len(self.synthesis)
            + len(self.decisions)
            + len(self.expertise)
        )
