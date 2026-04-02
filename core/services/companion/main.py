"""Companion meeting service — FastAPI on port 7603."""

import setproctitle; setproctitle.setproctitle("aos-companion")

import asyncio
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from capture import AudioCapture, SpeechSegment
from engine import MeetingEngine, MeetingState

app = FastAPI(title="AOS Companion")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins — secured by Tailscale network boundary
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = MeetingEngine()
capture = AudioCapture()

_meeting_task: asyncio.Task | None = None


# -- SSE stream for dashboard --

@app.get("/stream")
async def meeting_stream(request: Request):
    """SSE stream of meeting events for the dashboard."""
    queue = engine.subscribe()

    async def event_generator():
        try:
            # Send current state on connect
            if engine.meeting:
                yield f"event: meeting_state\ndata: {json.dumps(engine.meeting.to_dict())}\n\n"

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2.0)
                    yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f"event: ping\ndata: {{}}\n\n"
        finally:
            engine.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- Meeting lifecycle API --

@app.post("/meeting/create")
async def create_meeting(request: Request):
    """Create a new meeting with optional question framework."""
    body = await request.json()
    title = body.get("title", "Untitled Meeting")
    questions = body.get("questions", [])
    participants = body.get("participants", [])
    meeting = await engine.create_meeting(title, questions, participants)
    return meeting.to_dict()


@app.post("/meeting/start")
async def start_meeting(request: Request):
    """Start the active meeting. Pass source='phone' to skip Mac mic capture."""
    global _meeting_task

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    source = body.get("source", "mac")

    engine.start_meeting()

    # Only start local mic capture if source is "mac"
    # Remote sources (phone, MacBook) stream via WebSocket
    if source == "mac":
        _meeting_task = asyncio.create_task(_run_meeting_pipeline())

    return {"ok": True, "state": "active", "source": source}


@app.post("/meeting/pause")
async def pause_meeting():
    """Pause the active meeting."""
    engine.pause_meeting()
    return {"ok": True, "state": "paused"}


@app.post("/meeting/resume")
async def resume_meeting():
    """Resume a paused meeting."""
    engine.resume_meeting()
    return {"ok": True, "state": "active"}


async def _run_meeting_pipeline():
    """Main meeting loop — VAD-driven capture → in-process STT → live transcript.

    Audio flows continuously through the microphone. Silero VAD detects speech
    onset/offset and emits SpeechSegment objects. Each segment is transcribed
    in-process via mlx-whisper (no HTTP round trip). Results stream to the
    dashboard in real time.
    """
    log = logging.getLogger("companion.pipeline")
    log.info("Starting live voice pipeline...")

    # Save full recording to ~/.aos/meetings/
    from datetime import datetime
    meetings_dir = Path.home() / ".aos" / "meetings"
    meetings_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    recording_path = meetings_dir / f"meeting-{ts}.wav"

    meeting_start = engine.meeting.started_at if engine.meeting else time.time()

    segment_queue = await capture.start(
        recording_path=recording_path,
        meeting_start_time=meeting_start,
    )
    log.info("Recording to %s", recording_path)
    if engine.meeting:
        engine.meeting.audio_path = str(recording_path)

    # Start level broadcaster (for waveform display)
    level_queue = capture.level_queue

    async def broadcast_levels():
        while True:
            try:
                level = await level_queue.get()
                if level is None:
                    break
                engine._broadcast("audio_level", level)
            except asyncio.CancelledError:
                break

    level_task = asyncio.create_task(broadcast_levels())

    log.info("Voice pipeline active — VAD listening, STT ready")

    try:
        while True:
            segment = await segment_queue.get()
            if segment is None:
                log.info("Received end signal, stopping pipeline")
                break

            if not isinstance(segment, SpeechSegment):
                continue

            log.info(
                "Speech segment: %.0fms, RMS=%.4f — transcribing in-process...",
                segment.duration_ms, segment.rms,
            )

            # Transcribe directly — no HTTP, no file I/O
            await engine.process_speech_segment(
                audio=segment.audio,
                start_time=segment.start_time,
            )

    except asyncio.CancelledError:
        log.info("Pipeline cancelled")
    finally:
        level_task.cancel()
        await capture.stop()


