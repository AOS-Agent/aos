"""Meeting engine — state machine, transcription pipeline, AI processing.

Uses in-process STT (mlx-whisper) and TTS (Kokoro via mlx-audio).
No HTTP round trips for transcription — audio numpy arrays go straight to the model.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import httpx

from stt import SpeechToText
from tts import TextToSpeech

DASHBOARD_URL = "http://127.0.0.1:4096"


class MeetingState(str, Enum):
    SETUP = "setup"
    ACTIVE = "active"
    ENDING = "ending"
    SUMMARY = "summary"


@dataclass
class SpeechBlock:
    speaker: str
    text: str
    timestamp: float  # seconds since meeting start
    start_time: str  # HH:MM format
    confidence: float = 1.0


@dataclass
class Meeting:
    id: str
    title: str
    state: MeetingState = MeetingState.SETUP
    started_at: float | None = None
    ended_at: float | None = None
    questions: list[dict] = field(default_factory=list)  # [{text, covered, covered_at}]
    transcript: list[SpeechBlock] = field(default_factory=list)
    notes: dict = field(default_factory=dict)  # topic -> [note strings]
    research: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    summary: str = ""
    participants: list[str] = field(default_factory=lambda: ["You"])
    audio_path: str = ""

    @property
    def duration_seconds(self) -> float:
        if not self.started_at:
            return 0
        end = self.ended_at or time.time()
        return end - self.started_at

    @property
    def duration_str(self) -> str:
        s = int(self.duration_seconds)
        return f"{s // 60:02d}:{s % 60:02d}"

    @property
    def full_transcript_text(self) -> str:
        lines = []
        for block in self.transcript:
            lines.append(f"[{block.start_time}] {block.speaker}: {block.text}")
        return "\n".join(lines)

    @property
    def topics_covered(self) -> int:
        return sum(1 for q in self.questions if q.get("covered"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state.value,
            "started_at": self.started_at,
            "duration": self.duration_str,
            "questions": self.questions,
            "transcript": [
                {"speaker": b.speaker, "text": b.text, "timestamp": b.timestamp, "start_time": b.start_time}
                for b in self.transcript
            ],
            "notes": self.notes,
            "research": self.research,
            "topics_covered": self.topics_covered,
            "topics_total": len(self.questions),
            "participants": self.participants,
            "summary": self.summary,
        }

    def save_metadata(self) -> Path:
        """Persist meeting metadata to ~/.aos/meetings/metadata/{id}.json."""
        metadata_dir = Path.home() / ".aos" / "meetings" / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)

        date_str = ""
        if self.started_at:
            date_str = datetime.fromtimestamp(self.started_at).astimezone().isoformat()

        payload = {
            "id": self.id,
            "title": self.title,
            "date": date_str,
            "duration_seconds": int(self.duration_seconds),
            "participants": self.participants,
            "transcript": [
                {"speaker": b.speaker, "text": b.text, "timestamp": b.timestamp, "start_time": b.start_time}
                for b in self.transcript
            ],
            "notes": self.notes,
            "summary": self.summary,
            "audio_path": self.audio_path,
        }

        out_path = metadata_dir / f"{self.id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return out_path


class MeetingEngine:
    """Orchestrates a live meeting session."""

    def __init__(self):
        self.meeting: Meeting | None = None
        self._subscribers: list[asyncio.Queue] = []
        self._transcription_count = 0
        self._ai_task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(timeout=30.0)
        # Token optimization state
        self._last_processed_index = 0
        self._running_context = ""
        self._last_ai_time = 0.0
        # AOS context — loaded at meeting start
        self._aos_context = ""
        # In-process STT and TTS engines
        self.stt = SpeechToText()
        self.tts = TextToSpeech()

    # -- Event broadcasting --

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _broadcast(self, event_type: str, data: dict):
        msg = {"type": event_type, "data": data, "ts": time.time()}
        for q in self._subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    # -- AOS Context --

    async def _load_aos_context(self, participant_names: list[str] | None = None) -> str:
        """Build a context brief from AOS sources — operator, work, past meetings, vault."""
        import logging
        import subprocess
        import yaml
        log = logging.getLogger("companion.context")
        parts = []

        # 1. Operator profile
        op_file = Path.home() / ".aos" / "config" / "operator.yaml"
        if op_file.exists():
            try:
                op = yaml.safe_load(op_file.read_text())
                name = op.get("name", "Unknown")
                tz = op.get("timezone", "")
                parts.append(f"You are {name} (timezone: {tz}).")
            except Exception:
                pass

        # 2. Active tasks (top 5, compact)
        try:
            result = subprocess.run(
                ["python3", str(Path.home() / "aos" / "core" / "work" / "cli.py"), "next", "--json"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                tasks = json.loads(result.stdout)
                if tasks:
                    task_lines = [f"- {t.get('title','')} [{t.get('id','')}]" for t in tasks[:5]]
                    parts.append("Active tasks:\n" + "\n".join(task_lines))
        except Exception as e:
            log.debug("Could not load tasks: %s", e)

        # 3. Past meetings with participants
        if participant_names:
            metadata_dir = Path.home() / ".aos" / "meetings" / "metadata"
            if metadata_dir.exists():
                for meta_file in sorted(metadata_dir.glob("*.json"), reverse=True)[:10]:
                    try:
                        data = json.loads(meta_file.read_text())
                        title = data.get("title", "")
                        # Check if any participant name appears in the meeting
                        for name in participant_names:
                            if name.lower() in title.lower() or name.lower() in json.dumps(data.get("transcript", [])).lower():
                                summary_preview = (data.get("summary", "") or "")[:200]
                                parts.append(f"Past meeting with {name}: \"{title}\" — {summary_preview}")
                                break
                    except Exception:
                        pass

        # 4. Vault search for participant context (via QMD)
        if participant_names:
            for name in participant_names[:3]:  # max 3 searches
                try:
                    result = subprocess.run(
                        [str(Path.home() / ".bun" / "bin" / "qmd"), "query", name, "-n", "2", "--json"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        results = json.loads(result.stdout)
                        hits = results if isinstance(results, list) else results.get("results", [])
                        for hit in hits[:2]:
                            snippet = hit.get("snippet", hit.get("context", ""))[:150]
                            if snippet:
                                parts.append(f"Vault note about {name}: {snippet}")
                except Exception:
                    pass

        context = "\n".join(parts) if parts else "(No AOS context available)"
        log.info("Loaded AOS context: %d chars, %d sections", len(context), len(parts))
        return context

    # -- Meeting lifecycle --

    async def create_meeting(self, title: str, questions: list[str] | None = None,
                              participants: list[str] | None = None) -> Meeting:
        meeting_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Reset AI state for new meeting
        self._last_processed_index = 0
        self._running_context = ""
        self._last_ai_time = 0.0
        self._transcription_count = 0
        self._aos_context = ""

        all_participants = ["You"] + (participants or [])
        self.meeting = Meeting(
            id=meeting_id,
            title=title,
            questions=[{"text": q, "covered": False, "covered_at": None} for q in (questions or [])],
            participants=all_participants,
        )

        # Load AOS context in background (don't block meeting creation)
        self._aos_context = await self._load_aos_context(participants)

        return self.meeting

    def start_meeting(self):
        if not self.meeting:
            raise ValueError("No meeting created")
        self.meeting.state = MeetingState.ACTIVE
        self.meeting.started_at = time.time()
        self._broadcast("meeting_state", {"state": "active", "meeting": self.meeting.to_dict()})

    def pause_meeting(self):
        if not self.meeting or self.meeting.state != MeetingState.ACTIVE:
            return
        self.meeting.state = MeetingState.SETUP  # reuse SETUP as paused state
        self._broadcast("meeting_state", {"state": "paused"})

    def resume_meeting(self):
        if not self.meeting:
            return
        self.meeting.state = MeetingState.ACTIVE
        self._broadcast("meeting_state", {"state": "active"})

    def end_meeting(self):
        if not self.meeting:
            return
        self.meeting.state = MeetingState.ENDING
        self.meeting.ended_at = time.time()
        self._broadcast("meeting_state", {"state": "ending"})

    # -- Transcription processing --

    async def _cleanup_text(self, raw_text: str) -> str:
        """Clean up raw STT output: fix punctuation, remove filler, proper grammar.

        Uses Claude CLI for intelligent cleanup. Falls back to raw text on failure.
        """
        if not raw_text or len(raw_text.split()) < 3:
            return raw_text

        prompt = f"""Clean up this speech transcription. Fix punctuation, capitalization, and grammar. Remove filler words (um, uh, like, you know) unless they add meaning. Split run-on sentences. Keep the speaker's intent and meaning exactly — do NOT add, remove, or change what was said. Return ONLY the cleaned text, nothing else.

