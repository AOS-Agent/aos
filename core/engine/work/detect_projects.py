#!/usr/bin/env python3
"""
Auto-project detection — scans session history and directories
to find work that should be tracked as projects.

Detection signals:
1. Claude session directories with 3+ sessions
2. Directories with CLAUDE.md files (explicit project markers)
3. Git repositories under ~/project/ or ~/
4. Active threads that should be promoted

Output: list of suggested projects with evidence, or auto-create if configured.

Usage:
    python3 detect_projects.py              # Print suggestions
    python3 detect_projects.py --apply      # Create missing projects in work.yaml
    python3 detect_projects.py --json       # JSON output for dashboard API
"""

import getpass
import json
import os
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
USERNAME = getpass.getuser()


def _clean_project_name(dirname: str) -> str:
    """Convert Claude project dirname to readable name.

    -Users-agentalhadi-nuchay → nuchay
    -Users-agentalhadi-chief-ios-app → chief-ios-app
    -Users-agentalhadi-Desktop-mac-mini-agent → mac-mini-agent
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
    # -Users-agentalhadi-nuchay → /Users/agentalhadi/nuchay
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
    existing_names = {p.get("title", "").lower() for p in existing.values()}
    # No path field in projects yet, so we rely on name matching

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

        # Skip if already tracked
        if any(name.lower() in ex.lower() for ex in existing_names):
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
        if any(name.lower() in ex.lower() for ex in existing_names):
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
