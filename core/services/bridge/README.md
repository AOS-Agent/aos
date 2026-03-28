# Bridge

Telegram/Slack messaging bridge that connects external chat channels to the AOS agent runtime. Routes incoming messages through an intent classifier to dispatch quick commands, manages conversation sessions, and sends outbound agent responses back to the originating channel. Runs as a persistent daemon with no HTTP port.

## Quick Reference
- **Port**: daemon (no port)
- **Restart**: `launchctl kickstart -k gui/$(id -u)/com.aos.bridge`
- **Logs**: `~/.aos/logs/bridge.log`
- **Config**: `~/.aos/config/bridge-topics.yaml`

## Key Files
- `main.py` — Entry point, wires channels to the agent runtime
- `telegram_channel.py` — Telegram polling loop and outbound sender (1774 lines)
- `intent_classifier.py` — Classifies messages as quick commands vs. agent tasks (1147 lines)
- `daily_briefing.py` — Scheduled morning briefing composer and sender
- `session_manager.py` — Tracks per-user conversation sessions

## Debugging
- Check if running: `pgrep -f "bridge/main.py"`
- Tail logs: `tail -f ~/.aos/logs/bridge.log`