Raw: {raw_text}"""

        try:
            cleaned = await self._claude_call(prompt, timeout_s=10)
            # Sanity check: cleaned text should be similar length (not a refusal or explanation)
            if cleaned and 0.3 < len(cleaned) / len(raw_text) < 2.0:
                return cleaned.strip()
        except Exception as e:
            import logging
            logging.getLogger("companion.cleanup").debug("Cleanup failed: %s", e)

        return raw_text

    async def process_speech_segment(self, audio: "np.ndarray", start_time: float = 0.0):
        """Transcribe a speech segment: Whisper for accuracy, then LLM cleanup.

        Pipeline: audio → Whisper (accurate) → Claude (clean grammar) → transcript
        """
        import numpy as np

        if not self.meeting or self.meeting.state != MeetingState.ACTIVE:
            return

        # Run Whisper STT in a thread
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self.stt.transcribe, audio)

        if result is None:
            return

        raw_text = result["text"]

        # LLM cleanup: fix punctuation, grammar, filler words
        text = await self._cleanup_text(raw_text)

        minutes = int(start_time) // 60
        seconds = int(start_time) % 60

        block = SpeechBlock(
            speaker="You",  # Phase 2: speaker ID via ECAPA-TDNN embeddings
            text=text,
            timestamp=start_time,
            start_time=f"{minutes:02d}:{seconds:02d}",
        )
        self.meeting.transcript.append(block)
        self._transcription_count += 1

        self._broadcast("transcript", {
            "speaker": block.speaker,
            "text": block.text,
            "timestamp": block.timestamp,
            "start_time": block.start_time,
            "inference_ms": result.get("inference_ms", 0),
            "audio_ms": result.get("audio_ms", 0),
        })

        # Adaptive AI trigger: process when we have enough new content
        new_block_count = len(self.meeting.transcript) - self._last_processed_index
        if new_block_count >= 3:
            asyncio.create_task(self._ai_process())

    # -- AI processing (via claude CLI) --

    async def _claude_call(self, prompt: str, timeout_s: int = 30) -> str:
        """Call Claude via CLI --print. Returns response text."""
        proc = await asyncio.create_subprocess_exec(
            "claude", "--print", "--model", "sonnet",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("claude CLI timed out")
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {stderr.decode()[:200]}")
        return stdout.decode().strip()

    @staticmethod
    def _is_duplicate(new_item: str, existing: list[str], threshold: float = 0.7) -> bool:
        """Fuzzy dedup via SequenceMatcher — catches rephrased duplicates."""
        from difflib import SequenceMatcher
        new_lower = new_item.lower().strip()
        for ex in existing:
            if SequenceMatcher(None, new_lower, ex.lower().strip()).ratio() > threshold:
                return True
        return False

    def _should_analyze(self, new_blocks: list[SpeechBlock]) -> bool:
        """Decide if new speech blocks warrant AI analysis."""
        import re
        if not new_blocks:
            return False

        combined = " ".join(b.text for b in new_blocks).lower()
        word_count = len(combined.split())

        # Skip very short utterances
        if word_count < 12:
            return False

        # Skip pure filler
        filler = re.compile(r"^(uh huh|yeah|okay|right|mm hmm|hmm|sure|got it|i see|مرحبا)\W*$", re.I)
        if all(filler.match(b.text.strip()) for b in new_blocks):
            return False

        # High-priority: always analyze
        priority_signals = ["?", "action item", "todo", "follow up", "deadline", "decision",
                            "budget", "cost", "price", "problem", "issue", "we should", "let's"]
        if any(s in combined for s in priority_signals):
            return True

        # Default: analyze if enough substance
        return word_count >= 20

    async def _ai_process(self):
        """Delta-only AI processing with rolling context and dedup."""
        import logging
        log = logging.getLogger("companion.ai")

        if not self.meeting or not self.meeting.transcript:
            return

        # Get only NEW blocks since last processing
        new_blocks = self.meeting.transcript[self._last_processed_index:]
        if not new_blocks:
            return

        # Selective: skip if content isn't worth analyzing
        if not self._should_analyze(new_blocks):
            log.info("Skipping AI — %d blocks, not enough substance", len(new_blocks))
            self._last_processed_index = len(self.meeting.transcript)
            return

        # Throttle: min 10s between calls
        now = time.time()
        if now - self._last_ai_time < 10:
            return
        self._last_ai_time = now

        new_text = "\n".join(f"[{b.start_time}] {b.speaker}: {b.text}" for b in new_blocks)
        log.info("AI processing: %d new blocks (%d total)", len(new_blocks), len(self.meeting.transcript))

        # Build existing items list for dedup instruction
        existing_notes = []
        for items in self.meeting.notes.values():
            existing_notes.extend(items)
        existing_tasks = self.meeting.notes.get("Tasks", [])
        existing_ideas = self.meeting.notes.get("Ideas", [])

        # Build prompt with rolling context + delta only
        framework_section = ""
        if self.meeting.questions:
            questions_text = "\n".join(
                f"{'[x]' if q['covered'] else '[ ]'} {q['text']}"
                for q in self.meeting.questions
            )
            framework_section = f"\nTOPIC FRAMEWORK:\n{questions_text}\n"

        prompt = f"""You are a meeting intelligence assistant. Analyze ONLY the new speech below.

