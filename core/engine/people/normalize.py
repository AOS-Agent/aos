"""Contact normalization for identity resolution.

Provides deterministic normalization for phones, emails, and names.
These are the building blocks for both deterministic and fuzzy matching.

All functions are pure — no database access, no side effects.
"""

from __future__ import annotations

import re
import unicodedata

import phonenumbers

# ── Phonetic groups for Arabic name transliterations ────────────────────
# Canonical form -> list of known transliteration variants.
# Mirrors the dict in core/engine/comms/resolver.py (copied here to avoid
# import dependency on the comms module).

PHONETIC_GROUPS: dict[str, list[str]] = {
    "muhammad": ["muhammad", "mohammed", "mohammad", "muhammed", "mohamed", "mohamad"],
    "hamza": ["hamza", "hamzah", "hamzeh", "humza"],
    "ahmad": ["ahmad", "ahmed", "ahmet"],
    "omar": ["omar", "omer", "umar", "umair"],
    "ayesha": ["ayesha", "aisha", "aysha", "aaisha", "aishah"],
    "fatima": ["fatima", "fatimah", "faatima"],
    "yusuf": ["yusuf", "yousuf", "yousef", "yosef", "youssef", "joseph"],
    "ibrahim": ["ibrahim", "ebrahim", "ibraheem"],
    "zain": ["zain", "zayn", "zein"],
    "hassan": ["hassan", "hasan", "hasen"],
    "hussain": ["hussain", "husain", "husein", "hussein", "hossein"],
    "tariq": ["tariq", "tarek", "tareq", "tarik"],
    "bilal": ["bilal", "bilaal", "belal"],
    "usama": ["usama", "osama", "usamah"],
    "imran": ["imran", "emran", "imraan"],
    "asma": ["asma", "asmaa", "asma'"],
    "maryam": ["maryam", "mariam", "maryum", "miriam"],
    "sana": ["sana", "sanaa", "thana"],
    "talha": ["talha", "talhah", "talhat"],
    "adnan": ["adnan", "adnaan"],
    "qasim": ["qasim", "kasim", "qassim", "kasem"],
    "sohail": ["sohail", "suhail", "soheil", "suhayl"],
    "abdullah": ["abdullah", "abdallah", "abdulla"],
    "ali": ["ali", "aly"],
    "faisal": ["faisal", "faysal", "feisal"],
    "idris": ["idris", "idrees", "idriss", "edris"],
    "shareef": ["shareef", "sharif", "sherif", "shreef"],
    "zeeshan": ["zeeshan", "zishan", "zeshan"],
    "hisham": ["hisham", "hesham", "hicham"],
    "khalid": ["khalid", "khaled"],
    "rashid": ["rashid", "rasheed", "rashed"],
    "nasir": ["nasir", "nasser", "naseer", "nasr"],
    "samir": ["samir", "sameer"],
    "nadia": ["nadia", "nadya", "naadya"],
}

# Reverse lookup: variant spelling -> canonical form
_PHONETIC_REVERSE: dict[str, str] = {}
for _canon, _variants in PHONETIC_GROUPS.items():
    for _v in _variants:
        _PHONETIC_REVERSE[_v] = _canon


# ── Title prefixes to strip from names ──────────────────────────────────

_TITLE_PATTERNS = re.compile(
    r"^(?:"
    r"mr\.?|mrs\.?|ms\.?|miss\.?|dr\.?|prof\.?|eng\.?|engr\.?"
    r"|sheikh|shaikh|shaykh|ustadh|ustaz|maulana|mufti"
    r"|haji|hajj|sayyid|syed|sir|lady|dame"
    r"|abu|umm?|ibn|bin|bint"
    r")\s+",
    re.IGNORECASE,
)

# Emoji pattern: Unicode emoji ranges
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002600-\U000027BF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002702-\U000027B0"  # dingbats
    "\U0000200D"             # zero width joiner
    "\U000020E3"             # combining enclosing keycap
    "]+",
    flags=re.UNICODE,
)

# Gmail domains that support dot-stripping and plus-alias removal
_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


# ── Public API ──────────────────────────────────────────────────────────


