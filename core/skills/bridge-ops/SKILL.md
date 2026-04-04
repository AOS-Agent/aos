---
name: bridge-ops
description: "Skill: Bridge Operations — diagnose, manage, and repair the Telegram bridge service. Trigger on: bridge down, bridge restart, voice not working, messages not delivering, forum topic routing, bridge logs, or any bridge service issue."
---

# Skill: Bridge Operations

Operational knowledge for diagnosing and managing the Telegram bridge system.

## Service Management

### Restart Bridge (safe procedure)
```bash
# 1. Check for running Claude subprocesses (they'll be killed on restart)
ps aux | grep "claude -p" | grep -v grep

# 2. Unload (stops the process)
launchctl unload ~/Library/LaunchAgents/com.aos.bridge.plist

# 3. Wait for Telegram lock to release (CRITICAL — prevents Conflict errors)
sleep 20

# 4. Load (starts fresh)
launchctl load ~/Library/LaunchAgents/com.aos.bridge.plist

# 5. Verify
sleep 3 && ps aux | grep "bridge.*main.py" | grep -v grep
```

### Check Service Status
```bash
# Bridge
launchctl list | grep com.aos.bridge
ps aux | grep "bridge.*main.py" | grep -v grep

# Listen server
curl -sf http://localhost:7600/jobs && echo "OK" || echo "DOWN"

# Dashboard
curl -sf http://localhost:4096/api/health && echo "OK" || echo "DOWN"
```

## Common Failure Modes

### 1. Telegram Conflict Error
**Symptom**: `telegram.error.Conflict: Conflict: terminated by other getUpdates request`
**Cause**: Two bridge instances running simultaneously
**Fix**: Kill all bridge processes, wait 20 seconds, restart one instance
```bash
pkill -f "core/services/bridge/main.py"
sleep 20
launchctl load ~/Library/LaunchAgents/com.aos.bridge.plist
```

### 2. Voice Not Transcribing
**Symptom**: Voice messages get no response, no `voice_transcriber` log entries
**Diagnose**: Check if voice handler is being triggered
```bash
grep -i "voice" logs/bridge.err.log | tail -20
```
**Common causes**:
- Voice sent in forum topic without topic routing
- Whisper model failed to load (check for import errors)
- ffmpeg not installed (needed for OGG→WAV conversion)

### 3. Messages Not Delivering (400 Bad Request)
**Symptom**: `sendMessage "HTTP/1.1 400 Bad Request"` in logs
**Common causes**:
- HTML formatting error (unclosed tags)
- Message too long (>4096 chars)
- Zero-width space (`\u200b`) rejected in some contexts
**Fix**: Check `telegram_formatter.py` output, ensure `_truncate()` is working

### 4. Bot Not Polling
**Symptom**: No `getUpdates` entries in recent logs
**Diagnose**:
```bash
tail -50 logs/bridge.err.log | grep getUpdates
```
**Fix**: Bridge likely crashed — check for Python tracebacks, restart

### 5. Forum Topic Not Routing
**Symptom**: Messages in a topic go to default handler instead of agent
**Diagnose**: Check `projects.yaml` in `~/.aos/config/` or `~/aos/config/`
**Fix**: Ensure `message_thread_id` matches the topic's actual thread ID

## Log Analysis

### Key log patterns
```bash
# Recent errors
grep -i "error\|fail\|traceback" logs/bridge.err.log | tail -20

# Voice transcription activity
grep -i "voice\|transcri\|whisper" logs/bridge.err.log | tail -20

# Message delivery
grep "sendMessage" logs/bridge.err.log | tail -20

# Bot polling
grep "getUpdates" logs/bridge.err.log | tail -5

# Reaction acknowledgments
grep "setMessageReaction" logs/bridge.err.log | tail -10
```

## File Locations

| File | Purpose |
|------|---------|
| `core/services/bridge/main.py` | Entry point — secrets, forum config, service startup |
| `core/services/bridge/telegram_channel.py` | Message handlers, streaming, buttons |
| `core/services/bridge/claude_cli.py` | Claude CLI wrapper, StreamEvent, session management |
| `core/services/bridge/voice_transcriber.py` | Whisper transcription (fast/accurate modes) |
| `core/services/bridge/telegram_formatter.py` | Markdown → Telegram HTML |
| `core/services/bridge/interactive_buttons.py` | Inline keyboards (option detection, quick actions) |
| `core/services/bridge/heartbeat.py` | 30-min health checks |
| `core/services/bridge/activity_client.py` | Dashboard activity logging |
| `data/bridge/sessions.json` | Per-user Claude session IDs |
| `~/Library/LaunchAgents/com.aos.bridge.plist` | LaunchAgent config |

## Session Management

Sessions stored in `data/bridge/sessions.json`:
```json
{
  "telegram:6679471412": "session-uuid-here",
  "telegram:<FORUM_GROUP_ID>:topic:2": "another-session-uuid"
}
```

- Direct chat key: `telegram:<chat_id>`
- Forum topic key: `telegram:<group_id>:topic:<thread_id>`
- Agent dispatch (`ask <agent> to...`) always starts fresh (no session resume)
- Regular messages resume the last session via `--resume <session_id>`

## Onboarding a New Agent Topic

1. Get the bot token: `bin/agent-secret get TELEGRAM_BOT_TOKEN`
2. Create topic via API:
   ```bash
   curl -s -X POST "https://api.telegram.org/bot<TOKEN>/createForumTopic" \
     -d "chat_id=<FORUM_GROUP_ID>&name=<AGENT_NAME>&icon_color=7322096"
   ```
3. Note the `message_thread_id` from the response
4. Add to `~/.aos/config/projects.yaml`:
   ```yaml
   projects:
     agent-name:
       path: ~/path/to/project
       telegram:
         forum_group_id: <GROUP_ID>
         forum_topic_id: <THREAD_ID>
   ```
5. Restart bridge (safe procedure above)
6. Test: send a message in the new topic
