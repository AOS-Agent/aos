# Event Bus

In-process pub/sub event bus. Services publish typed events; consumers subscribe and react. Keeps services decoupled — publishers don't know who is listening. Consumer modules under `consumers/` are discovered and wired at import time.

## Quick Reference
- **Port**: N/A (library)
- **Restart**: N/A
- **Logs**: `~/.aos/logs/bus.log`
- **Config**: N/A (code-level registration)

## Key Files
- `bus.py` — Pub/sub core: publish, subscribe, dispatch
- `consumer.py` — Base class all event consumers inherit from
- `consumers/` — Individual event handler modules (one file per concern)

## Debugging
- Check if running: `pgrep -f "bus/bus.py"`
- Tail logs: `tail -f ~/.aos/logs/bus.log`
