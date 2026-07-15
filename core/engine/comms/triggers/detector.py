"""Phrase matching for trigger detection.

A trigger is a phrase that, when sent OUTBOUND by the operator on an
enabled channel, hands off the conversational commitment to an AOS agent.

Matching rules:
- Case-insensitive
- Word-boundary regex (no substring matches inside other words)
- Must appear as a directive clause: standalone, or terminating a sentence,
  or preceded by a connector ("…, consider it done.")
- Phrase inside quotes ("she said 'consider it done'") is IGNORED
- Operator's own agent sends (metadata.source='sentinel') are IGNORED
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class TriggerMatch:
    """A successful trigger detection."""
    phrase: str           # the phrase that matched, e.g. "consider it done"
    text: str             # the original message text
    position: int         # character offset of the match
    is_directive: bool    # passed the directive-clause heuristic


# Connectors that commonly precede a directive clause
DIRECTIVE_PRECEDERS = (
    r"^",            # start of message
    r"[.!?]\s*",     # end of previous sentence
    r"[,—–-]\s*",    # connector
    r"\n+\s*",       # new line
)

# Connectors that commonly follow a directive clause
DIRECTIVE_FOLLOWERS = (
    r"$",            # end of message
    r"\s*[.!?]",     # end punctuation
    r"\s*[,—–-]",    # connector
    r"\s*\n",        # new line
)


def _build_phrase_pattern(phrase: str) -> re.Pattern:
    """Build a case-insensitive, word-bounded regex for a phrase."""
    # @aos contains '@' which isn't a word char — handle specially
    escaped = re.escape(phrase.lower())
    # Require non-word char (or boundary) on each side
    return re.compile(rf"(?<![\w@]){escaped}(?![\w])", re.IGNORECASE)


def _is_inside_quotes(text: str, position: int) -> bool:
    """True if the position falls inside a quoted span."""
    # Count unescaped quotes before the position; odd = inside a quote
    before = text[:position]
    # Strip escaped quotes
    before_clean = before.replace("\\\"", "").replace("\\'", "")
    double = before_clean.count('"')
    single = before_clean.count("'") - before_clean.count("it's") - before_clean.count("I'm") - before_clean.count("don't")
    # Imperfect, but catches the common case "she said 'X'"
    return (double % 2 == 1) or (single % 2 == 1 and "'" in text[max(0, position-30):position])


def _is_directive_clause(text: str, match: re.Match) -> bool:
    """Check if the match looks like a directive (vs incidental) phrase.

    Heuristic: phrase is at start of message, end of message, or surrounded
    by sentence-boundary punctuation.
    """
    start, end = match.span()
    text_len = len(text)
    margin = 8  # chars of context to scan

    # Look at what precedes
    before = text[max(0, start - margin):start]
    after = text[end:min(text_len, end + margin)]

    preceder_ok = any(re.search(p + r"$", before) for p in DIRECTIVE_PRECEDERS)
    follower_ok = any(re.match(p, after) for p in DIRECTIVE_FOLLOWERS)

    return preceder_ok or follower_ok


class TriggerDetector:
    """Scans message text for trigger phrases."""

    def __init__(self, phrases: Iterable[str]):
        # Sorted longest-first to prefer specific over generic
        self._phrases = sorted({p.strip() for p in phrases if p.strip()},
                                key=len, reverse=True)
        self._patterns = [(p, _build_phrase_pattern(p)) for p in self._phrases]

    @property
    def phrases(self) -> list[str]:
        return list(self._phrases)

    def find_trigger(self, text: str) -> Optional[TriggerMatch]:
        """Return the first matching trigger phrase, or None."""
        if not text:
            return None
        for phrase, pattern in self._patterns:
            for m in pattern.finditer(text):
                if _is_inside_quotes(text, m.start()):
                    continue
                directive = _is_directive_clause(text, m)
                if not directive:
                    continue
                return TriggerMatch(
                    phrase=phrase,
                    text=text,
                    position=m.start(),
                    is_directive=True,
                )
        return None

    @classmethod
    def from_config(cls, config_path) -> "TriggerDetector":
        """Build from a sentinel.yaml-style config dict or path."""
        from pathlib import Path

        import yaml
        data = yaml.safe_load(Path(config_path).read_text())
        phrases = data.get("trigger_phrases", [])
        return cls(phrases)


# ── self-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    d = TriggerDetector(["consider it done", "@aos"])

    # Should match
    pos = [
        "Yeah I'll find you a good Italian spot. Consider it done.",
        "consider it done",
        "Got it — consider it done!",
        "@aos handle this please",
        "ok let me look into that, @aos",
    ]
    # Should NOT match
    neg = [
        "She said 'consider it done' but didn't follow through.",
        "I'll consider it. Done!",
        "weemail consider it done@example.com",
        "I emailed @aos2 about it",
    ]

    print("POSITIVE cases:")
    for t in pos:
        m = d.find_trigger(t)
        ok = "✓" if m else "✗"
        print(f"  {ok} {t!r} → {m.phrase if m else None}")

    print("\nNEGATIVE cases (should all be None):")
    for t in neg:
        m = d.find_trigger(t)
        ok = "✓" if not m else "✗"
        print(f"  {ok} {t!r} → {m.phrase if m else None}")
