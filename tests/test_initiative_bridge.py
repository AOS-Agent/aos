#!/usr/bin/env python3
"""Verification suite for Initiative Pipeline + Bridge v2.

Covers: code integrity, migrations, reconcile, bridge v2 functional,
initiative pipeline functional, and release readiness.

Run:  python3 ~/project/aos/tests/test_initiative_bridge.py
"""

import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# This is a standalone dev-machine verification / release-readiness script, not
# a unit test. Its checks run at module scope, it shells out to live services
# (bridge process, reconcile runner, work CLI) and reads ~/.aos and ~/vault,
# and it calls sys.exit() at the end — which would abort pytest collection.
# When imported by pytest, skip the whole module cleanly. Run it directly on a
# dev box instead:  python3 tests/test_initiative_bridge.py
if __name__ != "__main__":
    import pytest

    _dev_workspace = Path.home() / "project" / "aos"
    _reason = (
        "dev-machine verification script — run it directly "
        "(python3 tests/test_initiative_bridge.py), not under pytest"
    )
    if not _dev_workspace.is_dir():
        _reason = "not a dev machine (no ~/project/aos); " + _reason
    pytest.skip(_reason, allow_module_level=True)

# Setup paths
AOS_DEV = Path.home() / "project" / "aos"
AOS_RUNTIME = Path.home() / "aos"
AOS_USER = Path.home() / ".aos"
VAULT = Path.home() / "vault"

sys.path.insert(0, str(AOS_DEV))

PASSED = 0
FAILED = 0
ERRORS = []


def check(name: str, condition: bool, detail: str = ""):
    """Register a test result."""
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        msg = f"  ❌ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        ERRORS.append(msg)


def section(title: str):
    print(f"\n{'━' * 60}")
    print(f"  {title}")
    print(f"{'━' * 60}")


# ─────────────────────────────────────────────────────────────
# PART 1: CODE INTEGRITY
# ─────────────────────────────────────────────────────────────

section("PART 1: Code Integrity")

# Files that must exist in dev workspace
REQUIRED_FILES = [
    # Bridge v2
    "core/services/bridge/daily_briefing.py",
    "core/services/bridge/evening_checkin.py",
    "core/services/bridge/topic_manager.py",
    "core/services/bridge/intent_classifier.py",
    "core/services/bridge/telegram_channel.py",
    "core/services/bridge/message_renderer.py",
    "core/services/bridge/bridge_events.py",
    "core/services/bridge/main.py",
    "core/services/bridge/pyproject.toml",
    # Work engine / Initiative
    "core/engine/work/engine.py",
    "core/engine/work/cli.py",
    "core/engine/work/inject_context.py",
    "core/engine/work/session_close.py",
    # Shared lib
    "core/infra/lib/__init__.py",
    "core/infra/lib/notify.py",
    # Migrations
    "core/infra/migrations/017_bridge_topics.py",
    "core/infra/migrations/018_initiative_infrastructure.py",
    # Reconcile
    "core/infra/reconcile/checks/__init__.py",
    "core/infra/reconcile/checks/initiatives.py",
    # Cron
    "core/bin/crons/stale-initiatives",
    # Config
    "config/crons.yaml",
    # Docs
    "core/infra/lib/CHANGES-initiative-pipeline.md",
]

for f in REQUIRED_FILES:
    path = AOS_DEV / f
    check(f"File exists: {f}", path.exists())

# Python syntax check
PYTHON_FILES = [f for f in REQUIRED_FILES if f.endswith(".py")]
for f in PYTHON_FILES:
    path = AOS_DEV / f
    if path.exists():
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True
        )
        check(f"Compiles: {f}", result.returncode == 0, result.stderr.strip())

# Import checks
print("\n  — Import verification —")

