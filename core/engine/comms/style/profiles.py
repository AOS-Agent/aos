"""
Style Intelligence — Stage 3: Profile Persistence & Runtime Mode Detection.

Handles writing computed style profiles to people.db (style_profiles and
style_modes tables), reading them back, and detecting the active communication
mode at message send-time.
"""

import json
import logging
import math
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Register cues — keywords/emoji that signal a specific mode.
# Currently English-centric. These serve as a quick heuristic; the mode
# detection cascade falls through to texture matching and topic correlation
# if no cue matches, so missing languages degrade gracefully.
_REGISTER_CUES: dict[str, list[str]] = {
    "banter": ["lol", "haha", "lmao", "rofl", "\U0001F602", "\U0001F923", "joke", "joking"],
    "professional": ["formal", "professional", "sir", "madam", "regarding"],
    "deep": ["serious", "important", "urgent", "need to talk"],
    "warm": ["love", "miss", "praying", "dua", "hope you're well"],
}

# Signature dimensions used for mode distance calculation
_SIGNATURE_DIMS = [
    "avg_length", "emoji_rate", "lowercase_ratio",
    "arabic_mix", "fragment_ratio", "formality_score",
]

# Weights for each dimension in distance calculation
_DIM_WEIGHTS = {
    "avg_length": 0.25,
    "emoji_rate": 0.20,
    "lowercase_ratio": 0.15,
    "arabic_mix": 0.10,
    "fragment_ratio": 0.15,
    "formality_score": 0.15,
}

# Distance threshold for texture-based mode matching
_TEXTURE_DISTANCE_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Heuristic mode naming (for on-demand compute without LLM)
# ---------------------------------------------------------------------------

_HEURISTIC_RULES: list[tuple[str, callable]] = [
    # (mode_name, predicate on signature dict)
    ("banter", lambda s: s.get("fragment_ratio", 0) > 0.7 and s.get("avg_length", 999) < 30),
    ("professional", lambda s: s.get("formality_score", 0) > 0.3 and s.get("fragment_ratio", 0) < 0.3),
    ("warm", lambda s: s.get("emoji_rate", 0) > 0.03 or s.get("arabic_mix", 0) > 0.2),
    ("deep", lambda s: s.get("avg_length", 0) > 80 and s.get("fragment_ratio", 0) < 0.3),
    ("transactional", lambda _: True),  # fallback
]


def _heuristic_mode_name(signature: dict) -> str:
    """Assign a mode name based on cluster signature stats (no LLM)."""
    for name, predicate in _HEURISTIC_RULES:
        try:
            if predicate(signature):
                return name
        except Exception:
            continue
    return "transactional"


# ---------------------------------------------------------------------------
# On-demand profile compute (fast, no LLM)
# ---------------------------------------------------------------------------

