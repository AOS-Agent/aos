"""extractor — Extract full content from URLs using the crawler CLI."""

import asyncio
import json
from pathlib import Path

# Path to the crawler CLI, executed via the crawler venv's Python
CRAWLER_VENV_PYTHON = Path.home() / ".aos" / "services" / "crawler" / ".venv" / "bin" / "python"
CRAWLER_CLI = Path.home() / "aos" / "core" / "services" / "crawler" / "crawl_cli.py"

# Timeout per URL extraction
EXTRACT_TIMEOUT = 30  # seconds


async def extract_content(url: str, platform: str = "") -> dict | None:
    """Extract content from a URL using the crawler CLI.

    Calls: crawl_cli.py URL --format json
    Parses the JSON output and returns:
        {content: str, author: str, title: str, metadata: dict}
    Returns None on failure.
    """
    if not url:
        return None

    # Verify the crawler venv exists
    if not CRAWLER_VENV_PYTHON.exists():
        print(f"[extractor] Crawler venv not found: {CRAWLER_VENV_PYTHON}")
        return None

    if not CRAWLER_CLI.exists():
        print(f"[extractor] Crawler CLI not found: {CRAWLER_CLI}")
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            str(CRAWLER_VENV_PYTHON),
            str(CRAWLER_CLI),
            url,
            "--format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=EXTRACT_TIMEOUT,
        )

        if proc.returncode != 0:
            err_msg = stderr.decode().strip()[:200] if stderr else "unknown error"
            print(f"[extractor] Crawler failed for {url}: {err_msg}")
            return None

        raw = stdout.decode().strip()
        if not raw:
            print(f"[extractor] Empty output for {url}")
            return None

        data = json.loads(raw)
        return _normalize_output(data, platform)

    except asyncio.TimeoutError:
        print(f"[extractor] Timeout extracting {url}")
        # Kill the process if it's still running
        try:
            proc.kill()
        except Exception:
            pass
        return None
    except json.JSONDecodeError as e:
        print(f"[extractor] Invalid JSON from crawler for {url}: {e}")
        return None
    except Exception as e:
        print(f"[extractor] Error extracting {url}: {e}")
        return None


def _normalize_output(data: dict, platform: str) -> dict:
    """Normalize crawler output into a consistent format.

    The crawler CLI returns different shapes for tweets vs web pages:
    - Tweet: {url, author, handle, text, created_at, likes, ...}
    - Web:   {url, status, content, metadata}
    """
    # Twitter/X tweet format
    if "text" in data and "handle" in data:
        author_name = data.get("author", "")
        handle = data.get("handle", "")
        author = f"{author_name} (@{handle})" if author_name else handle
        return {
            "content": data.get("text", ""),
            "author": author,
            "title": f"Tweet by {author}",
            "metadata": {
                "likes": data.get("likes", 0),
                "retweets": data.get("retweets", 0),
                "views": data.get("views", 0),
                "created_at": data.get("created_at", ""),
            },
        }

    # Standard web page format
    metadata = data.get("metadata", {})
    title = metadata.get("title", "") if isinstance(metadata, dict) else ""
    author = metadata.get("author", "") if isinstance(metadata, dict) else ""

    return {
        "content": data.get("content", ""),
        "author": author,
        "title": title,
        "metadata": metadata if isinstance(metadata, dict) else {},
    }
