"""Qareen Voice Pipeline.

Browser audio -> WebSocket -> VAD -> STT -> EventBus -> SSE -> Frontend

Components:
    websocket.py  — FastAPI WebSocket endpoint (/ws/audio)
    manager.py    — VoiceManager orchestrates VAD + STT + event emission
    silero_vad.py — Silero VAD v5 ONNX wrapper (optional, falls back to energy VAD)
"""
