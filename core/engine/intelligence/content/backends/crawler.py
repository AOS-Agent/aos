"""crawler backend — extract content from arbitrary URLs via crawl_cli.py."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..result import ExtractionError, ExtractionResult

CRAWLER_VENV_PYTHON = Path.home() / ".aos" / "services" / "crawler" / ".venv" / "bin" / "python"

_RUNTIME_CLI = Path.home() / "aos" / "core" / "services" / "crawler" / "crawl_cli.py"
_DEV_CLI = Path.home() / "project" / "aos" / "core" / "services" / "crawler" / "crawl_cli.py"
CRAWLER_CLI = _RUNTIME_CLI if _RUNTIME_CLI.exists() else _DEV_CLI


async def extract(url: str, *, platform: str = "", timeout: float = 30.0) -> ExtractionResult:
    if not url:
        raise ExtractionError("empty url", url=url, backend="crawler")

    if not CRAWLER_VENV_PYTHON.exists():
        raise ExtractionError(
            f"crawler venv not found: {CRAWLER_VENV_PYTHON}",
            url=url,
            backend="crawler",
        )

    if not CRAWLER_CLI.exists():
        raise ExtractionError(
            f"crawler CLI not found: {CRAWLER_CLI}",
            url=url,
            backend="crawler",
        )

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            str(CRAWLER_VENV_PYTHON),
            str(CRAWLER_CLI),
            url,
            "--format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        raise ExtractionError(
            f"timeout after {timeout}s",
            url=url,
            backend="crawler",
        ) from exc

    if proc.returncode != 0:
        err_msg = stderr.decode().strip()[:200] if stderr else "unknown error"
        raise ExtractionError(
            f"crawler exited {proc.returncode}: {err_msg}",
            url=url,
            backend="crawler",
        )

    raw = stdout.decode().strip()
    if not raw:
        raise ExtractionError("empty crawler output", url=url, backend="crawler")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"invalid JSON from crawler: {exc}",
            url=url,
            backend="crawler",
        ) from exc

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    return ExtractionResult(
        url=data.get("url") or url,
        platform=platform,
        title=data.get("title", "") or "",
        author=data.get("author", "") or "",
        content=data.get("content", "") or "",
        metadata=metadata,
        media=[],
        links=[],
        backend="crawler",
    )
