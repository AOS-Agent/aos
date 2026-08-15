#!/usr/bin/env python3
"""
AOS Work Context Injection Hook

Runs on SessionStart and PostCompact.
Reads work.yaml, finds active/due tasks and threads, outputs additionalContext JSON.

Also writes a .session-context.json file that session_close.py reads
to know which tasks were in scope during this session.

Claude Code hooks protocol:
- Read hook input from stdin (JSON with session info)
- Output JSON to stdout with optional additionalContext field
- MUST output valid JSON and exit 0 — any failure kills the session start
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _safe_exit(context: str = ""):
    """Always output valid JSON and exit clean. Never let this hook fail."""
    if context:
        print(json.dumps({"additionalContext": context}))
    else:
        print(json.dumps({}))
    sys.exit(0)


def _check_onboarding():
    """If fresh install, tell Chief to run onboarding."""
    onboarding_file = Path.home() / ".aos" / "config" / "onboarding.yaml"
    if not onboarding_file.exists():
        return "**ONBOARDING REQUIRED**: This is a fresh install. You MUST load the onboard skill (`~/.claude/skills/onboard/SKILL.md`) and run the onboarding flow NOW before doing anything else. Read the skill file and follow its protocol."
    return None


# ── Safe imports — if anything fails, exit clean ──────────────────────────

try:
    import yaml
except ImportError:
    _safe_exit(_check_onboarding() or "")

try:
    import glob as globmod
    import urllib.request
    from datetime import date
except Exception:
    _safe_exit(_check_onboarding() or "")

DASHBOARD_URL = "http://127.0.0.1:4096"

# people.db path — overridable for tests
PEOPLE_DB_PATH = Path.home() / ".aos" / "data" / "people.db"


def _build_people_section(db_path: Path | None = None, limit: int = 20) -> str:
    """Build the "Today's Relevant People" markdown section.

    Reads people.db directly (sqlite3) and returns up to `limit` currently
    relevant people ranked by signal density. Graceful fail: any error
    (missing file, missing tables, bad JSON) returns an empty string. This
    function is called from a session-start hook — it must NEVER crash.

    Selection:
      - person_classification.tier IN ('core', 'active', 'emerging')
      - people.is_archived = 0
      - Skips persons with no detectable last_interaction_date across signals
      - Ranked by density score (best-effort from signals_json)

    Format per entry:
      **Name** — tier[, tag1, tag2], last seen Nd ago, channel1, channel2[, +N more]
    """
    import sqlite3

    path = Path(db_path) if db_path else PEOPLE_DB_PATH
    try:
        if not path.exists():
            return ""
    except Exception:
        return ""

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        try:
            conn = sqlite3.connect(str(path))
        except Exception:
            return ""

    try:
        conn.row_factory = sqlite3.Row
        # Verify required tables exist
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except Exception:
            return ""
        if not {"people", "person_classification"}.issubset(tables):
            return ""
        has_signals = "signal_store" in tables

        try:
            if has_signals:
                rows = conn.execute(
                    """
                    SELECT
                      p.id AS person_id,
                      p.display_name AS display_name,
                      p.canonical_name AS canonical_name,
                      p.first_name AS first_name,
                      p.last_name AS last_name,
                      pc.tier AS tier,
                      pc.context_tags_json AS context_tags_json,
                      ss.source_name AS source_name,
                      ss.signals_json AS signals_json
                    FROM people p
                    INNER JOIN person_classification pc ON pc.person_id = p.id
                    LEFT JOIN signal_store ss ON ss.person_id = p.id
                    WHERE p.is_archived = 0
                      AND pc.tier IN ('core', 'active', 'emerging')
                    ORDER BY
                      CASE pc.tier
                        WHEN 'core' THEN 0
                        WHEN 'active' THEN 1
                        WHEN 'emerging' THEN 2
                        ELSE 3
                      END ASC,
                      pc.created_at DESC
                    LIMIT 500
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT
                      p.id AS person_id,
                      p.display_name AS display_name,
                      p.canonical_name AS canonical_name,
                      p.first_name AS first_name,
                      p.last_name AS last_name,
                      pc.tier AS tier,
                      pc.context_tags_json AS context_tags_json,
                      NULL AS source_name,
                      NULL AS signals_json
                    FROM people p
                    INNER JOIN person_classification pc ON pc.person_id = p.id
                    WHERE p.is_archived = 0
                      AND pc.tier IN ('core', 'active', 'emerging')
                    ORDER BY
                      CASE pc.tier
                        WHEN 'core' THEN 0
                        WHEN 'active' THEN 1
                        WHEN 'emerging' THEN 2
                        ELSE 3
                      END ASC,
                      pc.created_at DESC
                    LIMIT 500
                    """
                ).fetchall()
        except Exception:
            return ""
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Aggregate per person across signal_store source rows
    people_map: dict = {}
    for row in rows:
        pid = row["person_id"]
        if not pid:
            continue
        rec = people_map.get(pid)
        if rec is None:
            # Pick best name
            name = (
                row["display_name"]
                or row["canonical_name"]
                or " ".join(
                    x for x in (row["first_name"], row["last_name"]) if x
                ).strip()
                or "Unknown"
            )
            # Parse context tags
            tags: list = []
            try:
                raw_tags = row["context_tags_json"]
                if raw_tags:
                    parsed = json.loads(raw_tags)
                    if isinstance(parsed, list):
                        tags = [str(t) for t in parsed if t]
            except Exception:
                tags = []
            rec = {
                "name": name,
                "tier": row["tier"],
                "tags": tags,
                "channels": [],
                "channel_msg_counts": {},
                "last_seen": None,  # datetime
                "density": 0.0,
                "total_messages": 0,
                "total_calls": 0,
                "total_photos": 0,
            }
            people_map[pid] = rec

        signals_json = row["signals_json"]
        if not signals_json:
            continue
        try:
            signals = json.loads(signals_json)
        except Exception:
            continue
        if not isinstance(signals, dict):
            continue

        # CommunicationSignals: list of {channel, total_messages, last_message_date, ...}
        comms = signals.get("communication") or []
        if isinstance(comms, list):
            for c in comms:
                if not isinstance(c, dict):
                    continue
                ch = c.get("channel")
                tm = c.get("total_messages") or 0
                try:
                    tm = int(tm)
                except Exception:
                    tm = 0
                if ch:
                    prev = rec["channel_msg_counts"].get(ch, 0)
                    rec["channel_msg_counts"][ch] = prev + tm
                rec["total_messages"] += tm
                lmd = c.get("last_message_date")
                _update_last_seen(rec, lmd)

        # VoiceSignal
        voice = signals.get("voice") or []
        if isinstance(voice, list):
            for v in voice:
                if not isinstance(v, dict):
                    continue
                try:
                    rec["total_calls"] += int(v.get("total_calls") or 0)
                except Exception:
                    pass
                if (v.get("total_calls") or 0) and "phone" not in rec["channel_msg_counts"]:
                    rec["channel_msg_counts"]["phone"] = int(v.get("total_calls") or 0)
                _update_last_seen(rec, v.get("last_call_date"))

        # PhysicalPresence
        phys = signals.get("physical_presence") or []
        if isinstance(phys, list):
            for p in phys:
                if not isinstance(p, dict):
                    continue
                try:
                    rec["total_photos"] += int(p.get("total_photos") or 0)
                except Exception:
                    pass
                if (p.get("total_photos") or 0) and "photos" not in rec["channel_msg_counts"]:
                    rec["channel_msg_counts"]["photos"] = int(p.get("total_photos") or 0)
                _update_last_seen(rec, p.get("last_photo_date"))

        # Professional (email)
        prof = signals.get("professional") or []
        if isinstance(prof, list):
            for pr in prof:
                if not isinstance(pr, dict):
                    continue
                te = pr.get("total_emails") or 0
                try:
                    te = int(te)
                except Exception:
                    te = 0
                if te and "email" not in rec["channel_msg_counts"]:
                    rec["channel_msg_counts"]["email"] = te
                _update_last_seen(rec, pr.get("last_date"))

    # Filter: only keep people with a detectable last_seen
    now = _now_utc_naive()
    candidates = []
    for pid, rec in people_map.items():
        if rec["last_seen"] is None:
            continue
        try:
            days = max(0, int((now - rec["last_seen"]).total_seconds() // 86400))
        except Exception:
            continue
        rec["days_since"] = days
        # Density: simple composite
        rec["density"] = (
            rec["total_messages"]
            + rec["total_calls"] * 3
            + rec["total_photos"] * 2
            + len(rec["channel_msg_counts"]) * 10
        )
        # Tier rank for stable sort
        tier_rank = {"core": 0, "active": 1, "emerging": 2}.get(rec["tier"], 3)
        rec["sort_key"] = (tier_rank, -rec["density"], days)
        candidates.append(rec)

    if not candidates:
        return ""

    candidates.sort(key=lambda r: r["sort_key"])
    top = candidates[:limit]

    # Format
    out_lines = ["## Today's Relevant People", ""]
    out_lines.append(
        f"Currently in your active circle (top {len(top)} by signal density):"
    )
    out_lines.append("")
    for rec in top:
        # Sort channels by message count desc
        sorted_channels = sorted(
            rec["channel_msg_counts"].items(), key=lambda kv: -kv[1]
        )
        channel_names = [c for c, _ in sorted_channels]
        shown_channels = channel_names[:3]
        extra_n = len(channel_names) - len(shown_channels)
        channel_part = ", ".join(shown_channels)
        if extra_n > 0:
            channel_part = (
                channel_part + f", +{extra_n} more" if channel_part else f"+{extra_n} more"
            )

        top_tags = rec["tags"][:2]
        tag_part = ", ".join(top_tags)

        segments = [rec["tier"]]
        if tag_part:
            segments.append(tag_part)
        segments.append(f"last seen {rec['days_since']}d ago")
        if channel_part:
            segments.append(channel_part)

        out_lines.append(f"- **{rec['name']}** — " + ", ".join(segments))

    return "\n".join(out_lines)


def _now_utc_naive():
    """Return current UTC time as a naive datetime (for subtraction with parsed dates)."""
    from datetime import datetime as _dt
    return _dt.utcnow()


def _update_last_seen(rec: dict, iso_str) -> None:
    """Parse an ISO date string and update rec['last_seen'] if newer. Best-effort."""
    if not iso_str or not isinstance(iso_str, str):
        return
    from datetime import datetime as _dt
    try:
        # Normalize trailing Z
        s = iso_str.replace("Z", "+00:00")
        parsed = _dt.fromisoformat(s)
        # Strip tz for naive comparison
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
    except Exception:
        # Try date-only
        try:
            from datetime import datetime as _dt2
            parsed = _dt2.strptime(iso_str[:10], "%Y-%m-%d")
        except Exception:
            return
    if rec["last_seen"] is None or parsed > rec["last_seen"]:
        rec["last_seen"] = parsed

# Add ontology backend to path
_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)  # for query.py (still in this dir)
_work_dir = os.path.abspath(os.path.join(_this_dir, '..', '..', 'work'))
sys.path.insert(0, _work_dir)  # for backend.py

try:
    import backend as engine
    import query
except Exception:
    _safe_exit(_check_onboarding() or "")


def main():
    # Read hook input
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, Exception):
        hook_input = {}

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", os.getcwd())

    try:
        tasks = engine.get_all_tasks()
    except Exception:
        # Even when the work engine can't load (e.g. brand-new install with
        # no work.yaml), the onboarding banner must still reach the session —
        # this early exit used to swallow it.
        _safe_exit(_check_onboarding() or "")

    today = date.today().isoformat()

    active = query.active_tasks(tasks)
    due = query.due_today(tasks, today)
    todo_high = query.filter_tasks(
        query.filter_tasks(tasks, status="todo"),
        priority=1
    ) + query.filter_tasks(
        query.filter_tasks(tasks, status="todo"),
        priority=2
    )

    # Find tasks relevant to current working directory
    project_tasks = engine.find_tasks_by_project_or_cwd(cwd)
    project_active = [t for t in project_tasks if t.get("status") == "active"]

    # Find active thread for this directory
    current_thread = engine.find_thread_by_cwd(cwd)

    summary = engine.summary()
    inbox_count = summary["inbox"]
    summary["threads"]

    # Build context string
    lines = []

    # ── Budget-first rendering ──────────────────────────────────────────
    # The briefing has a hard byte budget: every AOS feature that appends a
    # section competes for space instead of accreting forever. Droppable
    # sections register their span + drop_rank (higher = dropped first);
    # if the assembled briefing exceeds the budget, whole sections are
    # removed and replaced by a pointer to the command that shows them.
    BRIEFING_BUDGET = 4000  # bytes
    _sections = []

    def _mark(name, start, rank, fallback=None):
        if len(lines) > start:
            _sections.append({
                "name": name, "start": start, "end": len(lines),
                "rank": rank, "fallback": fallback,
            })

    # Current thread (continuity)
    if current_thread:
        session_count = len(current_thread.get("sessions", []))
        lines.append(f"**Current thread**: {current_thread['id']} — {current_thread['title']} ({session_count} sessions)")
        if current_thread.get("notes"):
            # Show last note only
            last_note = current_thread["notes"].strip().split("\n\n")[-1]
            if len(last_note) > 200:
                last_note = last_note[:200] + "..."
            lines.append(f"  Last note: {last_note}")

    # Helper: subtask progress for a task
    def _subtask_info(parent_task, all_tasks):
        subs = [t for t in all_tasks if t.get("parent") == parent_task["id"]]
        if not subs:
            return ""
        done = sum(1 for s in subs if s.get("status") == "done")
        return f" ({done}/{len(subs)} parts done)"

    # Helper: initiative linkage
    def _initiative_info(t):
        ref = t.get("source_ref", "")
        if not ref or "initiatives/" not in ref:
            return ""
        slug = ref.split("initiatives/")[-1].replace(".md", "")
        return f" → initiative:{slug}"

    # Project-specific active tasks first (most relevant)
    if project_active:
        lines.append("**Active in this project:**")
        for t in project_active:
            sub = _subtask_info(t, tasks)
            init = _initiative_info(t)
            lines.append(f"- {t['id']}: {t['title']}{sub}{init}")

    # All active tasks — most recently touched first, capped. A 24-item list
    # of half-stale actives is noise; the tail is one command away.
    other_active = [t for t in active if t not in project_active]
    if other_active:
        def _recency(t):
            return t.get("updated") or t.get("created") or ""
        other_sorted = sorted(other_active, key=_recency, reverse=True)
        shown = other_sorted[:8]
        lines.append("**Active (other projects):**")
        for t in shown:
            proj = f" [{t['project']}]" if t.get("project") else ""
            sub = _subtask_info(t, tasks)
            init = _initiative_info(t)
            lines.append(f"- {t['id']}: {t['title']}{proj}{sub}{init}")
        if len(other_sorted) > len(shown):
            lines.append(
                f"  …+{len(other_sorted) - len(shown)} more active — `work list --status active`"
            )

    if due:
        lines.append("**Due today/overdue:**")
        for t in due:
            lines.append(f"- {t['id']}: {t['title']} (due {t['due']})")

    if todo_high:
        lines.append("**High priority (todo):**")
        for t in todo_high[:5]:
            lines.append(f"- {t['id']}: {t['title']}")

    if inbox_count > 0:
        lines.append(f"**Inbox:** {inbox_count} items awaiting triage")

    # Memory proposals from reconciler
    memory_proposals_file = Path.home() / ".aos" / "work" / "memory-proposals.yaml"
    if memory_proposals_file.exists():
        try:
            proposals = yaml.safe_load(memory_proposals_file.read_text()) or []
            if proposals:
                lines.append(f"**Memory proposals ({len(proposals)}):** Significant sessions detected that may need memory updates. Review with `cat ~/.aos/work/memory-proposals.yaml` and update MEMORY.md if needed.")
        except Exception:
            pass

    # --- Initiative Context ---
    # Scan active initiatives and inject state digests
    initiative_ids = []
    try:
        init_dir = os.path.join(str(Path.home()), "vault", "knowledge", "initiatives")
        if os.path.isdir(init_dir):
            init_files = sorted(
                globmod.glob(os.path.join(init_dir, "*.md")),
                key=os.path.getmtime, reverse=True
            )
            for fpath in init_files[:5]:  # cap at 5 most recent
                try:
                    with open(fpath) as f:
                        raw = f.read(3000)  # frontmatter + state digest
                    if not raw.startswith("---"):
                        continue
                    fm_end = raw.find("---", 3)
                    if fm_end == -1:
                        continue
                    fm = yaml.safe_load(raw[3:fm_end])
                    if not fm or fm.get("status") not in ("research", "shaping", "planning", "executing"):
                        continue
                    title = fm.get("title", os.path.basename(fpath))
                    slug = fm.get("slug", "")
                    initiative_ids.append({"title": title, "slug": slug, "status": fm["status"]})
                    # Extract state digest (between ```\n and \n```)
                    digest = f"Status: {fm['status']}"
                    digest_start = raw.find("## State Digest")
                    if digest_start != -1:
                        block = raw[digest_start:digest_start + 500]
                        code_start = block.find("```\n")
                        code_end = block.find("\n```", code_start + 4) if code_start != -1 else -1
                        if code_start != -1 and code_end != -1:
                            digest = block[code_start + 4:code_end].strip()
                    # Check blocked_by — if dependency is done, flag as unblocked
                    blocked_by = fm.get("blocked_by")
                    blocked_note = ""
                    if blocked_by:
                        # Check if the blocking initiative is done
                        blocker_done = False
                        for other in init_files:
                            try:
                                with open(other) as bf:
                                    braw = bf.read(500)
                                if not braw.startswith("---"):
                                    continue
                                bfm_end = braw.find("---", 3)
                                if bfm_end == -1:
                                    continue
                                bfm = yaml.safe_load(braw[3:bfm_end])
                                if bfm and bfm.get("slug") == blocked_by:
                                    if bfm.get("status") in ("done", "review"):
                                        blocker_done = True
                                    break
                            except Exception:
                                pass
                        if blocker_done:
                            blocked_note = f" 🔓 UNBLOCKED — {blocked_by} is complete. Ready to resume."
                        else:
                            blocked_note = f" ⏳ Blocked by: {blocked_by}"
                    lines.append(f"\n**Initiative: {title}**")
                    lines.append(digest + blocked_note)
                except Exception:
                    pass  # skip malformed files, never crash
    except Exception:
        pass  # never crash the hook

    # --- Unanswered Messages ---
    try:
        triage_file = Path.home() / ".aos" / "work" / "triage-state.json"
        if triage_file.exists():
            triage_state = json.loads(triage_file.read_text())
            unanswered_entries = list(triage_state.get("unanswered", {}).values())
            # Staleness filter: a message unanswered for months is not a
            # briefing item — injecting it daily just numbs the section.
            from datetime import datetime as _dtf
            from datetime import timedelta as _tdf

            def _fresh(entry, max_days=14):
                ts = entry.get("received_at", "")
                if not ts:
                    return False
                try:
                    msg_dt = _dtf.fromisoformat(ts.replace("Z", "+00:00"))
                    now_dt = _dtf.now(msg_dt.tzinfo) if msg_dt.tzinfo else _dtf.now()
                    return (now_dt - msg_dt) <= _tdf(days=max_days)
                except Exception:
                    return False

            unanswered_entries = [e for e in unanswered_entries if _fresh(e)]
            if unanswered_entries:
                # Sort oldest first
                unanswered_entries.sort(key=lambda e: e.get("received_at", ""))
                _s = len(lines)
                lines.append("**Unanswered messages (≤14d):**")
                for entry in unanswered_entries[:3]:
                    name = entry.get("person_name", "Unknown")
                    channel = entry.get("channel", "?")
                    preview = entry.get("text_preview", "")
                    # Compute relative time
                    ago = "recently"
                    try:
                        from datetime import datetime as _dt
                        ts = entry.get("received_at", "")
                        if ts:
                            msg_dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                            now_dt = _dt.now(msg_dt.tzinfo) if msg_dt.tzinfo else _dt.now()
                            delta_s = int((now_dt - msg_dt).total_seconds())
                            if delta_s < 60:
                                ago = "just now"
                            elif delta_s < 3600:
                                ago = f"{delta_s // 60}m ago"
                            elif delta_s < 86400:
                                ago = f"{delta_s // 3600}h ago"
                            else:
                                ago = f"{delta_s // 86400}d ago"
                    except Exception:
                        pass
                    lines.append(f"- {name} ({channel}, {ago}): {preview[:60]}")
                _mark("unanswered", _s, 3, "comms-recall search --since <date>")
    except Exception:
        pass  # Never crash the hook

    # --- Today's Relevant People ---
    # A short awareness list (top 6 by signal density). The full circle is one
    # query away in people.db — 20 entries with channel lists was dashboard
    # content, not briefing content.
    try:
        people_section = _build_people_section(limit=6)
        if people_section:
            _s = len(lines)
            lines.append("")
            lines.append(people_section)
            _mark("people", _s, 4, "people.db via resolver/profile CLI")
    except Exception:
        pass  # Never crash the hook

    # --- People nudges ---
    # Surface live nudges from intelligence_queue (birthday, drift, reconnect).
    # Reads from morning-context.yaml which is refreshed daily by the cron.
    try:
        mc_path = Path.home() / ".aos" / "work" / "morning-context.yaml"
        if mc_path.exists():
            mc = yaml.safe_load(mc_path.read_text()) or {}
            nudges_list = mc.get("people_today") or []
            if nudges_list:
                _s = len(lines)
                lines.append("")
                lines.append("**People nudges:**")
                for nudge in nudges_list[:3]:
                    if isinstance(nudge, dict):
                        prompt = nudge.get("prompt", "")
                        if prompt:
                            lines.append(f"- {prompt}")
                _mark("nudges", _s, 3, "cat ~/.aos/work/morning-context.yaml")
    except Exception:
        pass  # Never crash the hook

    # --- Ambient digest (comms-derived: knowing → doing) ---
    # A bounded (<=15 line) block distilled from the enriched entity store:
    # what the operator owes, what's owed to him, unanswered questions, recent
    # purchases, and top people nudges. Best-effort: any failure is swallowed so
    # the hook never blocks session start, and the whole thing is skipped if the
    # comms substrate isn't present yet.
    try:
        core_dir = str(Path.home() / "aos")
        if core_dir not in sys.path:
            sys.path.insert(0, core_dir)
        from core.engine.comms.ambient.digest import build_digest
        # surface_nudges=False: people nudges already have their own section
        # above — the ambient copy was a straight duplicate every session.
        ambient = build_digest(surface_nudges=False)
        if ambient:
            _s = len(lines)
            lines.append("")
            lines.append(ambient)
            _mark("ambient", _s, 3, "comms ambient digest CLI")
    except Exception:
        pass  # Never crash the hook

    # --- System Capabilities (pointer) ---
    # The full fallback-chain map (~1.5KB, 29 apps) is static reference, not
    # briefing content — it lives in ~/aos/config/capabilities.yaml merged
    # with integration manifests, and the autonomous-execution skill owns the
    # protocol. One line preserves the behavioral rule.
    lines.append("")
    lines.append(
        '**Capabilities:** before saying "I can\'t do X" or asking the operator '
        "to do something manually, load the `autonomous-execution` skill — full "
        "app fallback chains (cheapest first) in `~/aos/config/capabilities.yaml` "
        "+ integration manifests. Never stop at first failure."
    )

    # --- Handoff context ---
    handoff_tasks = [t for t in (project_active or active) if t.get("handoff")]
    if handoff_tasks:
        lines.append("**Handoff (pick up where last session left off):**")
        for t in handoff_tasks[:2]:
            h = t["handoff"]
            lines.append(f"- {t['id']}: {t['title']}")
            if h.get("next_step"):
                lines.append(f"  Next: {h['next_step'][:150]}")
            if h.get("blockers"):
                lines.append(f"  Blockers: {', '.join(h['blockers'][:2])[:150]}")
        if len(handoff_tasks) > 2:
            lines.append(
                f"  …+{len(handoff_tasks) - 2} more with handoffs — `work dispatch <id>`"
            )

    # --- Inbox preview ---
    try:
        inbox_items = engine.get_inbox()
        if inbox_items:
            _s = len(lines)
            lines.append(f"**Inbox ({len(inbox_items)} items):**")
            for item in inbox_items[:3]:
                text = item.get("text", str(item)) if isinstance(item, dict) else str(item)
                lines.append(f"- {text[:80]}")
            _mark("inbox-preview", _s, 4, "work inbox")
    except Exception:
        pass

    # --- Schedule awareness ---
    try:
        op_file = Path.home() / ".aos" / "config" / "operator.yaml"
        if op_file.exists():
            op = yaml.safe_load(op_file.read_text()) or {}
            blocks = op.get("schedule", {}).get("blocks", [])
            day_name = date.today().strftime("%a").lower()
            today_blocks = [b for b in blocks if day_name in b.get("days", [])]
            if today_blocks:
                lines.append("**Schedule today:**")
                for b in today_blocks:
                    lines.append(f"- {b['name']}: {b.get('start', '?')}–{b.get('end', '?')}")
    except Exception:
        pass

    # --- Suggested focus (the 10x piece — Chief never gathers, it reads) ---
    suggestions = []
    # 1. Handoff tasks = resume what was in progress
    if handoff_tasks:
        t = handoff_tasks[0]
        suggestions.append(f"Resume {t['id']}: {t['title']}")
    # 2. Due/overdue items
    if due:
        t = due[0]
        suggestions.append(f"Due: {t['id']}: {t['title']}")
    # 3. High-priority todo
    if todo_high and not any(t in handoff_tasks for t in todo_high):
        t = todo_high[0]
        suggestions.append(f"{t['id']}: {t['title']}")
    # 4. Stale initiatives
    for init_title in initiative_ids:
        # Already logged stale in initiative section above
        pass
    # 5. Initiative next action (from digest)
    for init_title in initiative_ids:
        # The digest is already in lines — Chief can read it
        pass

    if suggestions:
        lines.append("**Suggested focus:**")
        for i, s in enumerate(suggestions[:3], 1):
            lines.append(f"  {i}. {s}")

    # ── Stale live context recovery ──────────────────────
    # If .live-context.json exists from a previous session, that session
    # ended uncleanly (crash, terminal kill, SSH drop, machine sleep).
    # Recover: log what happened, clear it, tell the operator.
    stale_recovery = None
    try:
        live_ctx = engine.get_live_context()
        if live_ctx:
            old_session = live_ctx.get("session_id")
            # Stale if: different session, OR null session (pre-session-id era)
            if old_session != session_id:
                stale_task_id = live_ctx.get("task_id", "?")
                stale_title = live_ctx.get("title", "unknown task")
                stale_started = live_ctx.get("started_at", "?")

                # Log the unclean exit
                sessions_log = Path.home() / ".aos" / "logs" / "sessions.jsonl"
                sessions_log.parent.mkdir(parents=True, exist_ok=True)
                from datetime import datetime
                log_entry = {
                    "ts": datetime.now().isoformat(),
                    "session_id": old_session,
                    "event": "session_unclean_exit",
                    "active_task": stale_task_id,
                    "started_at": stale_started,
                    "recovered_by": session_id,
                }
                with open(sessions_log, "a") as f:
                    f.write(json.dumps(log_entry) + "\n")

                # Clear the stale context
                engine.clear_live_context()

                stale_recovery = (
                    f"**⚠ Recovered from unclean exit:** Previous session was working on "
                    f"**{stale_task_id}: {stale_title}** (started {stale_started}). "
                    f"Session ended without cleanup. Task remains active — pick it up with "
                    f"`work start {stale_task_id}` or check its status with `work show {stale_task_id}`."
                )
    except Exception:
        pass  # Never crash the hook

    # ── Budget enforcement ──────────────────────────────
    # Drop whole droppable sections (highest rank first, biggest first on
    # ties) until the briefing fits. Each dropped section leaves a pointer.
    dropped = []
    try:
        def _ctx_size():
            return len("\n".join(lines))

        while _ctx_size() > BRIEFING_BUDGET:
            candidates = sorted(
                (s for s in _sections if not s.get("dropped")),
                key=lambda s: (-s["rank"], -(s["end"] - s["start"])),
            )
            if not candidates:
                break
            victim = candidates[0]
            removed = victim["end"] - victim["start"]
            del lines[victim["start"]:victim["end"]]
            victim["dropped"] = True
            for other in _sections:
                if other.get("dropped"):
                    continue
                if other["start"] >= victim["end"]:
                    other["start"] -= removed
                    other["end"] -= removed
            label = victim["name"]
            if victim.get("fallback"):
                label += f" → `{victim['fallback']}`"
            dropped.append(label)
        if dropped:
            lines.append(f"(budget: trimmed {', '.join(dropped)})")
    except Exception:
        pass  # Budgeting must never break the briefing itself

    if stale_recovery:
        lines.insert(0, stale_recovery)

    # Onboarding trigger — MUST be inserted before the context string is
    # built (this previously happened after the join, so the banner was
    # silently dropped and fresh installs never saw it via the hook).
    onboarding_file = Path.home() / ".aos" / "config" / "onboarding.yaml"
    first_session_file = Path.home() / ".aos" / "config" / ".first-session-done"
    if not onboarding_file.exists():
        lines.insert(0, "**ONBOARDING REQUIRED**: This is a fresh install. You MUST load the onboard skill (`~/.claude/skills/onboard/SKILL.md`) and run the onboarding flow NOW before doing anything else. Read the skill file and follow its protocol.")
    elif not first_session_file.exists():
        lines.insert(0, "**FIRST SESSION AFTER ONBOARDING**: Read the 'Post-Onboarding: First Real Session' section in your agent definition and follow it. Verify Telegram, run morning briefing, remind about daily practice, check their first task.")

    if not lines:
        lines.append("No active tasks or urgent items.")

    context = "\n".join(lines)

    # Write session context file for session_close.py to read
    task_ids_in_scope = [t["id"] for t in project_active + active]
    context_file = Path.home() / ".aos" / "work" / ".session-context.json"
    try:
        context_file.parent.mkdir(parents=True, exist_ok=True)
        session_ctx = {
            "session_id": session_id,
            "task_ids": task_ids_in_scope,
            "cwd": cwd,
            "thread_id": current_thread["id"] if current_thread else None,
        }
        if initiative_ids:
            session_ctx["initiative_ids"] = initiative_ids
        context_file.write_text(json.dumps(session_ctx))
    except Exception:
        pass  # Non-fatal

    # Behavioral guidance
    guidance_lines = []
    guidance_lines.append("If this session involves substantial work: start a task, create subtasks as you go, write a handoff at the end, mark tasks done when complete.")
    if project_active:
        task_ids = ", ".join(t["id"] for t in project_active)
        guidance_lines.append(f"Active tasks in this project: {task_ids}")
    if due:
        guidance_lines.append("Overdue tasks exist — flag them to the operator if relevant.")

    guidance = "\n".join(guidance_lines)

    # Notify dashboard of session start (fire-and-forget)
    try:
        Path(cwd).name if cwd else "unknown"
        notify_data = json.dumps({
            "hook_type": "tool",
            "payload": {
                "session_id": session_id,
                "tool_name": "SessionStart",
                "tool_input": {},
                "cwd": cwd,
            }
        }).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/sessions/hook",
            data=notify_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass  # Dashboard may not be running

    output = {
        "additionalContext": f"[Work System]\n{context}\n---\n{guidance}"
    }

    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # This hook must NEVER fail — a crash here blocks session start
        _safe_exit()
