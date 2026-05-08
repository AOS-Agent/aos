"""
Style Intelligence — Stage 1: Texture Extraction & Clustering.

Extracts per-message texture vectors from outbound messages in comms.db,
then clusters them into coherent communication modes using quantile splitting
with variance-ratio scoring. Pure stdlib — no external dependencies.

Language-agnostic: uses Unicode script detection instead of hardcoded wordlists.
Works for any operator in any language.
"""

import logging
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
MIN_OUTBOUND = 20  # Need enough messages for stable clustering

# Regex for non-Latin script detection (anything outside Basic Latin + Latin Extended)
_RE_NON_LATIN = re.compile(
    r"[\u0600-\u06FF"       # Arabic
    r"\u0900-\u097F"        # Devanagari
    r"\u0400-\u04FF"        # Cyrillic
    r"\u4E00-\u9FFF"        # CJK Unified
    r"\u3040-\u309F"        # Hiragana
    r"\u30A0-\u30FF"        # Katakana
    r"\uAC00-\uD7AF"        # Hangul
    r"\u0A00-\u0A7F"        # Gurmukhi
    r"\u0980-\u09FF"        # Bengali
    r"\u0B80-\u0BFF"        # Tamil
    r"\u0C00-\u0C7F"        # Telugu
    r"\u0E00-\u0E7F"        # Thai
    r"\u1000-\u109F"        # Myanmar
    r"\u0590-\u05FF"        # Hebrew
    r"\u10A0-\u10FF]"       # Georgian
)

# Regex for emoji detection (broad Unicode ranges)
_RE_EMOJI = re.compile(
    r"[\U0001F600-\U0001F64F"
    r"\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF"
    r"\U0001F1E0-\U0001F1FF"
    r"\U00002702-\U000027B0"
    r"\U0000FE00-\U0000FE0F"
    r"\U0001F900-\U0001F9FF"
    r"\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF"
    r"\U00002600-\U000026FF]"
)

# Punctuation characters counted for density
_PUNCTUATION_CHARS = set(".!?,;:")

# Terminal punctuation for fragment detection
_RE_TERMINAL = re.compile(r"[.!?]\s*$")

# Top ~200 most common English words — used as a baseline for detecting
# loanwords and abbreviations. Intentionally minimal: we only need to
# distinguish "clearly English" from "possibly borrowed/abbreviated."
_COMMON_ENGLISH = frozenset([
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
    "or", "an", "will", "my", "one", "all", "would", "there", "their", "what",
    "so", "up", "out", "if", "about", "who", "get", "which", "go", "me",
    "when", "make", "can", "like", "time", "no", "just", "him", "know", "take",
    "people", "into", "year", "your", "good", "some", "could", "them", "see",
    "other", "than", "then", "now", "look", "only", "come", "its", "over",
    "think", "also", "back", "after", "use", "two", "how", "our", "work",
    "first", "well", "way", "even", "new", "want", "because", "any", "these",
    "give", "day", "most", "us", "is", "was", "are", "been", "has", "had",
    "were", "did", "been", "being", "am", "does", "done", "got", "going",
    "too", "much", "more", "very", "here", "still", "should", "need", "right",
    "big", "really", "where", "long", "down", "each", "same", "those", "both",
    "while", "own", "off", "always", "last", "every", "never", "thing", "things",
    "great", "before", "through", "many", "help", "between", "must", "home",
    "under", "such", "keep", "end", "let", "put", "again", "old", "found",
    "part", "around", "may", "might", "yes", "yeah", "ok", "okay", "sure",
    "please", "thanks", "thank", "sorry", "hello", "hey", "hi", "bye",
    "why", "what", "when", "where", "how", "who", "which", "been", "being",
])

