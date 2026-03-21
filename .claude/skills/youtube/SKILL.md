---
name: youtube
description: "DEPRECATED — use the `extract` skill instead. It handles YouTube and all other social media platforms through a unified pipeline."
---

# YouTube — Redirected

This skill has been replaced by the unified **extract** skill, which handles YouTube, Instagram, TikTok, and X/Twitter through one pipeline.

Use the `extract` skill instead. It lives at `.claude/skills/extract/` and uses the content engine at `apps/content-engine/`.

The old transcription script at `apps/transcriber/transcribe.py` still works but is no longer maintained. The new engine is at `apps/content-engine/cli.py`.
