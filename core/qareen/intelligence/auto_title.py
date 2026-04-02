"""Auto-title generation for companion sessions.

Generates a short, descriptive title (3-5 words) from the first transcript
segments. Uses a simple heuristic: extract the most distinctive words
(ignoring stopwords and filler), then compose a natural-sounding title.

Examples:
  "Nuchay Pricing Discussion"
  "Morning Planning Session"
  "Tafsir Root Analysis"
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Common English stopwords + conversational filler
STOPWORDS = frozenset({
    # Articles & prepositions
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "up", "about", "into", "through", "during", "before",
    "after", "above", "below", "between", "under", "over",
    # Pronouns
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    # Conjunctions & common verbs
    "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "neither", "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing",
    "will", "would", "shall", "should", "may", "might", "must", "can",
    "could",
    # Common words
    "that", "this", "these", "those", "what", "which", "who", "whom",
    "when", "where", "why", "how", "if", "then", "than", "too", "very",
    "just", "also", "now", "here", "there", "all", "each", "every",
    "any", "some", "no", "more", "most", "other", "such", "only",
    "own", "same", "as", "like", "get", "got", "go", "going", "went",
    "come", "came", "make", "made", "take", "took", "give", "gave",
    "say", "said", "tell", "told", "know", "knew", "think", "thought",
    "see", "saw", "want", "need", "let", "put", "try", "keep",
    # Filler
    "um", "uh", "like", "yeah", "okay", "ok", "right", "well",
    "actually", "basically", "literally", "really", "stuff", "thing",
    "things", "something", "anything", "everything", "nothing",
    "kind", "sort", "lot", "lots", "way", "much", "many",
})

# Time-of-day for fallback titles
_TIME_LABELS = {
    (5, 12): "Morning",
    (12, 17): "Afternoon",
    (17, 21): "Evening",
    (21, 5): "Late Night",
}

# Session type suffixes for fallback
_TYPE_SUFFIXES = {
    "conversation": "Session",
    "processing": "Processing",
}


def _extract_words(text: str) -> list[str]:
    """Extract cleaned words from text, filtering stopwords."""
    # Remove timestamps, speaker labels like "[14:30] You:"
    text = re.sub(r"\[\d{1,2}:\d{2}\]\s*\w+:", "", text)
    # Keep only word characters (including unicode for Arabic etc.)
    words = re.findall(r"\b[\w']+\b", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2 and not w.isdigit()]


def _capitalize_title(words: list[str]) -> str:
    """Title-case a list of words, preserving proper nouns / acronyms."""
    return " ".join(w.capitalize() for w in words)


def generate_title_from_transcript(
    transcript: list[dict[str, Any]],
    max_segments: int = 5,
) -> str:
    """Generate a 3-5 word title from transcript segments.

    Args:
        transcript: List of transcript blocks with 'text' field.
        max_segments: How many segments to consider (from the start).

    Returns:
        A short title string, or empty string if not enough content.
    """
    if not transcript:
        return ""

    # Take first N segments
    segments = transcript[:max_segments]
    combined_text = " ".join(seg.get("text", "") for seg in segments)

    if len(combined_text.strip()) < 10:
        return ""

    words = _extract_words(combined_text)
    if not words:
        return ""

    # Count word frequencies — most frequent distinctive words
    counter = Counter(words)

    # Boost words that appear in subject position (first few words of segments)
    for seg in segments:
        first_words = _extract_words(seg.get("text", ""))[:3]
        for w in first_words:
            counter[w] += 1

    # Get top distinctive words
    top_words = [word for word, _ in counter.most_common(8)]

    # Build title: pick 3-5 of the most distinctive
    title_words: list[str] = []
    for word in top_words:
        if len(title_words) >= 4:
            break
        # Skip very short words and duplicates
        if len(word) > 2 and word not in title_words:
            title_words.append(word)

    if not title_words:
        return ""

    # Add a session-type suffix if the title is very short
    if len(title_words) <= 2:
        title_words.append("Session")

    return _capitalize_title(title_words)


def get_fallback_title(
    session_type: str = "conversation",
    hour: int | None = None,
) -> str:
    """Generate a fallback title when transcript is empty.

    Returns something like "Morning Session" or "Afternoon Processing".
    """
    from datetime import datetime as _dt

    if hour is None:
        hour = _dt.now().hour

    time_label = "Session"
    for (start, end), label in _TIME_LABELS.items():
        if start <= end:
            if start <= hour < end:
                time_label = label
                break
        else:  # wraps midnight (21-5)
            if hour >= start or hour < end:
                time_label = label
                break

    suffix = _TYPE_SUFFIXES.get(session_type, "Session")
    return f"{time_label} {suffix}"
