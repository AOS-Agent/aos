"""Per-person ambient snapshots — precomputed nightly, read as files by the hook.

Phase 5, mention-triggered context. The UserPromptSubmit hook must add a
person's mini-profile with a <100ms budget and NO DB or model calls. So the
expensive part — resolving people and querying comms.db/people.db — runs once
nightly (after enrichment) and is frozen to small JSON files the hook simply
reads:

    ~/.aos/cache/ambient/persons/<person_id>.json   one snapshot per person
    ~/.aos/cache/ambient/names.json                 {name_or_alias -> person_id}

A snapshot holds: last-interaction date, open commitments in BOTH directions
with that person, their unanswered questions to the operator, and recent
topics/mentions. The names index maps lowercased names and aliases to
person_id for O(1) hook lookup.

PRIVACY (locked decision #2, reused from recall): snapshots are built ONLY for
contacts with privacy_level < PRIVATE_THRESHOLD. A restricted contact simply
has no snapshot and no names-index entry, so the hook can never surface them —
fail-closed by construction. Self is skipped. Read-only over both DBs.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.engine.comms.recall import PRIVATE_THRESHOLD  # noqa: E402

COMMS_DB = Path.home() / ".aos" / "data" / "comms.db"
PEOPLE_DB = Path.home() / ".aos" / "data" / "people.db"
CACHE_DIR = Path.home() / ".aos" / "cache" / "ambient"

SURFACE_MIN = 0.80
RECENT_TOPIC_DAYS = 30
_MAX_COMMITMENTS = 4
_MAX_QUESTIONS = 3
_MAX_TOPICS = 5


def _comms_ro(comms_db: Path, people_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{comms_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    if people_db.exists():
        conn.execute("ATTACH DATABASE ? AS people", (f"file:{people_db}?mode=ro",))
    return conn


def _fields(row) -> dict:
    try:
        return json.loads(row["fields_json"] or "{}")
    except Exception:
        return {}


def _eligible_people(people_db: Path) -> list[dict]:
    """People to snapshot: not self, not archived, privacy_level < threshold."""
    if not people_db.exists():
        return []
    conn = sqlite3.connect(f"file:{people_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        rows = conn.execute(
            "SELECT id, canonical_name, first_name, last_name, display_name, "
            "COALESCE(privacy_level,1) AS pl, COALESCE(importance,3) AS importance "
            "FROM people "
            "WHERE COALESCE(is_archived,0)=0 AND COALESCE(is_self,0)=0 "
            "AND COALESCE(privacy_level,1) < ?",
            (PRIVATE_THRESHOLD,)).fetchall()
        people = [dict(r) for r in rows]
        alias_map: dict[str, list[str]] = {}
        try:
            for a in conn.execute("SELECT person_id, alias FROM aliases"):
                alias_map.setdefault(a["person_id"], []).append(a["alias"])
        except Exception:
            pass
        for p in people:
            p["aliases"] = alias_map.get(p["id"], [])
        return people
    finally:
        conn.close()


def _person_snapshot(conn, person_id: str, surface_min=SURFACE_MIN) -> dict:
    """Build one person's snapshot from comms.db entities + messages."""
    def rows(sql, params):
        try:
            return conn.execute(sql, params).fetchall()
        except Exception:
            return []

    base = ("SELECT e.entity_type, e.value, e.fields_json, m.direction AS direction, "
            "m.timestamp AS ts FROM message_entities e "
            "JOIN messages m ON m.id = json_extract(e.source_ids,'$[0]') "
            "WHERE e.person_id = ? AND e.status='active' ")

    owed_by, owed_to = [], []
    for r in rows(base + "AND e.entity_type='commitment' AND e.confidence >= ? "
                  "ORDER BY m.timestamp DESC", (person_id, surface_min)):
        f = _fields(r)
        item = {"what": f.get("what") or r["value"], "due": f.get("due"), "ts": r["ts"]}
        if r["direction"] == "outbound" and len(owed_by) < _MAX_COMMITMENTS:
            owed_by.append(item)
        elif r["direction"] == "inbound" and len(owed_to) < _MAX_COMMITMENTS:
            owed_to.append(item)

    questions = []
    for r in rows(base + "AND e.entity_type='question_open' AND e.confidence >= ? "
                  "AND m.direction='inbound' ORDER BY m.timestamp DESC LIMIT ?",
                  (person_id, surface_min, _MAX_QUESTIONS)):
        f = _fields(r)
        questions.append({"q": f.get("value") or r["value"], "ts": r["ts"]})

    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - RECENT_TOPIC_DAYS * 86400))
    topics = []
    for r in rows(base + "AND e.entity_type='topic' AND substr(m.timestamp,1,10) >= ? "
                  "ORDER BY m.timestamp DESC LIMIT ?", (person_id, since, _MAX_TOPICS)):
        f = _fields(r)
        v = f.get("value") or r["value"]
        if v and v not in topics:
            topics.append(v)

    last = rows("SELECT MAX(timestamp) AS t FROM messages WHERE person_id = ?", (person_id,))
    last_at = last[0]["t"] if last else None

    return {"person_id": person_id, "last_interaction": last_at,
            "owed_by_you": owed_by, "owed_to_you": owed_to,
            "unanswered_questions": questions, "recent_topics": topics}


