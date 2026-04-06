"""enricher — Generate summaries from extracted content. No LLM required."""

import re

# Sentence-ending patterns
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')

# Markdown patterns to strip
_MD_HEADING = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_MD_IMAGE = re.compile(r'!\[[^\]]*\]\([^)]+\)')
_MD_BOLD_ITALIC = re.compile(r'[*_]{1,3}([^*_]+)[*_]{1,3}')
_MD_CODE_BLOCK = re.compile(r'```[\s\S]*?```')
_MD_INLINE_CODE = re.compile(r'`([^`]+)`')
_MD_BLOCKQUOTE = re.compile(r'^>\s*', re.MULTILINE)
_MD_HR = re.compile(r'^---+\s*$', re.MULTILINE)

MAX_SUMMARY_CHARS = 300


def summarize(content: str, max_sentences: int = 3) -> str:
    """Extract first N sentences as a summary.

    - Strips markdown formatting (headings, links, code blocks)
    - Splits by sentence boundaries (. ! ?)
    - Returns first max_sentences sentences
    - Caps at 300 characters
    """
    if not content:
        return ""

    text = _strip_markdown(content)

    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    if not text:
        return ""

    # Split into sentences
    sentences = _SENTENCE_RE.split(text)

    # Filter out empty or very short fragments
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

    if not sentences:
        # No proper sentences found — return truncated text
        return text[:MAX_SUMMARY_CHARS].rstrip() + ("..." if len(text) > MAX_SUMMARY_CHARS else "")

    # Take first N sentences
    selected = sentences[:max_sentences]
    summary = " ".join(selected)

    # Cap at max chars
    if len(summary) > MAX_SUMMARY_CHARS:
        # Truncate at a word boundary
        truncated = summary[:MAX_SUMMARY_CHARS]
        last_space = truncated.rfind(" ")
        if last_space > MAX_SUMMARY_CHARS // 2:
            truncated = truncated[:last_space]
        summary = truncated.rstrip(".,;:!? ") + "..."

    return summary


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting from text."""
    # Remove code blocks first (they can contain other patterns)
    text = _MD_CODE_BLOCK.sub(' ', text)
    # Remove images
    text = _MD_IMAGE.sub(' ', text)
    # Convert links to just their text
    text = _MD_LINK.sub(r'\1', text)
    # Remove headings markers
    text = _MD_HEADING.sub('', text)
    # Remove bold/italic markers
    text = _MD_BOLD_ITALIC.sub(r'\1', text)
    # Remove inline code backticks
    text = _MD_INLINE_CODE.sub(r'\1', text)
    # Remove blockquote markers
    text = _MD_BLOCKQUOTE.sub('', text)
    # Remove horizontal rules
    text = _MD_HR.sub('', text)

    return text
