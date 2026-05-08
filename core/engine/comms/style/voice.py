"""
Operator Voice Profile — deep analysis of how the operator communicates.

This is the PRIMARY source of truth for message drafting. It analyzes the
operator's outbound messages across ALL channels and ALL people to build
a comprehensive voice profile that captures:

- Vocabulary DNA (words they reach for, words they never use)
- Sentence construction patterns
- Emotional register (how they express agreement, excitement, frustration, etc.)
- Punctuation and formatting habits
- Language mixing patterns (any language/script, detected universally)
- Phrase inventory (actual phrases they use, not paraphrases)

Language-agnostic: uses Unicode script detection instead of hardcoded
language wordlists. Works for any operator in any language.

The profile is stored as a structured document, not statistical markers.
It's designed to be included in drafting prompts so Claude can internalize
the voice, not just follow rules.

The per-relationship style profiles (profiles.py) layer on TOP of this
as secondary adjustments (mode, exemplars, enhancement level).
"""

import json
import logging
import re
import sqlite3
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
VOICE_PROFILE_PATH = Path.home() / ".aos" / "data" / "operator-voice.json"

# Minimum messages needed to generate a voice profile
MIN_MESSAGES = 500

# Sample size for LLM analysis (too many tokens is wasteful)
SAMPLE_SIZE = 200


