"""Relationship Graph Pipeline.

Builds a NetworkX graph from people + interactions + group co-membership,
runs Louvain community detection at multiple resolutions, and persists
detected communities as circles.
"""

from __future__ import annotations

import sqlite3
import string
import time
from collections import defaultdict
from pathlib import Path
from random import choices
from typing import Any

import networkx as nx
from networkx.algorithms.community import louvain_communities

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path.home() / ".aos" / "data" / "people.db"

_ID_CHARS = string.ascii_lowercase + string.digits

# Louvain resolution levels: low (larger communities) to high (smaller)
_RESOLUTIONS = [0.5, 1.0, 2.0]

# Edge weight components
_W_COMMUNICATION = 0.50
_W_CO_MEMBERSHIP = 0.35
_W_METADATA_SIM = 0.15

# Minimum edge weight to include (filter noise)
_MIN_EDGE_WEIGHT = 0.05

# Keywords that signal religious community groups
_RELIGIOUS_KEYWORDS = {"masjid", "quran", "islamic", "mosque", "halaqa", "deen", "sunnah", "hadith"}


def _gen_id(prefix: str) -> str:
    return prefix + "_" + "".join(choices(_ID_CHARS, k=8))


def _now() -> int:
    return int(time.time())


def _connect(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    if conn is not None:
        return conn
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0] > 0


# ---------------------------------------------------------------------------
# GraphPipeline
# ---------------------------------------------------------------------------


