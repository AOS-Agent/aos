# Listen

Job queue server on port 7600. Accepts task payloads over HTTP, queues them, and dispatches them to background workers. Decouples slow or async work (transcription, indexing, agent calls) from the services that trigger it.

## Quick Reference
- **Port**: 7600
- **Restart**: `launchctl kickstart -k gui/$(id -u)/com.aos.listen`
- **Logs**: `~/.aos/logs/listen.log`
- **Config**: `~/.aos/config/listen.yaml`

## Key Files
- `main.py` — HTTP server and queue manager
- `worker.py` — Worker pool that pulls and executes jobs from the queue

## Debugging
- Check if running: `pgrep -f "listen/main.py"`
- Tail logs: `tail -f ~/.aos/logs/listen.log`