@app.post("/meeting/end")
async def end_meeting():
    """End the active meeting — stop capture, generate summary."""
    global _meeting_task

    engine.end_meeting()

    # Stop the pipeline (safe even if capture wasn't started)
    try:
        await capture.stop()
    except Exception:
        pass
    if _meeting_task:
        _meeting_task.cancel()
        try:
            await _meeting_task
        except asyncio.CancelledError:
            pass
        _meeting_task = None

    # Generate summary
    summary = await engine.generate_summary()

    meeting_id = engine.meeting.id if engine.meeting else None
    return {"ok": True, "summary": summary, "meeting_id": meeting_id}


@app.post("/meeting/export")
async def export_meeting():
    """Export meeting notes to vault."""
    path = engine.export_to_vault()
    return {"ok": True, "path": str(path)}


@app.post("/meeting/speak")
async def speak(request: Request):
    """TTS — say something aloud (MVP: macOS say command)."""
    body = await request.json()
    text = body.get("text", "")
    if text:
        await engine.speak(text)
    return {"ok": True}


@app.post("/meeting/input")
async def meeting_input(request: Request):
    """Handle manual input during a meeting — /task, /research, or plain note."""
    body = await request.json()
    input_type = body.get("type", "note")
    value = body.get("value", "").strip()
    if not value or not engine.meeting:
        return {"ok": False}

    if input_type == "research":
        asyncio.create_task(engine._research(value))
    elif input_type == "task":
        if "Tasks" not in engine.meeting.notes:
            engine.meeting.notes["Tasks"] = []
        engine.meeting.notes["Tasks"].append(value)
        engine._broadcast("meeting_notes", {"topic": "Tasks", "notes": [value]})
    else:
        if "Manual Notes" not in engine.meeting.notes:
            engine.meeting.notes["Manual Notes"] = []
        engine.meeting.notes["Manual Notes"].append(value)

    return {"ok": True}


@app.get("/meeting/state")
async def get_state():
    """Get current meeting state."""
    if engine.meeting:
        return engine.meeting.to_dict()
    return {"state": "none"}


# -- Meeting history API --

@app.get("/meetings")
async def list_meetings():
    """List all past meetings, sorted newest first."""
    from datetime import datetime

    meetings_dir = Path.home() / ".aos" / "meetings"
    metadata_dir = meetings_dir / "metadata"

    # Collect meeting IDs from WAV files and metadata JSON files
    meeting_ids: dict[str, dict] = {}

    # Scan WAV files — filename: meeting-YYYYMMDD-HHMMSS.wav
    if meetings_dir.exists():
        for wav in meetings_dir.glob("meeting-*.wav"):
            stem = wav.stem  # "meeting-20260330-010725"
            parts = stem.split("-", 1)
            if len(parts) == 2:
                mid = parts[1]  # "20260330-010725"
                meeting_ids[mid] = {
                    "id": mid,
                    "title": "Untitled Meeting",
                    "date": "",
                    "duration_seconds": 0,
                    "has_transcript": False,
                    "has_summary": False,
                    "audio_path": str(wav),
                }

    # Overlay / fill from metadata JSON files
    if metadata_dir.exists():
        for meta_file in metadata_dir.glob("*.json"):
            mid = meta_file.stem
            try:
                data = json.loads(meta_file.read_text())
            except Exception:
                continue

            entry = meeting_ids.get(mid, {"id": mid, "audio_path": data.get("audio_path", "")})
            entry["title"] = data.get("title", entry.get("title", "Untitled Meeting"))
            entry["date"] = data.get("date", "")
            entry["duration_seconds"] = data.get("duration_seconds", 0)
            entry["has_transcript"] = bool(data.get("transcript"))
            entry["has_summary"] = bool(data.get("summary"))
            # Include a one-line summary preview for list display
            summary_text = data.get("summary", "")
            if summary_text:
                # Strip markdown headers and formatting, take first meaningful line
                preview = summary_text.replace("#", "").replace("**", "").strip()
                lines = [l.strip() for l in preview.split("\n") if l.strip() and not l.strip().startswith("-")]
                entry["summary_preview"] = lines[0][:120] if lines else ""
            else:
                entry["summary_preview"] = ""
            if not entry.get("audio_path") and data.get("audio_path"):
                entry["audio_path"] = data["audio_path"]
            meeting_ids[mid] = entry

    # Fill in date from id for entries that have no metadata date yet
    for mid, entry in meeting_ids.items():
        if not entry.get("date"):
            try:
                # id format: YYYYMMDD-HHMMSS
                dt = datetime.strptime(mid, "%Y%m%d-%H%M%S")
                entry["date"] = dt.isoformat()
            except ValueError:
                entry["date"] = ""

    # Sort newest first
    def _sort_key(e: dict) -> str:
        return e.get("date", "") or ""

    result = sorted(meeting_ids.values(), key=_sort_key, reverse=True)
    return result