try:
    # Canonical pattern per notify.py's own docstring: callers put AOS/core
    # on sys.path and import lib.notify (namespace-package resolution of
    # core.infra.lib is unreliable once other test sections mutate sys.path).
    import sys as _s
    _core = str(AOS_DEV / "core")
    if _core not in _s.path:
        _s.path.insert(0, _core)
    from lib.notify import send_telegram
    check("Import: lib.notify.send_telegram (canonical)", True)
except ImportError as e:
    check("Import: lib.notify.send_telegram (canonical)", False, str(e))

# shared_context/context_loader: council-substrate-only modules, never shipped
# to main (tracked as leftovers). Import checks removed 2026-07-15.

try:
    from core.services.bridge.topic_manager import TopicManager
    check("Import: TopicManager class", True)
except ImportError as e:
    check("Import: TopicManager", False, str(e))

try:
    from core.services.bridge.bridge_events import bridge_event
    check("Import: bridge_events.bridge_event", True)
except ImportError as e:
    check("Import: bridge_events", False, str(e))

try:
    from core.services.bridge.intent_classifier import classify, dispatch
    check("Import: intent_classifier (classify, dispatch)", True)
except ImportError as e:
    check("Import: intent_classifier", False, str(e))

try:
    from core.engine.work.engine import add_task
    sig = inspect.signature(add_task)
    check("add_task has source_ref param", "source_ref" in sig.parameters)
except ImportError as e:
    check("Import: work engine add_task", False, str(e))

try:
    from core.engine.work.cli import cmd_initiatives
    check("Import: cli.cmd_initiatives", True)
except ImportError as e:
    check("Import: cli.cmd_initiatives", False, str(e))

try:
    from core.infra.reconcile.checks import ALL_CHECKS
    check_names = [c.__name__ for c in ALL_CHECKS]
    check("Reconcile: InitiativeDirectoriesCheck registered",
          "InitiativeDirectoriesCheck" in check_names)
    check("Reconcile: BridgeTopicsCheck registered",
          "BridgeTopicsCheck" in check_names)
except ImportError as e:
    check("Import: reconcile ALL_CHECKS", False, str(e))

# Dev/runtime sync
print("\n  — Dev/runtime sync —")
dev_head = subprocess.run(
    ["git", "-C", str(AOS_DEV), "rev-parse", "--short", "HEAD"],
    capture_output=True, text=True
).stdout.strip()
# Runtime is a release snapshot (no .git since release-channel deploys);
# the deployed commit is encoded in the release dir name: vX.Y.Z-<short>.
_rt_target = Path(AOS_RUNTIME).resolve().name
runtime_head = _rt_target.rsplit("-", 1)[-1] if "-" in _rt_target else ""
_n = min(len(dev_head), len(runtime_head)) or 1
check(f"Dev ({dev_head}) == Runtime ({runtime_head})",
      bool(runtime_head) and dev_head[:_n] == runtime_head[:_n],
      f"DRIFT: dev={dev_head} runtime={runtime_head}")


# ─────────────────────────────────────────────────────────────
# PART 2: MIGRATIONS
# ─────────────────────────────────────────────────────────────

section("PART 2: Migration Artifacts")

# Check that migration artifacts exist
check("bridge-topics.yaml exists",
      (AOS_USER / "config" / "bridge-topics.yaml").exists())

check("operator.yaml has initiatives config",
      "initiatives:" in (AOS_USER / "config" / "operator.yaml").read_text())

check("vault/knowledge/initiatives/ exists",
      (VAULT / "knowledge" / "initiatives").is_dir())

check("vault/knowledge/expertise/ exists",
      (VAULT / "knowledge" / "expertise").is_dir())

check("vault/knowledge/captures/ exists",
      (VAULT / "knowledge" / "captures").is_dir())


# ─────────────────────────────────────────────────────────────
# PART 3: RECONCILE CHECKS
# ─────────────────────────────────────────────────────────────

section("PART 3: Reconcile Checks")

