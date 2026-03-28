"""Long-form response handler — vault save + Telegraph publish.

When Claude's response exceeds LONGFORM_THRESHOLD, this module:
1. Saves the full markdown to vault/log/
2. Publishes to Telegraph for Instant View reading
3. Extracts a summary (first heading + first paragraph) for inline Telegram delivery
"""

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("bridge.longform")

LONGFORM_THRESHOLD = 6000  # chars — about 1.5 Telegram messages
VAULT_RESPONSES_DIR = Path.home() / "vault" / "log"


def _slugify(text: str, max_len: int = 40) -> str:
    """Create a filesystem-safe slug from text."""
    # Strip markdown formatting
    slug = re.sub(r'[#*_`\[\]()]', '', text)
    # Keep alphanumeric, spaces, hyphens
    slug = re.sub(r'[^\w\s-]', '', slug)
    # Collapse whitespace to hyphens
    slug = re.sub(r'\s+', '-', slug.strip())
    # Lowercase and truncate
    return slug.lower()[:max_len].rstrip('-') or "response"


def _extract_summary(markdown: str, max_chars: int = 500) -> str:
    """Extract the first heading + first paragraph as a natural summary.

    Returns markdown text suitable for Telegram display.
    """
    lines = markdown.split('\n')
    heading = ""
    paragraph_lines = []
    found_heading = False
    in_paragraph = False

    for line in lines:
        stripped = line.strip()

        # Grab first heading
        if not found_heading and stripped.startswith('#'):
            heading = stripped.lstrip('#').strip()
            found_heading = True
            continue

        # Skip empty lines between heading and first paragraph
        if found_heading and not in_paragraph and not stripped:
            continue

        # Collect first paragraph (non-empty lines after heading)
        if found_heading and stripped:
            in_paragraph = True
            # Stop at next heading, list, table, or code block
            if stripped.startswith('#') or stripped.startswith('```'):
                break
            paragraph_lines.append(stripped)
            # Enough for a preview
            if len(' '.join(paragraph_lines)) > max_chars:
                break
        elif in_paragraph and not stripped:
            # End of first paragraph
            break

    # If no heading found, use first ~500 chars
    if not heading and not paragraph_lines:
        # Take first meaningful lines
        text = markdown[:max_chars].rsplit('\n', 1)[0]
        return text.strip()

    parts = []
    if heading:
        parts.append(f"**{heading}**")
    if paragraph_lines:
        para = ' '.join(paragraph_lines)
        if len(para) > max_chars:
            para = para[:max_chars].rsplit(' ', 1)[0] + "..."
        parts.append(para)

    return '\n\n'.join(parts)


def _extract_title(markdown: str) -> str:
    """Extract the first heading as a title, or generate one from content."""
    for line in markdown.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#'):
            return stripped.lstrip('#').strip()
    # No heading — use first 60 chars
    first_line = markdown.strip().split('\n')[0]
    return first_line[:60].strip() or "Response"


def _save_to_vault(title: str, markdown: str, metadata: dict | None = None) -> Path:
    """Save markdown response to vault with YAML frontmatter.

    Returns the path to the saved file.
    """
    VAULT_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    slug = _slugify(title)
    filename = f"{now.strftime('%Y%m%d-%H%M')}-{slug}.md"
    filepath = VAULT_RESPONSES_DIR / filename

    # Build frontmatter
    meta = metadata or {}
    frontmatter_lines = [
        "---",
        f'date: "{now.strftime("%Y-%m-%d")}"',
        f'time: "{now.strftime("%H:%M")}"',
        'type: longform-response',
    ]
    if meta.get("source_url"):
        frontmatter_lines.append(f'source_url: "{meta["source_url"]}"')
    if meta.get("session_id"):
        frontmatter_lines.append(f'session_id: "{meta["session_id"]}"')
    if meta.get("user_query"):
        # Escape quotes in user query
        query = meta["user_query"][:200].replace('"', '\\"')
        frontmatter_lines.append(f'user_query: "{query}"')
    frontmatter_lines.append('tags: [longform, response, auto-saved]')
    frontmatter_lines.append("---")
    frontmatter_lines.append("")

    content = '\n'.join(frontmatter_lines) + markdown

    filepath.write_text(content, encoding='utf-8')
    logger.info(f"Saved longform response to {filepath}")
    return filepath


def publish_longform(
    markdown: str,
    title: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Save to vault + publish to Telegraph. Returns paths and summary.

    Args:
        markdown: The full markdown response text
        title: Optional title (auto-extracted from first heading if not given)
        metadata: Optional dict with source_url, session_id, user_query

    Returns:
        {
            "vault_path": Path to saved vault file,
            "telegraph_url": Telegraph article URL (or None on failure),
            "summary": Extracted summary text (markdown),
            "title": The title used,
        }
    """
    if not title:
        title = _extract_title(markdown)

    summary = _extract_summary(markdown)

    # Save to vault (always succeeds or raises)
    vault_path = _save_to_vault(title, markdown, metadata)

    # Publish to Telegraph (best-effort — don't fail the whole flow)
    telegraph_url = None
    try:
        from telegraph_publisher import publish_md
        telegraph_url = publish_md(title, markdown)
        logger.info(f"Published to Telegraph: {telegraph_url}")
    except Exception as e:
        logger.error(f"Telegraph publish failed: {e}")

    return {
        "vault_path": vault_path,
        "telegraph_url": telegraph_url,
        "summary": summary,
        "title": title,
    }


def is_longform(text: str) -> bool:
    """Check if text exceeds the longform threshold."""
    return len(text) > LONGFORM_THRESHOLD
