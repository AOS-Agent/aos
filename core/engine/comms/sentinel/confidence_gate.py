"""Confidence gate — the only safety rail in autonomous mode.

Reads a draft file produced by Sentinel and evaluates against the 6
high-confidence criteria + hard floor blocks. Returns a GateResult that
the spawner uses to decide: send via dispatcher, or escalate to pending.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

CONFIG_PATH = Path.home() / ".aos" / "config" / "sentinel.yaml"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"

# Hard floor blocked-intent words (default; can be overridden in config)
DEFAULT_BLOCKED_WORDS = [
    "book", "schedule", "pay", "buy", "send money", "reserve", "transfer money",
]

# Stopwords for the relevance check (trigger ↔ draft keyword overlap).
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "yours",
    "with", "this", "that", "have", "has", "had", "from", "they", "them",
    "what", "when", "where", "which", "while", "would", "could", "should",
    "about", "into", "their", "there", "then", "than", "been", "being",
    "just", "like", "some", "such", "these", "those", "very", "really",
    "thanks", "thank", "please", "consider", "done", "aos", "yeah", "yes",
    "okay", "right", "going", "tell", "told", "said", "saying", "want",
    "wants", "wanted", "make", "made", "making", "lets", "thats", "dont",
    "didnt", "wont", "cant", "isnt", "doesnt", "much", "more", "most",
    "only", "also", "even", "well", "still", "back", "good", "great",
    "ever", "every", "anything", "something", "nothing", "everything",
    "today", "tomorrow", "yesterday", "tonight", "morning", "evening",
    "actually", "maybe", "perhaps", "sure", "fine", "cool", "nice",
}


def _significant_tokens(text: str, min_len: int = 4) -> set[str]:
    """Extract significant lowercase tokens from a string.

    Strips trigger phrases ("@aos", "consider it done") first so the actual
    request keywords surface. Drops stopwords, short tokens, and pure digits
    unless they look like dates/times.
    """
    if not text:
        return set()
    s = text.lower()
    # Strip known trigger phrases so they don't dominate the keyword set
    for phrase in ("consider it done", "@aos"):
        s = s.replace(phrase, " ")
    # Replace non-alphanumerics with spaces
    s = re.sub(r"[^a-z0-9]+", " ", s)
    toks: set[str] = set()
    for t in s.split():
        if len(t) < min_len:
            continue
        if t in _STOPWORDS:
            continue
        if t.isdigit():
            continue
        toks.add(t)
    return toks


@dataclass
class GateResult:
    high_confidence: bool
    reasons_against: list[str]
    hard_floor_violated: bool
    decision: str           # "send" | "pending" | "blocked"

    @property
    def can_auto_send(self) -> bool:
        return self.decision == "send"


@dataclass
class ParsedDraft:
    frontmatter: dict
    body: str

    @property
    def confidence(self) -> str:
        return str(self.frontmatter.get("confidence", "low")).lower()

    @property
    def in_scope(self) -> bool:
        return bool(self.frontmatter.get("in_scope", False))

    @property
    def task_inferred(self) -> str:
        return str(self.frontmatter.get("task_inferred", ""))

    @property
    def sources(self) -> list[dict]:
        s = self.frontmatter.get("sources") or []
        return s if isinstance(s, list) else []

    @property
    def requires_research(self) -> bool:
        """Whether this task requires external research/citations.

        Sentinel sets this to False for pure-composition tasks (e.g.,
        "tell my wife she's amazing", "say happy birthday") where no
        sources are needed. Defaults to True for backward compat — drafts
        that omit the field continue to be held to the research-task bar.
        """
        v = self.frontmatter.get("requires_research", True)
        if isinstance(v, str):
            return v.strip().lower() not in {"false", "no", "0", "off"}
        return bool(v)

    @property
    def external_sources(self) -> list[dict]:
        """Sources backed by an external URL (web research)."""
        return [s for s in self.sources if isinstance(s, dict) and s.get("url")]

    @property
    def internal_sources(self) -> list[dict]:
        """Sources from our own data (vault, comms.db, people.db, etc.).

        A source is internal if it declares a `source_type` other than 'web'/'url'/'external'.
        Examples: source_type: vault | comms | comms_db | people | people_db | internal | db | local.
        """
        out = []
        for s in self.sources:
            if not isinstance(s, dict):
                continue
            st = str(s.get("source_type", "")).lower().strip()
            if st and st not in {"web", "url", "external"}:
                out.append(s)
        return out


def parse_draft_file(path: Path) -> Optional[ParsedDraft]:
    """Parse a Sentinel draft file (YAML frontmatter + markdown body)."""
    if not path.exists():
        return None
    text = path.read_text()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return None
    body = m.group(2).strip()
    return ParsedDraft(frontmatter=fm, body=body)


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


class ConfidenceGate:
    """Evaluates a draft against the 6 criteria + hard floor."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or _load_config()
        self.criteria = self.config.get("confidence_criteria", {})
        self.hard_floor = self.config.get("hard_floor", {})

    def evaluate(self, draft: ParsedDraft, contact_importance: int,
                 last_sentinel_send_ts: Optional[int] = None,
                 trigger_text: Optional[str] = None) -> GateResult:
        reasons = []
        hard_violated = False

        # === HARD FLOOR ===
        # Inner circle block
        block_imp = int(self.hard_floor.get("block_inner_circle_importance", 1))
        if contact_importance == block_imp:
            reasons.append("HARD FLOOR: inner-circle contact")
            hard_violated = True

        # Blocked intent words in body
        blocked = [w.lower() for w in self.hard_floor.get("blocked_intent_words", DEFAULT_BLOCKED_WORDS)]
        body_lower = draft.body.lower()
        for w in blocked:
            if re.search(rf"\b{re.escape(w)}\b", body_lower):
                reasons.append(f"HARD FLOOR: draft contains blocked intent word '{w}'")
                hard_violated = True
                break

        # Relevance hard floor — the draft (or inferred task) must share at
        # least one significant token with the operator's trigger message.
        # Prevents Sentinel from re-sending unrelated prior research when the
        # current trigger is unrelated to old context (the Aesop/Hermes bug).
        if trigger_text and self.hard_floor.get("require_trigger_overlap", True):
            trg_tokens = _significant_tokens(trigger_text)
            if trg_tokens:
                draft_tokens = _significant_tokens(
                    draft.body + " " + draft.task_inferred
                )
                overlap = trg_tokens & draft_tokens
                if not overlap:
                    reasons.append(
                        "HARD FLOOR: draft does not address trigger message "
                        f"(no keyword overlap; trigger tokens={sorted(trg_tokens)[:6]})"
                    )
                    hard_violated = True

        # Scope from draft itself
        if not draft.in_scope:
            reasons.append("draft marked out of scope by Sentinel")

        # === CRITERIA ===
        # 1. Pure research
        if self.criteria.get("require_pure_research", True):
            if not draft.in_scope:
                # already counted above
                pass

        # 2. Source count — internal sources (vault/comms/people) count separately
        #    from external (web). Pure-synthesis tasks (e.g. summarising our own
        #    comms.db) only need 1 internal source. Web claims still need 2 external.
        #    Pure-composition tasks (requires_research: false) skip this check
        #    entirely — e.g. "tell my wife she's amazing" needs no sources.
        min_external = int(self.criteria.get("min_sources", 2))
        min_internal = int(self.criteria.get("min_internal_sources", 1))
        n_external = len(draft.external_sources)
        n_internal = len(draft.internal_sources)
        n_total_sources = n_external + n_internal
        if draft.requires_research:
            if n_external < min_external and n_internal < min_internal:
                reasons.append(
                    f"insufficient sources: {n_external} external (need {min_external}) "
                    f"and {n_internal} internal (need {min_internal})"
                )

        # 3. Style match — Sentinel self-declares via confidence string
        #    (we trust her per-draft assessment; quantitative match is future work)

        # 4. Inner circle (also a hard floor — counted above)

        # 5. Unverified facts — best-effort: look for numbers/dates with no sources
        #    Any source (internal or external) provides backing; only flag if zero sources total.
        #    Skipped for pure-composition drafts (no factual claims to verify).
        if (draft.requires_research and
                self.criteria.get("block_unverified_facts", True) and
                n_total_sources == 0):
            if re.search(r"\b\d{1,4}\s*(am|pm|\$|/|\d)", body_lower) or \
               re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", body_lower):
                reasons.append("unsourced concrete claim (number/date) detected")

        # 6. Rate limit
        rate_hours = int(self.criteria.get("rate_limit_hours", 1))
        if last_sentinel_send_ts is not None:
            elapsed_h = (time.time() - last_sentinel_send_ts) / 3600
            if elapsed_h < rate_hours:
                reasons.append(f"rate limit: last send was {elapsed_h:.1f}h ago (need {rate_hours}h)")

        # 7. Self-declared confidence — if Sentinel herself says not high, trust her
        if draft.confidence != "high":
            reasons.append(f"Sentinel marked confidence as '{draft.confidence}'")

        # Empty body is never sendable
        if not draft.body.strip():
            reasons.append("empty draft body")

        high = (len(reasons) == 0)

        if hard_violated:
            decision = "blocked"
        elif high:
            decision = "send"
        else:
            decision = "pending"

        return GateResult(
            high_confidence=high,
            reasons_against=reasons,
            hard_floor_violated=hard_violated,
            decision=decision,
        )


def last_sentinel_send_for_person(person_id: str) -> Optional[int]:
    """Look up timestamp of the most recent successful Sentinel send to this person."""
    if not person_id:
        return None
    try:
        conn = sqlite3.connect(str(COMMS_DB))
        row = conn.execute("""
            SELECT MAX(sent_at) FROM agent_triggers
            WHERE person_id = ? AND status = 'sent'
        """, (person_id,)).fetchone()
        conn.close()
        return int(row[0]) if row and row[0] else None
    except Exception:
        return None
