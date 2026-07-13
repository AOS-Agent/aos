"""Companion Intelligence Engine — AI processing loop for Qareen.

Ported from the proven meeting engine (~/aos/core/services/companion/engine.py).
Delta-only processing with rolling context, fuzzy dedup, and async claude CLI calls.

Emits structured NoteGroup events for the companion UI:
  - note_group: A structured note group with typed bullets and entity tags
  - companion_notes/companion_tasks/companion_ideas: Legacy flat events (kept for compat)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from ..ontology.types import ObjectType

if TYPE_CHECKING:
    from ..events.bus import EventBus
    from ..ontology.model import Ontology
    from .session import SessionManager

logger = logging.getLogger(__name__)


class CompanionIntelligenceEngine:
    """Delta-only AI processing with rolling context and fuzzy dedup."""

    def __init__(
        self,
        session_manager: SessionManager,
        ontology: Ontology | None = None,
        bus: EventBus | None = None,
        push_event: Callable[..., Coroutine] | None = None,
    ) -> None:
        self._session_mgr = session_manager
        self._ontology = ontology
        self._bus = bus
        self._push_event = push_event
        self._running_context: str = ""
        self._last_ai_time: float = 0.0
        self._aos_context: str = ""
        self._processing_lock = asyncio.Lock()
        self._input_lock = asyncio.Lock()

    # -- Public API --------------------------------------------------------

    async def start_session(self) -> str:
        """Create a new companion session. Returns session_id."""
        self._running_context = ""
        self._last_ai_time = 0.0
        self._aos_context = await self._load_aos_context()
        session = self._session_mgr.create_session()
        session_id = session["id"] if isinstance(session, dict) else session
        logger.info("Intelligence engine started session: %s", session_id)
        return session_id

    async def process_input(self, text: str, speaker: str, session_id: str) -> None:
        """Append utterance to transcript, trigger AI if enough content."""
        async with self._input_lock:
            session = self._session_mgr.get_session(session_id)
            if not session or session["status"] != "active":
                return
            transcript = session["transcript_json"]
            now = datetime.now()
            block = {
                "speaker": speaker, "text": text,
                "timestamp": now.isoformat(), "start_time": now.strftime("%H:%M"),
            }
            transcript.append(block)
            utterance_count = session["utterance_count"] + 1
            self._session_mgr.update_session(
                session_id, transcript_json=transcript,
                utterance_count=utterance_count,
            )
            self._session_mgr.log_event(session_id, "transcript", block)
            new_count = len(transcript) - session["last_processed_index"]

        # Auto-generate title after 3 utterances (if no title yet)
        if utterance_count == 3:
            asyncio.create_task(self._auto_title(session_id))

        if new_count >= 3:
            asyncio.create_task(self._ai_process(session_id))

    async def _auto_title(self, session_id: str) -> None:
        """Generate and push auto-title after enough transcript segments."""
        try:
            session = self._session_mgr.get_session(session_id)
            if not session:
                return
            # Skip if title was already set manually
            if session.get("title"):
                return

            title = self._session_mgr.auto_generate_title(session_id)
            if title:
                logger.info("Auto-title for session %s: %s", session_id, title)
                await self._emit("companion_session_title", {
                    "session_id": session_id,
                    "title": title,
                })
        except Exception as e:
            logger.debug("Auto-title failed for %s: %s", session_id, e)

    async def end_session(self, session_id: str) -> dict:
        """End session, process remaining blocks, generate AI summary, return final state."""
        await self._ai_process(session_id, force=True)
        self._session_mgr.end_session(session_id)
        session = self._session_mgr.get_session(session_id)

        # Generate AI executive summary from transcript (async, best-effort)
        if session:
            try:
                ai_summary = await self._generate_ai_summary(session)
                if ai_summary:
                    summary = session.get("summary_json", {})
                    if isinstance(summary, str):
                        summary = json.loads(summary) if summary else {}
                    summary["executive_summary"] = ai_summary
                    self._session_mgr.update_session(
                        session_id, summary_json=json.dumps(summary, ensure_ascii=False)
                    )
                    session = self._session_mgr.get_session(session_id)
            except Exception as e:
                logger.debug("AI summary failed for %s: %s", session_id, e)

        self._running_context = ""
        self._last_ai_time = 0.0
        if self._push_event:
            await self._push_event("companion_session_ended", {
                "session_id": session_id,
                "utterance_count": session["utterance_count"] if session else 0,
            })
        return session or {}

    async def _generate_ai_summary(self, session: dict) -> str | None:
        """Generate an executive summary from the session transcript via Claude."""
        transcript = session.get("transcript_json", [])
        if not transcript or len(transcript) < 2:
            return None

        # Build compact transcript text (cap at ~3000 chars)
        lines = []
        for block in transcript:
            speaker = block.get("speaker", "?")
            text = block.get("text", "")
            lines.append(f"{speaker}: {text}")
        transcript_text = "\n".join(lines)
        if len(transcript_text) > 3000:
            transcript_text = transcript_text[:3000] + "\n[...truncated]"

        notes = session.get("notes_json", [])
        notes_text = ""
        if isinstance(notes, list) and notes:
            parts = []
            for g in notes[:10]:
                if isinstance(g, dict):
                    topic = g.get("topic", "Notes")
                    items = g.get("items", [])
                    parts.append(f"**{topic}**: " + "; ".join(items[:5]))
            notes_text = "\n".join(parts)

        prompt = f"""Summarize this conversation session in 2-3 concise sentences. Focus on what was discussed, what was decided, and what actions were taken. Be direct and specific.

