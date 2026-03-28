---
name: extract
description: "Extract content from any social media URL — YouTube, Instagram, TikTok, X/Twitter. Auto-detects platform, pulls metadata, transcribes audio, and saves to vault. TRIGGER when: user pastes any social media link, asks to transcribe/summarize/extract a video or post, says 'what does this say', 'check this out', 'research this', or sends a URL from youtube.com, youtu.be, instagram.com, tiktok.com, x.com, or twitter.com. Also triggers on /capture with social media URLs. This skill replaces the separate instagram and youtube skills — use it for ALL social media content extraction."
---

# Skill: Content Extraction

Extract, transcribe, and research content from YouTube, Instagram, TikTok, and X/Twitter through one unified pipeline. Auto-detects the platform, pulls metadata, transcribes spoken audio via Whisper, and optionally saves structured vault notes.

## How It Works

The extraction engine lives at `apps/content-engine/`. It uses:

1. **yt-dlp** as the universal backend — handles metadata and audio download for all 4 platforms without login or API keys
2. **YouTube captions API** as the fast path for YouTube (instant, free) before falling back to Whisper
3. **mlx-whisper** (Apple Silicon optimized) for local audio transcription when captions aren't available
4. Outputs vault-compatible markdown with unified frontmatter for QMD indexing

### Supported Platforms

| Platform | Metadata | Transcription | Notes |
|----------|----------|---------------|-------|
| YouTube | Full | Captions → Whisper | Best support, captions usually available |
| Instagram | Full | Whisper only | No captions API, always transcribes audio |
| TikTok | Best-effort | Whisper | yt-dlp may be IP-blocked, falls back gracefully |
| X/Twitter | Video tweets | Whisper | Text-only tweets need Chrome MCP fallback |

### Fallback Chain

```
YouTube captions (free, instant)
  → mlx-whisper (Apple Silicon, ~10s per minute of audio)
    → openai-whisper CLI (last resort)
```

## Procedure

### Step 1: Read the user's intent

Before extracting anything, determine what the user actually needs. This shapes everything — how much work to do, whether to save, and how to respond.

| Intent | Signals | Extraction depth | Save to vault? |
|--------|---------|-----------------|---------------|
| **Quick question** | "what's this about?", "quickly tell me", "someone sent me this" | Metadata + transcript, summarize concisely | **No** — just answer |
| **Research / deep dive** | "research this", "analyze", bare URL with no context, "extract" | Full extraction + analysis | **Yes** |
| **Capture / archive** | `/capture`, "save this", "log this", "add to vault" | Full extraction | **Yes** |
| **Transcribe** | "transcribe this", "what does this say exactly" | Full extraction, show transcript | **Yes** (for reference) |

The key insight: not every link needs to be persisted. A casual "what's this reel about?" deserves a fast, direct answer — not a 2-minute extraction pipeline with vault save. Match your effort to the user's energy.

### Step 2: Detect the URL

Find the social media URL in the user's message. The engine accepts all common formats:

- `youtube.com/watch?v=...`, `youtu.be/...`, `youtube.com/shorts/...`
- `instagram.com/reel/...`, `instagram.com/p/...`, `instagram.com/tv/...`, `instagram.com/stories/...`
- `tiktok.com/@user/video/...`, `vm.tiktok.com/...`
- `x.com/user/status/...`, `twitter.com/user/status/...`
- URLs with tracking params (`?igsh=...`, `?utm_source=...`) are handled automatically

### Step 3: Run the extraction

**For quick questions** (user just wants to know what it's about):
```bash
cd ~/aos/apps/content-engine && python3 cli.py "URL" --force
```
No `--vault` flag. Just extract, read the output, answer the question, done.

**For research / capture / transcription** (user wants to keep it):
```bash
cd ~/aos/apps/content-engine && python3 cli.py "URL" --vault --force
```

**Metadata only** (when caption alone is enough — e.g., image posts, text tweets):
```bash
cd ~/aos/apps/content-engine && python3 cli.py "URL" --metadata-only
```

**Batch processing:**
```bash
cd ~/aos/apps/content-engine && python3 cli.py --batch urls.txt --vault
```

### Step 4: Respond based on intent

**Quick question** ("what's this about?", "someone sent me this"):
- Give a concise, conversational summary (2-4 sentences)
- Include the key takeaway — what is it, who made it, what's the point
- Match the user's tone — if they're casual, be casual
- Do NOT dump raw transcripts, metadata tables, or file paths
- Do NOT do tech relevance analysis

**Research / bare URL paste**:
- Provide a structured summary with key points
- Include metadata (author, engagement, hashtags)
- Show a transcript preview (first 10 lines) with link to full vault note
- Do the Tech Relevance analysis (see Step 5)

**Capture / archive**:
- Confirm what was saved and where
- Brief content summary
- File path to vault note

**Transcription request**:
- Show the transcript (or first 15-20 lines with "full transcript at [path]")
- Include timestamps

### Step 5: Tech Relevance Analysis (research/capture only)

Only run this for **research and capture** intents — NOT for quick questions.

After extracting, scan the caption + transcript for signals related to coding, AI, agents, automation, developer tools, or infrastructure.

If tech signals are found:

```
## Tech Relevance

**Topics**: [key topics]
**Verdict**: EXPLORE / IMPLEMENT / SKIP / ALREADY HAVE
**Why**: [2-3 sentences]
**Next steps**: [specific actions if EXPLORE/IMPLEMENT]
```

Skip for non-tech content entirely. Be honest — not everything is worth pursuing.

### Step 6: Initiative Source Linking (research/capture only)

Only run this for **research and capture** intents when `operator.yaml → initiatives.enabled: true`.

After saving to vault, check if the extracted content matches an active initiative in research phase:

```bash
# Check active initiatives
python3 ~/aos/core/work/cli.py initiatives 2>/dev/null
```

For each active initiative in research/shaping status:
- Compare the extracted content's topics/tags against the initiative's tags
- If there's a clear match, offer to link:
  "This content relates to your '{initiative_title}' initiative (currently in {status}). Link it as a research source?"

If operator approves:
1. Add the vault note path to the initiative's `sources:` frontmatter list
2. Update the initiative's `updated:` date

```python
# Example: update initiative frontmatter
# Read initiative doc
# Parse frontmatter
# Append to sources list
# Write back with atomic replacement
```

This is opt-in — always ask before linking. If no initiatives match, skip silently.

## CLI Flags Reference

| Flag | Effect |
|------|--------|
| `--metadata-only` | Skip transcription, just extract metadata (~2s) |
| `--vault` | Save result as vault markdown note |
| `--vault-dir PATH` | Custom vault directory |
| `--model SIZE` | Whisper model: tiny/base/small/medium/large (default: medium) |
| `--batch FILE` | Process multiple URLs from a file |
| `--force` | Reprocess even if URL was already extracted |
| `--json` | Output as JSON |

## Platform-Specific Notes

**TikTok**: yt-dlp may be IP-blocked. If metadata returns a minimal result (just content ID), try Chrome MCP as a fallback to get the visible text content.

**X/Twitter text-only tweets**: yt-dlp only handles video tweets. For text-only tweets, use Chrome MCP's `get_page_text` on the tweet URL, then format as caption with no transcript.

## Integration

- Vault notes land in `~/vault/knowledge/captures/`
- QMD indexes them every 30 minutes (or manually via `bin/qmd-reindex`)
- The `/recall` skill can find extracted content via semantic search
- Bridge `/capture` should route social media URLs here
- Dedup tracking prevents reprocessing the same URL twice

**Related skills:** recall (search extracted content), obsidian-cli (vault operations)