@dataclass
class VoiceProfile:
    """The operator's communication voice — ground truth for all drafting."""

    generated_at: int = 0
    message_count: int = 0
    channel_breakdown: dict[str, int] = field(default_factory=dict)

    # ── Structural DNA ──────────────────────────────────
    avg_message_length: float = 0
    median_message_length: float = 0
    ends_no_punctuation_pct: float = 0  # % of messages ending with letter
    ends_period_pct: float = 0
    ends_question_pct: float = 0
    ends_emoji_pct: float = 0
    trailing_space_pct: float = 0
    starts_capitalized_pct: float = 0

    # ── Vocabulary DNA ──────────────────────────────────
    top_sentence_starters: list[str] = field(default_factory=list)  # ["and", "but", "okay", ...]
    filler_words: list[str] = field(default_factory=list)  # ["bro", "ngl", "literally", ...]
    connector_words: list[str] = field(default_factory=list)  # ["cuz", "like", "so", ...]
    abbreviations_used: list[str] = field(default_factory=list)  # ["rn", "wdyt", "ngl", ...]

    # ── Phrase Inventory ────────────────────────────────
    # These are ACTUAL phrases the operator uses — the drafting prompt
    # should use these, not synonyms.
    agreement_phrases: list[str] = field(default_factory=list)
    disagreement_phrases: list[str] = field(default_factory=list)
    laugh_patterns: list[str] = field(default_factory=list)
    greeting_phrases: list[str] = field(default_factory=list)
    apology_phrases: list[str] = field(default_factory=list)
    affection_phrases: list[str] = field(default_factory=list)
    excitement_phrases: list[str] = field(default_factory=list)
    plan_making_phrases: list[str] = field(default_factory=list)

    # ── Language & Script Mixing ──────────────────────────
    primary_script: str = "latin"              # detected from messages (e.g., "latin", "arabic", "cyrillic")
    secondary_scripts: list[str] = field(default_factory=list)  # other scripts found
    script_mixing_ratio: float = 0.0          # fraction of messages with secondary scripts
    uses_loanwords: bool = False              # mixes in words from other languages
    cultural_expressions: list[str] = field(default_factory=list)  # LLM-discovered, any culture
    secondary_language_expressions: list[str] = field(default_factory=list)  # LLM-discovered

    # ── Anti-patterns ───────────────────────────────────
    never_uses: list[str] = field(default_factory=list)  # phrases/words the operator NEVER uses

    # ── LLM-generated voice summary ─────────────────────
    voice_summary: str = ""  # rich paragraph from Sonnet analyzing the voice

    def to_prompt_block(self) -> str:
        """Render as a prompt block for the drafter.

        This is the PRIMARY voice context — goes before any per-relationship
        context in the drafting prompt.
        """
        lines = []
        lines.append("## OPERATOR VOICE PROFILE (primary source of truth)")
        lines.append("")

        if self.voice_summary:
            lines.append(self.voice_summary)
            lines.append("")

        lines.append("### Hard Rules")
        # Punctuation
        if self.ends_no_punctuation_pct > 80:
            lines.append(f"- Messages end WITHOUT punctuation {self.ends_no_punctuation_pct:.0f}% of the time — do NOT add periods")
        if self.ends_period_pct < 5:
            lines.append(f"- Periods are used in only {self.ends_period_pct:.1f}% of messages — almost never end with a period")
        if self.trailing_space_pct > 25:
            lines.append(f"- {self.trailing_space_pct:.0f}% of messages have a trailing space — this is natural")
        if self.starts_capitalized_pct > 75:
            lines.append("- Usually capitalizes the first letter")
        elif self.starts_capitalized_pct < 30:
            lines.append("- Rarely capitalizes the first letter")

        # Length
        lines.append(f"- Average message length: {self.avg_message_length:.0f} chars (median: {self.median_message_length:.0f})")
        if self.median_message_length < 30:
            lines.append("- Writes SHORT messages — do not write paragraphs when a sentence will do")

        lines.append("")

        # Vocabulary
        if self.filler_words:
            lines.append(f"### Words They Reach For")
            lines.append(f"- Fillers/tics: {', '.join(self.filler_words[:10])}")
        if self.connector_words:
            lines.append(f"- Connectors: {', '.join(self.connector_words[:8])}")
        if self.abbreviations_used:
            lines.append(f"- Abbreviations: {', '.join(self.abbreviations_used[:10])}")
        if self.top_sentence_starters:
            lines.append(f"- Starts sentences with: {', '.join(self.top_sentence_starters[:8])}")
        lines.append("")

        # Phrase inventory
        lines.append("### Phrase Inventory (use THESE phrases, not synonyms)")
        if self.agreement_phrases:
            lines.append(f"- Agreement: {' / '.join(self.agreement_phrases[:6])}")
        if self.disagreement_phrases:
            lines.append(f"- Disagreement: {' / '.join(self.disagreement_phrases[:6])}")
        if self.laugh_patterns:
            lines.append(f"- Laughter: {' / '.join(self.laugh_patterns[:5])}")
        if self.apology_phrases:
            lines.append(f"- Apologies: {' / '.join(self.apology_phrases[:5])}")
        if self.excitement_phrases:
            lines.append(f"- Excitement: {' / '.join(self.excitement_phrases[:5])}")
        if self.greeting_phrases:
            lines.append(f"- Greetings: {' / '.join(self.greeting_phrases[:5])}")
        if self.affection_phrases:
            lines.append(f"- Affection: {' / '.join(self.affection_phrases[:5])}")
        lines.append("")

        # Language & cultural expressions
        if self.cultural_expressions or self.secondary_language_expressions:
            lines.append("### Language & Cultural Expressions")
            if self.cultural_expressions:
                lines.append(f"- Cultural: {' / '.join(self.cultural_expressions[:6])}")
            if self.secondary_language_expressions:
                lines.append(f"- Secondary language: {' / '.join(self.secondary_language_expressions[:6])}")
            lines.append("")

        # Anti-patterns
        if self.never_uses:
            lines.append("### NEVER Uses (instant AI tell if you include these)")
            lines.append(f"- {', '.join(self.never_uses[:8])}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def _collect_outbound(comms_conn: sqlite3.Connection) -> list[dict]:
    """Collect all outbound messages across all channels."""
    rows = comms_conn.execute("""
        SELECT channel, content FROM messages
        WHERE direction = 'outbound'
          AND content IS NOT NULL
          AND length(content) BETWEEN 3 AND 500
        ORDER BY timestamp DESC
    """).fetchall()
    return [{"channel": r[0], "content": r[1]} for r in rows]


def _compute_structural_dna(messages: list[str]) -> dict:
    """Compute structural patterns from all messages."""
    total = len(messages)
    if total == 0:
        return {}

    lengths = sorted(len(m) for m in messages)
    return {
        "avg_message_length": sum(lengths) / total,
        "median_message_length": lengths[total // 2],
        "ends_no_punctuation_pct": sum(1 for m in messages if m.rstrip() and m.rstrip()[-1].isalpha()) / total * 100,
        "ends_period_pct": sum(1 for m in messages if m.rstrip().endswith(".")) / total * 100,
        "ends_question_pct": sum(1 for m in messages if m.rstrip().endswith("?")) / total * 100,
        "ends_emoji_pct": sum(1 for m in messages if m.rstrip() and ord(m.rstrip()[-1]) > 0x2000) / total * 100,
        "trailing_space_pct": sum(1 for m in messages if m.endswith(" ")) / total * 100,
        "starts_capitalized_pct": sum(1 for m in messages if m.strip() and m.strip()[0].isupper()) / total * 100,
    }


def _compute_vocabulary_dna(messages: list[str]) -> dict:
    """Extract vocabulary patterns."""
    total = len(messages)

    # Sentence starters
    first_words = Counter()
    for m in messages:
        stripped = m.strip()
        if stripped:
            word = re.split(r"\s+", stripped)[0].lower().rstrip(".,!?")
            if word.isalpha() and len(word) > 1:
                first_words[word] += 1
    top_starters = [w for w, c in first_words.most_common(15) if c / total > 0.005]

    # Fillers / verbal tics (language-agnostic detection)
    # Find ALL words appearing >50 times that are <=8 chars and not in the
    # 1000 most common English words. These are the operator's filler words
    # regardless of language.
    from core.engine.comms.style.extractor import _COMMON_ENGLISH, _COMMON_ENGLISH_500
    _filler_stopwords = _COMMON_ENGLISH_500  # exclude very common words
    # Contraction fragments and URL parts to exclude
    _filler_noise = {"ll", "ve", "re", "don", "didn", "doesn", "isn", "wasn",
                     "won", "couldn", "shouldn", "wouldn", "ain", "hadn",
                     "https", "http", "www", "com", "org", "net", "html"}
    word_freq: Counter = Counter()
    for m in messages:
        # Skip messages that are URLs
        stripped = m.strip()
        if stripped.startswith("http") or stripped.startswith("www"):
            continue
        for w in re.findall(r"\b[a-zA-Z]+\b", m.lower()):
            if 2 <= len(w) <= 8:
                word_freq[w] += 1
    fillers = [
        (w, c) for w, c in word_freq.most_common(50)
        if c >= 50 and w not in _filler_stopwords and w not in _filler_noise
    ]
    fillers.sort(key=lambda x: -x[1])
    filler_words = [w for w, _ in fillers[:12]]

    # Connectors
    connector_candidates = ["cuz", "like", "so", "but", "and", "also",
                            "honestly", "basically", "plus", "tho", "btw"]
    connectors = []
    for word in connector_candidates:
        count = sum(1 for m in messages if f" {word} " in m.lower())
        if count >= 10:
            connectors.append((word, count))
    connectors.sort(key=lambda x: -x[1])

    # Abbreviations
    abbrev_candidates = [
        "rn", "ur", "u", "wdyt", "ngl", "tbh", "imo", "nvm", "omw",
        "lmk", "idk", "brb", "smth", "ppl", "bc", "cuz", "tho", "thru",
        "abt", "tmr", "hv", "msg", "txt", "prolly", "def", "w/",
    ]
    word_set = set()
    for m in messages:
        word_set.update(re.findall(r"\b[a-zA-Z/]+\b", m.lower()))
    found_abbrevs = [a for a in abbrev_candidates if a in word_set]

    return {
        "top_sentence_starters": top_starters,
        "filler_words": filler_words,
        "connector_words": [w for w, _ in connectors[:8]],
        "abbreviations_used": found_abbrevs,
    }


def _extract_phrase_inventory(messages: list[str]) -> dict:
    """Extract actual phrases the operator uses by category."""
    total = len(messages)

    def sample_matching(pattern_sql_likes: list[str], min_len=3, max_len=80, n=8):
        """Find messages matching patterns and pick common short ones."""
        matching = []
        for m in messages:
            lower = m.lower().strip()
            for pattern in pattern_sql_likes:
                if pattern in lower:
                    if min_len <= len(m.strip()) <= max_len:
                        matching.append(m.strip())
                    break
        # Count and pick most common
        counts = Counter(matching)
        return [phrase for phrase, _ in counts.most_common(n) if _ >= 2]

    agreement = sample_matching(
        ["yeah", "yea ", "yes", "okay", "ok ", "sure", "bet", "for sure",
         "okok", "sounds good", "that works", "perfect", "down", "insha"],
    )
    disagreement = sample_matching(
        ["no ", "nah", "nope", "not ", "i don't", "i cant", "nahi"],
    )
    laughs_raw = []
    laugh_re = re.compile(
        r"\b(?:h[aeiou]){2,}h?\b|(?:a[hH]){2,}|l+o+l+|l+m+a+o+|l+m+f+a+o+",
        re.IGNORECASE,
    )
    for m in messages:
        for match in laugh_re.finditer(m):
            laughs_raw.append(match.group())
    laugh_counts = Counter(laughs_raw)
    laugh_patterns = [p for p, _ in laugh_counts.most_common(6)]

    # Greetings: find any repeated message-initial phrase (first 3-4 words)
    # that appears >5 times — these are the operator's greetings, whatever
    # language they're in.
    greeting_counter: Counter = Counter()
    for m in messages:
        stripped = m.strip()
        if stripped:
            # Take first 3-4 words as a potential greeting
            words = stripped.split()[:4]
            prefix = " ".join(words).lower().rstrip(".,!?")
            if 2 <= len(prefix) <= 40:
                greeting_counter[prefix] += 1
    # Filter to phrases appearing >5 times and short enough to be greetings
    greetings = [
        phrase for phrase, count in greeting_counter.most_common(10)
        if count >= 5 and len(phrase.split()) <= 4
    ][:8]
    # Also check traditional pattern-based greetings for broader coverage
    greetings_pattern = sample_matching(
        ["hey ", "yo ", "hey!", "sup", "hello", "hi "],
    )
    # Merge, dedup
    seen_greetings = set(g.lower() for g in greetings)
    for g in greetings_pattern:
        if g.lower() not in seen_greetings:
            greetings.append(g)
            seen_greetings.add(g.lower())
    greetings = greetings[:8]
    apologies = sample_matching(
        ["sorry", "my bad", "apologize", "my fault"],
    )
    affection = sample_matching(
        ["love you", "habibi", "habibti", "jaan", "miss you", "❤", "🥰"],
        max_len=120,
    )
    excitement = sample_matching(
        ["brooo", "yooo", "lets go", "that's sick", "insane", "fire", "sheesh"],
    )
    plan_making = sample_matching(
        ["let's", "lets ", "wanna", "we should", "down to", "you free", "come "],
    )

    return {
        "agreement_phrases": agreement,
        "disagreement_phrases": disagreement,
        "laugh_patterns": laugh_patterns,
        "greeting_phrases": greetings,
        "apology_phrases": apologies,
        "affection_phrases": affection,
        "excitement_phrases": excitement,
        "plan_making_phrases": plan_making,
    }


def _detect_language_mixing(messages: list[str]) -> dict:
    """Detect script mixing and cultural expressions (language-agnostic).

    Instead of checking for specific languages, this:
    1. Detects primary script by Unicode block analysis
    2. Detects secondary scripts the same way
    3. Computes mixing ratio
    4. Detects loanwords (words with diacritics or non-standard patterns)
    5. Finds repeated non-common-English phrases as cultural expressions

    Specific cultural expression identification is left to the LLM voice
    summary, which can recognize any language/culture from the message samples.
    """
    from core.engine.comms.style.extractor import _detect_script, _COMMON_ENGLISH

    total = len(messages)
    if total == 0:
        return {
            "primary_script": "latin",
            "secondary_scripts": [],
            "script_mixing_ratio": 0.0,
            "uses_loanwords": False,
            "cultural_expressions": [],
            "secondary_language_expressions": [],
        }

    # Count scripts per message (sample up to 2000 for performance)
    script_msg_counts: Counter = Counter()
    sample = messages[:2000]
    for m in sample:
        msg_scripts: set[str] = set()
        for ch in m:
            s = _detect_script(ch)
            if s:
                msg_scripts.add(s)
        for s in msg_scripts:
            script_msg_counts[s] += 1

    ranked = script_msg_counts.most_common()
    primary_script = ranked[0][0] if ranked else "latin"
    secondary_scripts = [
        s for s, c in ranked[1:]
        if c / len(sample) > 0.03  # >3% of sampled messages
    ]

    # Mixing ratio
    non_primary = sum(
        1 for m in sample
        if any(_detect_script(ch) not in ("", primary_script) for ch in m)
    )
    script_mixing_ratio = non_primary / len(sample) if sample else 0.0

    # Loanwords: Latin words with diacritics mixed in
    uses_loanwords = False
    if primary_script == "latin":
        _re_diacritics = re.compile(r"\b\w*[^\x00-\x7F\s]\w*\b")
        diac_count = sum(1 for m in sample if _re_diacritics.search(m))
        uses_loanwords = diac_count / max(len(sample), 1) > 0.05

    # Find repeated non-English words as cultural/secondary-language expressions.
    # These are words that appear frequently but aren't common English.
    all_words: Counter = Counter()
    for m in messages[:5000]:
        for w in re.findall(r"[a-zA-Z\u00C0-\u024F]+", m.lower()):
            if len(w) >= 3 and w not in _COMMON_ENGLISH:
                all_words[w] += 1

    # Cultural expressions: non-English words appearing 5+ times
    cultural_expressions = [
        w for w, c in all_words.most_common(20)
        if c >= 5 and not w.isascii()  # has diacritics or extended Latin
    ][:8]

    # Secondary language expressions: frequent non-common-English ASCII words
    # that aren't standard abbreviations. These may be romanized loanwords.
    secondary_language_expressions = [
        w for w, c in all_words.most_common(30)
        if c >= 10 and w.isascii() and len(w) >= 4
    ][:8]

    return {
        "primary_script": primary_script,
        "secondary_scripts": secondary_scripts,
        "script_mixing_ratio": round(script_mixing_ratio, 3),
        "uses_loanwords": uses_loanwords,
        "cultural_expressions": cultural_expressions,
        "secondary_language_expressions": secondary_language_expressions,
    }


def _detect_anti_patterns(messages: list[str]) -> list[str]:
    """Detect words/phrases the operator NEVER uses — instant AI tells."""
    total = len(messages)
    if total < 200:
        return []

    # AI-favorite phrases to check for absence
    ai_phrases = [
        "I'd be happy to", "feel free to", "hope this helps",
        "looking forward to", "absolutely", "I completely understand",
        "That's a great question", "I appreciate", "Don't hesitate to",
        "touching base", "circle back", "at the end of the day",
        "moving forward", "as per", "in regards to", "per our conversation",
        "please find attached", "I wanted to follow up",
    ]

    never = []
    for phrase in ai_phrases:
        count = sum(1 for m in messages if phrase.lower() in m.lower())
        if count == 0:
            never.append(f'"{phrase}"')

    return never[:10]


def _generate_voice_summary(
    structural: dict, vocab: dict, phrases: dict, lang: dict,
    sample_messages: list[str],
) -> str:
    """Call Claude Sonnet to generate a rich voice analysis.

    This is the expensive step (~5K tokens) but only runs once and on
    profile refresh. The result is cached.
    """
    # Build a prompt with real message samples
    samples_text = "\n".join(f"- {m[:150]}" for m in sample_messages[:80])

    # Build language mixing context for the prompt
    lang_context_parts = []
    if lang.get("primary_script"):
        lang_context_parts.append(f"- Primary script: {lang.get('primary_script', 'latin')}")
    if lang.get("secondary_scripts"):
        lang_context_parts.append(f"- Secondary scripts: {', '.join(lang.get('secondary_scripts', []))}")
    if lang.get("script_mixing_ratio", 0) > 0.05:
        lang_context_parts.append(f"- Script mixing ratio: {lang.get('script_mixing_ratio', 0):.0%}")
    if lang.get("cultural_expressions"):
        lang_context_parts.append(f"- Cultural expressions found: {', '.join(lang.get('cultural_expressions', [])[:6])}")
    if lang.get("secondary_language_expressions"):
        lang_context_parts.append(f"- Secondary language words: {', '.join(lang.get('secondary_language_expressions', [])[:6])}")
    lang_block = "\n".join(lang_context_parts) if lang_context_parts else "- No significant language mixing detected"

    prompt = f"""You are analyzing a person's communication style from their actual messages.
Study these 80 real messages they sent across various messaging channels:

{samples_text}

Key statistics:
- Average message length: {structural.get('avg_message_length', 0):.0f} chars
- Messages ending without punctuation: {structural.get('ends_no_punctuation_pct', 0):.0f}%
- Messages ending with period: {structural.get('ends_period_pct', 0):.1f}%
- Messages starting capitalized: {structural.get('starts_capitalized_pct', 0):.0f}%
- Most used fillers: {', '.join(vocab.get('filler_words', [])[:8])}
- Laugh patterns: {', '.join(phrases.get('laugh_patterns', [])[:5])}

Language mixing:
{lang_block}

Write a 3-4 paragraph VOICE PROFILE that captures HOW this person communicates.
Focus on:
1. Their natural register — how formal/informal, their energy level, their rhythm
2. Specific vocabulary choices and verbal tics that make them distinctive
3. How they handle different conversational situations (agreeing, joking, being serious)
4. Language mixing patterns — identify what languages/scripts are present, describe any code-switching patterns, and note cultural expressions and their usage context

Write this as instructions for someone who needs to ghostwrite messages AS this person.
Be specific and concrete — use examples from the messages. DO NOT be generic.
Write in second person ("you write...", "your style is...").
"""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "sonnet", "--output-format", "text"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        log.error("Failed to generate voice summary: %s", e)

    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_voice_profile(force: bool = False) -> VoiceProfile | None:
    """Compute the operator's voice profile from all outbound messages.

    This is an expensive operation (~30s including LLM call). Should be run:
    - Once during onboarding
    - Nightly by the cron (if message count increased significantly)
    - On demand with force=True

    Returns:
        VoiceProfile, or None if not enough data.
    """
    if not force and VOICE_PROFILE_PATH.exists():
        existing = load_voice_profile()
        if existing and existing.generated_at > 0:
            # Check if enough new messages to warrant recompute
            if not COMMS_DB.exists():
                return existing
            conn = sqlite3.connect(str(COMMS_DB))
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE direction='outbound' AND content IS NOT NULL"
            ).fetchone()[0]
            conn.close()
            # Recompute if >20% more messages since last profile
            if count < existing.message_count * 1.2:
                return existing

    if not COMMS_DB.exists():
        return None

    comms_conn = sqlite3.connect(str(COMMS_DB))
    all_msgs = _collect_outbound(comms_conn)
    comms_conn.close()

    if len(all_msgs) < MIN_MESSAGES:
        log.info("Not enough outbound messages for voice profile (%d < %d)",
                 len(all_msgs), MIN_MESSAGES)
        return None

    contents = [m["content"] for m in all_msgs]
    channels = Counter(m["channel"] for m in all_msgs)

    # Compute each dimension
    structural = _compute_structural_dna(contents)
    vocab = _compute_vocabulary_dna(contents)
    phrases = _extract_phrase_inventory(contents)
    lang = _detect_language_mixing(contents)
    anti = _detect_anti_patterns(contents)

    # Generate LLM voice summary from a diverse sample
    import random
    sample = random.sample(contents, min(SAMPLE_SIZE, len(contents)))
    voice_summary = _generate_voice_summary(structural, vocab, phrases, lang, sample)

    # Merge all computed dimensions into the profile.
    # structural, vocab, phrases all have matching field names.
    # lang uses the new universal field names.
    profile = VoiceProfile(
        generated_at=int(time.time()),
        message_count=len(all_msgs),
        channel_breakdown=dict(channels),
        **structural,
        **vocab,
        **phrases,
        **lang,
        never_uses=anti,
        voice_summary=voice_summary,
    )

    # Save to disk
    save_voice_profile(profile)
    log.info("Voice profile computed from %d messages across %d channels",
             len(all_msgs), len(channels))
    return profile


def load_voice_profile() -> VoiceProfile | None:
    """Load the cached voice profile from disk."""
    if not VOICE_PROFILE_PATH.exists():
        return None
    try:
        data = json.loads(VOICE_PROFILE_PATH.read_text())
        profile = VoiceProfile()
        for key, value in data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        return profile
    except Exception as e:
        log.error("Failed to load voice profile: %s", e)
        return None


def save_voice_profile(profile: VoiceProfile) -> None:
    """Save the voice profile to disk."""
    try:
        VOICE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            k: v for k, v in profile.__dict__.items()
            if not k.startswith("_")
        }
        VOICE_PROFILE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        log.error("Failed to save voice profile: %s", e)