# Top ~500 most common English words — extended set for abbreviation detection.
# Short common words that should NOT be flagged as abbreviations.
_COMMON_ENGLISH_500 = _COMMON_ENGLISH | frozenset([
    "able", "above", "add", "age", "ago", "air", "also", "another", "area",
    "ask", "away", "bad", "base", "best", "bit", "body", "book", "boy",
    "bring", "call", "came", "care", "case", "city", "close", "cold",
    "cut", "dark", "dead", "deal", "dear", "deep", "door", "draw", "drop",
    "dry", "east", "easy", "eat", "else", "ever", "face", "fact", "fall",
    "far", "fast", "feel", "feet", "few", "fill", "find", "fine", "fire",
    "food", "foot", "four", "free", "full", "game", "gave", "girl", "glad",
    "gone", "grew", "grow", "half", "hand", "hard", "hate", "hear", "heat",
    "held", "high", "hold", "hope", "hot", "hour", "idea", "job", "kept",
    "kid", "kind", "knew", "land", "late", "lead", "left", "less", "life",
    "line", "list", "live", "look", "lose", "lost", "lot", "love", "low",
    "main", "man", "mark", "mind", "miss", "mom", "move", "name", "near",
    "next", "nice", "note", "note", "once", "open", "paid", "pass", "past",
    "pay", "pick", "plan", "play", "post", "pull", "push", "read", "real",
    "rest", "rich", "ride", "rise", "road", "room", "rule", "run", "safe",
    "said", "sat", "save", "seem", "self", "sell", "send", "set", "show",
    "shut", "side", "sign", "sit", "six", "size", "soon", "sort", "stay",
    "step", "stop", "talk", "tell", "ten", "test", "text", "told", "top",
    "try", "turn", "type", "upon", "used", "view", "wait", "wake", "walk",
    "wall", "war", "warm", "wash", "week", "went", "west", "wide", "wife",
    "win", "wish", "word", "wore", "wrap", "yard", "yet", "zero",
    "also", "been", "best", "both", "came", "does", "done", "each", "else",
    "even", "from", "gone", "good", "have", "here", "into", "just", "keep",
    "knew", "know", "last", "left", "like", "long", "look", "made", "make",
    "many", "more", "most", "much", "must", "next", "only", "open", "over",
    "said", "same", "seen", "some", "such", "take", "tell", "than", "that",
    "them", "then", "they", "this", "time", "took", "very", "want", "well",
    "went", "were", "what", "when", "will", "with", "work", "your",
    "ill", "ive", "dont", "cant", "wont", "isnt", "not", "but", "and",
    "the", "for", "are", "was", "all", "had", "her", "him", "his",
])


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TextureVector:
    """Per-message texture features."""
    message_id: str
    char_length: int
    emoji_count: int
    has_non_latin: bool  # True if message contains any non-Latin script
    lowercase_ratio: float
    punctuation_density: float
    is_fragment: bool

    # Backward compat alias — old code referencing has_arabic still works
    @property
    def has_arabic(self) -> bool:
        return self.has_non_latin


@dataclass
class Cluster:
    """A group of similarly-textured messages."""
    vectors: list[TextureVector] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    signature: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Texture computation
# ---------------------------------------------------------------------------

def compute_texture(message_id: str, content: str) -> TextureVector:
    """Compute texture features for a single message.

    Args:
        message_id: Unique message identifier.
        content: Raw message text.

    Returns:
        TextureVector with computed features.
    """
    char_length = len(content)
    emoji_count = len(_RE_EMOJI.findall(content))
    has_non_latin = bool(_RE_NON_LATIN.search(content))

    # Lowercase ratio — fraction of alpha chars that are lowercase
    alpha_chars = [c for c in content if c.isalpha()]
    if alpha_chars:
        lowercase_ratio = sum(1 for c in alpha_chars if c.islower()) / len(alpha_chars)
    else:
        lowercase_ratio = 1.0

    # Punctuation density — count of .!?,;: per character
    if char_length > 0:
        punct_count = sum(1 for c in content if c in _PUNCTUATION_CHARS)
        punctuation_density = punct_count / char_length
    else:
        punctuation_density = 0.0

    # Fragment: fewer than 8 words AND no terminal punctuation
    words = content.split()
    is_fragment = len(words) < 8 and not _RE_TERMINAL.search(content)

    return TextureVector(
        message_id=message_id,
        char_length=char_length,
        emoji_count=emoji_count,
        has_non_latin=has_non_latin,
        lowercase_ratio=lowercase_ratio,
        punctuation_density=punctuation_density,
        is_fragment=is_fragment,
    )


# ---------------------------------------------------------------------------
# Outbound extraction
# ---------------------------------------------------------------------------

def extract_outbound(
    person_id: str, comms_conn: sqlite3.Connection
) -> list[TextureVector]:
    """Extract texture vectors for all outbound messages to a person.

    Args:
        person_id: Target person ID in comms.db.
        comms_conn: Open connection to comms.db.

    Returns:
        List of TextureVector for each outbound message with content.
    """
    try:
        rows = comms_conn.execute(
            """
            SELECT id, content
            FROM messages
            WHERE person_id = ?
              AND direction = 'outbound'
              AND content IS NOT NULL
              AND length(content) > 0
            ORDER BY timestamp ASC
            """,
            (person_id,),
        ).fetchall()
    except sqlite3.Error as e:
        log.error("Failed to query outbound messages for %s: %s", person_id, e)
        return []

    vectors = []
    for msg_id, content in rows:
        vectors.append(compute_texture(msg_id, content))
    return vectors


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def _min_max_normalize(values: list[float]) -> list[float]:
    """Normalize a list of floats to [0, 1] using min-max scaling."""
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span == 0:
        return [0.5] * len(values)
    return [(v - lo) / span for v in values]


