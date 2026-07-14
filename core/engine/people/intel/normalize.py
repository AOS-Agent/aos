"""Canonical name normalization for the People Intelligence orchestrator.

People.db's ``canonical_name`` column is the authoritative identifier for
a person, but in practice it's a grab-bag of formats:

* Spaced full names: ``"Alex Kumar"``
* Concatenated camelCase: ``"FabricatedExample"`` → should match "Fabricated Example"
* Compound records with ``/`` separator: ``"NameOne/NameTwo"`` → two people in one row
* Honorific prefixes: ``"Dr. Example"`` or ``"TitledExample"`` where "Titled" is a title
* Relational suffixes: ``"ExampleUncle"`` / ``"ExampleFather"``
* Embedded location/org descriptors: ``"ExampleCityName"``
* Trailing event tags + years: ``"ExampleTrip2025"``
* Non-Latin scripts: concatenated Arabic strings without spaces

For signal adapters to match these against real message handles, photo face
tags, vault log text, etc., we need a normalization pass that:

1. Splits compound records on ``/``
2. Detects non-Latin scripts and passes them through
3. Splits camelCase at lowercase→uppercase and digit boundaries
4. Strips leading titles and trailing relation suffixes
5. Strips common embedded location/org/event noise
6. Generates a deduplicated list of matching variants
7. Rejects noise variants shorter than 3 characters

The normalizer is a pure function: same input → same output, no I/O.
Feeds ``SignalExtractor.build_person_index()`` in ``extractor.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Lexicons ─────────────────────────────────────────────────────────

# Leading honorifics / titles — stripped if they appear as the first word.
# Lowercased; match is case-insensitive and punctuation-tolerant.
_TITLES: frozenset[str] = frozenset({
    "dr", "dr.", "doctor",
    "mr", "mr.", "mrs", "mrs.", "ms", "ms.",
    "sir", "sh", "sh.",
    "qari", "hafiz", "shaykh", "sheikh", "imam", "mufti",
    "maulana", "moulana", "mawlana",
    "ustadh", "ustaz", "ustad", "ustaad",
    "prof", "prof.", "professor",
    "engr", "engr.",
})

# Trailing relational suffixes — stripped if they appear as the last word.
# Covers English kinship + common romanized Urdu/Arabic/Hindi kinship terms.
_RELATION_SUFFIXES: frozenset[str] = frozenset({
    "uncle", "aunty", "auntie", "aunt",
    "father", "mother", "dad", "mom",
    "brother", "sister", "bro", "sis",
    "grandfather", "grandmother", "grandpa", "grandma",
    "bhai", "baji", "baaji", "apa", "aapa",
    "mamu", "mamoo", "khala", "khalu",
    "phuppo", "phupho", "phupha",
    "chacha", "chachi", "chachu",
    "dada", "dadi", "nana", "nani",
    "cousin",
})

# Noise words that are embedded descriptors — cities, organizations, event tags.
# Intentionally small and generic. If a variant contains one of these, we strip
# the word; we do NOT strip based on curated real place names (that would leak
# operator-specific geography into the code).
_NOISE_WORDS: frozenset[str] = frozenset({
    "trip", "umrah", "hajj", "visit",
    "work", "office", "home",
})


# ── Data class ───────────────────────────────────────────────────────

@dataclass
class NormalizedName:
    """Result of normalizing a canonical_name."""
    raw: str                                  # original input, trimmed
    variants: list[str] = field(default_factory=list)  # lowercased match candidates
    primary: str = ""                         # best-guess display form
    is_non_latin: bool = False                # True for Arabic, CJK, etc.


# ── Regex patterns (compiled once) ───────────────────────────────────

# Insert a space between a lowercase→uppercase boundary, a letter→digit
# boundary, or a digit→letter boundary. Handles acronyms with a look for
# an upper followed by an upper+lower: "XMLParser" → "XML Parser".
_CAMEL_SPLIT = re.compile(
    r"(?<=[a-z])(?=[A-Z])"              # foo|Bar
    r"|(?<=[A-Z])(?=[A-Z][a-z])"        # XML|Parser
    r"|(?<=[a-zA-Z])(?=\d)"             # abc|123
    r"|(?<=\d)(?=[a-zA-Z])"             # 123|abc
)

# Latin letter range — anything outside this and outside digits/whitespace
# is treated as "non-Latin" for pass-through behavior.
_LATIN_CHAR = re.compile(r"[A-Za-z]")
_NON_LATIN_BLOCK = re.compile(
    r"[\u0590-\u05FF"       # Hebrew
    r"\u0600-\u06FF"        # Arabic
    r"\u0700-\u074F"        # Syriac
    r"\u0750-\u077F"        # Arabic Supplement
    r"\u08A0-\u08FF"        # Arabic Extended-A
    r"\u0900-\u097F"        # Devanagari
    r"\u0980-\u09FF"        # Bengali
    r"\u4E00-\u9FFF"        # CJK Unified Ideographs
    r"\u3040-\u309F"        # Hiragana
    r"\u30A0-\u30FF"        # Katakana
    r"\uAC00-\uD7AF"        # Hangul
    r"]"
)

# Trailing year-like digits: space + 19xx/20xx at end.
_TRAILING_YEAR = re.compile(r"\s*(?:19|20)\d{2}\s*$")

# Leading year-like digits (less common but occurs in logs).
_LEADING_YEAR = re.compile(r"^\s*(?:19|20)\d{2}\s+")

# Standalone digits as words.
_STANDALONE_DIGITS = re.compile(r"\b\d+\b")


# ── Helpers ──────────────────────────────────────────────────────────

def _is_non_latin(text: str) -> bool:
    """True if the string contains non-Latin script characters."""
    return bool(_NON_LATIN_BLOCK.search(text))


def _split_camel(text: str) -> str:
    """Insert spaces at camelCase / digit boundaries."""
    return _CAMEL_SPLIT.sub(" ", text)


def _strip_title(tokens: list[str]) -> list[str]:
    """Remove leading title token(s) if present."""
    while tokens and tokens[0].lower().rstrip(".") in _TITLES:
        tokens = tokens[1:]
    return tokens


def _strip_relation_suffix(tokens: list[str]) -> list[str]:
    """Remove trailing relational suffix token(s) if present.

    Also handles cases where a relational suffix is fused onto the last
    token (e.g., ``"NameUncle"`` → ``["Name", "Uncle"]`` after camel-split →
    drop ``"Uncle"``).
    """
    while tokens and tokens[-1].lower() in _RELATION_SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def _strip_noise_words(tokens: list[str]) -> list[str]:
    """Drop tokens that match the noise word list (event tags, generic words)."""
    return [t for t in tokens if t.lower() not in _NOISE_WORDS]


def _strip_years(text: str) -> str:
    """Remove leading and trailing year-like digits."""
    text = _LEADING_YEAR.sub("", text)
    text = _TRAILING_YEAR.sub("", text)
    return text.strip()


def _tokenize(text: str) -> list[str]:
    """Split on whitespace, drop empty tokens."""
    return [t for t in text.split() if t]


def _title_case(text: str) -> str:
    """Title-case a spaced name while preserving existing all-caps acronyms."""
    parts = text.split()
    out = []
    for p in parts:
        if p.isupper() and len(p) > 1:
            out.append(p)  # keep "XML" as "XML"
        else:
            out.append(p[:1].upper() + p[1:].lower() if p else p)
    return " ".join(out)


def _normalize_one(raw: str) -> list[str]:
    """Normalize a single name fragment (already split on '/').

    Returns a list of lowercased variants (may be empty if fragment reduces
    to noise).
    """
    text = raw.strip()
    if not text:
        return []

    # Non-Latin script: pass through without camel-split / title-strip.
    # Strip trailing/leading years if present (those are ASCII).
    if _is_non_latin(text):
        text = _strip_years(text)
        return [text.strip().lower()] if text.strip() else []

    # Latin path.
    text = _strip_years(text)

    # Drop standalone digit sequences inside the name (e.g., "Ahmed 3 Foo" →
    # after digit split: keep "Ahmed Foo").
    camel_split = _split_camel(text)
    tokens = _tokenize(camel_split)
    tokens = [t for t in tokens if not t.isdigit()]

    tokens = _strip_title(tokens)
    tokens = _strip_relation_suffix(tokens)
    tokens = _strip_noise_words(tokens)

    if not tokens:
        return []

    variants: list[str] = []

    # Full spaced form (primary variant).
    full_spaced = " ".join(tokens).lower()
    if len(full_spaced) >= 3:
        variants.append(full_spaced)

    # Concatenated form (fallback — matches original when it's already
    # camelCase in source text).
    concat = "".join(tokens).lower()
    if concat != full_spaced and len(concat) >= 3:
        variants.append(concat)

    # First token alone (for single-name matches) if it's distinctive enough.
    if tokens[0].lower() != full_spaced and len(tokens[0]) >= 4:
        variants.append(tokens[0].lower())

    # Two-word first+last if we have 3+ tokens (collapses middle names).
    if len(tokens) >= 3:
        first_last = f"{tokens[0]} {tokens[-1]}".lower()
        if first_last not in variants and len(first_last) >= 3:
            variants.append(first_last)

    return variants


# ── Public API ───────────────────────────────────────────────────────

def component_variants(spaced: str, min_length: int = 4) -> list[str]:
    """Return individual lowercased token variants from a spaced name.

    For matching first-name-only or component-only mentions in long
    narrative text. Only tokens >= min_length are returned to reduce
    false positives. Deduplicated, order preserved.
    """
    if not spaced:
        return []
    tokens = [t.lower() for t in spaced.split() if len(t) >= min_length]
    return list(dict.fromkeys(tokens))


def normalize_canonical_name(raw: str | None) -> NormalizedName:
    """Normalize a people.db canonical_name into matching variants.

    See module docstring for the full rule list. Pure function.
    """
    if not raw:
        return NormalizedName(raw="", variants=[], primary="")

    trimmed = raw.strip()
    if not trimmed:
        return NormalizedName(raw="", variants=[], primary="")

    non_latin = _is_non_latin(trimmed)

    # Split on '/' — treat each half as an independent fragment.
    fragments = [f.strip() for f in trimmed.split("/") if f.strip()]
    if not fragments:
        fragments = [trimmed]

    # Normalize each fragment, collect variants.
    seen: set[str] = set()
    all_variants: list[str] = []
    primary = ""

    for frag in fragments:
        fv = _normalize_one(frag)
        if fv and not primary:
            # Primary is the first non-empty variant of the first fragment,
            # title-cased for display.
            primary = _title_case(fv[0])
        for v in fv:
            if v not in seen:
                seen.add(v)
                all_variants.append(v)

    # If primary wasn't set (all fragments were noise or non-Latin), fall back
    # to the trimmed raw input.
    if not primary:
        primary = trimmed

    return NormalizedName(
        raw=trimmed,
        variants=all_variants,
        primary=primary,
        is_non_latin=non_latin,
    )
