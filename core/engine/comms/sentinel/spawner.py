"""Sentinel spawner — picks up detected triggers and runs the pipeline.

Pipeline per trigger:
  detected → spawning → researching → draft_ready
            → (high) sending (30s window) → sent
            → (low/med) pending
            → (hard floor) blocked
            → (error) failed
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import yaml
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
DRAFTS_DIR = Path.home() / ".aos" / "work" / "sentinel" / "drafts"
PENDING_DIR = Path.home() / ".aos" / "work" / "sentinel" / "pending"
LOG_DIR = Path.home() / ".aos" / "logs" / "sentinel"
CONFIG_PATH = Path.home() / ".aos" / "config" / "sentinel.yaml"
SENT_LOG = LOG_DIR / "sent.jsonl"

CLAUDE_BIN = shutil.which("claude") or "claude"


def _now() -> int:
    return int(time.time())


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


def _update_trigger(trigger_id: str, **fields) -> None:
    if not fields:
        return
    cols = []
    vals: list = []
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(v)
    vals.append(trigger_id)
    conn = sqlite3.connect(str(COMMS_DB))
    conn.execute(f"UPDATE agent_triggers SET {', '.join(cols)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def _fetch_detected_triggers(limit: int = 5) -> list[dict]:
    conn = sqlite3.connect(str(COMMS_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM agent_triggers
        WHERE status = 'detected'
        ORDER BY created_at ASC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _append_sent_log(entry: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with SENT_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


class SentinelSpawner:
    """Polls agent_triggers and runs Sentinel on detected rows."""

    def __init__(self):
        self.config = _load_config()
        self.timeout = int(self.config.get("spawn_timeout_seconds", 300))
        self.soft_window = int(self.config.get("soft_window_seconds", 30))
        self.paused_via_config = bool(self.config.get("paused", False))

    @property
    def paused(self) -> bool:
        # Re-read every check so live edits to config take effect
        cfg = _load_config()
        return bool(cfg.get("paused", False)) or not bool(cfg.get("enabled", True))

    def run_once(self) -> int:
        """Process all currently-detected triggers. Returns count handled.

        This is now a FALLBACK path — the kqueue watcher invokes handle_by_id()
        instantly. run_once is only useful as a slow safety net (e.g., 60s)
        in case the watcher misses an event or restarts.
        """
        if self.paused:
            log.debug("Spawner paused; skipping.")
            return 0
        triggers = _fetch_detected_triggers()
        for t in triggers:
            try:
                self._handle(t)
            except Exception as e:
                log.exception("Trigger %s failed in spawner: %s", t["id"], e)
                _update_trigger(t["id"], status="failed", error=str(e)[:200])
        return len(triggers)

    def run_forever(self, interval_sec: int = 60) -> None:
        """Slow fallback loop — primary driver is the watcher."""
        log.info("Sentinel spawner fallback loop (interval=%ds)", interval_sec)
        while True:
            try:
                self.run_once()
            except Exception as e:
                log.exception("Spawner loop error: %s", e)
            time.sleep(interval_sec)

    def handle_by_id(self, trigger_id: str) -> None:
        """Process a specific trigger immediately (called from watcher)."""
        conn = sqlite3.connect(str(COMMS_DB))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_triggers WHERE id = ?", (trigger_id,)
        ).fetchone()
        conn.close()
        if not row:
            log.warning("handle_by_id: no trigger %s", trigger_id)
            return
        if row["status"] != "detected":
            log.info("handle_by_id: %s already %s, skipping", trigger_id, row["status"])
            return
        try:
            self._handle(dict(row))
        except Exception as e:
            log.exception("handle_by_id: %s failed: %s", trigger_id, e)
            _update_trigger(trigger_id, status="failed", error=str(e)[:200])

    # ── Per-trigger pipeline ────────────────────────────────────────

    def _handle(self, trigger: dict) -> None:
        trigger_id = trigger["id"]
        log.info("Handling trigger %s (phrase=%s)", trigger_id, trigger["trigger_phrase"])

        # 1. Mark spawning
        _update_trigger(trigger_id, status="spawning", spawned_at=_now())

        # 2. Build context bundle
        try:
            from .context_builder import ContextBuilder
            bundle = ContextBuilder().build(trigger_id)
        except Exception as e:
            log.exception("Context build failed: %s", e)
            _update_trigger(trigger_id, status="failed",
                            error=f"context_build: {str(e)[:160]}")
            from .notifier import notify_failed
            notify_failed(trigger.get("person_id") or "<unknown>", str(e)[:80])
            return

        # Backfill person_id on the trigger row now that the context_builder
        # has resolved the recipient via chat.db → people.db. The watcher
        # inserts triggers with person_id NULL (outbound messages don't carry
        # a sender_id we can map). Without this, the rate-limit lookup in
        # last_sentinel_send_for_person silently no-ops.
        if bundle.contact.person_id and not trigger.get("person_id"):
            try:
                _update_trigger(trigger_id, person_id=bundle.contact.person_id)
            except Exception as e:
                log.warning("person_id backfill failed for %s: %s", trigger_id, e)

        # 3. Run Sentinel
        _update_trigger(trigger_id, status="researching")
        draft_path = Path(bundle.draft_path)
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        if draft_path.exists():
            draft_path.unlink()

        prompt = bundle.to_text()
        try:
            ret = self._invoke_claude(prompt, trigger_id)
        except Exception as e:
            log.exception("Claude invocation crashed: %s", e)
            _update_trigger(trigger_id, status="failed",
                            error=f"claude_invoke: {str(e)[:160]}")
            from .notifier import notify_failed
            notify_failed(bundle.contact.canonical_name, str(e)[:80])
            return

        if ret != 0 or not draft_path.exists():
            err = f"Claude rc={ret}, draft file missing" if not draft_path.exists() else f"Claude rc={ret}"
            log.error("Sentinel did not produce draft: %s", err)
            _update_trigger(trigger_id, status="failed", error=err[:200])
            from .notifier import notify_failed
            notify_failed(bundle.contact.canonical_name, err[:80])
            return

        # 4. Parse + evaluate confidence
        from .confidence_gate import (ConfidenceGate, parse_draft_file,
                                      last_sentinel_send_for_person)
        draft = parse_draft_file(draft_path)
        if not draft:
            _update_trigger(trigger_id, status="failed",
                            error="invalid draft file (frontmatter parse)")
            from .notifier import notify_failed
            notify_failed(bundle.contact.canonical_name, "invalid draft")
            return

        last_send = last_sentinel_send_for_person(trigger.get("person_id") or "")
        gate = ConfidenceGate(self.config)
        result = gate.evaluate(
            draft, bundle.contact.importance, last_send,
            trigger_text=bundle.trigger_text,
        )

        _update_trigger(
            trigger_id,
            status="draft_ready",
            draft_at=_now(),
            draft_path=str(draft_path),
            task_inferred=draft.task_inferred[:500],
            confidence=draft.confidence,
            confidence_reasons=json.dumps(result.reasons_against),
        )

        # 5. Route by decision
        if result.decision == "send":
            self._start_soft_window(trigger_id, bundle, draft)
        elif result.decision == "blocked":
            log.warning("Trigger %s blocked by hard floor: %s",
                        trigger_id, result.reasons_against)
            self._move_to_pending(trigger_id, bundle, draft, result, blocked=True)
        else:
            self._move_to_pending(trigger_id, bundle, draft, result, blocked=False)

    # ── Claude subprocess ───────────────────────────────────────────

    def _invoke_claude(self, prompt: str, trigger_id: str) -> int:
        """Run claude --print --agent Sentinel with the context bundle."""
        log_file = LOG_DIR / f"{trigger_id}.log"
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        cmd = [
            CLAUDE_BIN,
            "--print",
            "--agent", "Sentinel",
            "--dangerously-skip-permissions",
            "--allowedTools", "WebSearch,WebFetch,Read,Write,Glob,Grep,Bash",
        ]
        log.info("Invoking claude (trigger=%s, log=%s)", trigger_id, log_file)

        env = dict(os.environ)
        env["SENTINEL_TRIGGER_ID"] = trigger_id

        with log_file.open("w") as logf:
            logf.write(f"=== command: {' '.join(cmd)} <prompt via stdin: {len(prompt)} chars> ===\n")
            logf.flush()
            log.info("subprocess.run starting for %s (timeout=%ds)", trigger_id, self.timeout)
            try:
                # Pass prompt via stdin (claude --print expects this when arg flags
                # contend with the prompt positional)
                proc = subprocess.run(
                    cmd, input=prompt,
                    stdout=logf, stderr=subprocess.STDOUT,
                    timeout=self.timeout, env=env, text=True,
                )
                log.info("subprocess.run returned rc=%d for %s", proc.returncode, trigger_id)
                logf.write(f"\n=== rc={proc.returncode} ===\n")
                return proc.returncode
            except subprocess.TimeoutExpired:
                log.warning("subprocess timeout for %s after %ds", trigger_id, self.timeout)
                logf.write(f"\n=== TIMEOUT after {self.timeout}s ===\n")
                return 124
            except Exception as e:
                log.exception("subprocess.run crashed for %s: %s", trigger_id, e)
                logf.write(f"\n=== EXCEPTION: {e} ===\n")
                return 1

    # ── Routing ─────────────────────────────────────────────────────

    def _start_soft_window(self, trigger_id: str, bundle, draft):
        """Send IMMEDIATELY on high confidence — Option 1: no soft window.

        The reply itself is the signal that AOS got the trigger. If Sentinel
        hallucinates, iMessage's built-in 2-minute unsend is the safety net.
        """
        from .notifier import notify_sent, notify_failed
        from .dispatcher import send_draft

        _update_trigger(trigger_id, status="sending")

        ok, info = send_draft(
            trigger_id, bundle.contact.canonical_name,
            bundle.channel, draft.body,
            handle=bundle.contact.handle,
        )
        if ok:
            notify_sent(bundle.contact.canonical_name, draft.task_inferred)
            _append_sent_log({
                "trigger_id": trigger_id,
                "ts": _now(),
                "contact": bundle.contact.canonical_name,
                "channel": bundle.channel,
                "task": draft.task_inferred,
                "body": draft.body,
                "confidence": draft.confidence,
            })
        else:
            log.error("Dispatch failed: %s", info)
            notify_failed(bundle.contact.canonical_name, f"dispatch: {info[:80]}")

    def _move_to_pending(self, trigger_id: str, bundle, draft, result, blocked: bool):
        """Copy draft to pending/ and notify."""
        from .notifier import notify_pending
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        target = PENDING_DIR / f"{trigger_id}.md"
        shutil.copy(Path(bundle.draft_path), target)
        new_status = "blocked" if blocked else "pending"
        _update_trigger(trigger_id, status=new_status, decided_at=_now())
        notify_pending(bundle.contact.canonical_name, draft.task_inferred,
                       result.reasons_against)


def _fetch_status(trigger_id: str) -> Optional[str]:
    conn = sqlite3.connect(str(COMMS_DB))
    row = conn.execute("SELECT status FROM agent_triggers WHERE id = ?",
                        (trigger_id,)).fetchone()
    conn.close()
    return row[0] if row else None
