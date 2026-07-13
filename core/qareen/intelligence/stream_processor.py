"""Qareen Stream Processor — continuous voice transcript to threaded thought-units.

Takes raw transcript segments from speech-to-text, buffers them into
semantic thought-units (15+ words or sentence boundary), classifies each
unit (idea/task/decision/question/plan/context/emotion), assigns to a
conversation thread (detecting topic switches), extracts entity names,
and emits events via the EventBus.

Usage:
    from qareen.events.bus import EventBus
    from qareen.intelligence.stream_processor import StreamProcessor

    bus = EventBus()
    processor = StreamProcessor(bus, session_id="abc123")

    # Called on every final transcript segment from STT:
    await processor.ingest_segment("we should move the deadline", "You", timestamp)

    # On session end:
    await processor.flush()
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from uuid import uuid4

from ..events.types import Event
from ..events.bus import EventBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

VALID_CLASSIFICATIONS = frozenset({
    "idea", "task", "decision", "question", "plan", "context", "emotion",
})


@dataclass
class ThoughtUnit:
    """A single classified segment of speech within a conversation thread."""

    id: str
    thread_id: str
    text: str
    speaker: str
    timestamp: str  # ISO8601
    classification: str  # idea|task|decision|question|plan|context|emotion
    confidence: float  # 0.0-1.0
    entities: list[str]  # Raw entity names (proper nouns only)


@dataclass
class Thread:
    """A conversation thread grouping related thought-units by topic."""

    id: str
    title: str
    summary: str
    unit_ids: list[str]
    first_seen: str  # ISO8601
    last_seen: str  # ISO8601
    is_active: bool


# ---------------------------------------------------------------------------
# Stream Processor
# ---------------------------------------------------------------------------

class StreamProcessor:
    """Buffers transcript segments, classifies them, threads them, and emits events.

    The processor accumulates incoming speech segments until a semantic
    boundary is reached (15+ words or sentence-ending punctuation), then
    sends the combined text to Haiku for classification and threading in
    a single call. Results are stored locally and broadcast via EventBus.
    """

    # Minimum word count before forcing a processing pass
    MIN_BUFFER_WORDS = 15

    # Timeout for the Haiku classification subprocess
    CLASSIFY_TIMEOUT_S = 8

    def __init__(self, bus: EventBus, session_id: str | None = None) -> None:
        self._bus = bus
        self._session_id = session_id

        # Thread state
        self._threads: dict[str, Thread] = {}
        self._active_thread_id: str | None = None

        # Accumulated thought-units for the session
        self._units: list[ThoughtUnit] = []

        # Segment buffer (accumulates until boundary)
        self._segment_buffer: list[str] = []
        self._buffer_word_count: int = 0

    # -- Public API ---------------------------------------------------------

    def set_session(self, session_id: str) -> None:
        """Set or change the active session. Clears all state."""
        self._session_id = session_id
        self._threads.clear()
        self._active_thread_id = None
        self._units.clear()
        self._segment_buffer.clear()
        self._buffer_word_count = 0

    async def ingest_segment(
        self, text: str, speaker: str, timestamp: str
    ) -> None:
        """Called on every final (non-provisional) transcript segment from STT.

        Buffers the segment and processes when enough content has accumulated
        (>=15 words) or a natural sentence boundary is detected.

        Args:
            text: The transcript text for this segment.
            speaker: Speaker label (e.g. "You", "Caller", participant name).
            timestamp: ISO8601 timestamp of the segment.
        """
        stripped = text.strip()
        if not stripped:
            return

        self._segment_buffer.append(stripped)
        self._buffer_word_count += len(stripped.split())

        if (
            self._buffer_word_count >= self.MIN_BUFFER_WORDS
            or self._is_boundary(stripped)
        ):
            await self._process_buffer(speaker, timestamp)

    async def flush(self) -> None:
        """Process any remaining buffered segments.

        Call this when the session ends to ensure nothing is lost.
        """
        if self._segment_buffer:
            await self._process_buffer("You", datetime.now().isoformat())

    def get_threads(self) -> list[dict]:
        """Return all threads as dicts for JSON serialization."""
        return [asdict(t) for t in self._threads.values()]

    def get_thread_summaries(self) -> dict[str, str]:
        """Return {thread_id: summary} for voice model context injection."""
        return {tid: t.summary for tid, t in self._threads.items()}

    def get_active_thread_id(self) -> str | None:
        """Return the ID of the currently active thread, or None."""
        return self._active_thread_id

    def get_units(self) -> list[dict]:
        """Return all thought-units as dicts for JSON serialization."""
        return [asdict(u) for u in self._units]

    def get_unit_count(self) -> int:
        """Return total number of thought-units processed this session."""
        return len(self._units)

    # -- Internal -----------------------------------------------------------

    async def _process_buffer(self, speaker: str, timestamp: str) -> None:
        """Combine buffered segments, classify, thread, and emit events."""
        combined = " ".join(self._segment_buffer)
        self._segment_buffer = []
        self._buffer_word_count = 0

        # Classify via Haiku (single call for classification + threading + entities)
        result = await self._classify(combined)

        thread_id = result.get("thread_id", "")
        is_new_thread = False
        thread_switched = False
        prev_thread = self._active_thread_id

        # --- Thread assignment ---
        if thread_id == "NEW" or thread_id not in self._threads:
            thread_id = uuid4().hex[:10]
            title = result.get("thread_title", combined[:50])
            self._threads[thread_id] = Thread(
                id=thread_id,
                title=title,
                summary=combined[:200],
                unit_ids=[],
                first_seen=timestamp,
                last_seen=timestamp,
                is_active=True,
            )
            is_new_thread = True

        if self._active_thread_id and self._active_thread_id != thread_id:
            thread_switched = True
            if self._active_thread_id in self._threads:
                self._threads[self._active_thread_id].is_active = False

        self._active_thread_id = thread_id
        thread = self._threads[thread_id]
        thread.is_active = True
        thread.last_seen = timestamp

        # --- Create thought-unit ---
        classification = result.get("classification", "context")
        if classification not in VALID_CLASSIFICATIONS:
            classification = "context"

        confidence = result.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        confidence = max(0.0, min(1.0, float(confidence)))

        entities = result.get("entities", [])
        if not isinstance(entities, list):
            entities = []
        entities = [str(e) for e in entities if e]

        unit = ThoughtUnit(
            id=uuid4().hex[:12],
            thread_id=thread_id,
            text=combined,
            speaker=speaker,
            timestamp=timestamp,
            classification=classification,
            confidence=confidence,
            entities=entities,
        )

        self._units.append(unit)
        thread.unit_ids.append(unit.id)

        # --- Update rolling thread summary (max 200 chars) ---
        if len(thread.unit_ids) > 1:
            thread.summary = f"{thread.summary[:100]}... {combined[:100]}"

        # --- Emit events ---
        await self._bus.emit(Event(
            event_type="stream.unit",
            timestamp=datetime.now(),
            payload=asdict(unit),
            source="stream_processor",
        ))

        if is_new_thread:
            await self._bus.emit(Event(
                event_type="stream.thread_new",
                timestamp=datetime.now(),
                payload={"thread_id": thread_id, "title": thread.title},
                source="stream_processor",
            ))

        if thread_switched:
            await self._bus.emit(Event(
                event_type="stream.thread_switch",
                timestamp=datetime.now(),
                payload={"from": prev_thread, "to": thread_id},
                source="stream_processor",
            ))

    async def _classify(self, text: str) -> dict:
        """Single Haiku call: classify + thread assignment + entity extraction.

        Returns a dict with keys: thread_id, thread_title, classification,
        confidence, entities. On any failure, returns safe defaults so the
        processor never crashes.
        """
        thread_ctx = json.dumps(
            {tid: t.title for tid, t in self._threads.items()}
        ) if self._threads else "{}"

        prompt = (
            "Classify this speech segment. Return ONLY valid JSON.\n\n"
            f"Existing threads: {thread_ctx}\n"
            f"Active thread: {self._active_thread_id or 'none'}\n\n"
            f'Text: "{text}"\n\n'
            "Return: "
            '{"thread_id":"existing-id or NEW",'
            '"thread_title":"title if NEW",'
            '"classification":"idea|task|decision|question|plan|context|emotion",'
            '"confidence":0.0-1.0,'
            '"entities":["specific names only"]}\n\n'
            "Rules:\n"
            "- Reuse existing thread_id if topic matches. Return exact ID.\n"
            '- "NEW" only if this is clearly a different topic.\n'
            '- classification: "context" for background info. '
            '"task" only if someone needs to DO something (high bar). '
            '"decision" for choices being made. '
            '"question" for actual questions.\n'
            "- entities: only specific proper nouns "
            "(people names, project names, tools). Not generic words."
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--model", "haiku",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("Stream classify: claude CLI not found in PATH")
            return self._classify_fallback(text)

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(prompt.encode()),
                timeout=self.CLASSIFY_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("Stream classify: haiku call timed out")
            return self._classify_fallback(text)

        if proc.returncode != 0:
            logger.warning(
                "Stream classify: haiku returned non-zero exit code %d",
                proc.returncode,
            )
            return self._classify_fallback(text)

        raw = stdout.decode().strip()

        # Strip markdown code fences if Haiku wraps the JSON
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Expected JSON object")
            return parsed
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Stream classify: failed to parse haiku response: %s — raw: %.200s",
                exc,
                raw,
            )
            return self._classify_fallback(text)

    def _classify_fallback(self, text: str) -> dict:
        """Return safe defaults when classification fails."""
        return {
            "thread_id": self._active_thread_id or "NEW",
            "thread_title": text[:40],
            "classification": "context",
            "confidence": 0.3,
            "entities": [],
        }

    @staticmethod
    def _is_boundary(text: str) -> bool:
        """Check if text ends at a natural sentence boundary."""
        stripped = text.rstrip()
        return stripped.endswith((".", "?", "!", "..."))
