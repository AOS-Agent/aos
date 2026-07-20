"""Spam / phish gate."""
from __future__ import annotations

import json

from core.engine.comms.enrich.spam import is_spam

from ._helpers import msg


def test_gmail_spam_label_skipped():
    m = msg("e1", "You won a prize", ts="2026-04-01T00:00:00", channel="email",
            channel_metadata=json.dumps({"labels": ["SPAM"], "subject": "hi"}))
    skip, reason = is_spam(m)
    assert skip and reason == "label:SPAM"


def test_gmail_trash_label_skipped():
    m = msg("e2", "hello", ts="2026-04-01T00:00:00", channel="email",
            channel_metadata=json.dumps({"labelIds": ["TRASH"]}))
    assert is_spam(m)[0]


def test_phish_heuristic_on_body():
    # The exact lure family from sample §3 (source-blind extraction risk).
    m = msg("e3", "Cloud sync disabled due to Payment issue!! Your photos will be removed",
            ts="2026-04-01T00:00:00", channel="email",
            channel_metadata=json.dumps({"labels": ["INBOX"]}))
    skip, reason = is_spam(m)
    assert skip and reason == "phish_heuristic"


def test_phish_heuristic_on_subject():
    m = msg("e4", "regular body", ts="2026-04-01T00:00:00", channel="email",
            channel_metadata=json.dumps({"labels": ["INBOX"],
                                         "subject": "Your account has been suspended"}))
    assert is_spam(m)[0]


def test_phish_works_on_sms():
    m = msg("s1", "Click here to verify your account now", ts="2026-04-01T00:00:00",
            channel="sms")
    assert is_spam(m)[0]


def test_legit_payment_message_not_flagged():
    # A real receipt must pass — the heuristic is phrase-level, not word-level.
    m = msg("e5", "Receipt for $5.77 payment to Pirate Ship", ts="2026-04-01T00:00:00",
            channel="email", channel_metadata=json.dumps({"labels": ["INBOX"]}))
    assert not is_spam(m)[0]


def test_normal_chat_not_flagged():
    assert not is_spam(msg("m1", "want to grab iftar tonight?", ts="2026-03-09T18:00:00"))[0]


def test_malformed_metadata_survives():
    m = msg("m2", "hi", ts="2026-03-09T10:00:00", channel="email",
            channel_metadata="not json{{")
    assert not is_spam(m)[0]  # no crash, no label → not spam
