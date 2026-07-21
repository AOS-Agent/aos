"""Haiku extraction via the subscription (`claude --print`), with process-group
safety so a killed run never orphans quota-burning children.

WHY PROCESS GROUPS. Sample §8 observed the failure directly: when the driver was
killed mid-run, its in-flight `claude` children were reparented and KEPT RUNNING —
each still consuming subscription quota. `claude --print` also spawns its own node
subprocess, so killing just the `claude` pid can leave that grandchild alive.
Every worker is therefore spawned in its OWN session/process group
(`start_new_session=True`, so pgid == child pid), tracked in a thread-safe
`LiveGroups` registry, and torn down with `killpg` on the whole group. A SIGTERM/
SIGINT to the engine propagates to every live group before exit. Finished calls
unregister and reap normally; only the kill path uses the group teardown.

The invocation is the proven subscription path (sample §1, recovered from the
harness): `claude --print --model <m> --system-prompt <s> --output-format json`,
prompt on stdin, JSON envelope out. No API key, no anthropic SDK.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.engine.comms.enrich.authcheck import is_auth_failure

# Frozen extraction prompt (sample §5) + the recommended v1-final tweak that
# kills the two observed mistypes (rhetorical title → question, past reference →
# event): the "literal requests / scheduled items only" clause.
SYSTEM_PROMPT = """You are an entity-extraction engine for a personal knowledge system.
You are given a batch of messages (each with an id) from one person on one day.
Extract typed entities that are ACTUALLY, EXPLICITLY present. Do not infer,
guess, or invent. If the batch contains nothing extractable (small talk,
acknowledgements, emoji), return an empty entities array. Precision over recall.

Entity types (use exactly these type strings):
- "topic": a concrete subject discussed. fields: {value}
- "commitment": someone promised/agreed to do something. fields: {who, what, due}
- "transaction": money moved or a purchase/invoice/payment. fields: {merchant, amount, direction}  (direction = "outgoing"|"incoming"|"unknown")
- "event": a scheduled/booked event or meeting. fields: {value, when}
- "mention": a third person referenced by name. fields: {value}  (the person's name)
- "question_open": a question asked that expects an answer. fields: {value}

Only extract questions/events that are literal requests or scheduled items, not
rhetorical titles or references to past events.

Output STRICT JSON only, this exact shape, and nothing else — no preamble, no
explanation, no markdown fences:
{"entities":[{"type":"...", "fields":{...}, "confidence":0.0, "source_ids":["<msg id>"]}]}

confidence: your calibrated 0-1 certainty the entity is real and correctly typed.
source_ids: the message id(s) the entity came from. Every entity MUST cite >=1 id.
Unknown field values: use null. Never fabricate names, amounts, or dates."""


def build_prompt(batch, *, max_msg_chars: int) -> str:
    """User prompt: batch header + `[id] (dir) content` per message."""
    msgs = batch.messages
    channel = batch.channel
    header = [f"Batch: {len(msgs)} message(s), channel={channel}."]
    header.append("Messages:")
    for m in msgs:
        content = (m.get("content") or "").replace("\n", " ").strip()
        if len(content) > max_msg_chars:
            content = content[:max_msg_chars] + "…"
        direction = m.get("direction") or "?"
        header.append(f'[{m.get("id")}] ({direction}) {content}')
    header.append("\nExtract entities as strict JSON now.")
    return "\n".join(header)


def parse_result(text: str | None) -> dict | None:
    """Parse the model's `result` string into the envelope dict, tolerating
    stray fences/prose around the JSON (sample-proven fallbacks)."""
    if not text:
        return None
    s = text.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


# ── Process-group registry ────────────────────────────────────────────────────

def terminate_group(pid: int, sig: int = signal.SIGTERM) -> None:
    """Signal the whole process group led by `pid`. Swallows races (already
    dead). Kills `claude` AND its node grandchildren in one call."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


@dataclass
class LiveGroups:
    """Thread-safe registry of live worker process groups + signal teardown.

    A killed/redeployed run must not leave `claude` children burning quota, so
    on SIGTERM/SIGINT every registered group is killed before the engine exits.
    """
    _pids: set[int] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _shutting_down: bool = False

    def register(self, pid: int) -> None:
        with self._lock:
            self._pids.add(pid)

    def unregister(self, pid: int) -> None:
        with self._lock:
            self._pids.discard(pid)

    @property
    def shutting_down(self) -> bool:
        with self._lock:
            return self._shutting_down

    def terminate_all(self, sig: int = signal.SIGTERM) -> list[int]:
        """Kill every live group. Returns the pids that were signalled."""
        with self._lock:
            self._shutting_down = True
            pids = list(self._pids)
        for pid in pids:
            terminate_group(pid, sig)
        return pids

    def install_signal_handlers(self) -> Callable[[], None]:
        """Install SIGTERM/SIGINT handlers that tear down all groups, then
        re-raise the default disposition. Returns a restore() to undo them."""
        prev: dict[int, Any] = {}

        def handler(signum, _frame):
            self.terminate_all()
            # Restore default and re-raise so the process actually exits.
            signal.signal(signum, prev.get(signum, signal.SIG_DFL))
            os.kill(os.getpid(), signum)

        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                prev[s] = signal.getsignal(s)
                signal.signal(s, handler)
            except (ValueError, OSError):
                # Not in the main thread (e.g. under a test runner) — skip.
                pass

        def restore() -> None:
            for s, h in prev.items():
                try:
                    signal.signal(s, h)
                except (ValueError, OSError):
                    pass

        return restore


# ── Worker ────────────────────────────────────────────────────────────────────

# Seam for tests: override to avoid launching the real `claude` binary.
_SPAWN: Callable[..., subprocess.Popen] | None = None


def _spawn(cmd: list[str]) -> subprocess.Popen:
    if _SPAWN is not None:
        return _SPAWN(cmd)
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,  # own process group → killpg-able
    )


