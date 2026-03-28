# Transcriber

Voice-to-text service on port 7602 using mlx-whisper. Receives audio files or raw audio data, runs local transcription on Apple Silicon via MLX, and returns text. Used by the bridge and listen workers to transcribe voice messages.

## Quick Reference
- **Port**: 7602
- **Restart**: `launchctl kickstart -k gui/$(id -u)/com.aos.transcriber`
- **Logs**: `~/.aos/logs/transcriber.log`
- **Config**: `~/.aos/config/transcriber.yaml`

## Key Files
- `main.py` — HTTP server, request handling
- `engine.py` — mlx-whisper model loader and transcription runner
- `client.py` — Thin client used by other services to call the transcriber

## Debugging
- Check if running: `pgrep -f "transcriber/main.py"`
- Tail logs: `tail -f ~/.aos/logs/transcriber.log`