def normalize_phone(raw: str, default_region: str = "AE") -> str:
    """Normalize a phone number to E.164 format.

    Uses the ``phonenumbers`` library for parsing, falling back to simple
    digit extraction for inputs the library cannot parse.

    Args:
        raw: Raw phone string in any common format.
        default_region: ISO 3166-1 alpha-2 code used when the number has no
            country prefix.  Defaults to "AE" (United Arab Emirates).

    Returns:
        E.164 phone string (e.g. ``"+971501234567"``).

    Examples:
        >>> normalize_phone("+971-50-123-4567")
        '+971501234567'
        >>> normalize_phone("050 123 4567")
        '+971501234567'
        >>> normalize_phone("00971501234567")
        '+971501234567'
    """
    cleaned = raw.strip()
    if not cleaned:
        return ""

    # Replace leading "00" international prefix with "+"
    if cleaned.startswith("00") and len(cleaned) > 4:
        cleaned = "+" + cleaned[2:]

    try:
        parsed = phonenumbers.parse(cleaned, default_region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
    except phonenumbers.NumberParseException:
        pass

    # Fallback: strip non-digits, prepend "+" if long enough for an
    # international number (> 10 digits typically means country code present).
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return ""
    if len(digits) > 10:
        return f"+{digits}"
    # Short number — likely local.  Re-try the library with digits only.
    try:
        parsed = phonenumbers.parse(digits, default_region)
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
    except phonenumbers.NumberParseException:
        return f"+{digits}" if len(digits) > 6 else digits


def normalize_email(raw: str) -> str:
    """Normalize an email address.

    * Lowercases the whole address.
    * Strips dots from the local part for Gmail/Googlemail (they are ignored
      by Google's mail servers).
    * Removes ``+tag`` suffixes for Gmail (``user+tag@gmail.com`` becomes
      ``user@gmail.com``).
    * Trims leading/trailing whitespace.

    Args:
        raw: Raw email string.

    Returns:
        Normalized email string.

    Examples:
        >>> normalize_email("  A.B.C@Gmail.com  ")
        'abc@gmail.com'
        >>> normalize_email("user+promo@gmail.com")
        'user@gmail.com'
        >>> normalize_email("John@Company.COM")
        'john@company.com'
    """
    cleaned = raw.strip().lower()
    if "@" not in cleaned:
        return cleaned

    local, domain = cleaned.rsplit("@", 1)

    if domain in _GMAIL_DOMAINS:
        # Remove dots from local part (Gmail ignores them)
        local = local.replace(".", "")
        # Remove +tag suffix
        if "+" in local:
            local = local.split("+", 1)[0]

    return f"{local}@{domain}"


def normalize_name(raw: str) -> tuple[str, str, str]:
    """Normalize a person's name.

    Processing steps:
      1. Strip emoji characters.
      2. Unicode NFC normalization.
      3. Strip honorific/title prefixes (Mr., Dr., Sheikh, Abu, etc.).
      4. Collapse whitespace.
      5. Split into first / last.
      6. Title-case each part.

    Args:
        raw: Raw name string, potentially with emoji, titles, or irregular
            whitespace.

    Returns:
        A 3-tuple ``(canonical, first, last)`` where *canonical* is
        ``"First Last"`` and *first* / *last* are the individual parts.
        If the name is a single word, *last* will be an empty string.

    Examples:
        >>> normalize_name("  dr.  Muhammad   TARIQ  ")
        ('Muhammad Tariq', 'Muhammad', 'Tariq')
        >>> normalize_name("🇵🇰 Ahmed")
        ('Ahmed', 'Ahmed', '')
    """
    if not raw or not raw.strip():
        return ("", "", "")

    # Strip emoji
    name = _EMOJI_RE.sub("", raw)

    # Unicode NFC normalization
    name = unicodedata.normalize("NFC", name)

    # Collapse whitespace
    name = " ".join(name.split())

    # Strip title prefixes (may need multiple passes for compound titles
    # like "Dr. Sheikh")
    for _ in range(3):
        stripped = _TITLE_PATTERNS.sub("", name).strip()
        if stripped == name:
            break
        name = stripped

    # Final whitespace collapse after title stripping
    name = " ".join(name.split()).strip()

    if not name:
        return ("", "", "")

    # Split into first / last
    parts = name.split(None, 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""

    # Title-case each part (preserve already-cased Arabic or mixed scripts)
    def _smart_title(s: str) -> str:
        """Title-case only if the string is all-ASCII; leave mixed scripts."""
        try:
            s.encode("ascii")
            return s.title()
        except UnicodeEncodeError:
            # Contains non-ASCII (Arabic, Urdu, etc.) — leave as-is
            return s

    first = _smart_title(first)
    last = _smart_title(last)
    canonical = f"{first} {last}".strip()

    return (canonical, first, last)


def phonetic_key(name: str) -> str:
    """Return a phonetic blocking key for a name.

    Each word in the name is looked up in :data:`_PHONETIC_REVERSE`.  If the
    word is a known variant of an Arabic name, it is replaced with its
    canonical form.  Otherwise the lowered word is kept as-is.

    The resulting tokens are joined by a single space.  This key is used as
    a *blocking key* in the fuzzy matching step — only records that share
    the same phonetic key are compared pair-wise.

    Args:
        name: A person's name (one or more words).

    Returns:
        Space-joined canonical phonetic form.

    Examples:
        >>> phonetic_key("Mohammed Tarek")
        'muhammad tariq'
        >>> phonetic_key("John Smith")
        'john smith'
    """
    words = name.lower().split()
    return " ".join(_PHONETIC_REVERSE.get(w, w) for w in words)


def double_metaphone_key(name: str) -> str:
    """Return a Double Metaphone blocking key for a name.

    Uses the ``metaphone`` library to produce phonetic codes that handle
    Western names, non-Arabic transliterations, and spelling variations
    (e.g., "Catherine" vs "Katherine", "Steven" vs "Stephen").

    Complements :func:`phonetic_key` which handles Arabic-specific variants.

    Returns the primary metaphone code for each word, joined by space.
    Falls back to the lowered word if metaphone produces empty codes.
    """
    try:
        from metaphone import doublemetaphone
    except ImportError:
        # Graceful degradation — return phonetic_key if metaphone not installed
        return phonetic_key(name)

    words = name.lower().split()
    codes = []
    for w in words:
        primary, _secondary = doublemetaphone(w)
        codes.append(primary if primary else w)
    return " ".join(codes)
