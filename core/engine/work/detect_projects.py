#!/usr/bin/env python3
"""
Auto-project detection — scans session history and directories
to find work that should be tracked as projects, AND matches
already-tracked projects to the repo that implements them.

Detection signals (new-project suggestions):
1. Claude session directories with 3+ sessions
2. Directories with CLAUDE.md files (explicit project markers)
3. Git repositories under ~/project/ or ~/
4. Active threads that should be promoted

Repo matching (existing projects -> repo path, see match_projects_to_repos):
1. Exact directory name == project id
2. Known alias table for non-derivable slugs (hre -> hre-prototype handled
   generically by prefix match; a few genuinely irregular ones are listed
   explicitly)
3. Normalized (lowercase, alnum-only) exact match against id or title
4. Normalized prefix match, when unambiguous
A directory only counts as a match if it contains a real `.git` — a repo
that merely exists but isn't a git repo (e.g. hre-prototype today) is
reported, never linked.

Output: list of suggested projects with evidence, or auto-create if configured.

Usage:
    python3 detect_projects.py                # Print new-project suggestions
    python3 detect_projects.py --apply        # Create missing projects in work.yaml
    python3 detect_projects.py --match-repos  # Dry-run: project -> repo path mapping
    python3 detect_projects.py --json         # JSON output for dashboard API

--match-repos is report-only. It never writes. Applying a proposed path is a
separate, explicit action (apply_path_matches) that a caller must invoke by
hand after a human has reviewed the mapping.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_work_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'work'))
sys.path.insert(0, _work_dir)

try:
    import backend as engine
except ImportError:
    print("Work engine not available")
    sys.exit(1)

HOME = Path.home()
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
PROJECT_ROOT = HOME / "project"
USERNAME = getpass.getuser()

# Slugs a generic normalize/prefix match can't derive on its own. Only add
# an entry here when the mapping genuinely isn't discoverable mechanically —
# most cases (hre -> hre-prototype, deenoverdunya -> deenoverdunya) are
# handled by _normalize() + prefix matching without needing a table entry.
KNOWN_ALIASES = {
    # quran-garden-ios already has project.path set correctly (it's the ios/
    # subdir of quran-tools) — kept here as documentation, not relied on.
    "quran-garden-ios": "quran-tools",
}


def _normalize(s: str) -> str:
    """Lowercase, alnum-only. Makes 'Deen Over Dunya' == 'deenoverdunya'."""
    return re.sub(r'[^a-z0-9]', '', (s or "").lower())


def _clean_project_name(dirname: str) -> str:
    """Convert Claude project dirname to readable name.

    -Users-<user>-myproject → myproject
    -Users-<user>-some-ios-app → some-ios-app
    -Users-<user>-Desktop-scratch-agent → scratch-agent
    """
    prefix = f"-Users-{USERNAME}-"
    name = dirname
    if name.startswith(prefix):
        name = name[len(prefix):]
    # Strip common parent dirs
    for strip in ["Desktop-", "Documents-", "project-"]:
        if name.startswith(strip):
            name = name[len(strip):]
    return name.lower() if name else "home"


def _dir_from_project_name(dirname: str) -> Path:
    """Convert Claude project dirname back to filesystem path."""
    # -Users-alice-myproject → /Users/alice/myproject
    path_str = dirname.replace("-", "/")
    if path_str.startswith("/"):
        return Path(path_str)
    return Path("/" + path_str)


def scan_claude_sessions() -> list[dict]:
    """Scan ~/.claude/projects/ for session activity."""
    if not CLAUDE_PROJECTS.exists():
        return []

    results = []
    for project_dir in sorted(CLAUDE_PROJECTS.iterdir()):
        if not project_dir.is_dir():
            continue

        sessions = list(project_dir.glob("*.jsonl"))
        if len(sessions) < 2:
            continue  # Not enough signal

        name = _clean_project_name(project_dir.name)
        real_dir = _dir_from_project_name(project_dir.name)

        # Get date range
        dates = []
        for s in sessions:
            try:
                stat = s.stat()
                dates.append(datetime.fromtimestamp(stat.st_mtime))
            except Exception:
                pass

        dates.sort()

        results.append({
            "name": name,
            "dirname": project_dir.name,
            "path": str(real_dir) if real_dir.exists() else None,
            "session_count": len(sessions),
            "first_session": dates[0].isoformat() if dates else None,
            "last_session": dates[-1].isoformat() if dates else None,
            "days_active": (dates[-1] - dates[0]).days + 1 if len(dates) >= 2 else 1,
        })

    results.sort(key=lambda x: x["session_count"], reverse=True)
    return results


def scan_project_dirs() -> list[dict]:
    """Find directories that look like projects (have CLAUDE.md or .git)."""
    results = []

    # Check known locations
    scan_dirs = [HOME]
    project_dir = HOME / "project"
    if project_dir.exists():
        scan_dirs.append(project_dir)

    seen = set()
    for parent in scan_dirs:
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in ("aos", "vault", "project", "Library", "Applications",
                              "Desktop", "Documents", "Downloads", "Movies", "Music",
                              "Pictures", "Public", "go", "OrbStack"):
                # aos is already tracked, skip system dirs
                if child.name != "aos":
                    continue
                # aos is known — don't suggest it
                continue

            has_claude = (child / "CLAUDE.md").exists()
            has_git = (child / ".git").exists()

            if has_claude or has_git:
                name = child.name
                if name not in seen:
                    seen.add(name)
                    results.append({
                        "name": name,
                        "path": str(child),
                        "has_claude_md": has_claude,
                        "has_git": has_git,
                    })

    return results


# ── Existing-project -> repo matching ───────────────────────────────

def list_repo_candidates() -> list[dict]:
    """Every directory directly under ~/project/, with a real-git-repo flag."""
    candidates = []
    if not PROJECT_ROOT.exists():
        return candidates
    for child in sorted(PROJECT_ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        candidates.append({
            "name": child.name,
            "path": str(child),
            "is_git": (child / ".git").exists(),
        })
    return candidates


def match_projects_to_repos() -> list[dict]:
    """Propose a repo path for every work-system project that lacks one.

    Report-only — never writes. One row per project:
    {project_id, title, current_path, candidate_path, confidence, reason,
    is_git}. confidence is one of:
      already-set  — project.path is already populated
      high         — exact (or alias/normalized-exact) match, real git repo
      medium       — unambiguous prefix match, real git repo
      reject       — a directory matched but it isn't a real git repo
      none         — no matching directory under ~/project/
    """
    data = engine.load_all()
    projects = data.get("projects", [])
    repos = list_repo_candidates()
    repos_by_name = {r["name"]: r for r in repos}
    norm_repos: dict[str, list[dict]] = {}
    for r in repos:
        norm_repos.setdefault(_normalize(r["name"]), []).append(r)

    def _row(p, candidate=None, confidence="none", reason="", is_git=False):
        return {
            "project_id": p["id"],
            "title": p.get("title", ""),
            "current_path": p.get("path"),
            "candidate_path": candidate,
            "confidence": confidence,
            "reason": reason,
            "is_git": is_git,
        }

    results = []
    for p in projects:
        pid = p["id"]
        title = p.get("title", "")
        current_path = p.get("path")

        if current_path:
            expanded = Path(current_path).expanduser()
            is_git = (expanded / ".git").exists()
            reason = "path already set"
            if not is_git:
                reason += " (WARNING: not a git repo)"
            results.append(_row(p, current_path, "already-set", reason, is_git))
            continue

        norm_pid = _normalize(pid)
        norm_title = _normalize(title)
        matched = False

        # 1. known alias table (irregular slugs that no generic rule derives)
        alias_name = KNOWN_ALIASES.get(pid)
        if alias_name and alias_name in repos_by_name:
            r = repos_by_name[alias_name]
            conf = "high" if r["is_git"] else "reject"
            results.append(_row(p, r["path"], conf,
                                 f"known alias '{pid}' -> '{alias_name}'", r["is_git"]))
            matched = True

        # 2. exact directory name == project id
        if not matched and pid in repos_by_name:
            r = repos_by_name[pid]
            conf = "high" if r["is_git"] else "reject"
            results.append(_row(p, r["path"], conf,
                                 "directory name == project id", r["is_git"]))
            matched = True

        # 3. normalized exact match against id or title (unambiguous only)
        if not matched:
            for norm_key, label in ((norm_pid, "project id"), (norm_title, "project title")):
                hits = norm_repos.get(norm_key, [])
                if len(hits) == 1:
                    r = hits[0]
                    conf = "high" if r["is_git"] else "reject"
                    results.append(_row(p, r["path"], conf,
                                         f"directory name matches {label} (normalized)",
                                         r["is_git"]))
                    matched = True
                    break

        # 4. normalized prefix match (hre -> hre-prototype), unambiguous only
        if not matched and len(norm_pid) >= 3:
            prefix_hits = [r for r in repos if _normalize(r["name"]).startswith(norm_pid)]
            if len(prefix_hits) == 1:
                r = prefix_hits[0]
                conf = "medium" if r["is_git"] else "reject"
                results.append(_row(p, r["path"], conf,
                                     f"directory '{r['name']}' starts with project id '{pid}'",
                                     r["is_git"]))
                matched = True

        if not matched:
            results.append(_row(p, reason="no matching directory under ~/project/"))

    # Flag collisions: two projects proposed for the same path is a sign of
    # a duplicate project record (see "dod" vs "deenoverdunya" — same repo,
    # same title, 93 tasks vs 3). Never auto-resolve; just surface it so a
    # human decides which project record is canonical.
    by_path: dict[str, list[dict]] = {}
    for m in results:
        if m["candidate_path"] and m["confidence"] in ("high", "medium"):
            by_path.setdefault(m["candidate_path"], []).append(m)
    for path, rows in by_path.items():
        if len(rows) > 1:
            ids = ", ".join(r["project_id"] for r in rows)
            for r in rows:
                r["confidence"] = "conflict"
                r["reason"] += f"  CONFLICT: also matched by {ids} — likely duplicate project records, needs a human decision before linking either"

    return results


def render_match_report(matches: list[dict]) -> str:
    """Plain-text dry-run report for operator review before anything is applied."""
    lines = ["  Project -> repo matches (dry run — nothing written):\n"]
    icons = {"already-set": "=", "high": "+", "medium": "~", "reject": "x", "none": "-", "conflict": "!"}
    for m in matches:
        icon = icons.get(m["confidence"], "?")
        lines.append(f"  {icon} {m['project_id']:<20} {m['title']}")
        if m["candidate_path"]:
            lines.append(f"      -> {m['candidate_path']}  [{m['confidence']}]"
                          + ("" if m["is_git"] else "  NOT A GIT REPO"))
        lines.append(f"      {m['reason']}")
        lines.append("")
    return "\n".join(lines)


def apply_path_matches(matches: list[dict]) -> int:
    """Write candidate_path -> project.path for high/medium-confidence, git-backed
    matches only. NOT called automatically anywhere in this module — a human
    must review render_match_report() output and invoke this explicitly.
    """
    applied = 0
    for m in matches:
        if m["confidence"] in ("high", "medium") and m["is_git"] and m["candidate_path"]:
            engine.update_project(m["project_id"], path=m["candidate_path"])
            applied += 1
    return applied


def get_existing_projects() -> dict[str, dict]:
    """Get projects already in work.yaml, indexed by ID and name."""
    data = engine.load_all()
    projects = {}
    for p in data.get("projects", []):
        projects[p["id"]] = p
        # Also index by common name patterns
        title_lower = p.get("title", "").lower()
        projects[title_lower] = p
    return projects


def detect() -> list[dict]:
    """Run full detection, return suggestions for new projects."""
    existing = get_existing_projects()
    # Normalized (lowercase, alnum-only) titles AND ids. Plain lowercase
    # substring matching used to miss things like "deenoverdunya" (dir name,
    # no spaces) against "Deen Over Dunya" (title, has spaces) — that gap is
    # exactly how the "deenoverdunya" project ended up as a duplicate of
    # "dod" in the live data. Normalizing both sides before comparing closes
    # it.
    existing_norm = {_normalize(p.get("title", "")) for p in existing.values()}
    existing_norm |= {_normalize(pid) for pid in existing.keys()}
    existing_norm.discard("")

    session_data = scan_claude_sessions()
    dir_data = scan_project_dirs()

    suggestions = []

    # Match session data with directory data
    dir_by_name = {}
    for d in dir_data:
        dir_by_name[d["name"]] = d
        dir_by_name[d["name"].lower()] = d

    for sess in session_data:
        name = sess["name"]

        # Skip if already tracked (normalized: "deenoverdunya" == "Deen Over Dunya")
        if _normalize(name) in existing_norm:
            continue
        # Skip worktree sessions
        if "worktree" in name or "bold-fox" in name:
            continue
        # Skip sub-project sessions (apps/content-engine, vendor/*, etc.)
        if "/" in name:
            continue
        # Skip old v1 names
        if "mac-mini-agent" in name:
            continue

        # Merge with directory info (case-insensitive)
        dir_info = dir_by_name.get(name, {}) or dir_by_name.get(name.lower(), {})

        suggestion = {
            "name": name,
            "path": sess.get("path") or dir_info.get("path"),
            "reason": [],
            "session_count": sess["session_count"],
            "days_active": sess.get("days_active", 0),
            "last_session": sess.get("last_session"),
            "has_claude_md": dir_info.get("has_claude_md", False),
            "has_git": dir_info.get("has_git", False),
            "confidence": "low",
        }

        # Build reasoning and confidence
        if sess["session_count"] >= 10:
            suggestion["reason"].append(f"{sess['session_count']} sessions")
            suggestion["confidence"] = "high"
        elif sess["session_count"] >= 3:
            suggestion["reason"].append(f"{sess['session_count']} sessions")
            suggestion["confidence"] = "medium"

        if dir_info.get("has_claude_md"):
            suggestion["reason"].append("has CLAUDE.md")
            suggestion["confidence"] = "high"

        if dir_info.get("has_git"):
            suggestion["reason"].append("git repository")

        if sess.get("days_active", 0) > 3:
            suggestion["reason"].append(f"active {sess['days_active']} days")

        if suggestion["confidence"] != "low":
            suggestions.append(suggestion)

    # Also check dirs that have no sessions but have CLAUDE.md
    suggested_names = {s["name"].lower() for s in suggestions}
    # Also include paths
    suggested_paths = {s.get("path") for s in suggestions if s.get("path")}
    for d in dir_data:
        name = d["name"]
        if _normalize(name) in existing_norm:
            continue
        if name.lower() in suggested_names:
            continue  # Already suggested from session data
        if d.get("path") in suggested_paths:
            continue  # Same project detected via sessions
        if name not in {s["name"] for s in session_data}:
            if d.get("has_claude_md"):
                suggestions.append({
                    "name": name,
                    "path": d["path"],
                    "reason": ["has CLAUDE.md", "no sessions yet"],
                    "session_count": 0,
                    "confidence": "medium",
                    "has_claude_md": True,
                    "has_git": d.get("has_git", False),
                })

    # Deduplicate by name — keep the one with more evidence
    seen = {}
    for s in suggestions:
        key = s["name"].lower()
        if key not in seen or s.get("session_count", 0) > seen[key].get("session_count", 0):
            # Merge evidence
            if key in seen:
                old = seen[key]
                s["reason"] = list(set(s["reason"] + old.get("reason", [])))
                s["has_claude_md"] = s.get("has_claude_md") or old.get("has_claude_md")
                s["has_git"] = s.get("has_git") or old.get("has_git")
                s["path"] = s.get("path") or old.get("path")
            seen[key] = s
        else:
            # Merge into existing
            old = seen[key]
            old["reason"] = list(set(old["reason"] + s.get("reason", [])))
            old["has_claude_md"] = old.get("has_claude_md") or s.get("has_claude_md")
            old["has_git"] = old.get("has_git") or s.get("has_git")
            old["path"] = old.get("path") or s.get("path")

    return list(seen.values())


def apply_suggestions(suggestions: list[dict]):
    """Create projects in work.yaml for high-confidence suggestions."""
    created = 0
    for s in suggestions:
        if s["confidence"] in ("high", "medium"):
            title = s["name"].replace("-", " ").title()
            engine.add_project(title=title)
            print(f"  Created project: {title}")
            created += 1

    if created:
        print(f"\n  {created} project(s) created")
    else:
        print("  No new projects to create")


def main():
    if "--match-repos" in sys.argv:
        matches = match_projects_to_repos()
        if "--json" in sys.argv:
            print(json.dumps(matches, indent=2, default=str))
        else:
            print(render_match_report(matches))
        # Deliberately no --apply here. apply_path_matches() exists but must
        # be invoked explicitly, after a human reviews this report.
        return

    suggestions = detect()

    if "--json" in sys.argv:
        print(json.dumps(suggestions, indent=2, default=str))
        return

    if not suggestions:
        print("  No new projects detected")
        return

    print("  Detected projects:\n")
    for s in suggestions:
        conf_icon = {"high": "+", "medium": "~", "low": "-"}[s["confidence"]]
        reasons = ", ".join(s["reason"])
        print(f"  {conf_icon} {s['name']}")
        print(f"    {reasons}")
        if s.get("path"):
            print(f"    path: {s['path']}")
        print()

    if "--apply" in sys.argv:
        apply_suggestions(suggestions)


if __name__ == "__main__":
    main()
