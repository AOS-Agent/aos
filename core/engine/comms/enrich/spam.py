"""Spam / phishing gate — runs BEFORE extraction.

The Phase 2 sample (research §3) showed extraction is content-faithful but
source-blind: it happily lifted `transaction`/`topic` entities out of phishing
mail ("Cloud sync disabled due to Payment issue!! Your photos will be removed").
Those become false payment alerts downstream. So junk sources are gated out
before a message ever reaches Haiku, and marked terminally in the watermark
(`status='skipped_spam'`) so they are never re-attempted.

Two signals, both cheap and v1-simple:
  1. Channel label — Gmail carries its labels in `channel_metadata`; a message
     labelled SPAM or TRASH is dropped outright (authoritative, provider-side).
  2. Phish heuristic — keyword/pattern match over subject+body for the classic
     account-suspension / payment-failure / prize / credential-reset lures. This
     is deliberately a keyword pass (v1); precision over recall, and it only
     matters for the actionable domains, so a missed exotic phish just means an
     entity we would have dropped later anyway.

`is_spam(message)` returns (True, reason) or (False, ""). Non-email channels
skip the label check but still get the heuristic (SMS/WhatsApp phishing exists).
"""

from __future__ import annotations

import json
import re
from typing import Any

# Provider labels that mean "junk" outright.
_JUNK_LABELS = {"SPAM", "TRASH"}

# Phish lures — matched case-insensitively over subject + body. Each entry is a
# compiled pattern; a message needs a phrase-level hit, not a lone word, to keep
# false positives down (a legit "payment" mail must not trip this).
_PHISH_PATTERNS = [
    r"\bcloud sync (?:is )?disabled\b",
    r"\bpayment (?:issue|problem|failed|declined|was declined)\b",
    r"\byour (?:photos|files|account|data) will be (?:removed|deleted|suspended|lost)\b",
    r"\baccount (?:has been |is |will be )?(?:suspended|locked|disabled|compromised|limited)\b",
    r"\b(?:verify|confirm|update|re-?enter) your (?:account|password|payment|billing|identity)\b",
    r"\bunusual (?:sign[- ]?in|login|activity)\b",
    r"\bclick (?:here|the link|below) (?:to )?(?:verify|confirm|reactivate|unlock|restore)\b",
    r"\byou(?:'ve| have) won\b",
    r"\b(?:claim|collect) your (?:prize|reward|gift card|refund)\b",
    r"\b(?:wire|transfer) .{0,20}(?:immediately|urgently|asap)\b",
    r"\bpassword (?:will )?expire",
    r"\bstorage (?:is )?(?:full|almost full).{0,40}(?:upgrade|pay|billing)\b",
]
_PHISH_RE = re.compile("|".join(_PHISH_PATTERNS), re.IGNORECASE)


def _labels(channel_metadata: Any) -> list[str]:
    """Pull Gmail labels out of channel_metadata (JSON string or dict)."""
    if not channel_metadata:
        return []
    meta = channel_metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return []
    if not isinstance(meta, dict):
        return []
    labels = meta.get("labels") or meta.get("labelIds") or []
    if isinstance(labels, str):
        labels = [labels]
    return [str(x).upper() for x in labels]


def _subject(channel_metadata: Any) -> str:
    if not channel_metadata:
        return ""
    meta = channel_metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return ""
    if isinstance(meta, dict):
        return str(meta.get("subject") or "")
    return ""


def is_spam(message: dict) -> tuple[bool, str]:
    """Decide whether a message is junk and must skip extraction.

    message: a row dict with at least `content`, `channel`, `channel_metadata`.
    Returns (skip, reason). reason is stable and short (goes to logs).
    """
    meta = message.get("channel_metadata")

    # 1. Provider label (email only; authoritative).
    if message.get("channel") == "email":
        labels = _labels(meta)
        hit = _JUNK_LABELS.intersection(labels)
        if hit:
            return True, f"label:{sorted(hit)[0]}"

    # 2. Phish heuristic over subject + body (all channels).
    haystack = f"{_subject(meta)}\n{message.get('content') or ''}"
    if _PHISH_RE.search(haystack):
        return True, "phish_heuristic"

    return False, ""
