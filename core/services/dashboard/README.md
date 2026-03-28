# Dashboard

Flask-based web UI on port 4096. Displays live agent activity, registered agents, and system events via server-sent events (SSE). Provides a real-time view of what AOS is doing without requiring terminal access.

## Quick Reference
- **Port**: 4096
- **Restart**: `launchctl kickstart -k gui/$(id -u)/com.aos.dashboard`
- **Logs**: `~/.aos/logs/dashboard.log`
- **Config**: `~/.aos/config/dashboard.yaml`

## Key Files
- `main.py` — Flask routes and SSE event stream
- `agent_registry.py` — Tracks which agents are active and their last heartbeat
- `activity.py` — Aggregates recent events for the activity feed
- `templates/` — Jinja2 HTML templates for the web UI

## Debugging
- Check if running: `pgrep -f "dashboard/main.py"`
- Tail logs: `tail -f ~/.aos/logs/dashboard.log`