@app.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: str):
    """Get full meeting detail including transcript and summary."""
    from fastapi import HTTPException

    metadata_dir = Path.home() / ".aos" / "meetings" / "metadata"
    meta_file = metadata_dir / f"{meeting_id}.json"

    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")

    try:
        data = json.loads(meta_file.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read meeting metadata: {exc}")

    return data


@app.post("/meetings/{meeting_id}/title")
async def update_meeting_title(meeting_id: str, request: Request):
    """Update the title of a past meeting."""
    from fastapi import HTTPException

    body = await request.json()
    new_title = body.get("title", "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="title is required")

    metadata_dir = Path.home() / ".aos" / "meetings" / "metadata"
    meta_file = metadata_dir / f"{meeting_id}.json"

    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")

    try:
        data = json.loads(meta_file.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read meeting metadata: {exc}")

    data["title"] = new_title
    meta_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    return {"ok": True, "id": meeting_id, "title": new_title}


@app.delete("/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str):
    """Delete a meeting — removes metadata and audio file."""
    from fastapi import HTTPException

    meetings_dir = Path.home() / ".aos" / "meetings"
    metadata_dir = meetings_dir / "metadata"

    deleted = []

    # Delete metadata
    meta_file = metadata_dir / f"{meeting_id}.json"
    if meta_file.exists():
        meta_file.unlink()
        deleted.append("metadata")

    # Delete audio file
    wav_file = meetings_dir / f"meeting-{meeting_id}.wav"
    if wav_file.exists():
        wav_file.unlink()
        deleted.append("audio")

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")

    return {"ok": True, "id": meeting_id, "deleted": deleted}


@app.get("/meetings/{meeting_id}/audio")
async def get_meeting_audio(meeting_id: str):
    """Serve the meeting audio WAV file for playback."""
    from fastapi import HTTPException
    wav_path = Path.home() / ".aos" / "meetings" / f"meeting-{meeting_id}.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(str(wav_path), media_type="audio/wav", filename=f"meeting-{meeting_id}.wav")


@app.get("/meetings/{meeting_id}/summary")
async def get_meeting_summary(meeting_id: str):
    """Return structured session summary with key_points, tasks, ideas, decisions, stats."""
    from fastapi import HTTPException

    metadata_dir = Path.home() / ".aos" / "meetings" / "metadata"
    meta_file = metadata_dir / f"{meeting_id}.json"

    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")

    try:
        data = json.loads(meta_file.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read metadata: {exc}")

    # Extract structured data from notes
    notes = data.get("notes", {})
    tasks = notes.get("Tasks", [])
    ideas = notes.get("Ideas", [])
    key_points = []
    decisions = []
    for topic, items in notes.items():
        if topic in ("Tasks", "Ideas"):
            continue
        for item in items:
            lower = item.lower()
            if "decid" in lower or "decision" in lower or "agreed" in lower:
                decisions.append(item)
            else:
                key_points.append(item)

    transcript = data.get("transcript", [])
    summary_text = data.get("summary", "")

    return {
        "key_points": key_points,
        "tasks": tasks,
        "ideas": ideas,
        "decisions": decisions,
        "summary_text": summary_text,
        "stats": {
            "duration_seconds": data.get("duration_seconds", 0),
            "segment_count": len(transcript),
            "note_count": sum(len(v) for v in notes.values()),
            "approval_count": len(tasks) + len(decisions),
        },
    }


@app.get("/companion/session/{session_id}/summary")
async def get_companion_session_summary(session_id: str):
    """Return structured summary for a companion session.

    Falls back to the current meeting's data if session_id matches.
    """
    # Check if this is the current/recent meeting
    if engine.meeting and (engine.meeting.id == session_id or session_id in str(engine.meeting.id)):
        notes = engine.meeting.notes
        tasks = notes.get("Tasks", [])
        ideas = notes.get("Ideas", [])
        key_points = []
        decisions = []
        for topic, items in notes.items():
            if topic in ("Tasks", "Ideas"):
                continue
            for item in items:
                lower = item.lower()
                if "decid" in lower or "decision" in lower or "agreed" in lower:
                    decisions.append(item)
                else:
                    key_points.append(item)

        return {
            "key_points": key_points,
            "tasks": tasks,
            "ideas": ideas,
            "decisions": decisions,
            "stats": {
                "duration_seconds": int(engine.meeting.duration_seconds),
                "segment_count": len(engine.meeting.transcript),
                "note_count": sum(len(v) for v in notes.values()),
                "approval_count": len(tasks) + len(decisions),
            },
        }

    # Try metadata files
    metadata_dir = Path.home() / ".aos" / "meetings" / "metadata"
    meta_file = metadata_dir / f"{session_id}.json"
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text())
            notes = data.get("notes", {})
            tasks = notes.get("Tasks", [])
            ideas = notes.get("Ideas", [])
            key_points = []
            decisions = []
            for topic, items in notes.items():
                if topic in ("Tasks", "Ideas"):
                    continue
                for item in items:
                    lower = item.lower()
                    if "decid" in lower or "decision" in lower or "agreed" in lower:
                        decisions.append(item)
                    else:
                        key_points.append(item)
            return {
                "key_points": key_points,
                "tasks": tasks,
                "ideas": ideas,
                "decisions": decisions,
                "stats": {
                    "duration_seconds": data.get("duration_seconds", 0),
                    "segment_count": len(data.get("transcript", [])),
                    "note_count": sum(len(v) for v in notes.values()),
                    "approval_count": len(tasks) + len(decisions),
                },
            }
        except Exception:
            pass

    return {"key_points": [], "tasks": [], "ideas": [], "decisions": [], "stats": {}}


@app.post("/companion/session/{session_id}/save")
async def save_companion_session(session_id: str, request: Request):
    """Save session summary and optionally export to vault."""
    body = await request.json()
    summary_text = body.get("summary", "")
    save_to_vault = body.get("save_to_vault", False)

    # Update meeting metadata if this is the current meeting
    if engine.meeting and (engine.meeting.id == session_id or session_id in str(engine.meeting.id)):
        if summary_text:
            engine.meeting.summary = summary_text
        engine.meeting.save_metadata()

        if save_to_vault:
            try:
                path = engine.export_to_vault()
                return {"ok": True, "vault_path": str(path)}
            except Exception as e:
                return {"ok": True, "vault_error": str(e)}

    # Try to update a metadata file directly
    metadata_dir = Path.home() / ".aos" / "meetings" / "metadata"
    meta_file = metadata_dir / f"{session_id}.json"
    if meta_file.exists() and summary_text:
        try:
            data = json.loads(meta_file.read_text())
            data["summary"] = summary_text
            meta_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass

    return {"ok": True}


# -- Phone audio via WebSocket --

@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket):
    """Receive standalone audio files (webm/mp4) from phone browser.

    Each message is a COMPLETE audio file (5 seconds) — no accumulation needed.
    The client restarts MediaRecorder every 5s to produce standalone files.
    """
    await websocket.accept()
    log = logging.getLogger("companion.ws_audio")
    log.info("Phone audio WebSocket connected")

    import tempfile
    import time as _time
    from engine import SpeechBlock

    chunk_count = 0

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data and len(data["bytes"]) > 100:  # skip tiny empty chunks
                chunk_count += 1
                log.info("Chunk #%d: %d bytes", chunk_count, len(data["bytes"]))

                # Each chunk is a complete audio file — save and transcribe directly
                tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False, prefix="aos-phone-")
                tmp.write(data["bytes"])
                tmp.close()
                audio_path = Path(tmp.name)

                engine._broadcast("audio_level", {"rms": 0.05, "peak": 0.1})

                try:
                    resp = await engine._client.post(
                        "http://127.0.0.1:7602/transcribe",
                        json={"audio_path": str(audio_path), "mode": "accurate", "language_hint": "en", "timestamps": True},
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    result = resp.json()

                    text = result.get("text", "").strip()
                    if text and len(text.split()) >= 2 and engine.meeting:
                        hallucinations = ["مرحبا", "بسم الله", "Thank you for watching",
                                          "Subscribe", "The following is a conversation"]
                        if not any(h.lower() in text.lower() for h in hallucinations):
                            elapsed = _time.time() - (engine.meeting.started_at or _time.time())
                            block = SpeechBlock(
                                speaker="You", text=text, timestamp=elapsed,
                                start_time=f"{int(elapsed)//60:02d}:{int(elapsed)%60:02d}",
                            )
                            engine.meeting.transcript.append(block)
                            engine._transcription_count += 1
                            engine._broadcast("transcript", {
                                "speaker": block.speaker, "text": block.text,
                                "timestamp": block.timestamp, "start_time": block.start_time,
                            })
                            log.info("Transcribed: %s", text[:80])

                            if len(engine.meeting.transcript) - engine._last_processed_index >= 3:
                                asyncio.create_task(engine._ai_process())
                except Exception as e:
                    log.error("Transcription failed: %s", e)
                finally:
                    try:
                        audio_path.unlink()
                    except OSError:
                        pass

            elif "text" in data:
                try:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "end":
                        log.info("End signal received")
                        break
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        log.info("Phone audio WebSocket disconnected")

    log.info("Phone audio ended, %d chunks", chunk_count)


# -- Remote mic via WebSocket (MacBook over Tailscale) --

@app.websocket("/ws/mic")
async def ws_mic(websocket: WebSocket):
    """Live mic streaming — ring buffer + decoupled feed timer.

    Browser sends 100ms PCM chunks continuously.
    Server accumulates in a ring buffer.
    A 500ms timer feeds accumulated audio to Parakeet streaming.
    Parakeet returns finalized tokens (solid) and draft tokens (speculative).
    SSE pushes diffs to the frontend for smooth word-reveal display.

    This is the Google Meet / Deepgram pattern: decouple receive from inference.
    """
    await websocket.accept()
    log = logging.getLogger("companion.ws_mic")
    log.info("Remote mic connected: %s", websocket.client)

    import struct
    import numpy as np
    import mlx.core as mx

    # Ring buffer: accumulates 100ms chunks between feed cycles
    ring_buffer: list[np.ndarray] = []
    ring_lock = asyncio.Lock()

    # State
    prev_finalized = ""
    prev_draft = ""
    has_speech = False
    unchanged_feeds = 0          # Feed cycles with no text change
    feed_count = 0
    running = True

    FEED_INTERVAL = 1.0          # Feed Parakeet every 1s — tested sweet spot for accuracy
    FINALIZE_AFTER = 2           # 2 unchanged feeds (2s silence) → finalize utterance
    MAX_FEEDS = 12               # 12 feeds × 1s = 12s → force-finalize (prevents degradation)

    # Ensure STT is loaded
    if not engine.stt._loaded:
        log.info("Loading STT model...")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, engine.stt.load)

    engine.stt.new_stream()

    def _commit_utterance(final_text: str):
        """Add finalized utterance to meeting transcript."""
        if not final_text or not engine.meeting or engine.meeting.state != MeetingState.ACTIVE:
            return
        from engine import SpeechBlock
        elapsed = time.time() - (engine.meeting.started_at or time.time())
        block = SpeechBlock(
            speaker="You", text=final_text,
            timestamp=elapsed,
            start_time=f"{int(elapsed)//60:02d}:{int(elapsed)%60:02d}",
        )
        engine.meeting.transcript.append(block)
        engine._transcription_count += 1
        engine._broadcast("transcript", {
            "speaker": block.speaker, "text": block.text,
            "timestamp": block.timestamp, "start_time": block.start_time,
        })
        new_blocks = len(engine.meeting.transcript) - engine._last_processed_index
        if new_blocks >= 3:
            asyncio.create_task(engine._ai_process())

    # --- Feed loop: accumulates speech, transcribes on silence ---
    async def feed_loop():
        nonlocal has_speech, unchanged_feeds, feed_count, running

        speech_audio: list[np.ndarray] = []  # Audio of current utterance

        while running:
            await asyncio.sleep(FEED_INTERVAL)
            if not running:
                break

            # Grab accumulated audio from ring buffer
            async with ring_lock:
                if not ring_buffer:
                    if has_speech:
                        unchanged_feeds += 1
                    # Check if utterance ended (silence)
                    if has_speech and unchanged_feeds >= FINALIZE_AFTER:
                        if speech_audio:
                            full_audio = np.concatenate(speech_audio)
                            duration_ms = len(full_audio) / 16000 * 1000
                            log.info("Utterance: %.0fms — transcribing...", duration_ms)
                            # Batch transcribe full utterance (accurate)
                            loop = asyncio.get_running_loop()
                            result = await loop.run_in_executor(None, engine.stt.transcribe, full_audio)
                            if result:
                                log.info("Final: \"%s\"", result["text"][:80])
                                _commit_utterance(result["text"])
                        speech_audio = []
                        has_speech = False
                        unchanged_feeds = 0
                        feed_count = 0
                    continue

                audio = np.concatenate(ring_buffer)
                ring_buffer.clear()

            feed_count += 1

            # Energy gate
            chunk_rms = float(np.sqrt(np.mean(audio ** 2)))
            if chunk_rms < 0.008:
                if has_speech:
                    unchanged_feeds += 1
                # Same silence check
                if has_speech and unchanged_feeds >= FINALIZE_AFTER and speech_audio:
                    full_audio = np.concatenate(speech_audio)
                    duration_ms = len(full_audio) / 16000 * 1000
                    log.info("Utterance: %.0fms — transcribing...", duration_ms)
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, engine.stt.transcribe, full_audio)
                    if result:
                        log.info("Final: \"%s\"", result["text"][:80])
                        _commit_utterance(result["text"])
                    speech_audio = []
                    has_speech = False
                    unchanged_feeds = 0
                    feed_count = 0
                continue

            # Speech detected — accumulate audio
            unchanged_feeds = 0
            has_speech = True
            speech_audio.append(audio)

            # Broadcast a simple "listening" indicator so UI knows we're capturing
            if engine.meeting and engine.meeting.state == MeetingState.ACTIVE:
                elapsed = time.time() - (engine.meeting.started_at or time.time())
                speech_duration = sum(len(c) for c in speech_audio) / 16000
                engine._broadcast("transcript_partial", {
                    "text": f"Listening... ({speech_duration:.0f}s)",
                    "finalized": "",
                    "draft": f"Listening... ({speech_duration:.0f}s)",
                    "timestamp": elapsed,
                    "start_time": f"{int(elapsed)//60:02d}:{int(elapsed)%60:02d}",
                })

            if feed_count % 5 == 0:
                log.info("Accumulating speech: %d chunks, %.1fs", len(speech_audio),
                         sum(len(c) for c in speech_audio) / 16000)

            # Force-finalize long utterances
            total_samples = sum(len(c) for c in speech_audio)
            if total_samples > 16000 * MAX_FEEDS:
                full_audio = np.concatenate(speech_audio)
                log.info("Force-finalize: %.0fms", len(full_audio) / 16000 * 1000)
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, engine.stt.transcribe, full_audio)
                if result:
                    _commit_utterance(result["text"])
                speech_audio = []
                feed_count = 0

    # Start the feed loop as a background task
    feed_task = asyncio.create_task(feed_loop())

    # --- Receive loop: just accumulates audio into ring buffer ---
    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data and len(data["bytes"]) > 8:
                raw = data["bytes"]
                sample_rate, num_samples = struct.unpack("<II", raw[:8])
                audio = np.frombuffer(raw[8:], dtype=np.float32).copy()
                if len(audio) != num_samples:
                    audio = audio[:num_samples] if len(audio) > num_samples else audio

                rms = float(np.sqrt(np.mean(audio ** 2)))
                engine._broadcast("audio_level", {"rms": rms, "peak": float(np.max(np.abs(audio)))})

                async with ring_lock:
                    ring_buffer.append(audio)

            elif "text" in data:
                try:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "end":
                        log.info("Remote mic end signal")
                        break
                except json.JSONDecodeError:
                    pass

    except (WebSocketDisconnect, RuntimeError):
        log.info("Remote mic disconnected")

    # Cleanup
    running = False
    feed_task.cancel()
    try:
        await feed_task
    except asyncio.CancelledError:
        pass

    # Flush any remaining speech
    final_text = prev_finalized or prev_draft
    if has_speech and final_text:
        _commit_utterance(final_text)

    log.info("Remote mic session ended, %d feeds", feed_count)


