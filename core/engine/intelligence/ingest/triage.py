"""triage — Score feed items by relevance to the operator's interests."""

from datetime import datetime, timezone, timedelta


def score_item(item: dict, source: dict) -> tuple[float, list[str]]:
    """Score an item's relevance based on keywords, source priority, and recency.

    Scoring:
        - Keyword match on title+description: +0.3 per match (case-insensitive)
        - Source priority boost: high=+0.2, normal=0, low=-0.1
        - Recency boost: published in last 6 hours = +0.1
        - Base relevance (no keywords): 0.3
        - Capped at 1.0

    Returns:
        (score, matched_tags) where matched_tags is a list of keyword strings.
    """
    keywords = source.get("keywords", [])
    if not keywords:
        # No keywords configured — return base relevance
        score = 0.3
        score += _priority_boost(source)
        score += _recency_boost(item)
        return (min(max(score, 0.0), 1.0), [])

    # Build searchable text from title + description
    text = _searchable_text(item)

    # Keyword matching
    matched = []
    for kw in keywords:
        if not kw:
            continue
        # Case-insensitive substring match
        if kw.lower() in text:
            matched.append(kw)

    if not matched:
        # Keywords defined but none matched — low base
        score = 0.1
    else:
        score = len(matched) * 0.3

    score += _priority_boost(source)
    score += _recency_boost(item)

    return (min(max(score, 0.0), 1.0), matched)


def _searchable_text(item: dict) -> str:
    """Combine title and description into a single lowercase search string."""
    parts = []
    if item.get("title"):
        parts.append(item["title"])
    if item.get("description"):
        parts.append(item["description"])
    return " ".join(parts).lower()


def _priority_boost(source: dict) -> float:
    """Return score adjustment based on source priority."""
    priority = (source.get("priority") or "normal").lower()
    if priority == "high":
        return 0.2
    elif priority == "low":
        return -0.1
    return 0.0


def _recency_boost(item: dict) -> float:
    """Return +0.1 if the item was published within the last 6 hours."""
    published = item.get("published")
    if not published:
        return 0.0

    try:
        # Try ISO format first
        if isinstance(published, str):
            # Handle timezone-aware and naive strings
            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        else:
            return 0.0
    except (ValueError, TypeError):
        return 0.0

    now = datetime.now(timezone.utc)
    # Ensure pub_dt is timezone-aware
    if pub_dt.tzinfo is None:
        pub_dt = pub_dt.replace(tzinfo=timezone.utc)

    age = now - pub_dt
    if age < timedelta(hours=6):
        return 0.1

    return 0.0
