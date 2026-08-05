"""Converse — the supervisor daemon (Wave 2 / T3). PLAN.md §4.

One long-running process that, for each ACTIVE-ish session:
    poll its channel -> ingest_inbound (durable, cursor advances in-txn)
    -> debounce -> claim_batch -> run_turn -> apply_turn_result
    -> gate.evaluate_send -> channel.send() -> mark_message_sent/failed

This module is the loop itself (Supervisor); core/services/converse/main.py
is the thin LaunchAgent entrypoint that constructs one against the real
channels and calls run_forever(). Tests / verification construct a
Supervisor directly with a FAKE channel and a temp comms.db path and drive
it synchronously via process_now() — see the module docstring on that
method for why a synchronous, non-debounced path exists alongside the real
timer-driven one.

Consumes (never modifies) Wave 0/1: converse/db.py, converse/models.py,
converse/turn.py, converse/gate.py, converse/channels*.py,
converse/kqueue_watch.py. Two small pieces of daemon-owned bookkeeping are
NOT exposed by db.py's CRUD surface (releasing a claimed batch back to
'received' on an explicit turn failure, and resetting error_count on
success) — those are implemented here via db.connect() + raw SQL against
the same tables, using models.py's enum constants throughout. This is not a
modification of db.py; it is the supervisor's own persistence for its own
retry/backoff state machine, which PLAN.md §4 step 6 describes but which
sits above the CRUD layer T1 shipped (see the DEVIATIONS note in the T3
build report for the full justification).
"""

from __future__ import annotations

import logging
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Repo-root bootstrap — supervisor.py is imported both as part of the
# core.services.converse package (LaunchAgent, `python -m ...main`) and
# directly by tests, which may not have done the bootstrap yet. Idempotent;
# same precedence as converse/channels.py's ensure_repo_root_on_path.
# ---------------------------------------------------------------------------


def _ensure_repo_root_on_path() -> None:
    try:
        import core  # noqa: F401

        return
    except ImportError:
        pass
    for candidate in (Path.home() / "project" / "aos", Path.home() / "aos"):
        if (candidate / "core").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return


_ensure_repo_root_on_path()

from core.engine.comms.converse import db, gate, models, turn  # noqa: E402
from core.engine.comms.converse.channels import (  # noqa: E402
    ChannelAuthError,
    ConverseChannel,
)
from core.engine.comms.converse.kqueue_watch import KqueueFileWatcher  # noqa: E402

from . import notify as notify_mod  # noqa: E402

log = logging.getLogger("converse.supervisor")

CONFIG_PATH = Path.home() / ".aos" / "config" / "converse.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "max_concurrent_handlers": 2,
    "batch_quiet_seconds": 30,
    "defaults": {"trust_level": 2, "max_messages": 30, "expires_days": 5, "tools": "none"},
    "notify": {"on_send": True, "on_escalate": True, "on_complete": True, "on_fail": True},
}

# PLAN.md §4 step 6: "backoff retry (1m -> 5m -> 15m); 3rd consecutive
# failure -> escalated". Indexed by (error_count - 1); a failure that pushes
# error_count to ESCALATE_AFTER or beyond escalates instead of scheduling
# the next entry — see _fail_batch.
BACKOFF_SCHEDULE_S = (60, 300, 900)
ESCALATE_AFTER = 3

# Crash-sweep defaults (PLAN.md §4 "startup:"). A turn stuck in 'handling'
# longer than this was almost certainly killed mid-flight (daemon crash/
# restart), not a slow turn — the longest single turn profile (tools=full)
# times out at 1200s server-side, so 1200s here would race it; give it a
# comfortable margin.
HANDLING_TIMEOUT_S = 1500
ACTION_EXPIRY_H = 48

# Background-thread tuning.
_MAIN_LOOP_TICK_S = 15
_BUSY_RETRY_S = 2.0
_SLACK_JITTER_FRACTION = 0.2


def _load_operator_name() -> str:
    path = Path.home() / ".aos" / "config" / "operator.yaml"
    try:
        cfg = yaml.safe_load(path.read_text()) or {}
        return cfg.get("name") or "the operator"
    except Exception:
        return "the operator"