def _index_names(people: list[dict]) -> dict[str, str]:
    """Build {lowercased name/alias -> person_id}. Ambiguous single tokens are
    dropped (dupes would risk injecting the wrong person); full names and
    aliases are kept. Higher-importance person wins a full-name tie."""
    single_hits: dict[str, set[str]] = {}
    full: dict[str, tuple[int, str]] = {}

    def add_full(key: str, pid: str, importance: int):
        key = key.strip().lower()
        if not key:
            return
        prev = full.get(key)
        # importance: 1=highest. Lower number wins.
        if prev is None or importance < prev[0]:
            full[key] = (importance, pid)

    def add_single(tok: str, pid: str):
        tok = tok.strip().lower()
        if len(tok) < 3:
            return
        single_hits.setdefault(tok, set()).add(pid)

    for p in people:
        pid = p["id"]
        imp = int(p.get("importance") or 3)
        names = [p.get("canonical_name"), p.get("display_name"),
                 " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)]
        for nm in names:
            if nm and nm.strip():
                add_full(nm, pid, imp)
        for al in p.get("aliases", []):
            if al and al.strip():
                add_full(al, pid, imp)
        # single tokens for ambiguity tracking
        for nm in names + list(p.get("aliases", [])):
            for tok in (nm or "").split():
                add_single(tok, pid)

    index: dict[str, str] = {k: v[1] for k, v in full.items()}
    # Add unambiguous single tokens that aren't already a full-name key.
    for tok, pids in single_hits.items():
        if len(pids) == 1:
            index.setdefault(tok, next(iter(pids)))
    return index


def build_all_snapshots(comms_db: Path = COMMS_DB, people_db: Path = PEOPLE_DB,
                        cache_dir: Path = CACHE_DIR) -> dict:
    """Rebuild every eligible person's snapshot + the names index. Nightly.

    Returns a small stats dict. Read-only over both DBs; writes only to the
    cache dir. Atomic-ish: writes names.json last so a half-built persons dir is
    never referenced by a stale index.
    """
    persons_dir = cache_dir / "persons"
    persons_dir.mkdir(parents=True, exist_ok=True)

    people = _eligible_people(people_db)
    if not comms_db.exists():
        return {"eligible": len(people), "snapshots": 0, "names": 0,
                "note": "comms.db absent"}

    conn = _comms_ro(comms_db, people_db)
    written = 0
    try:
        for p in people:
            try:
                snap = _person_snapshot(conn, p["id"])
                snap["name"] = (p.get("canonical_name") or p.get("display_name")
                                or p.get("first_name") or "")
                (persons_dir / f"{p['id']}.json").write_text(
                    json.dumps(snap, ensure_ascii=False))
                written += 1
            except Exception:
                continue
    finally:
        conn.close()

    index = _index_names(people)
    (cache_dir / "names.json").write_text(json.dumps(index, ensure_ascii=False))
    (cache_dir / ".built_at").write_text(str(int(time.time())))
    return {"eligible": len(people), "snapshots": written, "names": len(index)}


# ── hook-side read helpers (file reads only — no DB, no model) ──────────────

def load_names_index(cache_dir: Path = CACHE_DIR) -> dict[str, str]:
    try:
        return json.loads((cache_dir / "names.json").read_text())
    except Exception:
        return {}


def load_snapshot(person_id: str, cache_dir: Path = CACHE_DIR) -> dict | None:
    try:
        return json.loads((cache_dir / "persons" / f"{person_id}.json").read_text())
    except Exception:
        return None


if __name__ == "__main__":
    print(json.dumps(build_all_snapshots(), indent=2))