def _compute_composite(vectors: list[TextureVector]) -> list[float]:
    """Compute a weighted composite score per vector for clustering.

    Weights: 0.3*norm_length + 0.2*emoji_rate + 0.2*(1-formality)
             + 0.15*fragment_ratio + 0.15*arabic_mix
    """
    if not vectors:
        return []

    raw_lengths = [float(v.char_length) for v in vectors]
    raw_emoji = [float(v.emoji_count) for v in vectors]
    # Formality proxy: punctuation_density + (1 - lowercase_ratio)
    raw_formality = [v.punctuation_density + (1.0 - v.lowercase_ratio) for v in vectors]
    raw_fragment = [1.0 if v.is_fragment else 0.0 for v in vectors]
    raw_script_mix = [1.0 if v.has_non_latin else 0.0 for v in vectors]

    n_length = _min_max_normalize(raw_lengths)
    n_emoji = _min_max_normalize(raw_emoji)
    n_formality = _min_max_normalize(raw_formality)
    n_fragment = _min_max_normalize(raw_fragment)
    n_script_mix = _min_max_normalize(raw_script_mix)

    composites = []
    for i in range(len(vectors)):
        score = (
            0.30 * n_length[i]
            + 0.20 * n_emoji[i]
            + 0.20 * (1.0 - n_formality[i])
            + 0.15 * n_fragment[i]
            + 0.15 * n_script_mix[i]  # language mixing signal (any non-Latin script)
        )
        composites.append(score)
    return composites


