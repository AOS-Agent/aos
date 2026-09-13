"""Telegram notification router — topic-aware outbound delivery.

Every proactive sender (crons, bus consumers, scheduler, council) routes
through here instead of hitting the Bot API with a bare TELEGRAM_CHAT_ID.

Routing tiers (deterministic — no content analysis):

    1. Sender declares a category via ``topic=`` — the sender knows what
       it is (watchdog -> "system", work reminders -> "work", ...).
    2. Kind infers when no topic given: ``kind="alert"`` -> alerts topic,
       everything else -> system topic.
    3. Fallback chain on delivery: forum topic -> group General -> operator
       DM. A disabled-Topics toggle or deleted topic never drops a message.

Quiet hours (aos#235): outside the operator's waking window a non-urgent
message is stored rather than pushed, and delivered as one digest once the
window opens. Urgency is whatever the sender declared -- ``kind="alert"`` or an
explicit ``urgent=True`` -- never something read out of the text. Nothing is
ever dropped: if the queue itself cannot be written, the message goes out.

Every message is also run through the one humanization layer
(``core/infra/reconcile/alert_copy.py`` -> ``humanize_notice``) on the way out.
Before aos#235 only reconcile did that, and the 2026-09-13 review found the
other senders arriving with a single emoji prefix bolted onto raw internal
text: paths, slugs, stack traces. A sender that composes its own phone-ready
copy can pass ``humanize=False`` -- an opt-out rather than the default, because
forgetting is what got us here.

Config: ``~/.aos/config/bridge-topics.yaml`` (written by the bridge's
TopicManager). Credentials: macOS Keychain via agent-secret. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

TOPICS_CONFIG = Path.home() / ".aos" / "config" / "bridge-topics.yaml"
OPERATOR_CONFIG = Path.home() / ".aos" / "config" / "operator.yaml"
AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

# Fallback window when operator.yaml says nothing at all.
QUIET_HOURS_DEFAULT = ((22, 0), (7, 0))

VALID_TOPICS = ("daily", "alerts", "work", "knowledge", "system")

TELEGRAM_MSG_LIMIT = 4096
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds


class _RateLimiter:
    """Minimum-interval limiter, thread-safe.

    Inlined rather than imported from ``lib.rate_limit``: aos-notify loads
    this module by file path, so router.py must stay stdlib-only.
    """

    def __init__(self, max_per_second: float = 1.0):
        self._interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_call = time.monotonic()


# Telegram's recommended ceiling is 1 message/second per bot.
_RATE_LIMITER = _RateLimiter(max_per_second=1.0)

# Telegram 400 descriptions that mean "the thread/forum is gone" — retry
# the same chat without a thread id rather than failing.
_THREAD_GONE_MARKERS = (
    "message thread not found",
    "topic_deleted",
    "topic_closed",
    "not a forum",
    "forum",
)

_KIND_PREFIX = {"alert": "⚠️", "success": "✅", "info": "ℹ️"}

# MESSAGE_STYLE.md rule 2: one emoji per section. Humanized copy already leads
# with the right one ("🧹 Found 7 old scripts..."), so bolting the kind
# prefix on in front produced "ℹ️ 🧹 Found 7 old scripts" -- two emoji and a
# tone clash. reconcile has been sending that since aos#170; its own comment
# says kind="info" is passed so "the router does not prepend" anything, which
# was never true.
_LEADS_WITH_EMOJI_RE = re.compile(
    "^[\u2190-\u21ff\u2300-\u23ff\u25a0-\u27bf\u2b00-\u2bff"
    "\U0001f000-\U0001faff]"
)

# The humanization layer, loaded by file path rather than by import: this module
# is itself loaded by path from `aos-notify`, so there is no package to import
# through, and `core/infra/reconcile` is on nobody's sys.path.
_ALERT_COPY = (Path(__file__).resolve().parents[3]
               / "core" / "infra" / "reconcile" / "alert_copy.py")
_humanizer = None


def _load_humanizer():
    """Return `humanize_notice`, or None if the module cannot be loaded.

    A missing humanizer must never cost the operator a message -- an unpolished
    notice beats a dropped one.
    """
    global _humanizer
    if _humanizer is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("aos_alert_copy", _ALERT_COPY)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _humanizer = mod.humanize_notice
        except Exception as e:  # noqa: BLE001 - any failure means "send it raw"
            log.warning("Could not load the message humanizer (%s) - sending raw", e)
            _humanizer = False
    return _humanizer or None


# The conversation store, loaded by path for the same reason as the humanizer:
# this module is itself loaded by path, and the bridge's directory is on nobody's
# sys.path. One store, so a queued message sits in the same table as a sent one.
_STORE_PATH = (Path(__file__).resolve().parents[3]
               / "core" / "services" / "bridge" / "conversation_store.py")
_store = None


def _load_store():
    """Return the conversation store module, or None if it cannot be loaded."""
    global _store
    if _store is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("aos_conversation_store", _STORE_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _store = mod
        except Exception as e:  # noqa: BLE001
            log.warning("Could not load the conversation store (%s)", e)
            _store = False
    return _store or None


def _now() -> datetime:
    """Wall clock, as its own function so tests can freeze it."""
    return datetime.now()


def _parse_hhmm(value) -> tuple[int, int] | None:
    """'22:00' / '9:30 PM' / 2200 -> (hour, minute). None if unparseable."""
    if value is None:
        return None
    text = str(value).strip().strip("'\"")
    if not text:
        return None
    ampm = ""
    upper = text.upper()
    if upper.endswith(("AM", "PM")):
        ampm = upper[-2:]
        text = upper[:-2].strip()
    try:
        if ":" in text:
            hh, mm = text.split(":", 1)
            hour, minute = int(hh), int(mm[:2])
        elif len(text) == 4 and text.isdigit():
            hour, minute = int(text[:2]), int(text[2:])
        else:
            hour, minute = int(text), 0
    except (TypeError, ValueError):
        return None
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _read_operator_config() -> dict:
    """operator.yaml as a dict. Tolerates a missing file and a missing PyYAML."""
    if not OPERATOR_CONFIG.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(OPERATOR_CONFIG.read_text()) or {}
    except ImportError:
        return _parse_operator_minimal(OPERATOR_CONFIG.read_text())
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to read %s: %s", OPERATOR_CONFIG, e)
        return {}


def _parse_operator_minimal(text: str) -> dict:
    """PyYAML-free reader for the four keys quiet hours needs.

    Same reason as `_parse_topics_minimal`: a bash-invoked path may have no
    PyYAML, and quiet hours going wrong must not mean notifications going wrong.
    """
    data: dict = {"notifications": {"quiet_hours": {}}, "daily_loop": {}}
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            section = stripped[:-1] if stripped.endswith(":") else None
            continue
        if section == "daily_loop" and ":" in stripped:
            key, _, val = stripped.partition(":")
            data["daily_loop"][key.strip()] = val.strip()
        elif section == "notifications" and ":" in stripped:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip()
            if key in ("start", "end", "enabled"):
                data["notifications"]["quiet_hours"][key] = val
    return data


def quiet_window() -> tuple[tuple[int, int], tuple[int, int]] | None:
    """(start, end) as (hour, minute) pairs, or None when quiet hours are off.

    Resolution order, most explicit first:

    1. ``notifications.quiet_hours.start`` / ``.end`` -- the operator said so.
    2. ``daily_loop.evening_checkin`` -> ``daily_loop.morning_briefing`` -- the
       operator's day already has a start and an end, and a second set of times
       meaning the same thing is a second set of times to keep in sync.
    3. ``QUIET_HOURS_DEFAULT`` (22:00-07:00).

    ``schedule.blocks`` is deliberately not consulted: those are calendar
    commitments (teaching, a class), not a notification policy. Suppressing
    alerts because someone is in a lesson is a different decision from
    suppressing them because they are asleep, and conflating the two would
    silence the machine several times a day without anyone asking for it.
    """
    config = _read_operator_config()
    notif = (config.get("notifications") or {})
    quiet = notif.get("quiet_hours")
    if isinstance(quiet, dict):
        enabled = quiet.get("enabled", True)
        if str(enabled).strip().lower() in ("false", "no", "0", "off"):
            return None
        start = _parse_hhmm(quiet.get("start"))
        end = _parse_hhmm(quiet.get("end"))
        if start and end:
            return start, end
    elif isinstance(quiet, str) and "-" in quiet:
        # "22:00-07:00" as a single string, the shape active_hours uses.
        left, _, right = quiet.partition("-")
        start, end = _parse_hhmm(left), _parse_hhmm(right)
        if start and end:
            return start, end

    daily = config.get("daily_loop") or {}
    start = _parse_hhmm(daily.get("evening_checkin"))
    end = _parse_hhmm(daily.get("morning_briefing"))
    if start and end and start != end:
        return start, end

    return QUIET_HOURS_DEFAULT


def in_quiet_hours(now: datetime | None = None) -> bool:
    """True when `now` falls inside the quiet window (which may span midnight)."""
    window = quiet_window()
    if window is None:
        return False
    (sh, sm), (eh, em) = window
    current = (now or _now())
    minutes = current.hour * 60 + current.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end  # spans midnight


def is_urgent(kind: str = "info", urgent: bool | None = None) -> bool:
    """Urgency as declared, never inferred.

    `kind="alert"` is the existing signal for the things that must wake someone
    up: a service down, a drive gone, a security finding. A sender with better
    information passes `urgent=` explicitly. Replies the operator is waiting for
    do not come through this router at all -- they are the bridge's chat path,
    which is by definition a conversation already in progress.
    """
    if urgent is not None:
        return bool(urgent)
    return kind == "alert"


def _queue_for_digest(text: str, topic: str | None, kind: str) -> int | None:
    """Park a message for the next flush. None means the queue is unusable."""
    store = _load_store()
    if store is None:
        return None
    try:
        return store.queue_outbound(text, topic=topic, kind=kind)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not queue a notification for the digest (%s)", e)
        return None


def flush_quiet_queue(force: bool = False) -> dict:
    """Deliver everything queued during quiet hours as one digest.

    Called on a schedule (`core/bin/crons/notify-flush`, every 15 minutes) rather
    than at a fixed hour: the window is the operator's to change, and a cron that
    hardcodes 07:00 is wrong the moment they do. Inside quiet hours this is a
    no-op, so the digest lands within a quarter hour of the window opening.

    Returns {"flushed": n, "delivered": bool, "error": str | None}.
    """
    if not force and in_quiet_hours():
        return {"flushed": 0, "delivered": False, "error": None}

    store = _load_store()
    if store is None:
        return {"flushed": 0, "delivered": False, "error": "no conversation store"}

    rows = store.queued(limit=50)
    if not rows:
        return {"flushed": 0, "delivered": False, "error": None}

    items = [r["text_redacted"] for r in rows if (r["text_redacted"] or "").strip()]
    ids = [r["id"] for r in rows]

    body = "\n".join(f"• {item}" for item in items)
    humanizer = _load_humanizer()
    if humanizer:
        body = humanizer(body, kind="alert") or body  # alert: capped at four items
    count = len(items)
    lead = ("🌅 One thing came in overnight:" if count == 1
            else f"🌅 {count} things came in overnight:")
    digest = f"{lead}\n\n{body}"

    result = send_notification(digest, topic="daily", kind="info",
                              humanize=False, queue_when_quiet=False)
    if result.get("delivered"):
        # Mark them either way they were shown: the queue is a queue, not an
        # archive, and a row left 'queued' would be re-sent on the next flush.
        store.mark_digested(ids)
        return {"flushed": len(ids), "delivered": True, "error": result.get("error")}
    return {"flushed": 0, "delivered": False, "error": result.get("error")}


def _get_secret(name: str) -> str | None:
    try:
        result = subprocess.run(
            [str(AGENT_SECRET), "get", name],
            capture_output=True, text=True, timeout=5,
        )
        value = result.stdout.strip()
        return value if value and result.returncode == 0 else None
    except Exception:
        return None


def _load_topics() -> tuple[int | None, dict[str, int]]:
    """Return (forum_group_id, {topic: thread_id}) from bridge-topics.yaml.

    Uses a tolerant line parser so bash-invoked paths need no PyYAML.
    """
    if not TOPICS_CONFIG.exists():
        return None, {}
    try:
        import yaml  # available in the service venvs
        data = yaml.safe_load(TOPICS_CONFIG.read_text()) or {}
    except ImportError:
        data = _parse_topics_minimal(TOPICS_CONFIG.read_text())
    except Exception as e:
        log.warning("Failed to read %s: %s", TOPICS_CONFIG, e)
        return None, {}

    gid = data.get("forum_group_id")
    topics = {}
    for name, entry in (data.get("topics") or {}).items():
        if isinstance(entry, dict) and entry.get("thread_id"):
            topics[name] = int(entry["thread_id"])
    return (int(gid) if gid else None), topics


def _parse_topics_minimal(text: str) -> dict:
    """PyYAML-free fallback parser for bridge-topics.yaml's flat shape."""
    data: dict = {"topics": {}}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("forum_group_id:"):
            try:
                data["forum_group_id"] = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("  ") and stripped.endswith(":") and not line.startswith("    "):
            current = stripped[:-1]
            data["topics"][current] = {}
        elif current and stripped.startswith("thread_id:"):
            try:
                data["topics"][current]["thread_id"] = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
    return data


