"""
Style Intelligence — Stage 2: LLM Labeling via Claude Haiku.

Takes clusters and global traits from Stage 1, calls Claude Haiku to name
each cluster's communication mode and produce a prose summary. Follows the
same Claude CLI pattern as the enrich-comms cron.
"""

import json
import logging
import random
import sqlite3
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Valid mode names — Haiku must pick from exactly these
VALID_MODES = frozenset(["banter", "transactional", "deep", "warm", "professional"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _select_exemplars(
    cluster, comms_conn: sqlite3.Connection, n: int = 5
) -> list[str]:
    """Select the most representative messages from a cluster.

    Delegates to the shared select_representative_exemplars() in extractor.py
    which picks messages closest to the median texture, avoiding near-duplicates.

    Args:
        cluster: A Cluster object with message_ids and vectors.
        comms_conn: Open connection to comms.db (unused, kept for signature compat).
        n: Number of exemplars to select.

    Returns:
        List of message ID strings.
    """
    from core.engine.comms.style.extractor import select_representative_exemplars
    return select_representative_exemplars(cluster.vectors, n=n)


def _fetch_message_texts(
    message_ids: list[str], comms_conn: sqlite3.Connection
) -> list[tuple[str, str]]:
    """Fetch message content by IDs from comms.db.

    Args:
        message_ids: List of message IDs to fetch.
        comms_conn: Open connection to comms.db.

    Returns:
        List of (message_id, content) tuples. Missing IDs are silently skipped.
    """
    if not message_ids:
        return []

    try:
        placeholders = ",".join("?" for _ in message_ids)
        rows = comms_conn.execute(
            f"SELECT id, content FROM messages WHERE id IN ({placeholders})",
            message_ids,
        ).fetchall()
        return [(row[0], row[1]) for row in rows if row[1]]
    except sqlite3.Error as e:
        log.error("Failed to fetch message texts: %s", e)
        return []


def _build_labeling_prompt(
    person_name: str,
    clusters_with_samples: list[dict],
    global_traits: dict,
) -> str:
    """Build the prompt for Claude Haiku to label clusters.

    Args:
        person_name: Display name of the person.
        clusters_with_samples: List of dicts, each with 'index', 'signature',
            and 'samples' (list of truncated message texts).
        global_traits: Dict from extract_global_traits().

    Returns:
        Prompt string.
    """
    cluster_sections = []
    for c in clusters_with_samples:
        sig = c["signature"]
        samples_text = "\n".join(
            f"  - {s[:120]}" for s in c["samples"]
        )
        cluster_sections.append(
            f"Cluster {c['index']}:\n"
            f"  Stats: avg_length={sig.get('avg_length', 0):.0f}, "
            f"emoji_rate={sig.get('emoji_rate', 0):.2f}, "
            f"lowercase_ratio={sig.get('lowercase_ratio', 0):.2f}, "
            f"arabic_mix={sig.get('arabic_mix', 0):.2f}, "
            f"fragment_ratio={sig.get('fragment_ratio', 0):.2f}, "
            f"formality_score={sig.get('formality_score', 0):.3f}\n"
            f"  Sample messages:\n{samples_text}"
        )

    clusters_block = "\n\n".join(cluster_sections)

    traits_block = (
        f"Language mix: {global_traits.get('language_mix', 'unknown')}\n"
        f"Uses romanized Arabic: {global_traits.get('romanized_ar', False)}\n"
        f"Signs off: {global_traits.get('signs_off', False)}\n"
        f"Uses periods: {global_traits.get('uses_periods', False)}\n"
        f"Response style: {global_traits.get('response_style', 'unknown')}"
    )

    return f"""You are analyzing communication patterns in messages sent TO {person_name}.

Global traits:
{traits_block}

Message clusters:
{clusters_block}

For each cluster, assign exactly ONE mode name from this list:
banter, transactional, deep, warm, professional

Also identify 1-3 topic correlations per cluster (what topics tend to appear in this mode).

Write a single prose_summary paragraph (2-4 sentences) describing this person's overall communication style.

Return ONLY valid JSON in this exact format:
{{"modes": [{{"cluster_index": 0, "name": "banter", "topic_correlations": ["humor", "memes"]}}, {{"cluster_index": 1, "name": "warm", "topic_correlations": ["family", "wellbeing"]}}], "prose_summary": "..."}}

Rules:
- mode name must be exactly one of: banter, transactional, deep, warm, professional
- topic_correlations: 1-3 short keywords per cluster
- prose_summary: 2-4 sentences, natural language
- Do not invent modes outside the allowed list"""


def _call_haiku(prompt: str) -> dict | None:
    """Call Claude Haiku via CLI and parse the JSON response.

    Follows the exact pattern from enrich-comms cron.

    Args:
        prompt: Full prompt string.

    Returns:
        Parsed dict from Haiku's JSON response, or None on failure.
    """
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.warning("Haiku CLI returned non-zero: %d", result.returncode)
            return None

        outer = json.loads(result.stdout.strip())
        raw = outer.get("result", "") if isinstance(outer, dict) else str(outer)

        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]

        return json.loads(raw.strip())

    except subprocess.TimeoutExpired:
        log.error("Haiku CLI timed out")
        return None
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Failed to parse Haiku response: %s", e)
        return None
    except FileNotFoundError:
        log.error("'claude' CLI not found on PATH")
        return None
    except Exception as e:
        log.error("Unexpected error calling Haiku: %s", e)
        return None


# ---------------------------------------------------------------------------
# Main labeling function
# ---------------------------------------------------------------------------

def label_clusters(
    person_name: str,
    person_id: str,
    clusters: list,
    global_traits: dict,
    comms_conn: sqlite3.Connection,
) -> dict | None:
    """Label clusters with communication mode names via Claude Haiku.

    For each cluster, selects diversity-sampled exemplars, builds a prompt
    with cluster signatures and samples, and asks Haiku to name the modes
    and write a prose summary.

    Args:
        person_name: Display name of the person.
        person_id: Person ID (for logging).
        clusters: List of Cluster objects from extractor.cluster_messages().
        global_traits: Dict from extractor.extract_global_traits().
        comms_conn: Open connection to comms.db.

    Returns:
        Dict with 'modes' (list of mode dicts) and 'prose_summary' (str),
        or None on failure.
    """
    if not clusters:
        log.info("No clusters to label for %s", person_id)
        return None

    # Build cluster data with samples
    clusters_with_samples = []
    exemplar_map: dict[int, list[str]] = {}  # cluster_index -> exemplar IDs

    for i, cluster in enumerate(clusters):
        exemplar_ids = _select_exemplars(cluster, comms_conn, n=5)
        exemplar_map[i] = exemplar_ids

        texts = _fetch_message_texts(exemplar_ids, comms_conn)
        samples = [content[:120] for _, content in texts]

        # If we got no samples from DB, try using a few random from cluster
        if not samples and cluster.message_ids:
            sample_ids = random.sample(
                cluster.message_ids, min(3, len(cluster.message_ids))
            )
            texts = _fetch_message_texts(sample_ids, comms_conn)
            samples = [content[:120] for _, content in texts]

        clusters_with_samples.append({
            "index": i,
            "signature": cluster.signature,
            "samples": samples,
        })

    prompt = _build_labeling_prompt(person_name, clusters_with_samples, global_traits)
    result = _call_haiku(prompt)

    if result is None:
        log.warning("Haiku returned no result for %s", person_id)
        return None

    # Validate and build output
    raw_modes = result.get("modes", [])
    prose_summary = result.get("prose_summary", "")

    modes = []
    for raw_mode in raw_modes:
        name = raw_mode.get("name", "").lower().strip()
        if name not in VALID_MODES:
            log.warning(
                "Haiku returned invalid mode '%s' for %s, skipping", name, person_id
            )
            continue

        cluster_idx = raw_mode.get("cluster_index", 0)
        if cluster_idx < 0 or cluster_idx >= len(clusters):
            cluster_idx = 0

        cluster = clusters[cluster_idx]
        topic_correlations = raw_mode.get("topic_correlations", [])

        # Weight from cluster size relative to total messages
        total_msgs = sum(len(c.vectors) for c in clusters)
        weight = len(cluster.vectors) / total_msgs if total_msgs > 0 else 0.0

        modes.append({
            "name": name,
            "weight": weight,
            "signature": cluster.signature,
            "exemplar_ids": exemplar_map.get(cluster_idx, []),
            "topic_correlations": topic_correlations,
        })

    if not modes:
        log.warning("No valid modes produced for %s", person_id)
        return None

    return {
        "modes": modes,
        "prose_summary": prose_summary,
    }
