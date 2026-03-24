"""AOS Transcriber Service — unified speech-to-text for all pipelines.

Single endpoint, single model, shared by bridge + content-engine + listen.

    POST /transcribe     — transcribe an audio file
    GET  /health         — service health + model status
    GET  /info           — model info + supported languages

Runs on port 7601.
"""

import asyncio
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

import engine

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("TRANSCRIBER_PORT", "7601"))
HOST = "127.0.0.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("transcriber")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TranscribeRequest(BaseModel):
    audio_path: str = Field(description="Absolute path to audio file")
    mode: str = Field(default="accurate", pattern="^(fast|accurate)$")
    language_hint: str = Field(default="auto", description="ISO 639-1 code or 'auto'")
    timestamps: bool = Field(default=True)


class TranscribeResponse(BaseModel):
    text: str
    language: str
    language_probability: float
    segments: list[dict]
    duration_audio: float
    duration_processing: float
    source: str
    timestamped_text: str = ""


class HealthResponse(BaseModel):
    status: str
    model: str
    uptime_seconds: float
    requests_served: int


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_start_time: float = 0
_requests_served: int = 0
_model_ready: bool = False

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up model on startup."""
    global _start_time, _model_ready

    try:
        import setproctitle
        setproctitle.setproctitle("aos-transcriber")
    except ImportError:
        pass

    _start_time = time.monotonic()
    logger.info(f"Starting AOS Transcriber on {HOST}:{PORT}")

    # Warm up in a thread so it doesn't block the event loop
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, engine.warmup)
        _model_ready = True
        logger.info("Model ready. Accepting requests.")
    except Exception as e:
        logger.error(f"Model warm-up failed: {e}")
        logger.error("Service will attempt lazy loading on first request.")

    yield

    logger.info("Shutting down transcriber.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AOS Transcriber",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(req: TranscribeRequest):
    """Transcribe an audio file."""
    global _requests_served

    if not Path(req.audio_path).exists():
        raise HTTPException(status_code=400, detail=f"File not found: {req.audio_path}")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: engine.transcribe(
                audio_path=req.audio_path,
                mode=req.mode,
                language_hint=req.language_hint,
                timestamps=req.timestamps,
            ),
        )
        _requests_served += 1

        return TranscribeResponse(
            **result.to_dict(),
            timestamped_text=result.timestamped_text,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/transcribe/upload", response_model=TranscribeResponse)
async def transcribe_upload(
    file: UploadFile = File(...),
    mode: str = "accurate",
    language_hint: str = "auto",
    timestamps: bool = True,
):
    """Transcribe an uploaded audio file (for clients that can't share filesystem)."""
    global _requests_served

    with tempfile.NamedTemporaryFile(suffix=f"_{file.filename}", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: engine.transcribe(
                audio_path=tmp_path,
                mode=mode,
                language_hint=language_hint,
                timestamps=timestamps,
            ),
        )
        _requests_served += 1

        return TranscribeResponse(
            **result.to_dict(),
            timestamped_text=result.timestamped_text,
        )
    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ready" if _model_ready else "loading",
        model=engine.MODEL_REPO,
        uptime_seconds=round(time.monotonic() - _start_time, 1),
        requests_served=_requests_served,
    )


@app.get("/info")
async def info():
    """Model and service information."""
    return {
        "model": engine.MODEL_REPO,
        "parameters": "809M",
        "languages": "99+",
        "optimized_for": ["en", "ar"],
        "modes": {
            "fast": "Greedy decoding, ~2x faster, good for short voice messages",
            "accurate": "Beam search (5), best quality, good for long content",
        },
        "initial_prompt": "Bilingual EN/AR hint for language detection",
        "port": PORT,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=False,
    )