def get_routing() -> tuple[int | None, dict[str, int]]:
    """(forum_group_id, {topic: thread_id}) as the router currently sees it.

    Public read for health checks and setup tooling. Senders don't need this —
    they just call ``send_notification``.
    """
    return _load_topics()


def _split_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split into chunks that fit Telegram's limit.

    Prefers a newline boundary, then a space, then a hard cut. Ported from
    ``lib.notify`` so migrated senders keep byte-identical chunking.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def resolve_topic(topic: str | None, kind: str = "info") -> str:
    """Tier 1: sender-declared topic. Tier 2: infer from kind."""
    if topic in VALID_TOPICS:
        return topic
    if topic:
        log.warning("Unknown notify topic %r — falling back to inference", topic)
    return "alerts" if kind == "alert" else "system"


def _send_raw(token: str, chat_id: int | str, text: str,
              thread_id: int | None = None, parse_mode: str | None = "HTML",
              silent: bool = False, no_preview: bool = False) -> tuple[bool, str, int | None]:
    """One sendMessage call. Returns (ok, error_description, http_status)."""
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_notification": silent,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if thread_id:
        payload["message_thread_id"] = thread_id
    if no_preview:
        payload["disable_web_page_preview"] = True

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    _RATE_LIMITER.wait()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        return bool(body.get("ok")), body.get("description", ""), resp.status
    except urllib.error.HTTPError as e:
        try:
            payload_err = json.loads(e.read().decode())
        except Exception:
            payload_err = {}
        desc = payload_err.get("description", str(e))
        if e.code == 429:
            retry_after = payload_err.get("parameters", {}).get("retry_after")
            if retry_after:
                desc = f"{desc}|retry_after={retry_after}"
        return False, desc, e.code
    except Exception as e:
        return False, str(e), None