class Supervisor:
    """The converse supervisor loop. See module docstring."""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        config_path: Path | str | None = None,
        config: dict[str, Any] | None = None,
        channels: dict[str, ConverseChannel] | None = None,
        claude_bin: str | None = None,
        dry_run: bool = False,
        operator_name: str | None = None,
        notify_fn=None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else db.DB_PATH
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        self._config_override = config  # if set, load_config() returns this verbatim (tests)
        self.channels: dict[str, ConverseChannel] = channels if channels is not None else self._default_channels()
        self.claude_bin = claude_bin
        self.dry_run = dry_run
        self.operator_name = operator_name or _load_operator_name()
        self._notify = notify_fn or notify_mod.notify

        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._debounce_timers: dict[str, threading.Timer] = {}
        self._backoff_until: dict[str, float] = {}
        self._watchers_started: set[str] = set()
        self._imessage_watcher: Optional[KqueueFileWatcher] = None
        self._imessage_thread: Optional[threading.Thread] = None
        self._slack_thread: Optional[threading.Thread] = None
        self._sem: Optional[threading.Semaphore] = None  # sized on first load_config()

    # ------------------------------------------------------------------
    # Channels / config
    # ------------------------------------------------------------------

    @staticmethod
    def _default_channels() -> dict[str, ConverseChannel]:
        channels: dict[str, ConverseChannel] = {}
        try:
            from core.engine.comms.converse.channels_imessage import iMessageChannel

            channels["imessage"] = iMessageChannel()
        except Exception as e:  # pragma: no cover - defensive
            log.warning("converse: could not construct iMessageChannel: %s", e)
        try:
            from core.engine.comms.converse.channels_slack import SlackChannel

            channels["slack"] = SlackChannel()
        except Exception as e:  # pragma: no cover - defensive
            log.warning("converse: could not construct SlackChannel: %s", e)
        return channels

    def load_config(self) -> dict[str, Any]:
        """Read converse.yaml fresh on every call — PLAN.md §4: "daemon
        reads ~/.aos/config/converse.yaml at start and per-pass ...
        enabled: false -> clean idle exit loop". Missing/unparseable file
        falls back to DEFAULT_CONFIG (fresh install / partial migration —
        never crash)."""
        if self._config_override is not None:
            cfg = self._config_override
        elif self.config_path.exists():
            try:
                cfg = yaml.safe_load(self.config_path.read_text()) or {}
            except Exception as e:
                log.warning("converse: could not parse %s: %s — using defaults", self.config_path, e)
                cfg = {}
        else:
            cfg = {}
        merged = {**DEFAULT_CONFIG, **cfg}
        merged.setdefault("defaults", DEFAULT_CONFIG["defaults"])
        merged.setdefault("notify", DEFAULT_CONFIG["notify"])
        with self._lock:
            want = int(merged.get("max_concurrent_handlers", 2))
            if self._sem is None or getattr(self._sem, "_initial_value", want) != want:
                self._sem = threading.Semaphore(want)
                self._sem._initial_value = want  # type: ignore[attr-defined]
        return merged

    # ------------------------------------------------------------------
    # Startup crash sweep
    # ------------------------------------------------------------------

    def crash_sweep(self) -> dict[str, int]:
        """PLAN.md §4 "startup:" — reset sessions/messages stuck in
        'handling' past a stale handling_started_at, and expire
        long-pending session_actions. Delegates entirely to
        db.sweep_stale(); logs the result. Safe to call more than once."""
        result = db.sweep_stale(
            handling_timeout_s=HANDLING_TIMEOUT_S,
            action_expiry_h=ACTION_EXPIRY_H,
            db_path=self.db_path,
        )
        log.info(
            "converse crash-sweep: sessions_reset=%d messages_reset=%d actions_expired=%d",
            result["sessions_reset"], result["messages_reset"], result["actions_expired"],
        )
        return result

    # ------------------------------------------------------------------
    # Wake / ingest (PLAN.md §4 steps 1-3: INGEST, GATE-IN, BATCH)
    # ------------------------------------------------------------------

    def wake(self, channel_name: str, reason: str) -> None:
        """Entry point a channel watcher calls on event/heartbeat/startup/
        poll-tick. Ingests inbound for every non-terminal session on that
        channel and (re)schedules each session's debounce timer."""
        cfg = self.load_config()
        if not cfg.get("enabled", True):
            return
        if not (cfg.get("channels") or {}).get(channel_name, {}).get("enabled", True):
            return
        channel = self.channels.get(channel_name)
        if channel is None:
            return
        log.debug("converse wake(%s, %s)", channel_name, reason)
        sessions = db.list_sessions(channel=channel_name, status=list(models.ACTIVE_STATUSES), db_path=self.db_path)
        for session in sessions:
            self._ingest_and_schedule(session, channel, immediate=False)

    def process_now(self, channel_name: str) -> None:
        """Synchronous, non-debounced single pass over every non-terminal
        session on `channel_name`: ingest, then (if a batch resulted and
        the session isn't gated out) claim+run_turn+apply+send inline,
        blocking until each triggered turn completes. This is the
        verification/dry-run entry point — the real daemon loop uses
        wake() + the timer-based debounce in _schedule_batch instead, so a
        production burst of messages still coalesces into one turn per
        PLAN.md §4 BATCH. Bypassing the timer here is deliberate: a test
        asserting "one full cycle ran" should not have to sleep through
        batch_quiet_seconds (default 30s) to observe it.
        """
        cfg = self.load_config()
        if not cfg.get("enabled", True):
            return
        channel = self.channels.get(channel_name)
        if channel is None:
            raise ValueError(f"no channel registered for {channel_name!r}")
        sessions = db.list_sessions(channel=channel_name, status=list(models.ACTIVE_STATUSES), db_path=self.db_path)
        for session in sessions:
            self._ingest_and_schedule(session, channel, immediate=True)

    def _ingest_and_schedule(self, session: "models.ConversationSession", channel: ConverseChannel, *, immediate: bool) -> None:
        try:
            msgs, new_cursor = channel.poll(session.conversation_ref, session.counterpart_handle, session.cursor)
        except ChannelAuthError as e:
            self._handle_auth_error(channel.name, str(e))
            return
        except Exception:
            log.exception("converse: poll failed for session=%s channel=%s", session.id, channel.name)
            return

        if msgs or new_cursor is not None:
            db.ingest_inbound(session.id, msgs, new_cursor, db_path=self.db_path)

        # Re-read: ingest_inbound may have advanced the cursor and another
        # thread/tick may have changed status concurrently; act on current
        # truth, not the pre-ingest snapshot.
        current = db.get_session(session.id, db_path=self.db_path)
        if current is None or current.is_terminal:
            return

        self._check_guards(current)
        current = db.get_session(session.id, db_path=self.db_path)
        if current is None or current.is_terminal:
            return

        # GATE-IN (PLAN.md §4 step 2).
        if current.status in (models.STATUS_PAUSED, models.STATUS_TAKEOVER):
            return  # recorded only, no handler
        if current.status == models.STATUS_ESCALATED:
            if msgs:
                cfg = self.load_config()
                self._notify(
                    "new_inbound_while_escalated",
                    enabled=cfg["notify"].get("on_escalate", True),
                    session_id=current.id, person=current.person_name or current.counterpart_handle,
                )
            return

        self._schedule_batch(current.id, channel, immediate=immediate)

    def _check_guards(self, session: "models.ConversationSession") -> None:
        """PLAN.md §4 "guards each pass": expiry -> 'expired'; sent_count
        >= max_messages -> 'capped'. Both terminal, both notify the
        operator, and (per PLAN.md) the contact never sees an error — no
        outbound is sent on either transition."""
        now = int(time.time())
        cfg = self.load_config()
        if session.expires_at and session.expires_at < now and not session.is_terminal:
            db.set_status(session.id, models.STATUS_EXPIRED, close_reason=models.STATUS_EXPIRED, db_path=self.db_path)
            self._notify(
                "expired", enabled=cfg["notify"].get("on_fail", True),
                session_id=session.id, person=session.person_name or session.counterpart_handle,
            )
            return
        if session.sent_count >= session.max_messages and not session.is_terminal:
            db.set_status(
                session.id, models.STATUS_CAPPED,
                paused_reason=models.PAUSED_REASON_CAPPED, close_reason=models.STATUS_CAPPED,
                db_path=self.db_path,
            )
            self._notify(
                "capped", enabled=cfg["notify"].get("on_fail", True),
                session_id=session.id, person=session.person_name or session.counterpart_handle,
                sent_count=session.sent_count, max_messages=session.max_messages,
            )

    # ------------------------------------------------------------------
    # Debounce (PLAN.md §4 step 3 BATCH) + concurrency-limited dispatch
    # ------------------------------------------------------------------

    def _schedule_batch(self, session_id: str, channel: ConverseChannel, *, immediate: bool) -> None:
        if immediate:
            self._fire_batch(session_id, channel)
            return
        cfg = self.load_config()
        quiet = float(cfg.get("batch_quiet_seconds", 30))
        with self._lock:
            old = self._debounce_timers.get(session_id)
            if old is not None:
                old.cancel()
            t = threading.Timer(quiet, self._fire_batch, args=(session_id, channel))
            t.daemon = True
            self._debounce_timers[session_id] = t
            t.start()

    def _fire_batch(self, session_id: str, channel: ConverseChannel) -> None:
        with self._lock:
            self._debounce_timers.pop(session_id, None)
            until = self._backoff_until.get(session_id)
        if until is not None and time.time() < until:
            delay = max(1.0, until - time.time())
            self._reschedule_after_backoff(session_id, channel, delay)
            return
        self._handle_session(session_id, channel)

    def _reschedule_after_backoff(self, session_id: str, channel: ConverseChannel, delay: float) -> None:
        t = threading.Timer(delay, self._fire_batch, args=(session_id, channel))
        t.daemon = True
        with self._lock:
            self._debounce_timers[session_id] = t
        t.start()

    def _handle_session(self, session_id: str, channel: ConverseChannel) -> None:
        sem = self._sem or threading.Semaphore(2)
        if not sem.acquire(blocking=False):
            log.debug("converse: max_concurrent_handlers busy — rescheduling session=%s", session_id)
            self._reschedule_after_backoff(session_id, channel, _BUSY_RETRY_S)
            return
        try:
            self._run_turn_cycle(session_id, channel)
        finally:
            sem.release()

    # ------------------------------------------------------------------
    # HANDLE + APPLY (PLAN.md §4 steps 4-5)
    # ------------------------------------------------------------------

    def _run_turn_cycle(self, session_id: str, channel: ConverseChannel) -> None:
        claimed = db.claim_batch(session_id, db_path=self.db_path)
        if not claimed:
            return  # nothing pending, or another handler already has it (single-flight)

        session = db.get_session(session_id, db_path=self.db_path)
        if session is None:  # pragma: no cover - defensive
            return
        transcript = db.list_messages(session_id, limit=turn.prompts.MAX_TRANSCRIPT_MESSAGES, db_path=self.db_path)

        outcome = turn.run_turn(
            session, claimed, transcript,
            operator_name=self.operator_name, claude_bin=self.claude_bin,
        )

        if not outcome.ok:
            self._fail_batch(session, outcome.error or "unknown turn failure", channel)
            return

        self._apply_outcome(session, outcome.parsed, channel)

    def _apply_outcome(self, session: "models.ConversationSession", parsed: "turn.ParsedTurn", channel: ConverseChannel) -> None:
        result = db.apply_turn_result(
            session.id,
            action=parsed.action,
            message=parsed.message,
            state_summary=parsed.state_summary,
            propose_actions=parsed.propose_actions,
            db_path=self.db_path,
        )
        self._reset_error_count(session.id)
        with self._lock:
            self._backoff_until.pop(session.id, None)

        cfg = self.load_config()
        person = session.person_name or session.counterpart_handle
        message_id = result["message_id"]
        if message_id and parsed.message:
            self._route_send(session.id, message_id, parsed, channel, session)

        if parsed.action == models.TURN_ESCALATE:
            self._notify(
                "escalate", enabled=cfg["notify"].get("on_escalate", True),
                session_id=session.id, person=person, summary=parsed.summary or parsed.reason or "",
            )
        elif parsed.action == models.TURN_COMPLETE:
            self._notify(
                "complete", enabled=cfg["notify"].get("on_complete", True),
                session_id=session.id, person=person, summary=parsed.summary or "",
            )

        # A message may have arrived while this turn was in flight (it would
        # have been ingested as 'received' concurrently, since claim_batch
        # only claims what existed at claim time). Re-check immediately
        # rather than waiting for the next external wake — self-healing,
        # and a no-op (claim_batch returns None) if there's nothing new.
        self._schedule_batch(session.id, channel, immediate=False)

    def _route_send(
        self, session_id: str, message_id: str, parsed: "turn.ParsedTurn",
        channel: ConverseChannel, session: "models.ConversationSession",
    ) -> None:
        is_first = not gate.has_sent_before(session_id, db_path=self.db_path)
        # people.db is a separate database from comms.db (self.db_path) — no
        # override needed/possible here; gate.contact_importance() defaults
        # to the real ~/.aos/data/people.db and returns None gracefully if
        # it's absent (e.g. in a test environment), never raising.
        contact_importance = gate.contact_importance(session.person_id)
        decision = gate.evaluate_send(
            session, parsed.message,
            confidence=parsed.confidence, is_first_outbound=is_first,
            contact_importance_value=contact_importance,
            db_path=self.db_path,
        )
        cfg = self.load_config()
        person = session.person_name or session.counterpart_handle
        if decision.auto_send:
            self._send_now(session_id, message_id, channel, session.conversation_ref, parsed.message)
        else:
            db.propose_action(
                session_id, models.ACTION_SEND_REPLY,
                {"message_id": message_id, "text": parsed.message},
                gate_reasons=decision.reasons, db_path=self.db_path,
            )
            self._notify(
                "held_for_approval", enabled=cfg["notify"].get("on_send", True),
                session_id=session_id, person=person, reasons=decision.reasons,
            )

    def _send_now(self, session_id: str, message_id: str, channel: ConverseChannel, conversation_ref: str, text: str) -> None:
        cfg = self.load_config()
        session = db.get_session(session_id, db_path=self.db_path)
        person = (session.person_name or session.counterpart_handle) if session else session_id

        if self.dry_run:
            db.mark_message_sent(message_id, db_path=self.db_path)
            log.info("converse [dry-run]: would send to session=%s: %r", session_id, text[:80])
            return

        result = channel.send(conversation_ref, text)
        if result.ok:
            db.mark_message_sent(message_id, channel_message_id=result.channel_message_id, db_path=self.db_path)
            self._notify("sent", enabled=cfg["notify"].get("on_send", True), session_id=session_id, person=person)
        else:
            db.mark_message_send_failed(message_id, result.error or "unknown send error", db_path=self.db_path)
            self._notify(
                "send_failed", enabled=cfg["notify"].get("on_fail", True),
                session_id=session_id, person=person, error=result.error or "unknown",
            )
            if result.error == "invalid_auth":
                self._handle_auth_error(channel.name, "invalid_auth on send")

    # ------------------------------------------------------------------
    # FAIL (PLAN.md §4 step 6) — release the claimed batch, backoff/escalate
    # ------------------------------------------------------------------

    def _fail_batch(self, session: "models.ConversationSession", error: str, channel: ConverseChannel) -> None:
        """A turn came back ok=False (timeout / rc != 0 / unparseable
        output). Not exposed by db.py's CRUD (see module docstring) —
        implemented here via db.connect() + models.py's own enum constants,
        matching PLAN.md §4 step 6 field-for-field: claimed rows go back to
        'received' (attempt_count is left as claim_batch bumped it),
        session status back to 'active' (or 'escalated' on the 3rd
        consecutive failure), error_count incremented, handling_started_at
        cleared."""
        conn = db.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE session_messages SET state = ? WHERE session_id = ? AND state = ?",
                (models.MSG_RECEIVED, session.id, models.MSG_HANDLING),
            )
            row = conn.execute(
                "SELECT error_count FROM conversation_sessions WHERE id = ?", (session.id,)
            ).fetchone()
            new_error_count = (row["error_count"] if row else 0) + 1
            ts = db.now_ts()
            escalate = new_error_count >= ESCALATE_AFTER
            if escalate:
                conn.execute(
                    "UPDATE conversation_sessions SET status = ?, paused_reason = ?, error_count = ?, "
                    "handling_started_at = NULL, updated_at = ? WHERE id = ?",
                    (models.STATUS_ESCALATED, models.PAUSED_REASON_ESCALATED, new_error_count, ts, session.id),
                )
            else:
                conn.execute(
                    "UPDATE conversation_sessions SET status = ?, error_count = ?, "
                    "handling_started_at = NULL, updated_at = ? WHERE id = ?",
                    (models.STATUS_ACTIVE, new_error_count, ts, session.id),
                )
            conn.commit()
        finally:
            conn.close()

        log.warning(
            "converse: turn failed session=%s error_count=%d error=%s escalate=%s",
            session.id, new_error_count, error, escalate,
        )
        cfg = self.load_config()
        person = session.person_name or session.counterpart_handle
        if escalate:
            self._notify(
                "escalated_after_failures", enabled=cfg["notify"].get("on_fail", True),
                session_id=session.id, person=person, error=error,
            )
            with self._lock:
                self._backoff_until.pop(session.id, None)
            return

        backoff_s = BACKOFF_SCHEDULE_S[min(new_error_count - 1, len(BACKOFF_SCHEDULE_S) - 1)]
        with self._lock:
            self._backoff_until[session.id] = time.time() + backoff_s
        self._reschedule_after_backoff(session.id, channel, backoff_s)

    def _reset_error_count(self, session_id: str) -> None:
        """Not exposed by db.py — a successful turn clears the consecutive-
        failure counter (models.py: "error_count -- consecutive handler
        failures"). Skipped as a no-op write when already 0."""
        conn = db.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE conversation_sessions SET error_count = 0 WHERE id = ? AND error_count != 0",
                (session_id,),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Reauth path (PLAN.md §3/§4 — sticky flag, pause, notify; never automatic)
    # ------------------------------------------------------------------

    def _handle_auth_error(self, channel_name: str, error: str) -> None:
        sessions = db.list_sessions(channel=channel_name, status=list(models.ACTIVE_STATUSES), db_path=self.db_path)
        paused = 0
        for s in sessions:
            if s.status == models.STATUS_PAUSED and s.paused_reason == models.PAUSED_REASON_REAUTH:
                continue
            db.set_status(s.id, models.STATUS_PAUSED, paused_reason=models.PAUSED_REASON_REAUTH, db_path=self.db_path)
            paused += 1
        log.error("converse: %s needs reauth (%s) — paused %d session(s)", channel_name, error, paused)
        cfg = self.load_config()
        self._notify(
            "reauth_needed", enabled=cfg["notify"].get("on_fail", True),
            channel=channel_name, error=error,
        )

    # ------------------------------------------------------------------
    # Channel watchers — imessage: kqueue (instant + 60s heartbeat);
    # slack: jittered poll. Started lazily, only once a session exists on
    # that channel (PLAN.md §4 "one supervisor, channel-appropriate
    # watching ... active only when slack sessions exist" — applied to
    # both channels here for symmetry/resource hygiene).
    # ------------------------------------------------------------------

    def _channel_has_sessions(self, channel_name: str) -> bool:
        return len(db.list_sessions(channel=channel_name, status=list(models.ACTIVE_STATUSES), limit=1, db_path=self.db_path)) > 0

    def _start_watchers_if_needed(self) -> None:
        with self._lock:
            need_imessage = "imessage" not in self._watchers_started
            need_slack = "slack" not in self._watchers_started
        if need_imessage and "imessage" in self.channels and self._channel_has_sessions("imessage"):
            self._start_imessage_watcher()
        if need_slack and "slack" in self.channels and self._channel_has_sessions("slack"):
            self._start_slack_watcher()

    def _start_imessage_watcher(self) -> None:
        channel = self.channels["imessage"]
        spec = channel.watch_spec()
        watcher = KqueueFileWatcher(
            spec.paths, lambda reason: self.wake("imessage", reason), heartbeat_s=60.0,
        )
        thread = threading.Thread(target=watcher.run, name="converse-imessage-watch", daemon=True)
        with self._lock:
            self._imessage_watcher = watcher
            self._imessage_thread = thread
            self._watchers_started.add("imessage")
        thread.start()
        log.info("converse: iMessage kqueue watcher started")

    def _start_slack_watcher(self) -> None:
        channel = self.channels["slack"]
        interval = channel.watch_spec().interval_s

        def _poll_loop() -> None:
            self.wake("slack", "startup")
            while not self._stop.is_set():
                jitter = random.uniform(0, max(0.0, interval * _SLACK_JITTER_FRACTION))
                if self._stop.wait(interval + jitter):
                    break
                self.wake("slack", "poll")

        thread = threading.Thread(target=_poll_loop, name="converse-slack-poll", daemon=True)
        with self._lock:
            self._slack_thread = thread
            self._watchers_started.add("slack")
        thread.start()
        log.info("converse: Slack poll watcher started (interval=%ss)", interval)

    # ------------------------------------------------------------------
    # Main loop / lifecycle
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        log.info("converse supervisor starting")
        self.crash_sweep()
        while not self._stop.is_set():
            cfg = self.load_config()
            if cfg.get("enabled", True):
                self._start_watchers_if_needed()
            else:
                log.info("converse: disabled via config — idling")
            if self._stop.wait(_MAIN_LOOP_TICK_S):
                break
        self._shutdown()
        log.info("converse supervisor stopped")

    def stop(self) -> None:
        self._stop.set()

    def _shutdown(self) -> None:
        with self._lock:
            timers = list(self._debounce_timers.values())
            self._debounce_timers.clear()
        for t in timers:
            t.cancel()
        if self._imessage_watcher is not None:
            self._imessage_watcher.stop()
