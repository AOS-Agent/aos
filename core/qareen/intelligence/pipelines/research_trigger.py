"""Research Trigger Pipeline — vault search on questions.

Subscribes to stream.unit events. When a unit is classified as a
question, queries QMD for relevant vault results and emits a
research.result event with the findings.

Emits: research.result
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime

from ...events.types import Event
from .base import Pipeline

logger = logging.getLogger(__name__)

# Maximum time to wait for a QMD query
_QMD_TIMEOUT_S = 5


class ResearchTriggerPipeline(Pipeline):
    """Trigger vault research when questions are detected in the stream."""

    def wire(self) -> None:
        self._bus.subscribe("stream.unit", self._on_unit)

    async def _on_unit(self, event: Event) -> None:
        try:
            payload = event.payload
            classification = payload.get("classification", "")
            if classification != "question":
                return

            text = payload.get("text", "").strip()
            if not text:
                return

            unit_id = payload.get("id", "")
            thread_id = payload.get("thread_id", "")

            vault_results = await self._query_vault(text)

            await self._bus.emit(Event(
                event_type="research.result",
                timestamp=datetime.now(),
                source="research_trigger",
                payload={
                    "query": text,
                    "thread_id": thread_id,
                    "unit_id": unit_id,
                    "vault_results": vault_results,
                    "source": "vault",
                },
            ))

            logger.info(
                "ResearchTrigger: emitted research.result for %r (%d results)",
                text[:60],
                len(vault_results),
            )
        except Exception:
            logger.exception(
                "ResearchTrigger: failed processing stream.unit"
            )

    async def _query_vault(self, text: str) -> list[dict]:
        """Run a QMD query via subprocess and return parsed results."""
        qmd_bin = shutil.which("qmd") or "/opt/homebrew/bin/qmd"

        try:
            proc = await asyncio.create_subprocess_exec(
                qmd_bin, "query", text, "-n", "3", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.debug("ResearchTrigger: qmd binary not found")
            return []

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_QMD_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("ResearchTrigger: qmd query timed out")
            return []

        if proc.returncode != 0:
            logger.debug(
                "ResearchTrigger: qmd exited with code %d", proc.returncode
            )
            return []

        raw = stdout.decode().strip()
        if not raw:
            return []

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "results" in parsed:
                return parsed["results"]
            return []
        except (json.JSONDecodeError, ValueError):
            logger.debug("ResearchTrigger: failed to parse qmd JSON output")
            return []
