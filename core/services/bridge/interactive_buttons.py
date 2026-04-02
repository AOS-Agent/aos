"""Interactive inline buttons for Telegram responses.

Detects when Claude's response contains a question with selectable options,
and renders them as Telegram InlineKeyboardButtons. Also provides default
quick-action buttons for long responses.
"""

import logging
import re
import time
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

OPTION_MAX_AGE = 3600  # 1 hour — auto-expire stale pending options

# ── Detection ────────────────────────────────────────────

# Question-phrasing patterns that must appear before a numbered list
_QUESTION_PHRASES = re.compile(
    r'(?:would you like|do you (?:want|prefer)|should I|shall I'
    r'|which (?:one|option|approach)|how (?:would|should|do) you'
    r'|what (?:would|should|do) you|choose|pick|select'
    r'|here are (?:some|the|a few) (?:options|approaches|choices)'
    r'|you (?:can|could) (?:either|choose))',
    re.IGNORECASE,
)

# Matches a numbered list: "1. text\n2. text\n..."
_NUMBERED_LIST = re.compile(
    r'((?:^|\n)\s*\d+[.)]\s+.{3,80}(?:\n\s*\d+[.)]\s+.{3,80}){1,7})',
    re.MULTILINE,
)

_SINGLE_ITEM = re.compile(r'^\s*\d+[.)]\s+(.+)$', re.MULTILINE)

MAX_OPTION_LENGTH = 80
MIN_OPTIONS = 2
MAX_OPTIONS = 6


@dataclass
class DetectedOptions:
    """Result of option detection on a response."""
    question_text: str
    options: list[str]


def detect_options(response_text: str) -> DetectedOptions | None:
    """Detect if a response ends with a question + numbered options.

    Only scans the last ~800 chars. Requires both a question signal
    and short numbered items to avoid false positives.
    """
    if not response_text or len(response_text) < 20:
        return None

    tail_start = max(0, len(response_text) - 800)
    tail = response_text[tail_start:]

    # Find the last numbered list in the tail
    match = None
    for m in _NUMBERED_LIST.finditer(tail):
        match = m

    if not match:
        return None

    list_block = match.group(0).strip()
    items = _SINGLE_ITEM.findall(list_block)

    if len(items) < MIN_OPTIONS or len(items) > MAX_OPTIONS:
        return None

    if any(len(item.strip()) > MAX_OPTION_LENGTH for item in items):
        return None

    # Check for question context before the list
    before_list = tail[:match.start()].strip()
    paragraphs = before_list.rsplit('\n\n', 1)
    last_para = paragraphs[-1].strip() if paragraphs else ""

    has_question_mark = '?' in last_para
    has_question_phrase = bool(_QUESTION_PHRASES.search(last_para))

    if not (has_question_mark or has_question_phrase):
        return None

    return DetectedOptions(
        question_text=last_para,
        options=[item.strip() for item in items],
    )


# ── Keyboard Building ───────────────────────────────────

# In-memory store: session_suffix -> {"options": {index: text}, "ts": timestamp}
_pending_options: dict[str, dict] = {}


def _prune_stale():
    """Remove entries older than OPTION_MAX_AGE."""
    cutoff = time.time() - OPTION_MAX_AGE
    stale = [k for k, v in _pending_options.items()
             if v.get("ts", 0) < cutoff]
    for k in stale:
        del _pending_options[k]


def _session_suffix(session_id: str) -> str:
    """Last 8 chars of session ID for compact callback_data."""
    return (session_id or "nosess")[-8:]


def build_option_keyboard(
    detected: DetectedOptions,
    session_id: str,
) -> InlineKeyboardMarkup:
    """Build InlineKeyboardMarkup from detected options."""
    _prune_stale()  # clean up old entries on each new keyboard build
    suffix = _session_suffix(session_id)
    option_map = {}
    buttons = []

    for i, option_text in enumerate(detected.options):
        option_map[i] = option_text
        label = option_text[:37] + "..." if len(option_text) > 40 else option_text
        buttons.append([InlineKeyboardButton(label, callback_data=f"opt:{suffix}:{i}")])

    _pending_options[suffix] = {"options": option_map, "ts": time.time()}
    return InlineKeyboardMarkup(buttons)


def build_quick_action_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Default quick-action buttons for long responses."""
    suffix = _session_suffix(session_id)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Go deeper", callback_data=f"qa:{suffix}:deeper"),
        InlineKeyboardButton("Do it", callback_data=f"qa:{suffix}:doit"),
    ]])


def resolve_callback(callback_data: str) -> tuple[str, str] | None:
    """Resolve callback_data into (type, message_to_send).

    Returns:
        ("option", "the full option text") for option buttons
        ("quick", "the quick action prompt") for quick-action buttons
        None if unrecognized or expired
    """
    parts = callback_data.split(":", 2)
    if len(parts) < 3:
        return None

    kind, suffix, value = parts

    if kind == "opt":
        try:
            idx = int(value)
        except ValueError:
            return None
        entry = _pending_options.get(suffix, {})
        option_map = entry.get("options", {}) if isinstance(entry, dict) and "options" in entry else entry
        text = option_map.get(idx)
        if text:
            _pending_options.pop(suffix, None)
            return ("option", text)
        return None

    elif kind == "qa":
        prompts = {
            "deeper": "Go deeper on this. Explain in more detail.",
            "doit": "Go ahead and do it.",
        }
        prompt = prompts.get(value)
        if prompt:
            return ("quick", prompt)
        return None

    return None