def _send_with_retry(token: str, chat_id: int | str, text: str,
                     thread_id: int | None = None, parse_mode: str | None = "HTML",
                     silent: bool = False, no_preview: bool = False) -> tuple[bool, str]:
    """Send one chunk, retrying transient failures. Returns (ok, error).

    Retries 429s (honouring ``retry_after``) and network/5xx errors with
    exponential backoff. A 400 is permanent and returns immediately so the
    caller's tier chain can move on — except an HTML parse failure, which is
    retried once as plain text.
    """
    for attempt in range(MAX_RETRIES):
        ok, err, code = _send_raw(token, chat_id, text, thread_id,
                                  parse_mode, silent, no_preview)
        if ok:
            return True, ""

        if code == 429:
            delay = BACKOFF_BASE * (2 ** attempt)
            if "|retry_after=" in err:
                try:
                    delay = float(err.split("|retry_after=")[1])
                except (ValueError, IndexError):
                    pass
            log.warning("Telegram rate limited, retrying after %ss", delay)
            time.sleep(delay)
            continue

        if code == 400:
            # HTML that Telegram won't parse: strip tags and try once as plain.
            if parse_mode and "parse" in err.lower():
                log.warning("Telegram parse failed (%s) — retrying as plain text", err)
                ok, plain_err, _ = _send_raw(token, chat_id, re.sub(r"<[^>]+>", "", text),
                                             thread_id, None, silent, no_preview)
                return (True, "") if ok else (False, plain_err)
            # Any other 400 (thread gone, chat not found, ...) is permanent.
            return False, err

        if attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_BASE * (2 ** attempt))

    return False, err


