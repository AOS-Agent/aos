"""Bridge-side handle on the one humanization layer (aos#235).

The layer itself is `core/infra/reconcile/alert_copy.py` — named for where it
started, used now by every sender (see MESSAGE_STYLE.md → "The five rules").
The bridge runs out of its own venv with only its own directory on `sys.path`,
so this module loads that file by path and re-exports the three functions the
bridge needs.

If the load fails the bridge keeps working with conservative built-in copy
rather than falling back to raw internal strings: a missing humanizer must not
be the reason a stack trace reaches the operator's phone.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger("aos.bridge.message_copy")

_ALERT_COPY = (Path(__file__).resolve().parents[3]
               / "core" / "infra" / "reconcile" / "alert_copy.py")

# Conservative copy for the (unexpected) case where the layer cannot be loaded.
_FALLBACK_JOB = {
    "dispatch_failed": "😕 Couldn't start that job — I'll try again in a moment.",
    "started": "🔄 On it.",
    "working": "🔄 Still working on it.",
    "done": "✅ Done.",
    "failed": "😕 That job didn't finish. Details are in the log.",
    "timeout": ("⏰ That job is taking much longer than it should, so I've stopped "
                "waiting on it. Details are in the log."),
    "error": "😕 Something went wrong starting that job. Details are in the log.",
}


def _load():
    spec = importlib.util.spec_from_file_location("aos_alert_copy", _ALERT_COPY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _copy = _load()
    humanize_job_report = _copy.humanize_job_report
    humanize_tool_status = _copy.humanize_tool_status
    humanize_notice = _copy.humanize_notice
except Exception as e:  # noqa: BLE001 — never let copy polish break the bridge
    logger.error(f"Could not load the humanization layer ({e}) — using fallback copy")

    def humanize_job_report(stage: str, detail: str | None = None) -> str:  # type: ignore[misc]
        return _FALLBACK_JOB.get(stage, _FALLBACK_JOB["working"])

    def humanize_tool_status(description: str | None) -> str:  # type: ignore[misc]
        return "Working on it…"

    def humanize_notice(text: str | None, kind: str = "info") -> str:  # type: ignore[misc]
        return "Something needs a look on my end. Details are in the log."