AOS CONTEXT:
{self._aos_context}
{framework_section}
MEETING SO FAR:
{self._running_context or "(Meeting just started)"}

ALREADY EXTRACTED (do NOT repeat these):
Tasks: {json.dumps(existing_tasks[-5:]) if existing_tasks else "[]"}
Notes: {json.dumps(existing_notes[-5:]) if existing_notes else "[]"}

NEW SPEECH (analyze this only):
{new_text}

Return JSON:
{{
  "context_update": "Updated 1-2 sentence summary of the full meeting so far",
  "notes": ["only genuinely NEW insights from the new speech"],
  "tasks": ["only NEW action items not already tracked"],
  "ideas": ["only NEW ideas or threads"],
  "suggestion": "follow-up question or null",
  "research": ["only genuinely unfamiliar entities worth looking up"]
}}
Return ONLY valid JSON."""

        try:
            text = await self._claude_call(prompt)
            log.info("AI response: %d chars", len(text))

            # Parse JSON robustly
            try:
                data = json.loads(text.strip())
            except json.JSONDecodeError:
                # Try extracting from markdown fences
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                data = json.loads(text.strip())

            # Update rolling context
            if data.get("context_update"):
                self._running_context = data["context_update"]

            # Update notes with fuzzy dedup
            if data.get("notes"):
                topic = "Key Points"
                if self.meeting.questions:
                    for i in reversed(data.get("topics_covered", [])):
                        if i < len(self.meeting.questions):
                            topic = self.meeting.questions[i]["text"][:40]
                            break
                if topic not in self.meeting.notes:
                    self.meeting.notes[topic] = []
                new_notes = [n for n in data["notes"] if not self._is_duplicate(n, existing_notes)]
                if new_notes:
                    self.meeting.notes[topic].extend(new_notes)
                    self._broadcast("meeting_notes", {"topic": topic, "notes": new_notes})
                    log.info("Broadcast %d new notes (filtered %d dupes)", len(new_notes), len(data["notes"]) - len(new_notes))

            # Tasks with dedup
            if data.get("tasks"):
                if "Tasks" not in self.meeting.notes:
                    self.meeting.notes["Tasks"] = []
                new_tasks = [t for t in data["tasks"] if not self._is_duplicate(t, existing_tasks)]
                if new_tasks:
                    self.meeting.notes["Tasks"].extend(new_tasks)
                    self._broadcast("meeting_notes", {"topic": "Tasks", "notes": new_tasks})
                    log.info("Broadcast %d tasks", len(new_tasks))

            # Ideas with dedup
            if data.get("ideas"):
                if "Ideas" not in self.meeting.notes:
                    self.meeting.notes["Ideas"] = []
                new_ideas = [i for i in data["ideas"] if not self._is_duplicate(i, existing_ideas)]
                if new_ideas:
                    self.meeting.notes["Ideas"].extend(new_ideas)
                    self._broadcast("meeting_notes", {"topic": "Ideas", "notes": new_ideas})
                    log.info("Broadcast %d ideas", len(new_ideas))

            # Research triggers
            if data.get("research"):
                for entity in data["research"]:
                    existing = {r.get("entity", "").lower() for r in self.meeting.research}
                    if entity.lower() not in existing:
                        self._broadcast("meeting_research_start", {"entity": entity})
                        asyncio.create_task(self._research(entity))

            # Topic coverage
            for idx in data.get("topics_covered", []):
                if idx < len(self.meeting.questions) and not self.meeting.questions[idx]["covered"]:
                    self.meeting.questions[idx]["covered"] = True
                    self.meeting.questions[idx]["covered_at"] = self.meeting.duration_str
                    self._broadcast("meeting_topic", {
                        "index": idx,
                        "text": self.meeting.questions[idx]["text"],
                        "covered_at": self.meeting.questions[idx]["covered_at"],
                    })

            # Suggestion
            if data.get("suggestion"):
                self._broadcast("meeting_suggestion", {"text": data["suggestion"]})
                log.info("Suggestion: %s", data["suggestion"][:60])

            # Mark processed
            self._last_processed_index = len(self.meeting.transcript)

        except Exception as e:
            log.error("AI processing failed: %s", e)
            # Still advance the index so we don't re-process the same blocks
            self._last_processed_index = len(self.meeting.transcript)

    async def _research(self, entity: str):
        """Research an entity mentioned in conversation via claude --print."""
        import logging
        log = logging.getLogger("companion.research")

        if not self.meeting:
            return

        existing = {r["entity"].lower() for r in self.meeting.research}
        if entity.lower() in existing:
            return

        log.info("Researching: %s", entity)
        try:
            summary = await self._claude_call(
                f"Give a 2-3 sentence factual summary of '{entity}'. What is it, key details, relevance. Be concise."
            )
            card = {"entity": entity, "summary": summary.strip(), "time": self.meeting.duration_str}
            self.meeting.research.append(card)
            self._broadcast("meeting_research", card)
            log.info("Research complete: %s", entity)
        except Exception as e:
            log.error("Research failed for %s: %s", entity, e)

    # -- Summary generation --

    async def generate_summary(self) -> str:
        """Generate post-meeting summary."""
        if not self.meeting:
            return ""

        self.meeting.state = MeetingState.SUMMARY

        # Use accumulated intelligence + sampled transcript (not full raw transcript)
        notes_text = json.dumps(self.meeting.notes, indent=2)
        research_text = "\n".join(f"- {r['entity']}: {r['summary']}" for r in self.meeting.research)

        # Sample transcript: first, every 5th, and last 3 blocks (saves ~60% tokens)
        blocks = self.meeting.transcript
        sampled = set()
        if blocks:
            sampled.add(0)
            sampled.update(range(0, len(blocks), 5))
            sampled.update(range(max(0, len(blocks) - 3), len(blocks)))
        sampled_text = "\n".join(
            f"[{blocks[i].start_time}] {blocks[i].speaker}: {blocks[i].text}"
            for i in sorted(sampled) if i < len(blocks)
        )

        questions_section = ""
        if self.meeting.questions:
            questions_section = "TOPIC FRAMEWORK:\n" + "\n".join(
                f"{'[x]' if q['covered'] else '[ ]'} {q['text']}"
                for q in self.meeting.questions
            ) + "\n"

        prompt = f"""Generate a comprehensive meeting summary.

