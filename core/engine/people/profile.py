"""People profile compiler — turns the people-DB schema into readable artifacts.

For any person_id, this module produces a compiled `Profile` dict that
aggregates everything the system knows about that person from:

  * `people` (canonical_name, importance, display_name, first/last)
  * `aliases` (every alternate name we've seen)
  * `person_identifiers` (phones, emails, WA JIDs, iMessage handles)
  * `contact_metadata` (birthday, city, organization, notes, ...)
  * `relationships` (direct family / colleague / friend edges + 1-hop)
  * `relationship_state` (last interaction, avg gap, trajectory)
  * `person_classification` (auto tier + LLM context tags)
  * `signal_store` (per-source extracted signals from Phase 1-5 adapters)
  * `comms.db.messages` (live message-level aggregates: counts, recency,
                         time-of-day, channel breakdown)
  * `comms.db.conversations` (conversation count, last conversation)

Two outputs from one compile call:

  1. A row in `profile_versions` (JSON blob, versioned per person)
  2. A markdown file at `~/vault/knowledge/people/<slug>.md`
     (operator-readable, follows vault frontmatter contract)

Design principles:
  * Pure SQL aggregation. No LLM. No inference. Facts only.
  * Cross-DB via ATTACH (one connection, two databases).
  * Idempotent — re-runs replace the markdown file and bump version.
  * Universal — works for any operator, any locale, any data shape.
  * Never crashes on missing tables (graceful degradation per source).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
VAULT_PEOPLE_DIR = Path.home() / "vault" / "knowledge" / "people"

PROFILE_VERSION = 1

# Family role labels we care about for the operator's tree
_FAMILY_SUBTYPES = {
    "spouse", "parent", "child", "sibling", "uncle", "aunt",
    "cousin", "grandparent", "grandchild", "in-law",
    "brother-in-law", "sister-in-law", "father-in-law", "mother-in-law",
}


# ── Connection helpers ──────────────────────────────────────────────────


def open_combined(
    people_db: Path = PEOPLE_DB,
    comms_db: Path = COMMS_DB,
) -> sqlite3.Connection:
    """Open people.db and ATTACH comms.db so cross-database queries work.

    The comms data is referenced as `c.<table>` (e.g. `c.messages`).
    Caller is responsible for closing the connection.
    """
    if not people_db.exists():
        raise FileNotFoundError(f"people.db not found at {people_db}")
    conn = sqlite3.connect(str(people_db))
    conn.row_factory = sqlite3.Row
    if comms_db.exists():
        conn.execute(f"ATTACH DATABASE '{comms_db}' AS c")
    return conn


def _has_table(conn: sqlite3.Connection, table: str, schema: str = "main") -> bool:
    return (
        conn.execute(
            f"SELECT COUNT(*) FROM {schema}.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()[0] > 0
    )


def _has_attached(conn: sqlite3.Connection, schema: str) -> bool:
    rows = conn.execute("PRAGMA database_list").fetchall()
    return any(r[1] == schema for r in rows)


# ── Source readers (each can fail-soft) ─────────────────────────────────


def _read_basics(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, canonical_name, display_name, first_name, last_name, "
        "       nickname, importance, is_self, is_archived, "
        "       created_at, updated_at "
        "FROM people WHERE id = ?",
        (person_id,),
    ).fetchone()
    if not row:
        return {}
    return dict(row)


def _read_aliases(conn: sqlite3.Connection, person_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT alias, type, priority FROM aliases WHERE person_id = ? "
        "ORDER BY priority DESC, alias",
        (person_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _read_identifiers(conn: sqlite3.Connection, person_id: str) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT type, value, normalized FROM person_identifiers "
        "WHERE person_id = ? ORDER BY type, normalized",
        (person_id,),
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for r in rows:
        bucket = grouped.setdefault(r["type"], [])
        v = r["normalized"] or r["value"]
        if v and v not in bucket:
            bucket.append(v)
    return grouped


def _read_metadata(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    if not _has_table(conn, "contact_metadata"):
        return {}
    row = conn.execute(
        "SELECT * FROM contact_metadata WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    if not row:
        return {}
    out = {k: row[k] for k in row.keys() if row[k] is not None}
    out.pop("person_id", None)
    return out


def _read_classification(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    if not _has_table(conn, "person_classification"):
        return {}
    row = conn.execute(
        "SELECT tier, model, run_id, created_at FROM person_classification WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    return dict(row) if row else {}


def _read_relationships(conn: sqlite3.Connection, person_id: str) -> list[dict[str, Any]]:
    """Direct edges (this person ↔ others). Returns list of dicts with the
    other person's name pre-joined for readability."""
    if not _has_table(conn, "relationships"):
        return []
    rows = conn.execute(
        """
        SELECT
            CASE WHEN r.person_a_id = ? THEN r.person_b_id ELSE r.person_a_id END AS other_id,
            CASE WHEN r.person_a_id = ? THEN 'outbound' ELSE 'inbound' END AS direction,
            r.type, r.subtype, r.strength, r.context, r.source, r.since
        FROM relationships r
        WHERE r.person_a_id = ? OR r.person_b_id = ?
        """,
        (person_id, person_id, person_id, person_id),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        other_row = conn.execute(
            "SELECT canonical_name FROM people WHERE id = ?",
            (d["other_id"],),
        ).fetchone()
        d["other_name"] = other_row["canonical_name"] if other_row else "(unknown)"
        out.append(d)
    return out


def _read_relationship_state(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    if not _has_table(conn, "relationship_state"):
        return {}
    row = conn.execute(
        "SELECT * FROM relationship_state WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    if not row:
        return {}
    out = {k: row[k] for k in row.keys() if row[k] is not None}
    out.pop("person_id", None)
    return out


def _read_signal_store(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    if not _has_table(conn, "signal_store"):
        return {}
    rows = conn.execute(
        "SELECT source_name, signals_json, extracted_at "
        "FROM signal_store WHERE person_id = ? ORDER BY source_name",
        (person_id,),
    ).fetchall()
    out: dict[str, Any] = {"sources": [], "extracted_at": None, "by_source": {}}
    for r in rows:
        out["sources"].append(r["source_name"])
        if out["extracted_at"] is None or r["extracted_at"] > out["extracted_at"]:
            out["extracted_at"] = r["extracted_at"]
        try:
            out["by_source"][r["source_name"]] = json.loads(r["signals_json"])
        except json.JSONDecodeError:
            pass
    return out


def _read_comms_messages(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    """Aggregate per-channel / per-direction stats from comms.db.messages."""
    if not _has_attached(conn, "c") or not _has_table(conn, "messages", schema="c"):
        return {}

    by_channel = conn.execute(
        """
        SELECT channel, direction, COUNT(*) AS n,
               MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts,
               SUM(LENGTH(content)) AS chars
        FROM c.messages
        WHERE person_id = ?
        GROUP BY channel, direction
        ORDER BY channel, direction
        """,
        (person_id,),
    ).fetchall()

    if not by_channel:
        return {}

    breakdown: dict[str, dict[str, Any]] = {}
    total_in = total_out = 0
    earliest = None
    latest = None
    for row in by_channel:
        ch = row["channel"]
        d = breakdown.setdefault(ch, {"inbound": 0, "outbound": 0, "first": None, "last": None})
        d[row["direction"]] = row["n"]
        if row["direction"] == "inbound":
            total_in += row["n"]
        else:
            total_out += row["n"]
        if d["first"] is None or row["first_ts"] < d["first"]:
            d["first"] = row["first_ts"]
        if d["last"] is None or row["last_ts"] > d["last"]:
            d["last"] = row["last_ts"]
        if earliest is None or row["first_ts"] < earliest:
            earliest = row["first_ts"]
        if latest is None or row["last_ts"] > latest:
            latest = row["last_ts"]

    # Recency windows (UNIX → ISO compare via datetime parse)
    now = datetime.now(timezone.utc)
    cutoffs = {
        "7d": (now.timestamp() - 7 * 86400),
        "30d": (now.timestamp() - 30 * 86400),
        "90d": (now.timestamp() - 90 * 86400),
        "365d": (now.timestamp() - 365 * 86400),
    }
    recency: dict[str, int] = {}
    for label, cutoff_ts in cutoffs.items():
        cutoff_iso = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()
        n = conn.execute(
            "SELECT COUNT(*) FROM c.messages WHERE person_id = ? AND timestamp >= ?",
            (person_id, cutoff_iso),
        ).fetchone()[0]
        recency[label] = n

    # Conversation count
    conv_count = 0
    if _has_table(conn, "conversations", schema="c"):
        conv_count = conn.execute(
            "SELECT COUNT(*) FROM c.conversations WHERE person_id = ?",
            (person_id,),
        ).fetchone()[0]

    return {
        "total_inbound": total_in,
        "total_outbound": total_out,
        "total_messages": total_in + total_out,
        "first_message_at": earliest,
        "last_message_at": latest,
        "channels": breakdown,
        "recency": recency,
        "conversation_count": conv_count,
    }


def _read_interactions(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    if not _has_table(conn, "interactions"):
        return {}
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS n,
            COUNT(DISTINCT channel) AS distinct_channels,
            MIN(occurred_at) AS first_at,
            MAX(occurred_at) AS last_at,
            SUM(CASE WHEN summary IS NOT NULL THEN 1 ELSE 0 END) AS enriched
        FROM interactions WHERE person_id = ?
        """,
        (person_id,),
    ).fetchone()
    if not row or not row["n"]:
        return {}
    return dict(row)


# ── Compile ─────────────────────────────────────────────────────────────


@dataclass
class Profile:
    person_id: str
    version: int
    generated_at: int
    basics: dict[str, Any] = field(default_factory=dict)
    aliases: list[dict[str, Any]] = field(default_factory=list)
    identifiers: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    relationship_state: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    comms: dict[str, Any] = field(default_factory=dict)
    interactions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "version": self.version,
            "generated_at": self.generated_at,
            "basics": self.basics,
            "aliases": self.aliases,
            "identifiers": self.identifiers,
            "metadata": self.metadata,
            "classification": self.classification,
            "relationships": self.relationships,
            "relationship_state": self.relationship_state,
            "signals": self.signals,
            "comms": self.comms,
            "interactions": self.interactions,
        }


def compile_profile(
    person_id: str,
    conn: sqlite3.Connection | None = None,
) -> Profile | None:
    """Compile every fact the system knows about a person.

    Pure read-only. Returns None if the person doesn't exist.
    """
    own_conn = False
    if conn is None:
        conn = open_combined()
        own_conn = True

    try:
        basics = _read_basics(conn, person_id)
        if not basics:
            return None

        return Profile(
            person_id=person_id,
            version=PROFILE_VERSION,
            generated_at=int(time.time()),
            basics=basics,
            aliases=_read_aliases(conn, person_id),
            identifiers=_read_identifiers(conn, person_id),
            metadata=_read_metadata(conn, person_id),
            classification=_read_classification(conn, person_id),
            relationships=_read_relationships(conn, person_id),
            relationship_state=_read_relationship_state(conn, person_id),
            signals=_read_signal_store(conn, person_id),
            comms=_read_comms_messages(conn, person_id),
            interactions=_read_interactions(conn, person_id),
        )
    finally:
        if own_conn:
            conn.close()


# ── Markdown rendering ──────────────────────────────────────────────────


_SLUG_RE = re.compile(r"[^\w\-]+")


def slug_for(profile: Profile) -> str:
    """Filesystem-safe slug for the profile's vault path."""
    name = profile.basics.get("canonical_name") or profile.person_id
    s = name.lower().replace(" ", "-")
    s = _SLUG_RE.sub("", s)
    return s or profile.person_id


def _fmt_ts(ts: int | str | None) -> str:
    if ts is None:
        return "—"
    if isinstance(ts, int):
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            return str(ts)
    return str(ts)[:10]


def _fmt_iso_date(s: str | None) -> str:
    if not s:
        return "—"
    return s[:10]


def render_markdown(profile: Profile) -> str:
    """Render the profile to a vault-ready markdown document."""
    b = profile.basics
    name = b.get("canonical_name", profile.person_id)
    importance = b.get("importance")
    display = b.get("display_name") or name
    auto_tier = profile.classification.get("tier") or "unclassified"

    lines: list[str] = []

    # Frontmatter
    lines.append("---")
    lines.append(f'title: "{name}"')
    lines.append("type: person")
    lines.append(f"person_id: {profile.person_id}")
    if importance is not None:
        lines.append(f"importance: {importance}")
    lines.append(f"auto_tier: {auto_tier}")
    if b.get("is_self"):
        lines.append("is_self: true")
    lines.append(f"generated: {datetime.fromtimestamp(profile.generated_at).isoformat(timespec='seconds')}")
    lines.append(f"generator_version: {profile.version}")
    if profile.signals.get("sources"):
        lines.append(f"sources: [{', '.join(profile.signals['sources'])}]")
    lines.append("tags:")
    lines.append("  - person")
    lines.append("  - profile")
    if importance == 1:
        lines.append("  - inner-circle")
    if b.get("is_self"):
        lines.append("  - self")
    lines.append("---")
    lines.append("")

    lines.append(f"# {display}")
    lines.append("")

    if name != display:
        lines.append(f"*Canonical: {name}*")
        lines.append("")

    # Identity block
    id_lines: list[str] = []
    if profile.aliases:
        alias_strs = sorted({a["alias"] for a in profile.aliases if a.get("alias")})
        if alias_strs:
            id_lines.append(f"- **Aliases**: {', '.join(alias_strs)}")
    if profile.metadata.get("birthday"):
        bday = profile.metadata["birthday"]
        if bday.startswith("0000-"):
            bday = bday[5:]  # MM-DD
        id_lines.append(f"- **Birthday**: {bday}")
    if profile.metadata.get("city"):
        id_lines.append(f"- **City**: {profile.metadata['city']}")
    if profile.metadata.get("country"):
        id_lines.append(f"- **Country**: {profile.metadata['country']}")
    if profile.metadata.get("organization"):
        id_lines.append(f"- **Organization**: {profile.metadata['organization']}")
    if profile.metadata.get("job_title"):
        id_lines.append(f"- **Title**: {profile.metadata['job_title']}")
    if profile.metadata.get("how_met"):
        id_lines.append(f"- **How met**: {profile.metadata['how_met']}")
    if id_lines:
        lines.append("## Identity")
        lines.extend(id_lines)
        lines.append("")

    # Identifiers
    if profile.identifiers:
        lines.append("## Reach")
        for kind in ("phone", "email", "wa_jid", "im_handle", "telegram", "slack"):
            vals = profile.identifiers.get(kind, [])
            if vals:
                shown = ", ".join(vals[:8])
                if len(vals) > 8:
                    shown += f" (+{len(vals) - 8} more)"
                label = {"wa_jid": "WhatsApp", "im_handle": "iMessage"}.get(kind, kind.title())
                lines.append(f"- **{label}**: {shown}")
        lines.append("")

    # Family / relationships
    if profile.relationships:
        lines.append("## Relationships")
        for r in profile.relationships:
            label = r.get("subtype") or r.get("type", "related")
            ctx = r.get("context")
            line = f"- {label}: **{r['other_name']}** (`{r['other_id']}`)"
            if ctx and ctx != f"{label.title()} — {r['other_name']}":
                line += f" — {ctx}"
            lines.append(line)
        lines.append("")

    # Conversation profile (live message-level — comms.db)
    if profile.comms:
        c = profile.comms
        lines.append("## Conversation profile")
        lines.append(f"- **Total messages**: {c['total_messages']:,} "
                     f"(in: {c['total_inbound']:,} / out: {c['total_outbound']:,})")
        if c.get("first_message_at"):
            lines.append(f"- **First contact**: {_fmt_iso_date(c['first_message_at'])}")
        if c.get("last_message_at"):
            lines.append(f"- **Last contact**: {_fmt_iso_date(c['last_message_at'])}")
        if c.get("conversation_count"):
            lines.append(f"- **Conversations**: {c['conversation_count']}")
        recency = c.get("recency", {})
        if recency:
            lines.append("- **Recency**: "
                         f"7d={recency.get('7d', 0)} / "
                         f"30d={recency.get('30d', 0)} / "
                         f"90d={recency.get('90d', 0)} / "
                         f"365d={recency.get('365d', 0)}")
        lines.append("")
        if c.get("channels"):
            lines.append("### Channels")
            lines.append("| Channel | Inbound | Outbound | First | Last |")
            lines.append("|---------|---------|----------|-------|------|")
            for ch in sorted(c["channels"].keys()):
                d = c["channels"][ch]
                lines.append(
                    f"| {ch} | {d.get('inbound', 0):,} | {d.get('outbound', 0):,} | "
                    f"{_fmt_iso_date(d.get('first'))} | {_fmt_iso_date(d.get('last'))} |"
                )
            lines.append("")

    # Relationship state (the rolling stats from people.db.relationship_state)
    if profile.relationship_state:
        rs = profile.relationship_state
        lines.append("## Relationship state")
        if rs.get("trajectory"):
            lines.append(f"- **Trajectory**: {rs['trajectory']}")
        if rs.get("avg_days_between") is not None:
            lines.append(f"- **Avg gap**: {round(float(rs['avg_days_between']), 1)} days")
        if rs.get("days_since_contact") is not None:
            lines.append(f"- **Days since last**: {rs['days_since_contact']}")
        if rs.get("interaction_count_30d") is not None:
            lines.append(f"- **30d interactions**: {rs['interaction_count_30d']}")
        if rs.get("recent_topics"):
            lines.append(f"- **Recent topics**: `{rs['recent_topics']}`")
        lines.append("")

    # Communication style — pull from any signal that has it
    style_block: list[str] = []
    apple_msg = profile.signals.get("by_source", {}).get("apple_messages", {})
    apple_comms = (apple_msg.get("communication") or [{}])[0] if apple_msg else {}
    if apple_comms.get("avg_message_length") is not None:
        style_block.append(f"- **Avg message length**: {round(apple_comms['avg_message_length'])} chars")
    if apple_comms.get("response_latency_median") is not None:
        med = apple_comms["response_latency_median"]
        if med is not None:
            style_block.append(f"- **Median response latency**: {round(med, 1)} min")
    if apple_comms.get("late_night_pct") is not None:
        style_block.append(f"- **Late-night ratio**: {round(apple_comms['late_night_pct'] * 100)}%")
    if apple_comms.get("evening_pct") is not None:
        style_block.append(f"- **Evening ratio**: {round(apple_comms['evening_pct'] * 100)}%")
    if apple_comms.get("business_hours_pct") is not None:
        style_block.append(f"- **Business hours ratio**: {round(apple_comms['business_hours_pct'] * 100)}%")
    if style_block:
        lines.append("## Communication style")
        lines.extend(style_block)
        lines.append("")

    # Source coverage
    if profile.signals.get("sources"):
        lines.append("## Sources")
        lines.append(", ".join(profile.signals["sources"]))
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Auto-generated by `core/engine/people/profile.py` v{profile.version}*")
    lines.append(f"*Last updated: {datetime.fromtimestamp(profile.generated_at).isoformat(timespec='seconds')}*")
    return "\n".join(lines) + "\n"


# ── Persistence ─────────────────────────────────────────────────────────


def _ensure_profile_versions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_versions (
            id              TEXT PRIMARY KEY,
            person_id       TEXT NOT NULL,
            version         INTEGER NOT NULL,
            generated_at    INTEGER NOT NULL,
            model           TEXT,
            trigger         TEXT,
            profile_json    TEXT NOT NULL,
            vault_path      TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_versions ON profile_versions(person_id, version DESC)"
    )


def persist(
    profile: Profile,
    conn: sqlite3.Connection,
    vault_dir: Path = VAULT_PEOPLE_DIR,
    write_vault: bool = True,
    trigger: str = "manual",
) -> str | None:
    """Write the profile to profile_versions and (optionally) to vault.

    Returns the vault path written to, or None if vault write was skipped.
    """
    _ensure_profile_versions_table(conn)

    vault_path: Path | None = None
    if write_vault:
        vault_dir.mkdir(parents=True, exist_ok=True)
        slug = slug_for(profile)
        vault_path = vault_dir / f"{slug}.md"
        vault_path.write_text(render_markdown(profile))

    import secrets, string
    chars = string.ascii_lowercase + string.digits
    profile_id = "pv_" + "".join(secrets.choice(chars) for _ in range(8))

    conn.execute(
        """
        INSERT INTO profile_versions
            (id, person_id, version, generated_at, model, trigger, profile_json, vault_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            profile.person_id,
            profile.version,
            profile.generated_at,
            "deterministic",
            trigger,
            json.dumps(profile.to_dict(), default=str),
            str(vault_path) if vault_path else None,
        ),
    )
    conn.commit()
    return str(vault_path) if vault_path else None


# ── Bulk compile ────────────────────────────────────────────────────────


def compile_all(
    conn: sqlite3.Connection | None = None,
    *,
    only_tiers: list[str] | None = None,
    only_importance_at_most: int | None = None,
    only_person_ids: list[str] | None = None,
    write_vault: bool = True,
    trigger: str = "bulk",
) -> dict[str, int]:
    """Compile profiles for many people in one pass.

    Filters (any combination):
      only_tiers: e.g. ['core', 'active', 'emerging']
      only_importance_at_most: e.g. 2 → importance 1 OR 2
      only_person_ids: explicit list
    """
    own_conn = False
    if conn is None:
        conn = open_combined()
        own_conn = True

    try:
        clauses = ["p.is_archived = 0"]
        params: list[Any] = []
        if only_tiers and _has_table(conn, "person_classification"):
            placeholders = ",".join("?" * len(only_tiers))
            clauses.append(f"pc.tier IN ({placeholders})")
            params.extend(only_tiers)
        if only_importance_at_most is not None:
            clauses.append("p.importance <= ?")
            params.append(only_importance_at_most)
        if only_person_ids:
            placeholders = ",".join("?" * len(only_person_ids))
            clauses.append(f"p.id IN ({placeholders})")
            params.extend(only_person_ids)
        sql = (
            "SELECT DISTINCT p.id FROM people p "
            "LEFT JOIN person_classification pc ON pc.person_id = p.id "
            f"WHERE {' AND '.join(clauses)}"
        )
        rows = conn.execute(sql, tuple(params)).fetchall()

        counts = {"compiled": 0, "skipped": 0, "errors": 0}
        for row in rows:
            try:
                profile = compile_profile(row["id"], conn=conn)
                if profile is None:
                    counts["skipped"] += 1
                    continue
                persist(profile, conn, write_vault=write_vault, trigger=trigger)
                counts["compiled"] += 1
            except Exception as e:
                logger.exception("compile failed for %s: %s", row["id"], e)
                counts["errors"] += 1
        return counts
    finally:
        if own_conn:
            conn.close()