def run_batch(batch, *, model: str, timeout_s: int, max_msg_chars: int,
              live: LiveGroups) -> dict:
    """Extract one batch. Returns a result dict with entities + usage/telemetry
    or an error marker. Registers/tears down its process group around the call."""
    prompt = build_prompt(batch, max_msg_chars=max_msg_chars)
    cmd = ["claude", "--print", "--model", model,
           "--system-prompt", SYSTEM_PROMPT, "--output-format", "json"]
    t0 = time.time()
    proc = _spawn(cmd)
    live.register(proc.pid)
    try:
        try:
            out, err = proc.communicate(input=prompt, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            terminate_group(proc.pid, signal.SIGKILL)
            proc.wait()
            return {"batch_key": batch.batch_key, "ok": False, "error": "timeout",
                    "entities": []}
        rc = proc.returncode
    finally:
        live.unregister(proc.pid)

    wall = round(time.time() - t0, 2)
    if rc != 0:
        # Distinguish an auth/login failure (whole-run fatal — pause + alert)
        # from an ordinary per-batch error (retry next run).
        auth = is_auth_failure(err, out, f"rc={rc}")
        return {"batch_key": batch.batch_key, "ok": False,
                "error": "auth_failure" if auth else f"rc={rc}",
                "auth_failure": auth,
                "stderr": (err or "")[:300], "entities": []}
    try:
        env = json.loads(out)
    except Exception:
        auth = is_auth_failure(out, err)
        return {"batch_key": batch.batch_key, "ok": False,
                "error": "auth_failure" if auth else "cli_json_fail",
                "auth_failure": auth,
                "stdout": (out or "")[:300], "entities": []}
    parsed = parse_result(env.get("result", ""))
    usage = env.get("usage", {}) or {}
    return {
        "batch_key": batch.batch_key,
        "ok": parsed is not None,
        "error": None if parsed is not None else "parse_fail",
        "entities": (parsed or {}).get("entities", []) if parsed else [],
        "wall_s": wall,
        "cost_usd": env.get("total_cost_usd"),
        "tokens_out": usage.get("output_tokens"),
        "model": env.get("model") or model,
        "raw_result": None if parsed is not None else (env.get("result", "") or "")[:500],
    }
