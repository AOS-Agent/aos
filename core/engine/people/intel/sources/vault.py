"""Vault signal adapter.

Scans the operator's personal vault (`~/vault/log/`) for mentions of each
person in the person_index. The vault is the single strongest signal of
who the operator actively thinks about: daily logs and session exports
are their own written record.

Two sub-sources are scanned:
  - Daily logs:     ~/vault/log/202*-*.md           (capped at 90 newest)
  - Session exports: ~/vault/log/sessions/*.md       (capped at 50 newest)

Matching is name-based. The `name` field in people.db can be either a
spaced canonical name ("Sam Taylor") or a concatenated camelCase token
("SamTaylor"). We build both variants and match each as a word-boundary
regex. Overlapping matches between variants at the same file offset are
deduplicated so we don't double-count a single real mention.

Short or stopword-like names are skipped entirely — "Sam", "Love", "Home"
would produce too much noise. Callers should pre-filter with higher-
confidence names where possible, but this layer guarantees a baseline.

YAML frontmatter is stripped before scanning so titles and tag blocks
never count as mentions. Only body text is scanned.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import ClassVar

from ..normalize import component_variants
from ..types import (
    MentionSignal,
    PersonSignals,
    SignalType,
)
from .base import SignalAdapter

logger = logging.getLogger(__name__)


# Hardcoded stopword set — names that are too generic to yield a useful
# signal on their own. Matched case-insensitively against the full name.
STOPWORDS: frozenset[str] = frozenset({
    "love", "work", "home", "help", "time", "life", "good", "best",
    "real", "mind", "plan", "task", "note", "data", "code", "test",
    "demo", "user", "team", "page", "file", "line", "item", "list",
})

# Regex matching YYYY-MM-DD anywhere in a filename.
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Regex matching daily log filenames: 202X-* prefix.
_DAILY_RE = re.compile(r"^202\d-.+\.md$")


def _name_variants(name: str) -> list[str]:
    """Return the name itself and a space-separated camelCase split.

    "SamTaylor" → ["SamTaylor", "Sam Taylor"]
    "Sam Taylor" → ["Sam Taylor"]
    """
    name = (name or "").strip()
    if not name:
        return []
    variants: set[str] = {name}
    # Insert spaces at lower→upper case boundaries.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    if spaced != name:
        variants.add(spaced)
    return list(variants)


def _strip_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block (--- ... ---) if present."""
    if not text.startswith("---\n"):
        return text
    # Find the closing fence after the opening fence.
    end = text.find("\n---\n", 4)
    if end == -1:
        # Malformed frontmatter — leave the file alone.
        return text
    return text[end + len("\n---\n"):]