# -- Mic client distribution --

@app.get("/mic-client/setup")
async def mic_client_setup():
    """Serve the one-command setup script for MacBook mic client."""
    setup_path = Path(__file__).parent / "setup-mic-client.sh"
    if not setup_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Setup script not found")
    from starlette.responses import Response
    return Response(content=setup_path.read_text(), media_type="text/plain")


@app.get("/mic-client/script")
async def mic_client_script():
    """Serve mic-client.py for MacBook."""
    script_path = Path(__file__).parent / "mic-client.py"
    if not script_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="mic-client.py not found")
    from starlette.responses import Response
    return Response(content=script_path.read_text(), media_type="text/plain")


# -- Vault API --

VAULT_ROOT = Path.home() / "vault"


@app.get("/vault/tree")
async def vault_tree():
    """Return the vault folder/file tree."""
    import os

    def _scan(dirpath: Path, rel: str = "") -> list[dict]:
        items = []
        try:
            entries = sorted(dirpath.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return items
        for entry in entries:
            if entry.name.startswith("."):
                continue
            rel_path = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                children = _scan(entry, rel_path)
                items.append({
                    "name": entry.name,
                    "path": rel_path,
                    "type": "folder",
                    "children": children,
                    "count": sum(1 for c in children if c["type"] == "file"),
                })
            elif entry.suffix in (".md", ".yaml", ".yml", ".json", ".txt"):
                stat = entry.stat()
                items.append({
                    "name": entry.name,
                    "path": rel_path,
                    "type": "file",
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
        return items

    return _scan(VAULT_ROOT)


@app.get("/vault/file/{file_path:path}")
async def vault_read(file_path: str):
    """Read a vault file. Returns content + parsed frontmatter."""
    from fastapi import HTTPException
    import yaml as _yaml

    full_path = VAULT_ROOT / file_path
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if not str(full_path.resolve()).startswith(str(VAULT_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    content = full_path.read_text(errors="replace")

    # Parse frontmatter
    frontmatter = None
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = _yaml.safe_load(parts[1])
            except Exception:
                pass
            body = parts[2].strip()

    return {
        "path": file_path,
        "name": full_path.name,
        "content": content,
        "body": body,
        "frontmatter": frontmatter,
        "size": full_path.stat().st_size,
        "modified": full_path.stat().st_mtime,
    }


@app.put("/vault/file/{file_path:path}")
async def vault_write(file_path: str, request: Request):
    """Write/update a vault file."""
    from fastapi import HTTPException

    full_path = VAULT_ROOT / file_path
    if not str(full_path.resolve()).startswith(str(VAULT_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    body = await request.json()
    content = body.get("content", "")

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)

    return {"ok": True, "path": file_path}


@app.get("/vault/search")
async def vault_search(q: str = ""):
    """Search vault files by name or content."""
    if not q or len(q) < 2:
        return []

    results = []
    q_lower = q.lower()
    for md_file in VAULT_ROOT.rglob("*.md"):
        rel = str(md_file.relative_to(VAULT_ROOT))
        # Name match
        if q_lower in md_file.name.lower():
            results.append({"path": rel, "name": md_file.name, "match": "name"})
            continue
        # Content match (first 10KB only for speed)
        try:
            text = md_file.read_text(errors="replace")[:10240]
            if q_lower in text.lower():
                # Find context snippet
                idx = text.lower().index(q_lower)
                start = max(0, idx - 60)
                end = min(len(text), idx + len(q) + 60)
                snippet = text[start:end].replace("\n", " ").strip()
                results.append({"path": rel, "name": md_file.name, "match": "content", "snippet": snippet})
        except Exception:
            pass

    return results[:50]


@app.get("/health")
async def health():
    return {
        "service": "companion",
        "status": "running",
        "meeting_active": engine.meeting is not None and engine.meeting.state == MeetingState.ACTIVE,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7603)