def ensure_profile(
    person_id: str,
    people_conn: sqlite3.Connection,
    comms_conn: sqlite3.Connection,
) -> bool:
    """Ensure a style profile exists for this person. Compute one if missing.

    Runs Stage 1 only (clustering + heuristic labels, no LLM) so it completes
    in <3 seconds. The nightly cron upgrades to Haiku-labeled modes later.

    Args:
        person_id: Person ID.
        people_conn: Open connection to people.db.
        comms_conn: Open connection to comms.db.

    Returns:
        True if a profile exists (either pre-existing or just computed).
    """
    # Already has a profile?
    existing = load_style_profile(person_id, people_conn)
    if existing is not None:
        return True

    try:
        from core.engine.comms.style.extractor import (
            compute_stage_1, select_representative_exemplars,
        )

        result = compute_stage_1(person_id, comms_conn)
        if result is None:
            return False
        vectors, clusters, content_map, global_traits, style_markers = result

        # Heuristic mode labeling (no LLM)
        total_msgs = sum(len(c.vectors) for c in clusters)
        used_names: dict[str, int] = {}  # track name usage for dedup
        modes = []
        for cluster in clusters:
            name = _heuristic_mode_name(cluster.signature)
            # Deduplicate: append count if name already used
            if name in used_names:
                used_names[name] += 1
                name = f"{name}_{used_names[name]}"
            else:
                used_names[name] = 1

            # Select exemplars using shared function
            exemplar_ids = select_representative_exemplars(cluster.vectors, n=5)

            modes.append({
                "name": name.split("_")[0],  # strip dedup suffix for DB
                "weight": len(cluster.vectors) / total_msgs if total_msgs else 0,
                "signature": cluster.signature,
                "exemplar_ids": exemplar_ids,
                "topic_correlations": [],
            })

        # Count outbound for recompute threshold
        cnt_row = comms_conn.execute(
            "SELECT COUNT(*) FROM messages WHERE person_id = ? AND direction = 'outbound'",
            (person_id,),
        ).fetchone()
        outbound_count = cnt_row[0] if cnt_row else len(vectors)

        upsert_profile(
            people_conn, person_id, global_traits,
            "",  # no prose summary without LLM
            len(vectors), outbound_count,
            style_markers=style_markers,
        )
        upsert_modes(people_conn, person_id, modes)

        log.info("On-demand style profile computed for %s (%d msgs, %d modes)",
                 person_id, len(vectors), len(modes))
        return True

    except Exception as e:
        log.warning("On-demand style compute failed for %s: %s", person_id, e)
        return False


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def upsert_profile(
    people_conn: sqlite3.Connection,
    person_id: str,
    global_traits: dict,
    prose_summary: str,
    sample_size: int,
    current_outbound_count: int,
    style_markers: list[str] | None = None,
) -> None:
    """Insert or replace a style profile in people.db.

    Sets recompute_after to current_outbound_count + 15 so the profile is
    refreshed after roughly 15 more outbound messages.

    Args:
        people_conn: Open connection to people.db.
        person_id: Person ID.
        global_traits: Dict from extractor.extract_global_traits().
        prose_summary: Prose summary from labeler.
        sample_size: Number of outbound messages used in computation.
        current_outbound_count: Current total outbound count.
        style_markers: Concrete style rules extracted from messages.
    """
    markers_json = json.dumps(style_markers) if style_markers else None
    try:
        people_conn.execute(
            """
            INSERT OR REPLACE INTO style_profiles
                (person_id, computed_at, sample_size, recompute_after,
                 language_mix, romanized_ar, signs_off, uses_periods,
                 response_style, prose_summary, style_markers)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person_id,
                int(time.time()),
                sample_size,
                current_outbound_count + 15,
                global_traits.get("language_mix", "en_only"),
                1 if global_traits.get("romanized_ar") else 0,
                1 if global_traits.get("signs_off") else 0,
                1 if global_traits.get("uses_periods") else 0,
                global_traits.get("response_style", "multi_short"),
                prose_summary,
                markers_json,
            ),
        )
        people_conn.commit()
    except sqlite3.Error as e:
        log.error("Failed to upsert style_profiles for %s: %s", person_id, e)


def upsert_modes(
    people_conn: sqlite3.Connection,
    person_id: str,
    modes: list[dict],
) -> None:
    """Replace all style modes for a person in people.db.

    Deletes existing modes then inserts new ones within a single transaction.

    Args:
        people_conn: Open connection to people.db.
        person_id: Person ID.
        modes: List of mode dicts, each with keys: name, weight, signature,
            exemplar_ids, topic_correlations.
    """
    # Merge duplicate mode names (LLM may assign same name to multiple clusters)
    merged: dict[str, dict] = {}
    for mode in modes:
        name = mode.get("name", "")
        if name in merged:
            existing = merged[name]
            existing["weight"] = existing.get("weight", 0) + mode.get("weight", 0)
            # Combine exemplar lists
            ex_existing = existing.get("exemplar_ids", [])
            ex_new = mode.get("exemplar_ids", [])
            if isinstance(ex_existing, list) and isinstance(ex_new, list):
                existing["exemplar_ids"] = ex_existing + ex_new
            # Keep first signature (larger cluster's)
        else:
            merged[name] = dict(mode)
    modes = list(merged.values())

    try:
        people_conn.execute(
            "DELETE FROM style_modes WHERE person_id = ?", (person_id,)
        )
        for mode in modes:
            signature_json = (
                json.dumps(mode["signature"])
                if isinstance(mode.get("signature"), dict)
                else mode.get("signature", "{}")
            )
            exemplar_json = (
                json.dumps(mode["exemplar_ids"])
                if isinstance(mode.get("exemplar_ids"), list)
                else mode.get("exemplar_ids", "[]")
            )
            topic_json = (
                json.dumps(mode["topic_correlations"])
                if isinstance(mode.get("topic_correlations"), list)
                else mode.get("topic_correlations")
            )
            people_conn.execute(
                """
                INSERT INTO style_modes
                    (person_id, mode_name, weight, signature, exemplar_ids, topic_correlations)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    person_id,
                    mode["name"],
                    mode.get("weight", 0.0),
                    signature_json,
                    exemplar_json,
                    topic_json,
                ),
            )
        people_conn.commit()
    except sqlite3.Error as e:
        log.error("Failed to upsert style_modes for %s: %s", person_id, e)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def load_style_profile(
    person_id: str, people_conn: sqlite3.Connection
) -> dict | None:
    """Load a style profile from people.db.

    Args:
        person_id: Person ID.
        people_conn: Open connection to people.db.

    Returns:
        Dict with profile fields, or None if no profile exists.
    """
    try:
        row = people_conn.execute(
            "SELECT * FROM style_profiles WHERE person_id = ?", (person_id,)
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in people_conn.execute(
            "SELECT * FROM style_profiles LIMIT 0"
        ).description]
        profile = dict(zip(columns, row))
        # Parse style_markers JSON
        raw_markers = profile.get("style_markers")
        if raw_markers and isinstance(raw_markers, str):
            try:
                profile["style_markers"] = json.loads(raw_markers)
            except json.JSONDecodeError:
                profile["style_markers"] = []
        elif not raw_markers:
            profile["style_markers"] = []
        return profile
    except sqlite3.Error as e:
        log.error("Failed to load style_profiles for %s: %s", person_id, e)
        return None


