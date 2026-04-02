"""
Migration 008: Clean up filesystem after v1→v2 restructure.

Issues found:
1. ~/aos/data/ and ~/aos/logs/ — instance data leaked into framework
2. ~/.aos-v2/ leftover — partially emptied, should be removed
3. ~/.claude/agents/*.pre-aos — backup files from migration, no longer needed
4. ~/.claude/skills/*.pre-aos — same
5. ~/kitchen-app/ — empty directory
6. ~/vendor/ — loose vendor dir (MCP servers), should be in ~/aos/vendor/ or removed
7. ~/go/ — Go pkg cache, fine to keep (standard location)
8. MCP servers at ~/ (chitchats-mcp, mcp-gsuite, wave_mcp) — should move to ~/aos/vendor/
9. ~/.aos/logs/v1-execution-log/ — duplicate of ~/.aos/logs/execution/

Actions:
- Move misplaced data/logs out of framework
- Remove leftover backup files
- Consolidate MCP servers into vendor
- Remove empty dirs
- Clean duplicate logs
"""

DESCRIPTION = "Clean up filesystem — remove leftovers, consolidate loose dirs"

import shutil
from pathlib import Path

AOS_DIR = Path.home() / "aos"
USER_DIR = Path.home() / ".aos"
HOME = Path.home()


def check() -> bool:
    """Applied if no cleanup is needed."""
    issues = _find_issues()
    return len(issues) == 0


def _find_issues() -> list[str]:
    """Find all filesystem issues."""
    issues = []

    # Framework dirs that should be empty or not exist
    # (symlinks to ~/.aos/ are fine — they're compatibility shims)
    for d in ["data", "logs"]:
        p = AOS_DIR / d
        if p.exists() and not p.is_symlink() and any(p.iterdir()):
            issues.append(f"~/aos/{d}/ has instance data")

    # Leftover .aos-v2
    if (HOME / ".aos-v2").exists():
        issues.append("~/.aos-v2/ leftover exists")

    # Pre-aos backups
    for f in (HOME / ".claude" / "agents").glob("*.pre-aos"):
        issues.append(f".pre-aos backup: {f.name}")
    for d in (HOME / ".claude" / "skills").glob("*.pre-aos"):
        issues.append(f".pre-aos backup: {d.name}")

    # Empty kitchen-app
    kitchen = HOME / "kitchen-app"
    if kitchen.exists() and not any(kitchen.iterdir()):
        issues.append("~/kitchen-app/ is empty")

    # Duplicate execution logs
    dup1 = USER_DIR / "logs" / "v1-execution-log"
    dup2 = USER_DIR / "logs" / "execution"
    if dup1.exists() and dup2.exists():
        issues.append("Duplicate execution logs (v1-execution-log + execution)")

    return issues


def up() -> bool:
    """Clean up filesystem."""

    # 1. Move ~/aos/data/ contents to ~/.aos/data/
    framework_data = AOS_DIR / "data"
    if framework_data.exists():
        for item in framework_data.iterdir():
            if item.name.startswith("."):
                continue
            dst = USER_DIR / "data" / item.name
            if not dst.exists():
                shutil.move(str(item), str(dst))
                print(f"       Moved ~/aos/data/{item.name} → ~/.aos/data/")
            else:
                shutil.rmtree(str(item))
                print(f"       Removed duplicate ~/aos/data/{item.name}")
        # Remove empty data dir from framework
        try:
            framework_data.rmdir()
            print("       Removed empty ~/aos/data/")
        except OSError:
            pass

    # 2. Clean empty ~/aos/logs/
    framework_logs = AOS_DIR / "logs"
    if framework_logs.exists():
        try:
            # Remove if empty
            if not any(framework_logs.iterdir()):
                framework_logs.rmdir()
                print("       Removed empty ~/aos/logs/")
        except OSError:
            pass

    # 3. Remove ~/.aos-v2/ leftover
    old_user = HOME / ".aos-v2"
    if old_user.exists():
        # Check if it's effectively empty (just logs dir or similar)
        contents = list(old_user.rglob("*"))
        real_files = [f for f in contents if f.is_file()]
        if not real_files:
            shutil.rmtree(str(old_user))
            print("       Removed empty ~/.aos-v2/")
        else:
            # Merge anything remaining into ~/.aos/
            for f in real_files:
                rel = f.relative_to(old_user)
                dst = USER_DIR / rel
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(dst))
            shutil.rmtree(str(old_user))
            print("       Merged ~/.aos-v2/ remainder into ~/.aos/ and removed")

    # 4. Remove .pre-aos backups
    for f in (HOME / ".claude" / "agents").glob("*.pre-aos"):
        f.unlink()
        print(f"       Removed {f.name}")
    for d in (HOME / ".claude" / "skills").glob("*.pre-aos"):
        shutil.rmtree(str(d))
        print(f"       Removed {d.name}/")

    # 5. Remove empty ~/kitchen-app/
    kitchen = HOME / "kitchen-app"
    if kitchen.exists() and not any(kitchen.iterdir()):
        kitchen.rmdir()
        print("       Removed empty ~/kitchen-app/")

    # 6. Consolidate duplicate execution logs
    dup1 = USER_DIR / "logs" / "v1-execution-log"
    dup2 = USER_DIR / "logs" / "execution"
    if dup1.exists() and dup2.exists():
        # Merge v1-execution-log into execution
        for f in dup1.iterdir():
            dst = dup2 / f.name
            if not dst.exists():
                shutil.move(str(f), str(dst))
        shutil.rmtree(str(dup1))
        print("       Merged v1-execution-log/ into execution/")

    return True
