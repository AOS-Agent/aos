# WhatsApp Data Exploration — Session Prompt

Paste this into a fresh Claude Code session in `~/project/aos/`.

---

## Context

We have a unified comms pipeline. comms.db (`~/.aos/data/comms.db`) stores 248K+ messages across all channels with FTS5 full-text search. The comms bus service polls adapters every 5 minutes and writes new messages to comms.db.

**The WhatsApp problem:** We only have ~16K WhatsApp messages in comms.db. The WhatsApp Desktop local DB also has ~16K. But the actual phone has way more — the desktop app only syncs a subset. We need the full history.

Additionally, of those 16K messages, **5,365 have empty content** because they're media:
- 582 voice notes (no transcription, invisible to search)
- 2,418 images
- 385 videos
- 600 documents
- 103 audio files
- 879 missed calls (not logged as interactions)
- 756 reactions (not captured)

Voice notes are especially important — they're a major communication channel that's completely invisible right now.

## What I Need You To Figure Out

### 1. Historical Data — Get ALL WhatsApp Messages

The whatsmeow bridge (`~/.aos/services/whatsmeow/`) connects to WhatsApp via the multi-device API. Investigate:

- Can whatsmeow fetch historical messages beyond what's on the desktop app?
- What does the WhatsApp multi-device API actually provide? (message history limits, media access)
- Is there a way to request a full data export from WhatsApp (GDPR export, Google Drive backup, etc.) and import that?
- Can we read the iPhone's WhatsApp backup (iCloud or local iTunes backup) to get the full history?
- What's the realistic maximum history we can get, and through which method?

**Check these files for current implementation:**
- `core/engine/comms/channels/whatsapp.py` — bridge HTTP adapter
- `core/engine/comms/channels/whatsapp_local.py` — desktop SQLite adapter
- `core/services/whatsmeow/` — the Go bridge itself

### 2. Voice Notes — Transcription Pipeline

582 voice notes exist as media files but have zero content in comms.db. We need:

- Where are the voice note audio files stored? (WhatsApp Desktop media directory, whatsmeow downloads?)
- Can the whatsmeow bridge download voice note audio?
- We have a transcription service running (`com.aos.transcriber` on the machine). How do we pipe voice notes through it?
- Design a pipeline: new voice note arrives → download audio → transcribe → write transcript to comms.db `content` field
- For historical voice notes: can we batch-transcribe the existing 582?

**Check:** `core/services/transcriber/` for the existing Whisper service.

### 3. Missed Calls — Log as Interactions

879 missed calls in the WhatsApp local DB (type=10). These should be:
- Logged as interactions in people.db (resolve caller → person_id)
- Stored in comms.db with `content = "[Missed call]"` or similar
- Queryable: "who called me this week?"

### 4. Reactions — Capture Intent Signals

756 reactions (type=59). These carry meaning:
- A thumbs-up on a message = acknowledgment
- A heart = strong positive signal
- Worth capturing as `message_entities` with type="reaction"

### 5. Media Messages — Smart Handling

Images, videos, documents are heavy. Don't store binary content. But:
- Can we get captions/descriptions? (WhatsApp allows captions on media)
- For forwarded content: the WhatsApp local DB has `ZISFROMME` and `ZFROMJID` — can we detect forwards vs originals?
- For documents: can we at least store the filename/type?
- For images: is there a lightweight way to get a description? (local vision model, or just store metadata)

### 6. Going Forward — Real-Time Media Pipeline

The comms bus polls every 5 min. For new messages:
- Text: already flowing into comms.db ✓
- Voice notes: need download → transcribe → store pipeline
- Media: need metadata extraction (captions, filenames, dimensions)
- Calls: need to be detected and logged
- Reactions: need to be captured

Design this as extensions to the existing `CommsStoreConsumer` or as a new `MediaPipelineConsumer`.

## Deliverables

1. **Report**: What's possible for full history recovery and through which method
2. **Voice pipeline**: Working prototype that transcribes voice notes into comms.db
3. **Call/reaction ingestion**: Extend the WhatsApp adapter or add a new consumer
4. **Media metadata**: At minimum, store type + filename + caption for media messages

## Key Paths

```
~/.aos/data/comms.db                              — unified message store
~/.aos/data/people.db                             — identity layer
~/.aos/services/whatsmeow/                        — WhatsApp bridge
~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite  — desktop DB
core/engine/comms/channels/whatsapp.py            — bridge adapter
core/engine/comms/channels/whatsapp_local.py      — local DB adapter
core/comms/consumers/comms_store.py               — bus → comms.db writer
core/services/comms_bus/main.py                   — bus daemon
core/services/transcriber/                        — Whisper transcription service
```
