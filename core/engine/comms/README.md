# Comms Engine

Communication intelligence layer. Resolves contact names and aliases to channel addresses, then routes outbound messages through the appropriate adapter. Supports 340 aliases across contacts. Adapters are pluggable — adding a new channel is a single file under `channels/`.

## Quick Reference
- **Port**: N/A (library)
- **Restart**: N/A
- **Logs**: `~/.aos/logs/comms.log`
- **Config**: `~/.aos/config/comms.yaml`

## Key Files
- `orchestrator.py` — Entry point: accept a message intent, resolve target, dispatch to adapter
- `resolver.py` — Name and alias resolution, 340 aliases (994 lines)
- `channels/` — One adapter per channel (Telegram, Slack, email, SMS)

## Debugging
- Check if running: `pgrep -f "comms/orchestrator.py"`
- Tail logs: `tail -f ~/.aos/logs/comms.log`
