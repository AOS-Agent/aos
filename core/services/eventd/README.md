# Eventd

Event daemon on port 4097. Receives events from any AOS component and fans them out to registered consumers. Auto-discovers consumer modules at startup — drop a new consumer file in the right directory and it loads without a config change.

## Quick Reference
- **Port**: 4097
- **Restart**: `launchctl kickstart -k gui/$(id -u)/com.aos.eventd`
- **Logs**: `~/.aos/logs/eventd.log`
- **Config**: `~/.aos/config/eventd.yaml`

## Key Files
- `main.py` — HTTP server, event ingestion, and fan-out loop
- `discovery.py` — Scans for and registers consumer modules at startup

## Debugging
- Check if running: `pgrep -f "eventd/main.py"`
- Tail logs: `tail -f ~/.aos/logs/eventd.log`