class GraphPipeline:
    """Build and analyze the social graph from people data."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self._conn = _connect(conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # ------------------------------------------------------------------
    # Graph Construction
    # ------------------------------------------------------------------

    def build_graph(self) -> nx.Graph:
        """Build a weighted undirected graph from people, interactions, and groups.

        Nodes: all non-archived people with at least 1 interaction.
        Edge weight = 0.50 * communication + 0.35 * co-membership + 0.15 * metadata_similarity

        Returns a NetworkX Graph with node attributes (name, org, city, last_name)
        and edge weight attributes.
        """
        G = nx.Graph()

        # --- Get eligible people (non-archived, with interactions) ---
        people = self.conn.execute("""
            SELECT DISTINCT p.id, p.canonical_name, p.last_name
            FROM people p
            WHERE p.is_archived = 0
              AND EXISTS (SELECT 1 FROM interactions i WHERE i.person_id = p.id)
        """).fetchall()

        if not people:
            return G

        # Build person metadata lookup
        person_ids = [p["id"] for p in people]
        pid_set = set(person_ids)

        # Load metadata for similarity comparison
        metadata: dict[str, dict[str, Any]] = {}
        for pid in person_ids:
            metadata[pid] = {"org": None, "city": None, "last_name": None}

        for p in people:
            if p["id"] in metadata:
                metadata[p["id"]]["last_name"] = p["last_name"]

        meta_rows = self.conn.execute(
            "SELECT person_id, organization, city FROM contact_metadata"
        ).fetchall()
        for row in meta_rows:
            if row["person_id"] in metadata:
                metadata[row["person_id"]]["org"] = row["organization"]
                metadata[row["person_id"]]["city"] = row["city"]

        # Add nodes
        for pid in person_ids:
            name = None
            for p in people:
                if p["id"] == pid:
                    name = p["canonical_name"]
                    break
            G.add_node(pid, name=name, **metadata.get(pid, {}))

        # --- Communication score: interaction counts between pairs ---
        # Build per-person interaction summaries, then compute pair scores
        # via the interactions table
        comm_scores: dict[tuple[str, str], float] = defaultdict(float)

        # Get all interactions with msg_count
        interactions = self.conn.execute(
            "SELECT person_id, occurred_at, msg_count FROM interactions ORDER BY occurred_at DESC"
        ).fetchall()

        # Build per-person total interaction weight for normalization
        person_total: dict[str, int] = defaultdict(int)
        for ix in interactions:
            if ix["person_id"] in pid_set:
                person_total[ix["person_id"]] += (ix["msg_count"] or 1)

        # For pair communication: use relationship_state msg_count_30d as proxy
        # or explicit relationships table for connected pairs
        placeholders = ",".join("?" * len(person_ids))
        rel_rows = self.conn.execute(
            f"""
            SELECT person_a_id, person_b_id, strength
            FROM relationships
            WHERE person_a_id IN ({placeholders}) AND person_b_id IN ({placeholders})
            """,
            person_ids + person_ids,
        ).fetchall()

        for rel in rel_rows:
            a, b = rel["person_a_id"], rel["person_b_id"]
            if a in pid_set and b in pid_set:
                pair = tuple(sorted([a, b]))
                comm_scores[pair] = max(
                    comm_scores[pair],
                    rel["strength"] or 0.0,
                )

        # --- Group co-membership score ---
        co_membership: dict[tuple[str, str], int] = defaultdict(int)

        # Get all groups and their resolved members
        groups = self.conn.execute(
            "SELECT group_id, person_id FROM group_members WHERE person_id IS NOT NULL AND active = 1"
        ).fetchall()

        group_members: dict[str, list[str]] = defaultdict(list)
        for gm in groups:
            if gm["person_id"] in pid_set:
                group_members[gm["group_id"]].append(gm["person_id"])

        for gid, members in group_members.items():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    pair = tuple(sorted([members[i], members[j]]))
                    co_membership[pair] += 1

        # Normalize co-membership to 0-1 range
        max_co = max(co_membership.values()) if co_membership else 1
        co_norm = {k: v / max_co for k, v in co_membership.items()}

        # --- Metadata similarity ---
        # Collect all pairs we've seen
        all_pairs = set(comm_scores.keys()) | set(co_membership.keys())

        # Also add pairs from people in the same org or city
        org_groups: dict[str, list[str]] = defaultdict(list)
        city_groups: dict[str, list[str]] = defaultdict(list)
        for pid, meta in metadata.items():
            if meta["org"]:
                org_groups[meta["org"].lower().strip()].append(pid)
            if meta["city"]:
                city_groups[meta["city"].lower().strip()].append(pid)

        for group in list(org_groups.values()) + list(city_groups.values()):
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pair = tuple(sorted([group[i], group[j]]))
                    all_pairs.add(pair)

        # --- Compute final edge weights ---
        for pair in all_pairs:
            a, b = pair
            if a not in pid_set or b not in pid_set:
                continue

            comm = comm_scores.get(pair, 0.0)
            co_mem = co_norm.get(pair, 0.0)

            # Metadata similarity: same org or same city
            meta_sim = 0.0
            meta_a = metadata.get(a, {})
            meta_b = metadata.get(b, {})
            if meta_a.get("org") and meta_b.get("org"):
                if meta_a["org"].lower().strip() == meta_b["org"].lower().strip():
                    meta_sim += 0.5
            if meta_a.get("city") and meta_b.get("city"):
                if meta_a["city"].lower().strip() == meta_b["city"].lower().strip():
                    meta_sim += 0.5

            weight = (
                _W_COMMUNICATION * comm
                + _W_CO_MEMBERSHIP * co_mem
                + _W_METADATA_SIM * meta_sim
            )

            if weight >= _MIN_EDGE_WEIGHT:
                G.add_edge(a, b, weight=weight)

        return G

    # ------------------------------------------------------------------
    # Community Detection
    # ------------------------------------------------------------------

    def detect_communities(self, G: nx.Graph) -> list[dict[str, Any]]:
        """Run Louvain community detection at multiple resolutions.

        Returns a list of community dicts with:
        - members: list of person_ids
        - category: inferred category (family, work, religious, community, friends)
        - resolution: the Louvain resolution parameter used
        - size: number of members
        """
        if G.number_of_nodes() == 0:
            return []

        all_communities: list[dict[str, Any]] = []

        for resolution in _RESOLUTIONS:
            try:
                communities = louvain_communities(G, resolution=resolution, seed=42)
            except Exception:
                continue

            for community_set in communities:
                members = list(community_set)
                if len(members) < 2:
                    continue  # Skip singletons

                category = self._classify_community(G, members)

                all_communities.append({
                    "members": members,
                    "category": category,
                    "resolution": resolution,
                    "size": len(members),
                })

        return all_communities

    def _classify_community(self, G: nx.Graph, members: list[str]) -> str:
        """Classify a community by majority-vote heuristics."""
        if not members:
            return "friends"

        total = len(members)

        # Count family relationships and shared last names
        family_signals = 0
        for pid in members:
            last_name = G.nodes[pid].get("last_name")
            if last_name:
                same_ln = sum(
                    1 for m in members
                    if m != pid and G.nodes[m].get("last_name") == last_name
                )
                if same_ln > 0:
                    family_signals += 1

        # Check for explicit family relationships in DB
        if len(members) >= 2:
            placeholders = ",".join("?" * len(members))
            family_rels = self.conn.execute(
                f"""
                SELECT COUNT(*) as cnt FROM relationships
                WHERE type = 'family'
                  AND person_a_id IN ({placeholders})
                  AND person_b_id IN ({placeholders})
                """,
                members + members,
            ).fetchone()
            if family_rels and family_rels["cnt"] > 0:
                family_signals += family_rels["cnt"]

        if family_signals > total * 0.5:
            return "family"

        # Count shared organizations
        org_counts: dict[str, int] = defaultdict(int)
        for pid in members:
            org = G.nodes[pid].get("org")
            if org:
                org_counts[org.lower().strip()] += 1

        if org_counts:
            max_org_count = max(org_counts.values())
            if max_org_count > total * 0.5:
                return "work"

        # Check if community is derived from a religious WA group
        # Look at which groups these members share
        if len(members) >= 2:
            placeholders = ",".join("?" * len(members))
            shared_groups = self.conn.execute(
                f"""
                SELECT DISTINCT g.name
                FROM group_members gm
                JOIN groups g ON gm.group_id = g.id
                WHERE gm.person_id IN ({placeholders})
                  AND gm.active = 1
                """,
                members,
            ).fetchall()

            for grp in shared_groups:
                if grp["name"]:
                    name_lower = grp["name"].lower()
                    if any(kw in name_lower for kw in _RELIGIOUS_KEYWORDS):
                        return "religious"

            # If derived from WA group at all
            if shared_groups:
                return "community"

        return "friends"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist_communities(self, communities: list[dict[str, Any]]) -> int:
        """Write detected communities to circle + circle_membership tables.

        Clears previous graph-detected circles (source='graph_detection')
        before writing new ones.

        Returns count of circles persisted.
        """
        if not _has_table(self.conn, "circle") or not _has_table(self.conn, "circle_membership"):
            return 0

        now = _now()

        # Clear previous graph-detected circles
        old_circles = self.conn.execute(
            "SELECT id FROM circle WHERE source = 'graph_detection'"
        ).fetchall()
        old_ids = [c["id"] for c in old_circles]

        if old_ids:
            placeholders = ",".join("?" * len(old_ids))
            self.conn.execute(
                f"DELETE FROM circle_membership WHERE circle_id IN ({placeholders})",
                old_ids,
            )
            self.conn.execute(
                f"DELETE FROM circle WHERE id IN ({placeholders})",
                old_ids,
            )

        count = 0
        for comm in communities:
            circle_id = _gen_id("ci")

            # Generate a name from the category and resolution
            res_label = {0.5: "broad", 1.0: "mid", 2.0: "tight"}.get(
                comm["resolution"], "mid"
            )
            name = f"{comm['category'].title()} ({res_label}, {comm['size']} members)"

            self.conn.execute(
                """
                INSERT INTO circle (id, name, category, source, confidence, resolution, created_at)
                VALUES (?, ?, ?, 'graph_detection', ?, ?, ?)
                """,
                (circle_id, name, comm["category"], 0.8, comm["resolution"], now),
            )

            for pid in comm["members"]:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO circle_membership
                        (person_id, circle_id, confidence, added_at, source)
                    VALUES (?, ?, 0.8, ?, 'graph_detection')
                    """,
                    (pid, circle_id, now),
                )

            count += 1

        self.conn.commit()
        return count

    # ------------------------------------------------------------------
    # Full Pipeline
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Full pipeline: build graph, detect communities, persist results.

        Returns statistics dict.
        """
        G = self.build_graph()

        stats: dict[str, Any] = {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "communities": 0,
            "circles_persisted": 0,
            "resolutions": {},
        }

        if G.number_of_nodes() == 0:
            return stats

        communities = self.detect_communities(G)
        stats["communities"] = len(communities)

        # Break down by resolution
        for comm in communities:
            r = str(comm["resolution"])
            if r not in stats["resolutions"]:
                stats["resolutions"][r] = {"count": 0, "categories": defaultdict(int)}
            stats["resolutions"][r]["count"] += 1
            stats["resolutions"][r]["categories"][comm["category"]] += 1

        # Convert defaultdicts to regular dicts for serialization
        for r in stats["resolutions"]:
            stats["resolutions"][r]["categories"] = dict(
                stats["resolutions"][r]["categories"]
            )

        circles_persisted = self.persist_communities(communities)
        stats["circles_persisted"] = circles_persisted

        # Infer relationships from available signals
        rel_stats = self.infer_relationships()
        stats["relationships_inferred"] = rel_stats

        return stats

    # ── Relationship inference ──────────────────────────────────────

    _FAMILY_TITLES = {
        "mama": ("family", "parent"),
        "baba": ("family", "parent"),
        "aunty": ("family", "aunt"),
        "uncle": ("family", "uncle"),
        "phuppo": ("family", "aunt"),
        "chachi": ("family", "aunt"),
        "chachi": ("family", "aunt"),
        "taya": ("family", "uncle"),
        "nana": ("family", "grandparent"),
        "nani": ("family", "grandparent"),
        "dada": ("family", "grandparent"),
        "dadi": ("family", "grandparent"),
    }

    def infer_relationships(self) -> dict[str, int]:
        """Infer relationship edges from available signals.

        Sources:
        1. Relational names (Mama, Aunty, Uncle, Baba) → family
        2. Shared surnames within the operator's family cluster → family
        3. Apple Contacts "related names" metadata → family/friend
        4. High communication + evening/personal hours → friend
        5. Business hours + professional channels → colleague

        Only creates relationships that don't already exist.
        Returns {family: N, friend: N, colleague: N, total: N}.
        """
        now = _now()
        stats = {"family": 0, "friend": 0, "colleague": 0, "community": 0, "total": 0}

        # Get operator self ID
        self_row = self.conn.execute(
            "SELECT id FROM people WHERE is_self = 1"
        ).fetchone()
        self_id = self_row["id"] if self_row else None
        if not self_id:
            return stats

        # Get existing relationships to avoid duplicates
        existing = set()
        for r in self.conn.execute("SELECT person_a_id, person_b_id, type FROM relationships").fetchall():
            existing.add((r["person_a_id"], r["person_b_id"], r["type"]))
            existing.add((r["person_b_id"], r["person_a_id"], r["type"]))

        def _add_rel(person_id: str, rel_type: str, subtype: str, source: str, strength: float = 0.5, context: str = ""):
            if (self_id, person_id, rel_type) in existing:
                return False
            try:
                self.conn.execute(
                    """INSERT OR IGNORE INTO relationships
                       (person_a_id, person_b_id, type, subtype, strength, source, context, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self_id, person_id, rel_type, subtype, strength, source, context, now, now),
                )
                existing.add((self_id, person_id, rel_type))
                return True
            except Exception:
                return False

        # --- 1. Relational names ---
        people = self.conn.execute(
            "SELECT id, canonical_name, first_name, last_name FROM people WHERE is_archived = 0 AND is_self = 0"
        ).fetchall()

        for p in people:
            name_lower = (p["canonical_name"] or "").lower()
            for title, (rel_type, subtype) in self._FAMILY_TITLES.items():
                if title in name_lower.split():
                    if _add_rel(p["id"], rel_type, subtype, "name_inference",
                                strength=0.8, context=f"name contains '{title}'"):
                        stats["family"] += 1
                    break

        # --- 2. Shared surname with known family ---
        # Get operator's surname(s) from self record and parents
        family_surnames = set()
        op = self.conn.execute("SELECT last_name FROM people WHERE is_self = 1").fetchone()
        if op and op["last_name"]:
            family_surnames.add(op["last_name"].lower())

        # Add surnames from known family relationships
        for r in self.conn.execute(
            "SELECT p.last_name FROM relationships rel "
            "JOIN people p ON p.id = rel.person_b_id "
            "WHERE rel.person_a_id = ? AND rel.type = 'family' AND p.last_name IS NOT NULL",
            (self_id,)
        ).fetchall():
            if r["last_name"]:
                family_surnames.add(r["last_name"].lower())

        # Also add the surname of any person saved under a parent title
        # (mama/baba, per _FAMILY_TITLES) — a household commonly saves a
        # parent's contact card under a relational label rather than their
        # given name, so their surname wouldn't otherwise surface here.
        parent_titles = [t for t, (_, subtype) in self._FAMILY_TITLES.items() if subtype == "parent"]
        if parent_titles:
            placeholders = ",".join("?" for _ in parent_titles)
            for r in self.conn.execute(
                f"SELECT last_name FROM people WHERE lower(canonical_name) IN ({placeholders}) AND is_archived = 0",
                parent_titles,
            ).fetchall():
                if r["last_name"]:
                    family_surnames.add(r["last_name"].lower())

        # People sharing a family surname who aren't already related
        if family_surnames:
            for p in people:
                if not p["last_name"]:
                    continue
                if p["last_name"].lower() in family_surnames:
                    if _add_rel(p["id"], "family", "extended",
                                "surname_inference", strength=0.4,
                                context=f"shares surname '{p['last_name']}'"):
                        stats["family"] += 1

        # --- 3. Core/active tier with high reciprocity → friend ---
        core_active = self.conn.execute("""
            SELECT pc.person_id, pc.tier FROM person_classification pc
            JOIN people p ON pc.person_id = p.id
            WHERE p.is_archived = 0 AND p.is_self = 0
              AND pc.tier IN ('core', 'active')
        """).fetchall()

        for row in core_active:
            pid = row["person_id"]
            # Skip if already has a relationship
            if any((self_id, pid, t) in existing for t in ("family", "friend", "colleague")):
                continue
            if _add_rel(pid, "friend", None, "communication_pattern",
                        strength=0.6, context=f"tier={row['tier']}"):
                stats["friend"] += 1

        self.conn.commit()
        stats["total"] = stats["family"] + stats["friend"] + stats["colleague"] + stats["community"]
        return stats
