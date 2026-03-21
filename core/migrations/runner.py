#!/usr/bin/env python3
"""
AOS Migration Runner

Handles two migration paths:
1. Internal upgrades — aos evolves, user data needs schema changes
2. External imports — user migrating from a custom Mac Mini + Claude setup

Migrations are numbered scripts in this directory.
Each migration is idempotent — safe to re-run.

Usage:
    python3 runner.py migrate          # Run pending migrations
    python3 runner.py status           # Show current version + pending
    python3 runner.py discover         # Scan machine for existing setup
    python3 runner.py import           # Interactive import from discovered setup
"""

import os
import sys
import importlib.util
import yaml
from pathlib import Path
from datetime import datetime

AOS_DIR = Path.home() / "aos"
USER_DIR = Path.home() / ".aos"
VERSION_FILE = USER_DIR / ".version"
MIGRATION_DIR = AOS_DIR / "core" / "migrations"
MIGRATION_LOG = USER_DIR / "logs" / "migrations.yaml"


def load_version() -> int:
    """Get current migration version. 0 = never migrated."""
    if not VERSION_FILE.exists():
        return 0
    try:
        return int(VERSION_FILE.read_text().strip())
    except (ValueError, FileNotFoundError):
        return 0


def save_version(version: int):
    """Write current version."""
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(str(version))


