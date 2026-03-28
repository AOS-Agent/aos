"""Vault note generation — unified markdown output for all platforms."""

import re
from datetime import datetime
from pathlib import Path

from models import ExtractionResult

VAULT_DIR = Path.home() / "vault" / "materials" / "content-extract"


def sanitize_filename(text: str, max_len: int = 60) -> str:
    safe = re.sub(r'[^\w\s-]', '', text)
    safe = re.sub(r'\s+', '_', safe).strip('_')
    return safe[:max_len]


def generate_vault_note(result: ExtractionResult) -> str:
    """Generate vault-compatible markdown from an extraction result."""
    now = datetime.now()
    engagement = result.engagement.to_dict()
    hashtags_yaml = ", ".join(f'"{t}"' for t in result.hashtags[:10])

    # Build frontmatter
    md = f"""---
date: "{now.strftime('%Y-%m-%d')}"
time: "{now.strftime('%H:%M')}"
type: content-extract
platform: {result.platform}
content_type: {result.content_type}
content_id: "{result.content_id}"
source_url: "{result.url}"
author: "{result.author}"
author_id: "{result.author_id}"
duration: {result.duration}
engagement: {{{', '.join(f'{k}: {v}' for k, v in engagement.items())}}}
hashtags: [{hashtags_yaml}]
transcript_source: "{result.transcript_source}"
tags: [material, content-extract, {result.platform}]
---

{result.url}

## Metadata

- **Author**: {result.author} ({result.author_id})
- **Platform**: {result.platform} / {result.content_type}
- **Duration**: {result.duration}s
"""

    if engagement:
        stats = " | ".join(f"**{k.title()}**: {v:,}" for k, v in engagement.items())
        md += f"- {stats}\n"

    if result.upload_date:
        md += f"- **Uploaded**: {result.upload_date}\n"

    # Caption / description
    if result.description:
        md += f"\n## Caption\n\n{result.description}\n"

    # Transcript
    if result.has_transcript:
        md += f"\n## Transcript\n\n{result.transcript}\n"

    # Source context (if from a message)
    if result.source_context:
        ctx = result.source_context
        md += f"\n## Source Context\n\n"
        md += f"- **Sent by**: {ctx.sender}\n"
        if ctx.message:
            md += f"- **Message**: {ctx.message}\n"
        if ctx.chat:
            md += f"- **Chat**: {ctx.chat}\n"
        md += f"- **Via**: {ctx.platform}\n"

    return md


def save_vault_note(result: ExtractionResult, vault_dir: str | None = None) -> str:
    """Save extraction result as a vault note. Returns the file path."""
    target_dir = Path(vault_dir) if vault_dir else VAULT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    author_safe = sanitize_filename(result.author_id or result.author or "unknown", 30)
    filename = f"{now.strftime('%Y%m%d-%H%M')}-{result.platform}-{author_safe}-{result.content_id}.md"

    note_content = generate_vault_note(result)
    note_path = target_dir / filename
    note_path.write_text(note_content)

    result.vault_path = str(note_path)
    return str(note_path)