def load_style_modes(
    person_id: str, people_conn: sqlite3.Connection
) -> list[dict]:
    """Load style modes for a person from people.db.

    Parses JSON fields (signature, exemplar_ids, topic_correlations) into
    Python objects.

    Args:
        person_id: Person ID.
        people_conn: Open connection to people.db.

    Returns:
        List of mode dicts with parsed JSON fields. Empty list if none found.
    """
    try:
        rows = people_conn.execute(
            "SELECT * FROM style_modes WHERE person_id = ?", (person_id,)
        ).fetchall()
        if not rows:
            return []
        columns = [desc[0] for desc in people_conn.execute(
            "SELECT * FROM style_modes LIMIT 0"
        ).description]
        modes = []
        for row in rows:
            mode = dict(zip(columns, row))
            # Parse JSON fields
            for json_field in ("signature", "exemplar_ids", "topic_correlations"):
                raw = mode.get(json_field)
                if raw and isinstance(raw, str):
                    try:
                        mode[json_field] = json.loads(raw)
                    except json.JSONDecodeError:
                        mode[json_field] = None
            modes.append(mode)
        return modes
    except sqlite3.Error as e:
        log.error("Failed to load style_modes for %s: %s", person_id, e)
        return []


def fetch_exemplars(
    exemplar_ids: list[str], comms_conn: sqlite3.Connection
) -> list[str]:
    """Fetch exemplar message contents from comms.db.

    Args:
        exemplar_ids: List of message IDs.
        comms_conn: Open connection to comms.db.

    Returns:
        List of message content strings. Missing IDs are silently skipped.
    """
    if not exemplar_ids:
        return []
    try:
        placeholders = ",".join("?" for _ in exemplar_ids)
        rows = comms_conn.execute(
            f"SELECT content FROM messages WHERE id IN ({placeholders})",
            exemplar_ids,
        ).fetchall()
        return [row[0] for row in rows if row[0]]
    except sqlite3.Error as e:
        log.error("Failed to fetch exemplar messages: %s", e)
        return []