# Run reconcile check
result = subprocess.run(
    [sys.executable, str(AOS_DEV / "core" / "infra" / "reconcile" / "runner.py"), "check"],
    capture_output=True, text=True, cwd=str(AOS_DEV)
)
check("Reconcile runner executes (check mode)", result.returncode == 0,
      (result.stdout + result.stderr).strip()[:200] if result.returncode != 0 else "")

# Check initiative-specific reconcile (returns bool, not dict)
from core.infra.reconcile.checks.initiatives import (
    BridgeTopicsCheck,
    InitiativeDirectoriesCheck,
)

init_check = InitiativeDirectoriesCheck()
bridge_check = BridgeTopicsCheck()

try:
    init_result = init_check.check()
    check("InitiativeDirectoriesCheck passes", init_result is True,
          f"returned: {init_result}")
except Exception as e:
    check("InitiativeDirectoriesCheck passes", False, str(e))

try:
    bridge_result = bridge_check.check()
    check("BridgeTopicsCheck passes", bridge_result is True,
          f"returned: {bridge_result}")
except Exception as e:
    check("BridgeTopicsCheck passes", False, str(e))


# ─────────────────────────────────────────────────────────────
# PART 4: BRIDGE V2 FUNCTIONAL
# ─────────────────────────────────────────────────────────────

section("PART 4: Bridge v2 Functional")

# Daily briefing — test the actual builder
print("  — Daily Briefing —")
try:
    from core.services.bridge.daily_briefing import _build_briefing, _scan_initiatives
    check("daily_briefing._build_briefing callable", callable(_build_briefing))

    # Test initiative scanner
    initiatives = _scan_initiatives()
    check("_scan_initiatives returns list", isinstance(initiatives, list))
    if initiatives:
        for i in initiatives:
            check(f"  initiative '{i['title']}' has required fields",
                  all(k in i for k in ("title", "status", "stale")))

    # Test briefing generation
    briefing = _build_briefing()
    check("_build_briefing produces output", len(briefing) > 0, f"got {len(briefing)} chars")

    # BLUF format checks
    check("Briefing has URGENT section", "URGENT" in briefing)
    check("Briefing has IMPORTANT section", "IMPORTANT" in briefing)
    check("Briefing uses HTML bold tags", "<b>" in briefing)
    check("Briefing is Telegram-safe (under 4096 chars)", len(briefing) <= 4096,
          f"got {len(briefing)} chars — needs splitting")
except Exception as e:
    check("daily_briefing functional test", False, str(e))

# Evening checkin — test the actual builder
print("  — Evening Checkin —")
try:
    from core.services.bridge.evening_checkin import (
        _build_evening_wrap,
        _load_initiatives,
    )
    check("evening_checkin._build_evening_wrap callable", callable(_build_evening_wrap))

    wrap = _build_evening_wrap()
    check("_build_evening_wrap produces output", len(wrap) > 0, f"got {len(wrap)} chars")
    check("Wrap has 'Done today' section", "Done today" in wrap or "done today" in wrap.lower())
    check("Wrap has 'Still open' section", "Still open" in wrap or "open" in wrap.lower())
    check("Wrap uses HTML formatting", "<b>" in wrap)

    # Initiative matching
    init_list = _load_initiatives()
    check("_load_initiatives returns list", isinstance(init_list, list))
    if init_list:
        check("Initiative entries have tags for matching",
              all("tags" in i for i in init_list))
except Exception as e:
    check("evening_checkin functional test", False, str(e))

# Bridge service health
print("  — Bridge Service —")
bridge_pid = subprocess.run(
    ["pgrep", "-f", "aos-bridge"],
    capture_output=True, text=True
).stdout.strip()
check("Bridge process running (aos-bridge)", len(bridge_pid) > 0,
      "no aos-bridge process found")

# Shared context store: module never shipped to main (council-substrate
# leftover, tracked in the leftover ledger) — functional test removed 2026-07-15.

