"""Friction sensor — the eval-gated LLM judge over recent sessions.

Extracts operator messages from Claude Code session transcripts modified
in the window, runs the friction judge on each, and writes one signal
per REAL friction moment (label != none, machine_text false).

HARD GATE (council 2026-07-21, enforced here in code): the judge runs
ONLY if ~/.aos/data/loop/judge-gate-pass.json exists and its
judge_version matches judge.version_hash() — i.e. the CURRENT prompt +
model combination passed the frozen eval set. Any prompt or model edit
changes the hash and silently re-locks this sensor until re-gated via
`loop-eval run --record`.

Quota politeness: a 429/limit response aborts the remaining batch
gracefully (partial results stand; watermark does not advance past the
last judged session) — never retry into an exhausted subscription.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import judge as judge_mod
from .. import llm, signals

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
PASS_MARKER = Path.home() / ".aos" / "data" / "loop" / "judge-gate-pass.json"
WATERMARK = Path.home() / ".aos" / "data" / "loop" / "friction-watermark.json"

MAX_MESSAGES_PER_RUN = 300  # nightly budget cap
_SKIP_PREFIXES = (
    "<local-command", "<command-name", "<command-message", "<command-args",
    "<task-notification", "<local-command-stdout", "[Request interrupted",
    "This session is being continued",
)


class GateNotPassed(RuntimeError):
    """The current judge version has no matching gate-pass marker."""


def gate_check() -> dict:
    """Return the pass marker if valid for the current judge; raise otherwise."""
    if not PASS_MARKER.exists():
        raise GateNotPassed("no gate-pass marker — run: loop-eval run --record")
    marker = json.loads(PASS_MARKER.read_text())
    current = judge_mod.version_hash()
    if marker.get("judge_version") != current:
        raise GateNotPassed(
            f"judge changed (marker {marker.get('judge_version')} != current "
            f"{current}) — re-run the eval gate before this sensor may write"
        )
    return marker


def _extract_user_messages(session_file: Path, max_chars: int = 600):
    """Yield (idx, text, prev_assistant_snippet) for human-facing user turns."""
    prev_assistant = None
    idx = 0
    try:
        lines = session_file.read_text(errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        rtype = rec.get("type")
        msg = rec.get("message") or {}
        if rtype == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if texts:
                    prev_assistant = " ".join(texts)[-300:]
            continue
        if rtype != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            text = "\n".join(p for p in parts if p)
        elif isinstance(content, str):
            text = content
        else:
            continue
        text = (text or "").strip()
        if not text or text.startswith(_SKIP_PREFIXES):
            continue
        yield idx, text[:max_chars], prev_assistant
        idx += 1


async def run(window_hours: int = 24, projects_dir: Path | None = None) -> dict:
    """Judge recent session messages, write friction signals.

    Returns a summary dict: {sessions, judged, signals, aborted_on_limit}.
    Raises GateNotPassed if the judge isn't gate-approved.
    """
    gate_check()
    root = projects_dir or CLAUDE_PROJECTS
    if not root.exists():
        return {"sessions": 0, "judged": 0, "signals": 0, "aborted_on_limit": False}

    import time

    cutoff = time.time() - window_hours * 3600
    files = sorted(
        (p for p in root.glob("*/*.jsonl") if p.stat().st_mtime > cutoff),
        key=lambda p: p.stat().st_mtime,
    )

    judged = written = 0
    aborted = False
    for sf in files:
        if judged >= MAX_MESSAGES_PER_RUN or aborted:
            break
        session_key = sf.stem[:8]
        for idx, text, prev in _extract_user_messages(sf):
            if judged >= MAX_MESSAGES_PER_RUN:
                break
            try:
                verdict = await judge_mod.judge(text, prev)
            except llm.LLMError as exc:
                if "429" in str(exc) or "limit" in str(exc).lower():
                    aborted = True  # quota exhausted — stop politely
                    break
                continue  # single-message failure: skip, keep going
            judged += 1
            if verdict["machine_text"] or verdict["label"] == "none":
                continue
            signals.append_signal(
                sensor="friction_judge",
                signal_type=f"friction_{verdict['label']}",
                payload={
                    "excerpt": text[:200],
                    "label": verdict["label"],
                    "session": session_key,
                    "msg_idx": idx,
                },
                source_refs=[f"session:{sf.stem}:{idx}"],
                tainted=False,  # operator's own words — first-party
            )
            written += 1

    return {
        "sessions": len(files),
        "judged": judged,
        "signals": written,
        "aborted_on_limit": aborted,
    }