def send_notification(text: str, topic: str | None = None, kind: str = "info",
                      parse_mode: str | None = "HTML",
                      silent: bool = False, no_preview: bool = False,
                      humanize: bool = True, urgent: bool | None = None,
                      queue_when_quiet: bool = True) -> dict:
    """Deliver a notification, never silently dropping it.

    Messages over Telegram's 4096-char limit are split and delivered in
    order to a single destination. Transient failures (429, network, 5xx)
    are retried with backoff; unparseable HTML falls back to plain text.

    Returns a result dict: {"delivered": bool, "target": "topic:work" |
    "group" | "dm" | None, "error": str | None, "chunks": int,
    "partial": bool}. Callers may log it; they don't need to branch on it.
    """
    if not text:
        return {"delivered": False, "queued": False, "target": None,
                "error": "empty text"}

    token = _get_secret("TELEGRAM_BOT_TOKEN")
    if not token:
        log.info("Notification (no Telegram configured): %s", text[:100])
        return {"delivered": False, "queued": False, "target": None,
                "error": "no bot token"}

    if humanize:
        humanizer = _load_humanizer()
        if humanizer:
            cleaned = humanizer(text, kind=kind)
            if cleaned:
                text = cleaned

    prefix = _KIND_PREFIX.get(kind)
    if prefix and not _LEADS_WITH_EMOJI_RE.match(text):
        text = f"{prefix} {text}"

    # Quiet hours: park the non-urgent, push the rest. A failed queue falls
    # through to a normal send -- a late notification is a nuisance, a lost one
    # is a bug.
    if (queue_when_quiet and not is_urgent(kind, urgent) and in_quiet_hours()):
        queued_id = _queue_for_digest(text, topic, kind)
        if queued_id is not None:
            log.info("Queued for the morning digest (quiet hours): %s", text[:80])
            return {"delivered": False, "queued": True, "target": "digest",
                    "error": None, "chunks": 0, "partial": False}
        log.warning("Quiet-hours queue unavailable — sending now instead")

    group_id, topics = _load_topics()
    resolved = resolve_topic(topic, kind)
    thread_id = topics.get(resolved)

    # Build the tier chain: topic thread -> group General -> operator DM.
    tiers: list[tuple[int | str, int | None, str]] = []
    if group_id and thread_id:
        tiers.append((group_id, thread_id, f"topic:{resolved}"))
    if group_id:
        tiers.append((group_id, None, "group"))
    dm_chat_id = _get_secret("TELEGRAM_CHAT_ID")
    if dm_chat_id:
        tiers.append((dm_chat_id, None, "dm"))
    if not tiers:
        return {"delivered": False, "queued": False, "target": None,
                "error": "no delivery target configured"}

    chunks = _split_message(text)

    # Resolve the destination with the first chunk, then keep the rest of a
    # split message with it — a multi-part message must not scatter across
    # two different chats.
    chosen = None
    last_err = "no delivery target configured"
    for chat_id, thr, label in tiers:
        ok, err = _send_with_retry(token, chat_id, chunks[0], thr,
                                   parse_mode, silent, no_preview)
        if ok:
            chosen = (chat_id, thr, label)
            if label != tiers[0][2]:
                log.warning("Delivered to %s (preferred target unavailable)", label)
            break
        last_err = err
        if not any(m in err.lower() for m in _THREAD_GONE_MARKERS):
            log.warning("Send to %s failed (%s) — trying next target", label, err)

    if chosen is None:
        log.error("All notification targets failed: %s", last_err)
        return {"delivered": False, "queued": False, "target": None, "error": last_err}

    chat_id, thr, label = chosen
    partial_err = None
    for chunk in chunks[1:]:
        ok, err = _send_with_retry(token, chat_id, chunk, thr,
                                   parse_mode, silent, no_preview)
        if not ok:
            partial_err = err
            log.error("Chunk delivery to %s failed: %s", label, err)

    # The message reached the operator even if a trailing chunk did not, so
    # `delivered` stays True — a caller retrying would duplicate chunk 1.
    return {
        "delivered": True,
        "queued": False,
        "target": label,
        "error": partial_err,
        "chunks": len(chunks),
        "partial": partial_err is not None,
    }