class VaultAdapter(SignalAdapter):
    """Scan vault daily logs and session exports for person mentions."""

    name: ClassVar[str] = "vault"
    display_name: ClassVar[str] = "Vault Logs + Sessions"
    platform: ClassVar[str] = "any"
    signal_types: ClassVar[list[SignalType]] = [SignalType.MENTION]
    description: ClassVar[str] = (
        "Scans vault daily logs and session exports for person name mentions"
    )
    requires: ClassVar[list[str]] = ["dir:~/vault/log"]

    # Scan caps. Overridable by subclasses / tests.
    DAILY_SCAN_LIMIT: ClassVar[int] = 90
    SESSION_SCAN_LIMIT: ClassVar[int] = 50
    CONTEXTS_PER_PERSON: ClassVar[int] = 20
    # Full-name variants need 4+ chars to avoid matching "Bob" as a
    # standalone substring against every "bob" in prose.
    MIN_NAME_LENGTH: ClassVar[int] = 4
    # Component matches (single-token from a multi-word name) can be as
    # short as 3 chars — common real first names are "Ali", "Zia", "Bob".
    # False positives are prevented by COMPONENT_CONFIRM_WINDOW: a
    # component match only counts if another component from the same
    # canonical_name appears within N chars.
    MIN_COMPONENT_LENGTH: ClassVar[int] = 3
    SNIPPET_WINDOW: ClassVar[int] = 50  # chars before and after the match
    # Context-confirmation window for component-only matches. A component
    # match (e.g., "Sam" alone) counts only if another component from the
    # same canonical_name appears within this many characters of the match.
    COMPONENT_CONFIRM_WINDOW: ClassVar[int] = 200

    def __init__(self, vault_root: str | None = None):
        if vault_root is None:
            vault_root = str(Path.home() / "vault")
        self.vault_root = Path(vault_root).expanduser()
        self.log_root = self.vault_root / "log"
        self.sessions_root = self.log_root / "sessions"

    # ── Capability ──

    def is_available(self) -> bool:
        try:
            return self.log_root.is_dir()
        except Exception:
            return False

    # ── Extraction ──

    def extract_all(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        if not self.is_available():
            return {}

        try:
            return self._extract(person_index)
        except Exception as e:
            logger.exception("vault adapter: extract failed: %s", e)
            return {}

    def _extract(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        # Build per-person regex patterns, skipping ineligible names.
        #
        # Two pattern families per person:
        #   - patterns:  primary full-name variants (spaced + concat)
        #   - components: individual tokens (first name, last name, etc.)
        #     used only for a confirmation-gated second pass
        #
        # A person is eligible only when at least one full-name variant has
        # length >= MIN_NAME_LENGTH and the raw name isn't a stopword.
        patterns: dict[str, list[re.Pattern[str]]] = {}
        components: dict[str, list[re.Pattern[str]]] = {}
        names: dict[str, str] = {}

        for person_id, info in person_index.items():
            raw = (info or {}).get("name") or ""
            raw_stripped = raw.strip()
            if not raw_stripped:
                continue
            if raw_stripped.lower() in STOPWORDS:
                continue
            variants = _name_variants(raw_stripped)
            eligible = [v for v in variants if len(v) >= self.MIN_NAME_LENGTH]
            if not eligible:
                continue
            compiled: list[re.Pattern[str]] = []
            for variant in eligible:
                try:
                    compiled.append(
                        re.compile(
                            r"\b" + re.escape(variant) + r"\b",
                            re.IGNORECASE,
                        )
                    )
                except re.error:
                    continue
            if not compiled:
                continue
            patterns[person_id] = compiled
            names[person_id] = raw_stripped

            # Build component patterns from the SPACED variant. Single-
            # component names (e.g., "Kumar" on its own) are skipped — no
            # second token means no confirmation is possible.
            spaced = raw_stripped
            # Prefer the spaced camelCase-split form if available.
            for v in variants:
                if " " in v:
                    spaced = v
                    break
            comps = component_variants(spaced, min_length=self.MIN_COMPONENT_LENGTH)
            # Drop stopword components.
            comps = [c for c in comps if c not in STOPWORDS]
            if len(comps) >= 2:
                comp_patterns: list[re.Pattern[str]] = []
                for c in comps:
                    try:
                        comp_patterns.append(
                            re.compile(
                                r"\b" + re.escape(c) + r"\b",
                                re.IGNORECASE,
                            )
                        )
                    except re.error:
                        continue
                if comp_patterns:
                    components[person_id] = comp_patterns

        if not patterns:
            return {}

        # Per-person running totals.
        totals: dict[str, dict] = {
            pid: {
                "total": 0,
                "daily": 0,
                "session": 0,
                "contexts": [],  # newest-first, capped later
            }
            for pid in patterns
        }

        # Scan dailies — newest first.
        for path in self._iter_daily_files():
            self._scan_file(path, patterns, components, totals, is_session=False)

        # Scan sessions — newest first.
        for path in self._iter_session_files():
            self._scan_file(path, patterns, components, totals, is_session=True)

        # Build PersonSignals output.
        result: dict[str, PersonSignals] = {}
        for person_id, agg in totals.items():
            if agg["total"] < 1:
                continue
            contexts = agg["contexts"][: self.CONTEXTS_PER_PERSON]
            mention = MentionSignal(
                source=self.name,
                total_mentions=agg["total"],
                mention_contexts=contexts,
                daily_log_mentions=agg["daily"],
                session_mentions=agg["session"],
                work_task_mentions=0,
            )
            result[person_id] = PersonSignals(
                person_id=person_id,
                person_name=names.get(person_id, ""),
                source_coverage=[self.name],
                mentions=[mention],
            )

        return result

    # ── File iteration ──

    def _iter_daily_files(self) -> list[Path]:
        """Daily log files sorted newest-first, capped at DAILY_SCAN_LIMIT."""
        if not self.log_root.is_dir():
            return []
        candidates: list[Path] = []
        try:
            for child in self.log_root.iterdir():
                if not child.is_file():
                    continue
                if not _DAILY_RE.match(child.name):
                    continue
                candidates.append(child)
        except OSError:
            return []
        candidates.sort(key=lambda p: p.name, reverse=True)
        return candidates[: self.DAILY_SCAN_LIMIT]

    def _iter_session_files(self) -> list[Path]:
        """Session export files sorted newest-first, capped at SESSION_SCAN_LIMIT."""
        if not self.sessions_root.is_dir():
            return []
        candidates: list[Path] = []
        try:
            for child in self.sessions_root.iterdir():
                if not child.is_file():
                    continue
                if not child.name.endswith(".md"):
                    continue
                candidates.append(child)
        except OSError:
            return []
        candidates.sort(key=lambda p: p.name, reverse=True)
        return candidates[: self.SESSION_SCAN_LIMIT]

    # ── Single-file scan ──

    def _scan_file(
        self,
        path: Path,
        patterns: dict[str, list[re.Pattern[str]]],
        components: dict[str, list[re.Pattern[str]]],
        totals: dict[str, dict],
        *,
        is_session: bool,
    ) -> None:
        try:
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return

        body = _strip_frontmatter(raw_text)
        if not body:
            return

        # Extract YYYY-MM-DD from the filename (first match wins).
        date_match = _DATE_RE.search(path.name)
        file_date = date_match.group(1) if date_match else None

        # File path relative to the vault root for context reporting.
        try:
            rel_path = str(path.relative_to(self.vault_root))
        except ValueError:
            rel_path = str(path)

        body_len = len(body)

        confirm_window = self.COMPONENT_CONFIRM_WINDOW

        for person_id, pats in patterns.items():
            # ── Primary pass: full-name variants ──
            # Dedupe overlapping variants via (start, end) span set.
            spans: set[tuple[int, int]] = set()
            for pat in pats:
                for m in pat.finditer(body):
                    spans.add((m.start(), m.end()))

            # ── Second pass: component variants with context confirmation ──
            comp_pats = components.get(person_id)
            if comp_pats:
                # Skip component hits that fall inside a span already
                # matched by the primary full-name pass — otherwise
                # "Alice Smith" would be counted once by the full-name
                # regex and twice more by the "Alice"/"Smith" component
                # regexes.
                primary_spans = sorted(spans)

                def _inside_primary(s: int, e: int) -> bool:
                    for ps, pe in primary_spans:
                        if s >= ps and e <= pe:
                            return True
                        if ps >= e:
                            break
                    return False

                # Find every occurrence of every component in this file.
                # Each hit is (component_index, start, end).
                hits: list[tuple[int, int, int]] = []
                for idx, cp in enumerate(comp_pats):
                    for m in cp.finditer(body):
                        if _inside_primary(m.start(), m.end()):
                            continue
                        hits.append((idx, m.start(), m.end()))

                if hits:
                    # Sort by position for a stable sweep.
                    hits.sort(key=lambda h: h[1])
                    # For each hit, confirm another component (different
                    # token index) from the same canonical_name appears
                    # within confirm_window chars.
                    for i, (cidx, start, end) in enumerate(hits):
                        confirmed = False
                        # Walk neighbours forward and backward.
                        # Forward:
                        j = i + 1
                        while j < len(hits) and hits[j][1] - end <= confirm_window:
                            if hits[j][0] != cidx:
                                confirmed = True
                                break
                            j += 1
                        if not confirmed:
                            # Backward:
                            j = i - 1
                            while j >= 0 and start - hits[j][2] <= confirm_window:
                                if hits[j][0] != cidx:
                                    confirmed = True
                                    break
                                j -= 1
                        if confirmed:
                            spans.add((start, end))

            if not spans:
                continue

            # Stable iteration order: earliest matches first.
            ordered = sorted(spans)
            agg = totals[person_id]
            agg["total"] += len(ordered)
            if is_session:
                agg["session"] += len(ordered)
            else:
                agg["daily"] += len(ordered)

            # Collect snippets — capped globally per-person by CONTEXTS_PER_PERSON,
            # but we keep the cap at the aggregation layer (truncation at end).
            contexts = agg["contexts"]
            remaining = self.CONTEXTS_PER_PERSON - len(contexts)
            if remaining <= 0:
                continue

            for start, end in ordered[:remaining]:
                snip_start = max(0, start - self.SNIPPET_WINDOW)
                snip_end = min(body_len, end + self.SNIPPET_WINDOW)
                snippet = body[snip_start:snip_end].replace("\n", " ").strip()
                contexts.append(
                    {
                        "file": rel_path,
                        "snippet": snippet,
                        "date": file_date,
                    }
                )