def find_migrations() -> list[tuple[int, str, object]]:
    """Find all migration scripts, sorted by number.

    Returns list of (number, name, module).
    """
    migrations = []
    for f in sorted(MIGRATION_DIR.glob("[0-9][0-9][0-9]_*.py")):
        num = int(f.stem.split("_")[0])
        name = f.stem
        # Load as module
        spec = importlib.util.spec_from_file_location(name, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        migrations.append((num, name, mod))
    return migrations


def log_migration(num: int, name: str, status: str, details: str = ""):
    """Append to migration log."""
    MIGRATION_LOG.parent.mkdir(parents=True, exist_ok=True)

    log = []
    if MIGRATION_LOG.exists():
        with open(MIGRATION_LOG) as f:
            log = yaml.safe_load(f) or []

    log.append({
        "migration": name,
        "version": num,
        "status": status,
        "details": details,
        "timestamp": datetime.now().isoformat(),
    })

    with open(MIGRATION_LOG, "w") as f:
        yaml.dump(log, f, default_flow_style=False)


def cmd_migrate():
    """Run all pending migrations."""
    current = load_version()
    migrations = find_migrations()
    pending = [(n, name, mod) for n, name, mod in migrations if n > current]

    if not pending:
        print(f"✓ Already at version {current}. Nothing to migrate.")
        return True

    print(f"Current version: {current}")
    print(f"Pending: {len(pending)} migration(s)\n")

    for num, name, mod in pending:
        desc = getattr(mod, "DESCRIPTION", name)
        print(f"  [{num}] {desc}")

        # Check if already applied (idempotent check)
        if hasattr(mod, "check") and mod.check():
            print(f"       → Already applied, skipping")
            save_version(num)
            log_migration(num, name, "skipped", "Already applied")
            continue

        # Run migration
        try:
            result = mod.up()
            if result is False:
                print(f"       ✗ Failed")
                log_migration(num, name, "failed")
                return False
            print(f"       ✓ Done")
            save_version(num)
            log_migration(num, name, "applied")
        except Exception as e:
            print(f"       ✗ Error: {e}")
            log_migration(num, name, "error", str(e))
            return False

    print(f"\n✓ Migrated to version {pending[-1][0]}")
    return True


def cmd_status():
    """Show migration status."""
    current = load_version()
    migrations = find_migrations()
    pending = [(n, name, mod) for n, name, mod in migrations if n > current]
    applied = [(n, name, mod) for n, name, mod in migrations if n <= current]

    print(f"AOS Version: {current}")
    print(f"Applied: {len(applied)} | Pending: {len(pending)}")

    if pending:
        print(f"\nPending migrations:")
        for num, name, mod in pending:
            desc = getattr(mod, "DESCRIPTION", name)
            print(f"  [{num}] {desc}")

    if applied:
        print(f"\nApplied:")
        for num, name, mod in applied:
            desc = getattr(mod, "DESCRIPTION", name)
            print(f"  [{num}] {desc} ✓")


def cmd_discover():
    """Scan machine for existing Claude/agent setup.

    Outputs a report of what was found — used by import migrations
    and by new users to understand what they have.
    """
    report = {
        "timestamp": datetime.now().isoformat(),
        "found": {},
        "summary": [],
    }

    checks = [
        # (label, path, description)
        ("claude_global_md", Path.home() / ".claude" / "CLAUDE.md",
         "Global Claude context file"),
        ("claude_settings", Path.home() / ".claude" / "settings.json",
         "Claude Code settings"),
        ("claude_agents", Path.home() / ".claude" / "agents",
         "Active Claude agents"),
        ("claude_skills", Path.home() / ".claude" / "skills",
         "Claude skills"),
        ("claude_memory", Path.home() / ".claude" / "projects",
         "Claude project memory"),
        ("aos_v1", Path.home() / "aos",
         "AOS v1 installation"),
        ("aos_v2", Path.home() / "aos",
         "AOS v2 installation"),
        ("aos_v2_data", Path.home() / ".aos",
         "AOS v2 user data"),
        ("vault", Path.home() / "vault",
         "Obsidian knowledge vault"),
        ("homebrew", Path("/opt/homebrew"),
         "Homebrew package manager"),
        ("tailscale", Path("/opt/homebrew/bin/tailscale"),
         "Tailscale VPN"),
        ("qmd", Path.home() / ".bun" / "bin" / "qmd",
         "QMD semantic search"),
    ]

    for label, path, desc in checks:
        exists = path.exists()
        info = {"exists": exists, "description": desc, "path": str(path)}

        if exists and path.is_dir():
            # Count contents
            try:
                contents = list(path.iterdir())
                info["items"] = len(contents)
                if label == "claude_agents":
                    info["agents"] = [f.stem for f in path.glob("*.md")]
                elif label == "claude_skills":
                    info["skills"] = [d.name for d in path.iterdir() if d.is_dir()]
            except PermissionError:
                info["items"] = "permission denied"

        report["found"][label] = info
        if exists:
            report["summary"].append(f"✓ {desc}: {path}")
        else:
            report["summary"].append(f"✗ {desc}: not found")

    # Check LaunchAgents
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if la_dir.exists():
        aos_agents = [f.name for f in la_dir.glob("com.agent.*")]
        aos_agents += [f.name for f in la_dir.glob("com.aos.*")]
        report["found"]["launch_agents"] = {
            "exists": bool(aos_agents),
            "agents": aos_agents,
            "description": "AOS LaunchAgents",
        }

    # Check for custom agent setups (common patterns)
    custom_patterns = [
        ("mcp_config", Path.home() / ".claude" / "mcp.json",
         "MCP server configuration"),
        ("claude_hooks", Path.home() / ".claude" / "hooks",
         "Claude Code hooks"),
    ]
    for label, path, desc in custom_patterns:
        exists = path.exists()
        report["found"][label] = {
            "exists": exists, "description": desc, "path": str(path)
        }

    # Save report
    report_path = USER_DIR / "logs" / "discovery-report.yaml"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False)

    # Print
    print("=== AOS Discovery Report ===\n")
    for line in report["summary"]:
        print(f"  {line}")

    if report["found"].get("launch_agents", {}).get("agents"):
        print(f"\n  LaunchAgents: {', '.join(report['found']['launch_agents']['agents'])}")

    if report["found"].get("claude_agents", {}).get("agents"):
        print(f"  Active agents: {', '.join(report['found']['claude_agents']['agents'])}")

    if report["found"].get("claude_skills", {}).get("skills"):
        print(f"  Active skills: {', '.join(report['found']['claude_skills']['skills'])}")

    print(f"\n  Full report: {report_path}")
    return report


def cmd_import_guide():
    """Guide for users migrating from a custom setup."""
    print("=== AOS Import Guide ===\n")
    print("If you're coming from a custom Mac Mini + Claude Code setup,")
    print("here's how your existing pieces map to AOS:\n")
    print("  Your agents   → ~/.claude/agents/   (AOS manages, yours stay)")
    print("  Your skills   → ~/.claude/skills/   (AOS adds, yours stay)")
    print("  Your CLAUDE.md → backed up, AOS kernel installed")
    print("  Your MCP      → preserved in ~/.claude/mcp.json")
    print("  Your vault     → ~/vault/ (AOS indexes it with QMD)")
    print("  Your services  → registered in config/state.yaml")
    print()
    print("Run 'python3 runner.py discover' first to see what you have.")
    print("Then 'python3 runner.py migrate' to apply pending migrations.")
    print("Each migration is safe to re-run and will skip if already done.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "migrate":
        success = cmd_migrate()
        sys.exit(0 if success else 1)
    elif cmd == "status":
        cmd_status()
    elif cmd == "discover":
        cmd_discover()
    elif cmd == "import":
        cmd_import_guide()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: runner.py [migrate|status|discover|import]")
        sys.exit(1)