def needs_recompute(
    person_id: str,
    people_conn: sqlite3.Connection,
    comms_conn: sqlite3.Connection,
) -> bool:
    """Check whether a person's style profile needs recomputation.

    Returns True if no profile exists or if the current outbound message count
    exceeds the profile's recompute_after threshold.

    Args:
        person_id: Person ID.
        people_conn: Open connection to people.db.
        comms_conn: Open connection to comms.db.

    Returns:
        True if recomputation is needed.
    """
    profile = load_style_profile(person_id, people_conn)
    if profile is None:
        return True

    recompute_after = profile.get("recompute_after", 0)

    try:
        row = comms_conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE person_id = ?
              AND direction = 'outbound'
              AND content IS NOT NULL
              AND length(content) > 0
            """,
            (person_id,),
        ).fetchone()
        current_count = row[0] if row else 0
    except sqlite3.Error as e:
        log.error("Failed to count outbound for %s: %s", person_id, e)
        return False

    return current_count > recompute_after


# ---------------------------------------------------------------------------
# Mode detection at send-time
# ---------------------------------------------------------------------------

def detect_active_mode(
    person_id: str,
    recent_messages: list[dict],
    requested_topic: str | None,
    register_cues: list[str],
    people_conn: sqlite3.Connection,
    comms_conn: sqlite3.Connection,
) -> tuple[str, list[str]]:
    """Detect the active communication mode for a person at send-time.

    Uses a 4-priority cascade:
      1. Register cues (explicit keywords/emoji in current context)
      2. Recent thread texture (messages from last 2 hours)
      3. Topic correlation (match requested_topic to mode topics)
      4. Fallback to highest-weight mode

    Args:
        person_id: Person ID.
        recent_messages: List of dicts with at least 'content' and 'timestamp'
            keys, representing recent conversation messages.
        requested_topic: Optional topic string for the intended message.
        register_cues: List of keyword/emoji cues from the current context.
        people_conn: Open connection to people.db.
        comms_conn: Open connection to comms.db.

    Returns:
        Tuple of (mode_name, exemplar_texts). Returns ("", []) if no modes
        exist for this person.
    """
    modes = load_style_modes(person_id, people_conn)
    if not modes:
        return ("", [])

    selected_mode = None

    # --- Priority 1: Register cues ---
    if register_cues:
        cue_set = set(c.lower() for c in register_cues)
        best_match = None
        best_count = 0
        for mode_name, cue_keywords in _REGISTER_CUES.items():
            overlap = len(cue_set & set(cue_keywords))
            if overlap > best_count:
                best_count = overlap
                best_match = mode_name
        if best_match:
            selected_mode = _find_mode_by_name(modes, best_match)

    # --- Priority 2: Recent thread texture ---
    if selected_mode is None and recent_messages:
        # Import here to avoid circular imports
        from core.engine.comms.style.extractor import compute_texture

        # Filter to messages from the last 2 hours
        now = time.time()
        two_hours_ago = now - 7200
        recent = []
        for msg in recent_messages:
            ts = msg.get("timestamp")
            if ts is None:
                continue
            # Handle both epoch and ISO timestamps
            if isinstance(ts, (int, float)):
                msg_time = ts
            elif isinstance(ts, str):
                try:
                    from datetime import datetime
                    msg_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    continue
            else:
                continue
            if msg_time >= two_hours_ago:
                recent.append(msg)

        if recent:
            # Compute average texture of recent messages
            textures = []
            for msg in recent:
                content = msg.get("content", "")
                if content:
                    textures.append(compute_texture("recent", content))

            if textures:
                avg_sig = _avg_texture_signature(textures)
                best_mode = None
                best_dist = float("inf")
                for mode in modes:
                    sig = mode.get("signature", {})
                    if not sig:
                        continue
                    dist = _weighted_euclidean(avg_sig, sig)
                    if dist < best_dist:
                        best_dist = dist
                        best_mode = mode
                if best_mode and best_dist < _TEXTURE_DISTANCE_THRESHOLD:
                    selected_mode = best_mode

    # --- Priority 3: Topic correlation ---
    if selected_mode is None and requested_topic:
        topic_lower = requested_topic.lower()
        for mode in modes:
            correlations = mode.get("topic_correlations") or []
            if isinstance(correlations, str):
                try:
                    correlations = json.loads(correlations)
                except json.JSONDecodeError:
                    correlations = []
            for topic in correlations:
                if isinstance(topic, str) and topic.lower() in topic_lower:
                    selected_mode = mode
                    break
            if selected_mode:
                break

    # --- Priority 4: Fallback to highest-weight mode ---
    if selected_mode is None:
        selected_mode = max(modes, key=lambda m: m.get("weight", 0.0))

    # Fetch exemplar texts for the selected mode
    mode_name = selected_mode.get("mode_name", selected_mode.get("name", ""))
    exemplar_ids = selected_mode.get("exemplar_ids", [])
    if isinstance(exemplar_ids, str):
        try:
            exemplar_ids = json.loads(exemplar_ids)
        except json.JSONDecodeError:
            exemplar_ids = []

    exemplar_texts = fetch_exemplars(exemplar_ids, comms_conn)

    return (mode_name, exemplar_texts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_mode_by_name(modes: list[dict], name: str) -> dict | None:
    """Find a mode dict by name, case-insensitive."""
    for mode in modes:
        mode_name = mode.get("mode_name", mode.get("name", ""))
        if mode_name.lower() == name.lower():
            return mode
    return None


def _avg_texture_signature(textures: list) -> dict:
    """Compute average signature-like stats from a list of TextureVectors."""
    n = len(textures)
    if n == 0:
        return {}
    return {
        "avg_length": sum(t.char_length for t in textures) / n,
        "emoji_rate": sum(t.emoji_count for t in textures) / n,
        "lowercase_ratio": sum(t.lowercase_ratio for t in textures) / n,
        # Key kept as "arabic_mix" for DB/JSON backward compat — tracks any non-Latin script
        "arabic_mix": sum(1 for t in textures if t.has_non_latin) / n,
        "fragment_ratio": sum(1 for t in textures if t.is_fragment) / n,
        "formality_score": sum(
            t.punctuation_density + (1.0 - t.lowercase_ratio) for t in textures
        ) / n,
    }


def _weighted_euclidean(sig_a: dict, sig_b: dict) -> float:
    """Compute weighted Euclidean distance between two signature dicts.

    Normalizes avg_length by dividing by 500 (approximate max) before
    computing distance, so all dimensions are roughly on the same scale.
    """
    total = 0.0
    for dim in _SIGNATURE_DIMS:
        a = sig_a.get(dim, 0.0)
        b = sig_b.get(dim, 0.0)
        # Normalize avg_length to roughly 0-1 range
        if dim == "avg_length":
            a = a / 500.0
            b = b / 500.0
        weight = _DIM_WEIGHTS.get(dim, 0.1)
        total += weight * (a - b) ** 2
    return math.sqrt(total)