section("PART 5: Initiative Pipeline Functional")

# inject_context — can it scan initiatives?
print("  — Session Injection —")
try:
    from core.engine.work import inject_context
    funcs = [f for f in dir(inject_context) if not f.startswith("_") and callable(getattr(inject_context, f, None))]
    check("inject_context has callable functions", len(funcs) > 0, f"found: {funcs}")
except ImportError as e:
    check("inject_context importable", False, str(e))

# Check initiative docs exist and parse
print("  — Initiative Documents —")
init_dir = VAULT / "knowledge" / "initiatives"
if init_dir.exists():
    init_files = list(init_dir.glob("*.md"))
    check("Initiative docs exist", len(init_files) > 0, f"found {len(init_files)}")
    for f in init_files:
        content = f.read_text()
        has_frontmatter = content.startswith("---")
        check(f"  {f.name} has YAML frontmatter", has_frontmatter)
        if has_frontmatter:
            # Check required fields
            # status: enforcement moved to the vault_contract reconcile check
            for field in ["title:"]:
                check(f"  {f.name} has {field}", field in content.split("---")[1])
else:
    check("Initiative directory exists", False)

# session_close — verify it has surgical update logic
print("  — Session Close —")
try:
    from core.engine.work import session_close
    source = inspect.getsource(session_close)
    check("session_close uses re (regex)", "import re" in source or "re.sub" in source)
    check("session_close handles 'updated:' field",
          "updated:" in source or "updated" in source)
except Exception as e:
    check("session_close inspection", False, str(e))

# Stale initiatives cron
print("  — Stale Initiatives Cron —")
stale_script = AOS_DEV / "core" / "bin" / "crons" / "stale-initiatives"
check("stale-initiatives is executable", os.access(stale_script, os.X_OK))

# Check crons.yaml includes stale-initiatives
crons_yaml = (AOS_DEV / "config" / "crons.yaml").read_text()
check("crons.yaml references stale-initiatives", "stale-initiatives" in crons_yaml)

# Work CLI initiatives command
print("  — Work CLI —")
result = subprocess.run(
    [sys.executable, str(AOS_DEV / "core" / "engine" / "work" / "cli.py"), "initiatives"],
    capture_output=True, text=True
)
check("'work initiatives' command runs", result.returncode == 0,
      result.stderr.strip()[:200] if result.returncode != 0 else "")


# ─────────────────────────────────────────────────────────────
# PART 6: RELEASE READINESS
# ─────────────────────────────────────────────────────────────

section("PART 6: Release Readiness")

# VERSION file
version_file = AOS_DEV / "VERSION"
if version_file.exists():
    version = version_file.read_text().strip()
    check(f"VERSION file exists ({version})", True)
else:
    check("VERSION file exists", False)

# CHANGELOG
changelog_file = AOS_DEV / "CHANGELOG.md"
if changelog_file.exists():
    changelog = changelog_file.read_text()
    check("CHANGELOG.md exists", True)
    check("CHANGELOG mentions initiative", "initiative" in changelog.lower(),
          "No initiative entry in CHANGELOG")
    check("CHANGELOG mentions bridge", "bridge" in changelog.lower(),
          "No bridge entry in CHANGELOG")
else:
    check("CHANGELOG.md exists", False)

# Changes manifest
changes_file = AOS_DEV / "core" / "infra" / "lib" / "CHANGES-initiative-pipeline.md"
check("CHANGES manifest exists", changes_file.exists())


# ─────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────

section("SUMMARY")
total = PASSED + FAILED
print(f"\n  Total: {total}  |  Passed: {PASSED} ✅  |  Failed: {FAILED} ❌")
print(f"  Pass rate: {PASSED/total*100:.0f}%\n")

if ERRORS:
    print("  FAILURES:")
    for e in ERRORS:
        print(f"    {e}")
    print()

sys.exit(0 if FAILED == 0 else 1)
