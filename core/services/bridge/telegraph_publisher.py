"""Publish Markdown files to Telegraph for in-app reading in Telegram."""

import logging
import re
import subprocess
from pathlib import Path

import mistune
from mistune.plugins.table import table
from telegraph import Telegraph

logger = logging.getLogger(__name__)

# Load token from Keychain
_TOKEN = None


def _get_telegraph() -> Telegraph:
    global _TOKEN
    if _TOKEN is None:
        try:
            result = subprocess.run(
                ["bin/agent-secret", "get", "TELEGRAPH_TOKEN"],
                capture_output=True, text=True, timeout=5,
                cwd=str(Path.home() / "aos"),
            )
            _TOKEN = result.stdout.strip()
        except Exception as e:
            logger.error(f"Failed to get Telegraph token: {e}")
            raise
    t = Telegraph(access_token=_TOKEN)
    return t


def publish_md(title: str, md_content: str) -> str:
    """Convert Markdown to a Telegraph page and return the URL.

    Returns the full telegra.ph URL for Instant View in Telegram.
    """
    t = _get_telegraph()

    # Convert MD to HTML via mistune
    md = mistune.create_markdown(plugins=[table])
    html_content = md(md_content)

    # Telegraph doesn't support all HTML tags.
    # Allowed: p, h3, h4, ul, ol, li, blockquote, pre, code, a, img, br, hr, figure
    # Map unsupported tags:
    html_content = re.sub(r"<h1[^>]*>", "<h3>", html_content)
    html_content = html_content.replace("</h1>", "</h3>")
    html_content = re.sub(r"<h2[^>]*>", "<h3>", html_content)
    html_content = html_content.replace("</h2>", "</h3>")
    html_content = re.sub(r"<h5[^>]*>", "<h4>", html_content)
    html_content = html_content.replace("</h5>", "</h4>")
    html_content = re.sub(r"<h6[^>]*>", "<h4>", html_content)
    html_content = html_content.replace("</h6>", "</h4>")

    # Convert table to simple text (Telegraph doesn't support tables)
    html_content = re.sub(r"</?table[^>]*>", "", html_content)
    html_content = re.sub(r"</?thead[^>]*>", "", html_content)
    html_content = re.sub(r"</?tbody[^>]*>", "", html_content)
    html_content = re.sub(r"<tr[^>]*>", "<p>", html_content)
    html_content = html_content.replace("</tr>", "</p>")
    html_content = re.sub(r"<t[hd][^>]*>", "", html_content)
    html_content = re.sub(r"</t[hd]>", " | ", html_content)

    response = t.create_page(
        title=title,
        html_content=html_content,
        author_name="AOS",
    )
    return f"https://telegra.ph/{response['path']}"