Transcript:
{transcript_text}

{f"Notes extracted:{chr(10)}{notes_text}" if notes_text else ""}

Write only the summary, no preamble."""

        try:
            return await self._claude_call(prompt, timeout_s=15)
        except Exception as e:
            logger.debug("Claude summary call failed: %s", e)
            return None

    async def export_session_to_vault(self, session_id: str) -> str | None:
        """Export a session to the vault as a markdown capture note.

        Returns the vault path on success, None on failure.
        """
        session = self._session_mgr.get_session(session_id)
        if not session:
            return None

        title = session.get("title", "Untitled Session")
        started = session.get("started_at", "")
        summary = session.get("summary_json", {})
        if isinstance(summary, str):
            summary = json.loads(summary) if summary else {}
        transcript = session.get("transcript_json", [])
        notes = session.get("notes_json", [])

        # Build filename
        date_str = datetime.now().strftime("%Y-%m-%d")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower().strip())[:50].strip("-")
        vault_dir = Path.home() / "vault" / "knowledge" / "captures"
        vault_dir.mkdir(parents=True, exist_ok=True)
        filename = f"session-{date_str}-{slug}.md"
        filepath = vault_dir / filename

        # Build markdown
        lines = [
            "---",
            f'title: "{title}"',
            "type: session",
            f"date: {date_str}",
            f"started_at: {started}",
            f"stage: 1",
            f"tags: [session, companion]",
            "---",
            "",
            f"# {title}",
            "",
        ]

        # Executive summary
        exec_summary = summary.get("executive_summary", "")
        if exec_summary:
            lines.append(exec_summary)
            lines.append("")

        # Stats
        stats = summary.get("stats", {})
        if stats:
            duration = stats.get("duration_minutes")
            tasks = stats.get("tasks_created", 0)
            approved = stats.get("cards_approved", 0)
            parts = []
            if duration:
                parts.append(f"**Duration:** {duration:.0f}m")
            if tasks:
                parts.append(f"**Tasks created:** {tasks}")
            if approved:
                parts.append(f"**Cards approved:** {approved}")
            if parts:
                lines.append(" | ".join(parts))
                lines.append("")

        # Key points
        for section, label in [
            ("key_points", "Key Points"),
            ("tasks", "Tasks"),
            ("decisions", "Decisions"),
            ("ideas", "Ideas"),
        ]:
            items = summary.get(section, [])
            if items:
                lines.append(f"## {label}")
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

        # Notes
        if isinstance(notes, list) and notes:
            lines.append("## Notes")
            for group in notes:
                if isinstance(group, dict):
                    topic = group.get("topic", "")
                    items = group.get("items", [])
                    if topic:
                        lines.append(f"### {topic}")
                    for item in items:
                        lines.append(f"- {item}")
            lines.append("")

        # Transcript (collapsed)
        if transcript and len(transcript) > 0:
            lines.append("<details>")
            lines.append("<summary>Full Transcript</summary>")
            lines.append("")
            for block in transcript:
                speaker = block.get("speaker", "?")
                text = block.get("text", "")
                ts = block.get("start_time", "")
                lines.append(f"**{speaker}** ({ts}): {text}")
                lines.append("")
            lines.append("</details>")

        content = "\n".join(lines)
        filepath.write_text(content, encoding="utf-8")
        logger.info("Exported session to vault: %s", filepath)
        return str(filepath)

    # -- Claude CLI (ported from old engine lines ~342-358) ----------------

    @staticmethod
    async def _claude_call(prompt: str, timeout_s: int = 30) -> str:
        """Call Claude via CLI --print. Returns response text."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--model", "sonnet",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError("claude CLI not found in PATH")
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

    # -- Fuzzy dedup (ported EXACTLY from old engine) ----------------------

    @staticmethod
    def _is_duplicate(new_item: str, existing: list[str], threshold: float = 0.7) -> bool:
        """Fuzzy dedup via SequenceMatcher -- catches rephrased duplicates."""
        new_lower = new_item.lower().strip()
        for ex in existing:
            if SequenceMatcher(None, new_lower, ex.lower().strip()).ratio() > threshold:
                return True
        return False

    # -- Content filter (ported, adapted for general companion) ------------

    @staticmethod
    def _should_analyze(new_blocks: list[dict]) -> bool:
        """Decide if new speech blocks warrant AI analysis."""
        if not new_blocks:
            return False
        combined = " ".join(b["text"] for b in new_blocks).lower()
        word_count = len(combined.split())
        if word_count < 12:
            return False
        filler = re.compile(
            r"^(uh huh|yeah|okay|right|mm hmm|hmm|sure|got it|i see|mhm)\W*$", re.I,
        )
        if all(filler.match(b["text"].strip()) for b in new_blocks):
            return False
        priority_signals = [
            "?", "action item", "todo", "follow up", "deadline", "decision",
            "budget", "cost", "price", "problem", "issue", "we should", "let's",
            "idea", "important", "remember", "note", "plan", "strategy",
        ]
        if any(s in combined for s in priority_signals):
            return True
        return word_count >= 20

    # -- AOS context (ported from old engine lines ~168-238) ---------------

    async def _load_aos_context(self) -> str:
        """Build context brief from operator profile + active tasks."""
        parts: list[str] = []
        op_file = Path.home() / ".aos" / "config" / "operator.yaml"
        if op_file.exists():
            try:
                import yaml
                op = yaml.safe_load(op_file.read_text())
                parts.append(f"You are {op.get('name', 'Unknown')} (timezone: {op.get('timezone', '')}).")
            except Exception:
                pass
        if self._ontology:
            try:
                tasks = self._ontology.list(ObjectType.TASK, filters={"status": "todo"}, limit=5)
                if tasks:
                    lines = [f"- {getattr(t, 'title', '')} [{getattr(t, 'id', '')}]" for t in tasks]
                    parts.append("Active tasks:\n" + "\n".join(lines))
            except Exception as e:
                logger.debug("Could not load tasks: %s", e)
        context = "\n".join(parts) if parts else "(No AOS context available)"
        logger.info("Loaded AOS context: %d chars", len(context))
        return context

    # -- Note group helpers ---------------------------------------------------

    @staticmethod
    def _make_bullet(text: str, bullet_type: str = "note") -> dict[str, Any]:
        """Create a NoteBullet dict matching the frontend NoteGroup.bullets shape."""
        return {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "type": bullet_type,
            "isEditing": False,
        }

    @staticmethod
    def _find_group_by_title(
        notes_groups: list[dict[str, Any]], title: str,
    ) -> dict[str, Any] | None:
        """Find a note group by title (case-insensitive)."""
        title_lower = title.lower()
        for g in notes_groups:
            if g.get("title", "").lower() == title_lower:
                return g
        return None

    def _get_all_bullet_texts(self, notes_groups: list[dict[str, Any]]) -> list[str]:
        """Extract all bullet texts from all groups for dedup."""
        texts: list[str] = []
        for g in notes_groups:
            for b in g.get("bullets", []):
                if isinstance(b, dict):
                    texts.append(b.get("text", ""))
                elif isinstance(b, str):
                    texts.append(b)
        return texts

    def _get_bullet_texts_for_title(
        self, notes_groups: list[dict[str, Any]], title: str,
    ) -> list[str]:
        """Extract bullet texts from a specific group by title."""
        group = self._find_group_by_title(notes_groups, title)
        if not group:
            return []
        texts: list[str] = []
        for b in group.get("bullets", []):
            if isinstance(b, dict):
                texts.append(b.get("text", ""))
            elif isinstance(b, str):
                texts.append(b)
        return texts

    def _upsert_note_group(
        self,
        notes_groups: list[dict[str, Any]],
        title: str,
        new_items: list[str],
        bullet_type: str = "note",
    ) -> dict[str, Any]:
        """Insert or merge bullets into a note group. Returns the group dict."""
        group = self._find_group_by_title(notes_groups, title)
        now = datetime.now().isoformat()

        if group is None:
            group = {
                "id": uuid.uuid4().hex[:8],
                "title": title,
                "bullets": [self._make_bullet(item, bullet_type) for item in new_items],
                "entityTags": [],
                "timestamp": now,
                "isPinned": False,
            }
            notes_groups.append(group)
        else:
            for item in new_items:
                group["bullets"].append(self._make_bullet(item, bullet_type))
            group["timestamp"] = now

        return group

    @staticmethod
    def _migrate_notes_dict_to_groups(
        notes_dict: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        """Convert old dict-format notes_json to list-of-groups format."""
        title_to_type = {
            "key points": "note",
            "tasks": "action",
            "ideas": "insight",
        }
        groups: list[dict[str, Any]] = []
        for topic, items in notes_dict.items():
            if not isinstance(items, list):
                continue
            bullet_type = title_to_type.get(topic.lower(), "note")
            # Map old "Tasks" title to new "Action Items" title
            display_title = "Action Items" if topic.lower() == "tasks" else topic
            groups.append({
                "id": uuid.uuid4().hex[:8],
                "title": display_title,
                "bullets": [
                    {
                        "id": uuid.uuid4().hex[:8],
                        "text": item,
                        "type": bullet_type,
                        "isEditing": False,
                    }
                    for item in items
                ],
                "entityTags": [],
                "timestamp": datetime.now().isoformat(),
                "isPinned": False,
            })
        return groups

    # -- Core AI processing (ported from old engine lines ~397-552) --------

    async def _ai_process(self, session_id: str, force: bool = False) -> None:
        """Delta-only AI processing with rolling context and dedup.

        Emits structured note_group events for the companion UI, alongside
        legacy companion_notes/companion_tasks/companion_ideas events.
        """
        if self._processing_lock.locked() and not force:
            return
        async with self._processing_lock:
            session = self._session_mgr.get_session(session_id)
            if not session or session["status"] != "active":
                return
            transcript = session["transcript_json"]
            if not transcript:
                return
            last_idx = session["last_processed_index"]
            new_blocks = transcript[last_idx:]
            if not new_blocks:
                return
            if not force and not self._should_analyze(new_blocks):
                logger.info("Skipping AI -- %d blocks, not enough substance", len(new_blocks))
                self._session_mgr.update_session(session_id, last_processed_index=len(transcript))
                return
            now = time.time()
            if not force and now - self._last_ai_time < 10:
                return
            self._last_ai_time = now

            new_text = "\n".join(
                f"[{b.get('start_time', '')}] {b.get('speaker', 'Unknown')}: {b['text']}"
                for b in new_blocks
            )
            logger.info("AI processing: %d new blocks (%d total)", len(new_blocks), len(transcript))

            # -- Load notes_json as list of groups (new format) --
            notes_groups: list[dict[str, Any]] = session["notes_json"]
            if isinstance(notes_groups, dict):
                # Legacy dict format — convert on the fly
                notes_groups = self._migrate_notes_dict_to_groups(notes_groups)

            existing_all = self._get_all_bullet_texts(notes_groups)
            existing_tasks = self._get_bullet_texts_for_title(notes_groups, "Action Items")
            existing_ideas = self._get_bullet_texts_for_title(notes_groups, "Ideas")

            prompt = f"""You are a companion intelligence assistant. Analyze ONLY the new speech below.

AOS CONTEXT:
{self._aos_context}

CONVERSATION SO FAR:
{self._running_context or "(Conversation just started)"}

ALREADY EXTRACTED (do NOT repeat these):
Tasks: {json.dumps(existing_tasks[-5:]) if existing_tasks else "[]"}
Notes: {json.dumps(existing_all[-5:]) if existing_all else "[]"}

NEW SPEECH (analyze this only):
{new_text}

Return JSON:
{{
  "context_update": "Updated 1-2 sentence summary of the full conversation so far",
  "notes": ["only genuinely NEW insights from the new speech"],
  "tasks": ["only NEW action items not already tracked"],
  "ideas": ["only NEW ideas or threads"],
  "suggestion": "follow-up question or null",
  "research": ["only genuinely unfamiliar entities worth looking up"]
}}
Return ONLY valid JSON."""

            try:
                text = await self._claude_call(prompt)
                logger.info("AI response: %d chars", len(text))
                # Parse JSON robustly
                try:
                    data = json.loads(text.strip())
                except json.JSONDecodeError:
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0]
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0]
                    data = json.loads(text.strip())

                if data.get("context_update"):
                    self._running_context = data["context_update"]

                # -- Notes with fuzzy dedup --
                if data.get("notes"):
                    new_notes = [n for n in data["notes"] if not self._is_duplicate(n, existing_all)]
                    if new_notes:
                        group = self._upsert_note_group(notes_groups, "Key Points", new_notes, "note")
                        # Emit structured note_group event for new UI
                        await self._emit("note_group", {
                            "session_id": session_id,
                            **group,
                        })
                        # Legacy event (backward compat)
                        await self._emit("companion_notes", {
                            "session_id": session_id, "topic": "Key Points", "notes": new_notes,
                        })
                        logger.info("Notes: %d new, %d dupes filtered", len(new_notes), len(data["notes"]) - len(new_notes))

                # -- Tasks with dedup --
                if data.get("tasks"):
                    new_tasks = [t for t in data["tasks"] if not self._is_duplicate(t, existing_tasks)]
                    if new_tasks:
                        group = self._upsert_note_group(notes_groups, "Action Items", new_tasks, "action")
                        # Emit structured note_group event
                        await self._emit("note_group", {
                            "session_id": session_id,
                            **group,
                        })
                        # Legacy event (backward compat)
                        await self._emit("companion_tasks", {
                            "session_id": session_id, "tasks": new_tasks,
                        })

                # -- Ideas with dedup --
                if data.get("ideas"):
                    new_ideas = [i for i in data["ideas"] if not self._is_duplicate(i, existing_ideas)]
                    if new_ideas:
                        group = self._upsert_note_group(notes_groups, "Ideas", new_ideas, "insight")
                        # Emit structured note_group event
                        await self._emit("note_group", {
                            "session_id": session_id,
                            **group,
                        })
                        # Legacy event (backward compat)
                        await self._emit("companion_ideas", {
                            "session_id": session_id, "ideas": new_ideas,
                        })

                # -- Research triggers --
                research_list: list[dict] = session["research_json"]
                if data.get("research"):
                    existing_entities = {r.get("entity", "").lower() for r in research_list}
                    for entity in data["research"]:
                        if entity.lower() not in existing_entities:
                            await self._emit("companion_research_start", {"session_id": session_id, "entity": entity})
                            asyncio.create_task(self._research(entity, session_id))

                # -- Suggestion as note group --
                if data.get("suggestion"):
                    suggestion_text = data["suggestion"]
                    suggestion_group = self._upsert_note_group(
                        notes_groups, "Suggested Questions", [suggestion_text], "insight",
                    )
                    await self._emit("note_group", {
                        "session_id": session_id,
                        **suggestion_group,
                    })
                    # Legacy event
                    await self._emit("companion_suggestion", {
                        "session_id": session_id, "text": suggestion_text,
                    })

                # -- Persist (notes_json is now a list of groups) --
                self._session_mgr.update_session(
                    session_id, notes_json=notes_groups,
                    context_json={"running_context": self._running_context},
                    last_processed_index=len(transcript),
                )
                self._session_mgr.log_event(session_id, "ai_processed", {
                    "new_blocks": len(new_blocks),
                    "notes": len(data.get("notes", [])),
                    "tasks": len(data.get("tasks", [])),
                })

                # -- Async entity resolution (non-blocking) --
                if self._ontology:
                    asyncio.create_task(
                        self._resolve_entities(session_id, notes_groups, data)
                    )

            except Exception as e:
                logger.error("AI processing failed: %s", e)
                self._session_mgr.update_session(session_id, last_processed_index=len(transcript))

    # -- Entity resolution ---------------------------------------------------

    async def _resolve_entities(
        self,
        session_id: str,
        notes_groups: list[dict[str, Any]],
        ai_data: dict[str, Any],
    ) -> None:
        """Resolve entity names mentioned in notes against the ontology.

        Runs asynchronously after note groups are emitted so it does not
        block initial rendering. When entities are resolved, updated note
        groups (with entityTags) are re-emitted.
        """
        if not self._ontology:
            return

        # Collect all text from the AI response for entity scanning
        all_texts: list[str] = []
        for key in ("notes", "tasks", "ideas"):
            if ai_data.get(key):
                all_texts.extend(ai_data[key])

        if not all_texts:
            return

        combined_text = " ".join(all_texts)

        try:
            # Query ontology for people and projects that might match
            resolved_tags: list[dict[str, str]] = []

            for obj_type in (ObjectType.PERSON, ObjectType.PROJECT):
                try:
                    entities = self._ontology.list(obj_type, limit=50)
                    if not entities:
                        continue
                    for ent in entities:
                        name = getattr(ent, "name", "") or getattr(ent, "title", "")
                        if not name:
                            continue
                        # Simple substring match (case-insensitive)
                        if name.lower() in combined_text.lower():
                            resolved_tags.append({
                                "id": uuid.uuid4().hex[:8],
                                "name": name,
                                "type": obj_type.value,
                                "entityId": getattr(ent, "id", ""),
                            })
                except Exception:
                    logger.debug("Entity resolution failed for type %s", obj_type, exc_info=True)

            if not resolved_tags:
                return

            # Attach tags to the most recently updated groups and re-emit
            updated_groups: list[dict[str, Any]] = []
            for group in notes_groups:
                existing_tag_names = {t.get("name", "").lower() for t in group.get("entityTags", [])}
                new_tags = [t for t in resolved_tags if t["name"].lower() not in existing_tag_names]
                if new_tags:
                    group.setdefault("entityTags", []).extend(new_tags)
                    updated_groups.append(group)

            if updated_groups:
                # Persist and re-emit with entity tags
                self._session_mgr.update_session(session_id, notes_json=notes_groups)
                for group in updated_groups:
                    await self._emit("note_group", {
                        "session_id": session_id,
                        **group,
                    })
                logger.info("Entity resolution: %d tags attached to %d groups",
                            len(resolved_tags), len(updated_groups))

        except Exception as e:
            logger.debug("Entity resolution failed: %s", e, exc_info=True)

    # -- Research (ported from old engine lines ~554-576) -------------------

    async def _research(self, entity: str, session_id: str) -> None:
        """Research an entity mentioned in conversation via claude --print."""
        session = self._session_mgr.get_session(session_id)
        if not session:
            return
        existing = {r["entity"].lower() for r in session["research_json"]}
        if entity.lower() in existing:
            return
        logger.info("Researching: %s", entity)
        try:
            summary = await self._claude_call(
                f"Give a 2-3 sentence factual summary of '{entity}'. What is it, key details, relevance. Be concise."
            )
            card = {"entity": entity, "summary": summary.strip(), "time": datetime.now().strftime("%H:%M")}
            research = session["research_json"]
            research.append(card)
            self._session_mgr.update_session(session_id, research_json=research)
            await self._emit("companion_research", {"session_id": session_id, **card})
            logger.info("Research complete: %s", entity)
        except Exception as e:
            logger.error("Research failed for %s: %s", entity, e)

    # -- Event emission ----------------------------------------------------

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Push event via SSE and log to session events for recovery."""
        if self._push_event:
            try:
                await self._push_event(event_type, data)
            except Exception:
                logger.debug("SSE push failed: %s", event_type, exc_info=True)
        session_id = data.get("session_id")
        if session_id:
            try:
                self._session_mgr.log_event(session_id, event_type, data)
            except Exception:
                logger.debug("Event log failed: %s", event_type, exc_info=True)