def _variance(values: list[float]) -> float:
    """Compute variance of a list of floats."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _cluster_signature(vectors: list[TextureVector]) -> dict:
    """Compute aggregate signature stats for a cluster of vectors."""
    n = len(vectors)
    if n == 0:
        return {}
    return {
        "avg_length": sum(v.char_length for v in vectors) / n,
        "emoji_rate": sum(v.emoji_count for v in vectors) / n,
        "lowercase_ratio": sum(v.lowercase_ratio for v in vectors) / n,
        # Kept as "arabic_mix" key for DB/JSON backward compat — actually tracks any non-Latin script
        "arabic_mix": sum(1 for v in vectors if v.has_non_latin) / n,
        "fragment_ratio": sum(1 for v in vectors if v.is_fragment) / n,
        "formality_score": sum(
            v.punctuation_density + (1.0 - v.lowercase_ratio) for v in vectors
        ) / n,
    }


def _filter_outliers(vectors: list[TextureVector]) -> list[TextureVector]:
    """Remove outlier messages that would pollute clusters.

    Filters out messages that are abnormally long (>3x IQR above Q3) — these
    are typically copy-pasted content, forwarded emails, or AI-generated text
    that don't represent the operator's natural voice.
    """
    if len(vectors) < 10:  # Too few to reliably detect outliers
        return vectors

    lengths = sorted(v.char_length for v in vectors)
    q1_idx = len(lengths) // 4
    q3_idx = (3 * len(lengths)) // 4
    q1 = lengths[q1_idx]
    q3 = lengths[q3_idx]
    iqr = q3 - q1

    # Upper fence: Q3 + 3*IQR — statistical outlier threshold (3x IQR is standard)
    upper = q3 + 3 * iqr if iqr > 0 else q3 * 5  # Fallback 5x Q3 when IQR is 0

    # Also filter very short non-content (single char, attachment placeholders)
    filtered = [v for v in vectors if v.char_length <= upper and v.char_length > 1]

    # If we filtered too aggressively (>50% removed), back off to just removing 1-char
    if len(filtered) < len(vectors) * 0.5:
        return [v for v in vectors if v.char_length > 1]

    return filtered


def cluster_messages(
    vectors: list[TextureVector],
    k_range: tuple[int, int] = (2, 5),  # Most people have 2-5 distinct communication modes
) -> list[Cluster]:
    """Cluster messages into communication modes using multi-dimensional splitting.

    Uses a 2-pass approach:
    1. Filter outliers (copy-pasted walls of text, single-char messages)
    2. Cluster on TWO dimensions: formality (punctuation + capitalization) and
       expressiveness (emoji + fragments + length). This separates short-formal
       from short-casual — which single-dimension clustering misses.

    Args:
        vectors: List of TextureVector to cluster.
        k_range: (min_k, max_k) inclusive range of cluster counts to try.

    Returns:
        List of Cluster objects with computed signatures.
    """
    if not vectors:
        return []

    # Pass 1: Remove outliers
    clean = _filter_outliers(vectors)
    if len(clean) < 10:
        clean = vectors  # not enough after filtering, use all

    # Compute two independent axes
    # Axis 1: Formality (high = formal, low = casual)
    raw_formality = [v.punctuation_density + (1.0 - v.lowercase_ratio) for v in clean]
    # Axis 2: Expressiveness (high = expressive, low = terse)
    raw_expressive = [
        (min(v.char_length, 200) / 200) * 0.5 +  # length (capped at 200 chars)
        (min(v.emoji_count, 5) / 5) * 0.3 +       # emojis (capped at 5)
        (1.0 if v.has_non_latin else 0.0) * 0.2   # script mixing signal
        for v in clean
    ]

    n_formality = _min_max_normalize(raw_formality)
    n_expressive = _min_max_normalize(raw_expressive)

    # 2D points
    points = list(zip(n_formality, n_expressive))

    # Check if there's enough spread to cluster
    f_var = _variance(n_formality)
    e_var = _variance(n_expressive)
    if f_var < 1e-9 and e_var < 1e-9:
        cluster = Cluster(
            vectors=list(clean),
            message_ids=[v.message_id for v in clean],
            signature=_cluster_signature(clean),
        )
        return [cluster]

    # Try k=2..5 using 2D grid splitting
    best_k = 1
    best_ratio = -1.0
    best_assignments: list[int] = []

    for k in range(k_range[0], k_range[1] + 1):
        if k > len(clean):
            break

        # For k clusters, try splitting along each axis and pick best
        # Split along the axis with more variance
        if f_var >= e_var:
            primary = n_formality
            secondary = n_expressive
        else:
            primary = n_expressive
            secondary = n_formality

        # Sort by primary axis, split into k groups
        indexed = sorted(range(len(clean)), key=lambda i: primary[i])
        chunk = len(indexed) // k
        if chunk == 0:
            continue

        assignments = [0] * len(clean)
        groups_primary: list[list[float]] = [[] for _ in range(k)]
        for g in range(k):
            start = g * chunk
            end = start + chunk if g < k - 1 else len(indexed)
            for idx in indexed[start:end]:
                assignments[idx] = g
                groups_primary[g].append(primary[idx])

        # Score: 2D between/within variance
        group_means_p = [sum(g) / len(g) for g in groups_primary if g]
        between_p = _variance(group_means_p)
        within_p = sum(_variance(g) for g in groups_primary if g) / k

        # Also measure secondary axis separation
        groups_secondary: list[list[float]] = [[] for _ in range(k)]
        for i, g in enumerate(assignments):
            groups_secondary[g].append(secondary[i])
        group_means_s = [sum(g) / len(g) for g in groups_secondary if g]
        between_s = _variance(group_means_s)
        within_s = sum(_variance(g) for g in groups_secondary if g) / k

        # Combined ratio
        total_between = between_p + between_s * 0.5  # weight secondary less
        total_within = within_p + within_s * 0.5
        if total_within > 1e-12:
            ratio = total_between / total_within
        else:
            ratio = total_between * 1e6 if total_between > 0 else 0.0

        biased_ratio = ratio / (k - 1) if k > 1 else ratio

        if biased_ratio > best_ratio:
            best_ratio = biased_ratio
            best_k = k
            best_assignments = list(assignments)

    if best_k <= 1 or best_ratio < 0.01:  # 0.01 = minimum separation to justify splitting
        cluster = Cluster(
            vectors=list(clean),
            message_ids=[v.message_id for v in clean],
            signature=_cluster_signature(clean),
        )
        return [cluster]

    # Build clusters from assignments
    cluster_vectors: dict[int, list[TextureVector]] = {i: [] for i in range(best_k)}
    for i, g in enumerate(best_assignments):
        cluster_vectors[g].append(clean[i])

    clusters: list[Cluster] = []
    for g in range(best_k):
        vecs = cluster_vectors[g]
        if not vecs:
            continue
        clusters.append(Cluster(
            vectors=vecs,
            message_ids=[v.message_id for v in vecs],
            signature=_cluster_signature(vecs),
        ))

    return clusters


# ---------------------------------------------------------------------------
# Style marker extraction — concrete rules from actual messages
# ---------------------------------------------------------------------------

# Laugh pattern detection — covers major languages/scripts.
# Easy to extend: add a new line with a pattern and a comment.
_RE_LAUGH = re.compile(
    r"\b(?:h[aeiou]){2,}h?\b|"                      # hahaha, hehehehe (English/general)
    r"\b(?:a[hH]){2,}\b|"                            # ahahah (English/general)
    r"\b(?:l+o+l+)+\b|"                              # lol, lolol (English)
    r"\bl+m+a+o+\b|"                                 # lmao (English)
    r"\bl+m+f+a+o+\b|"                               # lmfao (English)
    r"\bro+fl+\b|"                                   # rofl (English)
    r"\b(?:ja){2,}\b|"                               # jajaja (Spanish)
    r"\bk{3,}\b|"                                    # kkk+ (Korean/Brazilian)
    r"\bw{3,}\b|"                                    # www+ (Japanese)
    r"\b5{3,}\b|"                                    # 555+ (Thai)
    r"\bx+d+\b|"                                      # xD, xDD, xxDD (international)
    r"\u0445\u0430(?:\u0445\u0430)+|"                # хахаха (Russian)
    r"\u0433\u0433+|"                                # ггг (Russian, like lol)
    r"\bmdr+\b",                                     # mdr (French, mort de rire)
    re.IGNORECASE,
)

# Extended character detection (e.g., "doingggg", "noooo", "yesssss")
_RE_EXTENDED = re.compile(r"([a-zA-Z])\1{2,}")


def extract_style_markers(contents: dict[str, str]) -> list[str]:
    """Extract concrete style rules from actual message content.

    Analyzes the operator's outbound messages and produces a list of specific,
    actionable rules that a drafter must follow to replicate the voice authentically.

    These are NOT vague guidelines — they're hard constraints like:
    - "Never adds periods at end of messages"
    - "Uses repeated emojis (🥰🥰🥰 not 🥰)"
    - "Abbreviates: ur, rn, wanna"

    Args:
        contents: Dict of message_id -> message text (outbound only).

    Returns:
        List of concrete style rule strings.
    """
    if not contents:
        return []

    messages = list(contents.values())
    total = len(messages)
    markers: list[str] = []

    # --- Capitalization habits ---
    starts_lowercase = 0
    starts_uppercase = 0
    for msg in messages:
        stripped = msg.lstrip()
        if stripped and stripped[0].isalpha():
            if stripped[0].islower():
                starts_lowercase += 1
            else:
                starts_uppercase += 1
    if starts_lowercase + starts_uppercase > 10:
        lc_ratio = starts_lowercase / (starts_lowercase + starts_uppercase)
        if lc_ratio > 0.7:
            markers.append("Rarely capitalizes the first letter of messages — keep lowercase starts")
        elif lc_ratio < 0.2:
            markers.append("Usually capitalizes the first letter of messages")

    # --- Period usage ---
    ends_with_period = sum(1 for m in messages if m.rstrip().endswith("."))
    period_ratio = ends_with_period / total if total > 0 else 0
    if period_ratio < 0.15:
        markers.append("Almost never ends messages with periods — do NOT add periods")
    elif period_ratio > 0.7:
        markers.append("Usually ends messages with periods")

    # --- Exclamation / question marks ---
    ends_exclaim = sum(1 for m in messages if m.rstrip().endswith("!"))
    exclaim_ratio = ends_exclaim / total if total > 0 else 0
    if exclaim_ratio > 0.2:
        markers.append(f"Frequently uses exclamation marks (~{int(exclaim_ratio*100)}% of messages)")

    # --- Trailing spaces ---
    trailing_space = sum(1 for m in messages if m != m.rstrip() and m.endswith(" "))
    if trailing_space / total > 0.3:
        markers.append("Often has trailing spaces after messages — this is natural, don't trim")

    # --- Emoji patterns ---
    emoji_messages = []
    for msg in messages:
        emojis = _RE_EMOJI.findall(msg)
        if emojis:
            emoji_messages.append(emojis)

    if emoji_messages:
        emoji_ratio = len(emoji_messages) / total
        if emoji_ratio > 0.15:
            # Check for repeated emojis (🥰🥰🥰)
            repeated = 0
            for em_list in emoji_messages:
                if len(em_list) >= 2 and len(set(em_list)) < len(em_list):
                    repeated += 1
            if repeated / len(emoji_messages) > 0.3:
                markers.append("Repeats emojis when using them (e.g., 🥰🥰🥰 not just 🥰) — match this pattern")

            # Find most common emojis
            from collections import Counter
            all_emojis = [e for em_list in emoji_messages for e in em_list]
            top = Counter(all_emojis).most_common(5)
            top_str = " ".join(e for e, _ in top)
            markers.append(f"Most used emojis: {top_str} — stick to these, don't introduce new ones")
        elif emoji_ratio < 0.05:
            markers.append("Rarely uses emojis — do NOT add emojis unless the exemplars show them")

    # --- Laugh patterns ---
    laugh_examples: list[str] = []
    for msg in messages:
        laughs = _RE_LAUGH.findall(msg)
        laugh_examples.extend(laughs)
    if laugh_examples:
        from collections import Counter
        top_laughs = Counter(laugh_examples).most_common(3)
        laugh_str = ", ".join(f'"{l}"' for l, _ in top_laughs)
        markers.append(f"Laugh style: uses {laugh_str} — match these exact patterns, don't normalize to 'haha'")

    # --- Extended characters (doingggg, noooo) ---
    extended_examples: list[str] = []
    for msg in messages:
        for match in _RE_EXTENDED.finditer(msg):
            # Get the full extended word
            start = match.start()
            end = match.end()
            # Expand to word boundaries
            while start > 0 and msg[start-1].isalpha():
                start -= 1
            while end < len(msg) and msg[end].isalpha():
                end += 1
            word = msg[start:end]
            if len(word) > 2:
                extended_examples.append(word)
    if extended_examples:
        from collections import Counter
        top_ext = Counter(extended_examples).most_common(5)
        ext_str = ", ".join(f'"{w}"' for w, _ in top_ext)
        markers.append(f"Uses extended/stretched words like {ext_str} — preserve these, never correct them")

    # --- Abbreviation usage (language-agnostic) ---
    # Detect short words (2-4 chars) that appear >10 times and aren't in the
    # 500 most common English words. These are the operator's abbreviations
    # regardless of what language they speak.
    word_counts: Counter = Counter()
    for msg in messages:
        for w in re.findall(r"\b[a-zA-Z]+\b", msg.lower()):
            if 2 <= len(w) <= 4:
                word_counts[w] += 1
    found_abbrevs = [
        w for w, c in word_counts.most_common(30)
        if c > 10 and w not in _COMMON_ENGLISH_500  # >10 appearances, not a common word
    ]
    if found_abbrevs:
        abbrev_str = ", ".join(f'"{a}"' for a in found_abbrevs[:8])
        markers.append(f"Uses abbreviations: {abbrev_str} — use these naturally, don't expand to full words")

    # --- Message length pattern ---
    lengths = sorted(len(m) for m in messages)
    median_len = lengths[len(lengths) // 2] if lengths else 0
    short_count = sum(1 for l in lengths if l < 20)
    if short_count / total > 0.5:
        markers.append(f"Keeps messages short (median {median_len} chars) — don't write long messages")

    # --- Multi-message pattern (consecutive short messages vs one long one) ---
    very_short = sum(1 for m in messages if len(m) < 10)
    if very_short / total > 0.3:
        markers.append("Sends very short bursts (often <10 chars) — single words, reactions, fragments are normal")

    # --- No greetings pattern ---
    greeting_pattern = re.compile(r"^\s*(?:hi|hey|hello|good morning|good evening)\b", re.IGNORECASE)
    greeting_count = sum(1 for m in messages if greeting_pattern.match(m))
    if greeting_count / total < 0.05:
        markers.append("Rarely starts with greetings — jump straight into the message")
    elif greeting_count / total > 0.3:
        markers.append("Often starts with a greeting")

    # --- Anti-patterns: things the operator NEVER does ---
    # These are the strongest AI-tell detectors
    anti_patterns = []

    # Check for absence of "lol" (AI loves inserting it)
    lol_count = sum(1 for m in messages if re.search(r"\blol\b", m, re.IGNORECASE))
    if lol_count / total < 0.01 and total > 50:
        anti_patterns.append("'lol'")

    # Check for absence of exclamation marks
    exclaim_count = sum(1 for m in messages if "!" in m)
    if exclaim_count / total < 0.03 and total > 50:
        anti_patterns.append("exclamation marks")

    # Check for absence of "haha" (they might use "ahahah" instead)
    haha_count = sum(1 for m in messages if re.search(r"\bhaha\b", m, re.IGNORECASE))
    if haha_count / total < 0.01 and laugh_examples and total > 50:
        anti_patterns.append("'haha' (uses different laugh patterns)")

    # Check for absence of common AI phrases
    ai_phrases = ["sounds good", "no worries", "hope this helps", "let me know if",
                   "feel free to", "looking forward", "absolutely", "definitely"]
    for phrase in ai_phrases:
        count = sum(1 for m in messages if phrase.lower() in m.lower())
        if count / total < 0.005 and total > 100:
            anti_patterns.append(f"'{phrase}'")

    if anti_patterns:
        # Cap at 5 most important
        markers.append(
            "NEVER uses: " + ", ".join(anti_patterns[:5])
            + " — if you include any of these, the message will sound fake"
        )

    return markers


# ---------------------------------------------------------------------------
# Global trait extraction
# ---------------------------------------------------------------------------

def _detect_script(char: str) -> str:
    """Return the broad Unicode script name for a character.

    Maps Unicode category/block to a human-readable script name.
    Returns "latin" for basic ASCII letters, the script name for known
    non-Latin blocks, or "" for non-letter characters.
    """
    if char.isascii() and char.isalpha():
        return "latin"
    cp = ord(char)
    # Major script blocks — same order as _RE_NON_LATIN
    if 0x0600 <= cp <= 0x06FF:
        return "arabic"
    if 0x0900 <= cp <= 0x097F:
        return "devanagari"
    if 0x0400 <= cp <= 0x04FF:
        return "cyrillic"
    if 0x4E00 <= cp <= 0x9FFF:
        return "cjk"
    if 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF:
        return "japanese"
    if 0xAC00 <= cp <= 0xD7AF:
        return "hangul"
    if 0x0A00 <= cp <= 0x0A7F:
        return "gurmukhi"
    if 0x0980 <= cp <= 0x09FF:
        return "bengali"
    if 0x0B80 <= cp <= 0x0BFF:
        return "tamil"
    if 0x0C00 <= cp <= 0x0C7F:
        return "telugu"
    if 0x0E00 <= cp <= 0x0E7F:
        return "thai"
    if 0x1000 <= cp <= 0x109F:
        return "myanmar"
    if 0x0590 <= cp <= 0x05FF:
        return "hebrew"
    if 0x10A0 <= cp <= 0x10FF:
        return "georgian"
    # Extended Latin with diacritics is still "latin"
    if unicodedata.category(char).startswith("L"):
        return "latin"
    return ""


def extract_global_traits(
    vectors: list[TextureVector], contents: dict[str, str]
) -> dict:
    """Extract person-level communication traits from all outbound messages.

    Language-agnostic: detects scripts by Unicode block, not by hardcoded
    language wordlists. Reports primary/secondary scripts and mixing ratio.

    Args:
        vectors: All texture vectors for a person.
        contents: Mapping of message_id -> raw message content.

    Returns:
        Dict with keys: primary_script, secondary_scripts, mixing_ratio,
        uses_loanwords, signs_off, uses_periods, response_style.
        Also includes legacy keys (language_mix, romanized_ar) for backward
        compatibility with existing DB schema.
    """
    if not vectors:
        return {
            "primary_script": "latin",
            "secondary_scripts": [],
            "mixing_ratio": 0.0,
            "uses_loanwords": False,
            "signs_off": False,
            "uses_periods": False,
            "response_style": "multi_short",
            # Legacy keys for DB compat
            "language_mix": "en_only",
            "romanized_ar": False,
        }

    total = len(vectors)

    # --- Script detection (language-agnostic) ---
    # Count which scripts appear in each message
    script_msg_counts: Counter = Counter()  # script -> number of messages containing it
    for content in contents.values():
        msg_scripts: set[str] = set()
        for ch in content:
            s = _detect_script(ch)
            if s:
                msg_scripts.add(s)
        for s in msg_scripts:
            script_msg_counts[s] += 1

    # Determine primary and secondary scripts
    if not script_msg_counts:
        primary_script = "latin"
        secondary_scripts: list[str] = []
        mixing_ratio = 0.0
    else:
        ranked = script_msg_counts.most_common()
        primary_script = ranked[0][0]
        # Secondary scripts: any script appearing in >3% of messages
        secondary_scripts = [
            s for s, c in ranked[1:]
            if c / total > 0.03  # 3% threshold to filter noise
        ]
        # Mixing ratio: fraction of messages containing any non-primary script
        non_primary_msgs = sum(
            1 for content in contents.values()
            if any(
                _detect_script(ch) not in ("", primary_script)
                for ch in content
            )
        )
        mixing_ratio = non_primary_msgs / total if total > 0 else 0.0

    # --- uses_loanwords (language-agnostic) ---
    # Detect words with diacritics or non-standard English character patterns
    # mixed into primarily Latin text. Don't try to identify WHICH language.
    uses_loanwords = False
    if primary_script == "latin":
        # Check for Latin words with diacritics (e.g., cafe, naive, nino, uber)
        _re_diacritics = re.compile(r"\b\w*[^\x00-\x7F\s]\w*\b")
        diacritic_count = 0
        sample_size = min(len(contents), 500)  # Don't scan all messages for perf
        for i, content in enumerate(contents.values()):
            if i >= sample_size:
                break
            if _re_diacritics.search(content):
                diacritic_count += 1
        uses_loanwords = diacritic_count / max(sample_size, 1) > 0.05  # >5% of messages

    # --- signs_off (language-agnostic) ---
    # Instead of checking for hardcoded English phrases, detect if the last
    # line of messages tends to repeat. If >20% end with a common short phrase,
    # that's a sign-off pattern (works in any language).
    last_lines: Counter = Counter()
    for content in contents.values():
        lines = content.rstrip().split("\n")
        last_line = lines[-1].strip().lower()
        # Only count short last lines (likely sign-offs, not content)
        if 2 <= len(last_line) <= 40:  # 2-40 chars = plausible sign-off
            last_lines[last_line] += 1
    # Check if any single last-line phrase appears in >20% of messages
    signs_off = False
    if last_lines and total > 10:  # Need enough messages to detect pattern
        most_common_signoff_count = last_lines.most_common(1)[0][1]
        signs_off = (most_common_signoff_count / total) > 0.20

    # --- uses_periods (already language-agnostic) ---
    multi_word_count = 0
    period_end_count = 0
    for v in vectors:
        content = contents.get(v.message_id, "")
        if len(content.split()) >= 2:  # Only check multi-word messages
            multi_word_count += 1
            if content.rstrip().endswith("."):
                period_end_count += 1
    uses_periods = (
        (period_end_count / multi_word_count) > 0.50  # >50% = habitual period user
        if multi_word_count > 0
        else False
    )

    # --- response_style (already language-agnostic) ---
    lengths = sorted(v.char_length for v in vectors)
    median_idx = len(lengths) // 2
    median_length = lengths[median_idx] if lengths else 0
    response_style = "multi_short" if median_length < 40 else "single_long"  # 40 chars ~= 1 sentence

    # --- Legacy backward-compat keys for DB schema ---
    non_latin_frac = sum(1 for v in vectors if v.has_non_latin) / total
    if non_latin_frac < 0.05:
        language_mix = "en_only"
    elif non_latin_frac > 0.60:
        language_mix = "ar_primary"
    else:
        language_mix = "en_ar_mixed"

    return {
        "primary_script": primary_script,
        "secondary_scripts": secondary_scripts,
        "mixing_ratio": round(mixing_ratio, 3),
        "uses_loanwords": uses_loanwords,
        "signs_off": signs_off,
        "uses_periods": uses_periods,
        "response_style": response_style,
        # Legacy keys — kept for DB backward compat (style_profiles table columns)
        "language_mix": language_mix,
        "romanized_ar": uses_loanwords,  # best approximation for legacy column
    }


# ---------------------------------------------------------------------------
# Shared helpers — used by both cron and on-demand compute
# ---------------------------------------------------------------------------


def select_representative_exemplars(
    vectors: list[TextureVector], n: int = 5,
) -> list[str]:
    """Select the most representative message IDs from a list of vectors.

    Picks messages closest to the median texture, avoiding near-duplicates
    in length. Used by both the LLM labeler and on-demand profile compute.

    Args:
        vectors: List of TextureVector to select from.
        n: Number of exemplars to return.

    Returns:
        List of message ID strings.
    """
    if not vectors:
        return []
    if len(vectors) <= n:
        return [v.message_id for v in vectors]

    lengths = sorted(v.char_length for v in vectors)
    median_len = lengths[len(lengths) // 2]

    # Score by distance from median length (lower = more representative)
    scored = sorted(vectors, key=lambda v: abs(v.char_length - median_len) / max(median_len, 1))

    selected: list[TextureVector] = []
    seen_buckets: dict[int, int] = {}  # length bucket -> count in selection
    for v in scored:
        bucket = round(v.char_length / 10) * 10  # 10-char buckets to avoid near-dupes
        if seen_buckets.get(bucket, 0) < 2:  # Max 2 per bucket
            selected.append(v)
            seen_buckets[bucket] = seen_buckets.get(bucket, 0) + 1
        if len(selected) >= n:
            break

    # Fill from remaining if bucket constraint was too strict
    if len(selected) < n:
        remaining = [v for v in scored if v not in selected]
        selected.extend(remaining[:n - len(selected)])

    return [v.message_id for v in selected[:n]]


def compute_stage_1(
    person_id: str, comms_conn: sqlite3.Connection
) -> tuple | None:
    """Run Stage 1: extract, filter, cluster, compute traits and markers.

    This is the shared computation used by both the nightly cron and the
    on-demand ensure_profile() path. Factored out to avoid duplication.

    Args:
        person_id: Person ID in comms.db.
        comms_conn: Open connection to comms.db.

    Returns:
        Tuple of (vectors, clusters, content_map, global_traits, style_markers)
        or None if insufficient data.
    """
    vectors = extract_outbound(person_id, comms_conn)
    if len(vectors) < MIN_OUTBOUND:
        return None

    clusters = cluster_messages(vectors)
    if not clusters:
        return None

    # Build content map for global traits and style marker extraction
    content_map: dict[str, str] = {}
    msg_ids = [v.message_id for v in vectors]
    for i in range(0, len(msg_ids), 500):  # Batch in chunks of 500 to avoid SQLite limits
        chunk = msg_ids[i:i + 500]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows = comms_conn.execute(
            f"SELECT id, content FROM messages WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        for r in rows:
            mid = r[0] if isinstance(r, tuple) else r["id"]
            content = r[1] if isinstance(r, tuple) else r["content"]
            if content:
                content_map[mid] = content

    global_traits = extract_global_traits(vectors, content_map)
    style_markers = extract_style_markers(content_map)

    return vectors, clusters, content_map, global_traits, style_markers


# ---------------------------------------------------------------------------
# Eligible persons
# ---------------------------------------------------------------------------

def get_eligible_persons(
    people_conn: sqlite3.Connection,
    comms_conn: sqlite3.Connection,
    tier_filter: tuple[int, ...] = (1, 2, 3),
) -> list[tuple[str, int]]:
    """Find persons eligible for style profiling.

    Queries people.db for persons with importance in tier_filter, then counts
    outbound messages per person in comms.db. Returns those with >= MIN_OUTBOUND
    outbound messages, ordered by importance ASC then count DESC.

    Args:
        people_conn: Open connection to people.db.
        comms_conn: Open connection to comms.db.
        tier_filter: Tuple of importance tiers to include.

    Returns:
        List of (person_id, outbound_count) tuples.
    """
    try:
        placeholders = ",".join("?" for _ in tier_filter)
        rows = people_conn.execute(
            f"SELECT id, importance FROM people WHERE importance IN ({placeholders})",
            tier_filter,
        ).fetchall()
    except sqlite3.Error as e:
        log.error("Failed to query people.db for eligible persons: %s", e)
        return []

    results: list[tuple[str, int, int]] = []
    for person_id, importance in rows:
        try:
            count_row = comms_conn.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE person_id = ?
                  AND direction = 'outbound'
                  AND content IS NOT NULL
                  AND length(content) > 0
                """,
                (person_id,),
            ).fetchone()
            count = count_row[0] if count_row else 0
        except sqlite3.Error:
            count = 0

        if count >= MIN_OUTBOUND:
            results.append((person_id, count, importance))

    # Sort by importance ASC, then outbound count DESC
    results.sort(key=lambda x: (x[2], -x[1]))
    return [(pid, cnt) for pid, cnt, _ in results]