MEETING: {self.meeting.title or 'Untitled'}
DURATION: {self.meeting.duration_str}
PARTICIPANTS: {', '.join(self.meeting.participants)}
{questions_section}
MEETING CONTEXT: {self._running_context}

EXTRACTED INTELLIGENCE:
{notes_text}

RESEARCH: {research_text or 'None'}

TRANSCRIPT SAMPLES (key moments):
{sampled_text}

Generate markdown with:
## Key Findings
- Bullet points of the most important facts

## Detailed Notes
Organized by topic

## Follow-Up Actions
Numbered list of concrete next steps

## Research
Key findings from any entities researched during the meeting

Keep it concise but thorough. Use direct quotes where impactful."""

        try:
            self.meeting.summary = await self._claude_call(prompt)
        except Exception as e:
            self.meeting.summary = f"Summary generation failed: {e}"

        self._broadcast("meeting_state", {"state": "summary", "summary": self.meeting.summary})

        # Auto-generate a smart title from the transcript
        if not self.meeting.title and self.meeting.transcript:
            try:
                title_prompt = f"Generate a short meeting title (3-6 words, no quotes) from this transcript:\n\n{self.meeting.full_transcript_text[:500]}"
                self.meeting.title = (await self._claude_call(title_prompt)).strip().strip('"\'')
            except Exception:
                self.meeting.title = f"Meeting {self.meeting.duration_str}"

        self.meeting.save_metadata()

        # Auto-export to vault
        try:
            vault_path = self.export_to_vault()
            import logging
            logging.getLogger("companion.ai").info("Auto-exported to vault: %s", vault_path)
        except Exception:
            pass

        return self.meeting.summary

    # -- Voice output (TTS) --

    async def speak(self, text: str, voice: str | None = None):
        """Speak text aloud via Kokoro TTS (mlx-audio). Supports interruption."""
        try:
            await self.tts.speak(text, voice=voice)
        except Exception as e:
            import logging
            logging.getLogger("companion.tts").error("TTS failed: %s", e)
            # Fallback to macOS say if Kokoro fails
            try:
                proc = await asyncio.create_subprocess_exec(
                    "say", "-v", "Samantha", "-r", "180", text,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except Exception:
                pass

    # -- Vault export --

    def export_to_vault(self) -> Path:
        """Export meeting to vault as a markdown file."""
        if not self.meeting:
            raise ValueError("No meeting to export")

        now = datetime.now()
        slug = self.meeting.title.lower().replace(" ", "-")[:40]
        filename = f"meeting-{now.strftime('%Y-%m-%d')}-{slug}.md"
        vault_path = Path.home() / "vault" / "knowledge" / "captures" / filename

        frontmatter = f"""---
title: "{self.meeting.title}"
type: meeting-notes
date: {now.strftime('%Y-%m-%d')}
tags: [meeting, discovery, client]
source_ref: meeting/{self.meeting.id}
duration: "{self.meeting.duration_str}"
participants: {json.dumps(self.meeting.participants)}
topics_covered: {self.meeting.topics_covered}
topics_total: {len(self.meeting.questions)}
---

"""
        content = frontmatter + self.meeting.summary

        content += "\n\n---\n\n## Full Transcript\n\n"
        for block in self.meeting.transcript:
            content += f"**{block.speaker}** [{block.start_time}]: {block.text}\n\n"

        vault_path.parent.mkdir(parents=True, exist_ok=True)
        vault_path.write_text(content)
        return vault_path
