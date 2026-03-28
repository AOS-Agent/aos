#!/usr/bin/env python3
"""Verification suite for Initiative Pipeline + Bridge v2.

Covers: code integrity, migrations, reconcile, bridge v2 functional,
initiative pipeline functional, and release readiness.

Run:  python3 ~/project/aos/tests/test_initiative_bridge.py
"""

import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

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
    "core/services/bridge/shared_context.py",
    "core/services/bridge/topic_manager.py",
    "core/services/bridge/intent_classifier.py",
    "core/services/bridge/telegram_channel.py",
    "core/services/bridge/message_renderer.py",
    "core/services/bridge/context_loader.py",
    "core/services/bridge/bridge_events.py",
    "core/services/bridge/main.py",
    "core/services/bridge/pyproject.toml",
    # Work engine / Initiative
    "core/work/engine.py",
    "core/work/cli.py",
    "core/work/inject_context.py",
    "core/work/session_close.py",
    # Shared lib
    "core/lib/__init__.py",
    "core/lib/notify.py",
    # Migrations
    "core/migrations/017_bridge_topics.py",
    "core/migrations/018_initiative_infrastructure.py",
    # Reconcile
    "core/reconcile/checks/__init__.py",
    "core/reconcile/checks/initiatives.py",
    # Cron
    "core/bin/stale-initiatives",
    # Config
    "config/crons.yaml",
    # Docs
    "core/lib/CHANGES-initiative-pipeline.md",
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
    from core.lib.notify import send_telegram
    check("Import: core.lib.notify.send_telegram", True)
except ImportError as e:
    check("Import: core.lib.notify.send_telegram", False, str(e))

try:
    from core.services.bridge.shared_context import load, add_decision, get_decisions
    check("Import: shared_context (load, add_decision, get_decisions)", True)
except ImportError as e:
    check("Import: shared_context", False, str(e))

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
    from core.work.engine import add_task
    sig = inspect.signature(add_task)
    check("add_task has source_ref param", "source_ref" in sig.parameters)
except ImportError as e:
    check("Import: work engine add_task", False, str(e))

try:
    from core.work.cli import cmd_initiatives
    check("Import: cli.cmd_initiatives", True)
except ImportError as e:
    check("Import: cli.cmd_initiatives", False, str(e))

try:
    from core.reconcile.checks import ALL_CHECKS
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
runtime_head = subprocess.run(
    ["git", "-C", str(AOS_RUNTIME), "rev-parse", "--short", "HEAD"],
    capture_output=True, text=True
).stdout.strip()
check(f"Dev ({dev_head}) == Runtime ({runtime_head})", dev_head == runtime_head,
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
    [sys.executable, str(AOS_DEV / "core" / "reconcile" / "runner.py"), "check"],
    capture_output=True, text=True, cwd=str(AOS_DEV)
)
check("Reconcile runner executes (check mode)", result.returncode == 0,
      (result.stdout + result.stderr).strip()[:200] if result.returncode != 0 else "")

# Check initiative-specific reconcile (returns bool, not dict)
from core.reconcile.checks.initiatives import InitiativeDirectoriesCheck, BridgeTopicsCheck
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
    from core.services.bridge.evening_checkin import _build_evening_wrap, _load_initiatives
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

# Shared context — test the store cycle
print("  — Shared Context Store —")
try:
    from core.services.bridge import shared_context

    # Use a temporary store for testing
    original_file = shared_context.STORE_FILE
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        shared_context.STORE_FILE = Path(tmp.name)
        tmp.write(b'{"decisions": [], "facts": []}')

    # Test cycle: load → add → get → prune
    store = shared_context.load()
    check("shared_context.load() returns dict", isinstance(store, dict))

    shared_context.add_decision("test-project", "Test decision", "test-session")
    decisions = shared_context.get_decisions("test-project")
    check("shared_context round-trip works", len(decisions) >= 1,
          f"got {len(decisions)} decisions")

    context = shared_context.get_context_for_session()
    check("get_context_for_session returns string", isinstance(context, str))

    shared_context.prune()
    check("shared_context.prune() runs without error", True)

    # Cleanup
    shared_context.STORE_FILE = original_file
    os.unlink(tmp.name)
except Exception as e:
    check("shared_context functional test", False, str(e))

# Intent classifier — test quick commands
print("  — Intent Classifier —")
try:
    from core.services.bridge.intent_classifier import classify

    test_cases = [
        ("add task: write unit tests", "add_task"),
        ("mark aos#33 done", "done_task"),
        ("what's on my plate", "list_tasks"),
        ("search vault for bridge v2", "vault_search"),
    ]
    for text, expected in test_cases:
        result = classify(text)
        intent = result.get("intent", "") if isinstance(result, dict) else result
        check(f"classify('{text}') → {expected}",
              expected in str(intent).lower() or str(intent) == expected,
              f"got: {intent}")
except Exception as e:
    check("intent_classifier functional", False, str(e))

# Topic manager — can it instantiate with required args?
print("  — Topic Manager —")
try:
    from core.services.bridge.topic_manager import TopicManager
    tm = TopicManager(bot_token="test-token", forum_group_id=-1234567890)
    check("TopicManager instantiates with args", True)
except Exception as e:
    check("TopicManager instantiation", False, str(e))


# ─────────────────────────────────────────────────────────────
# PART 5: INITIATIVE PIPELINE FUNCTIONAL
# ─────────────────────────────────────────────────────────────

section("PART 5: Initiative Pipeline Functional")

# inject_context — can it scan initiatives?
print("  — Session Injection —")
try:
    from core.work import inject_context
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
            for field in ["title:", "status:"]:
                check(f"  {f.name} has {field}", field in content.split("---")[1])
else:
    check("Initiative directory exists", False)

# session_close — verify it has surgical update logic
print("  — Session Close —")
try:
    from core.work import session_close
    source = inspect.getsource(session_close)
    check("session_close uses re (regex)", "import re" in source or "re.sub" in source)
    check("session_close handles 'updated:' field",
          "updated:" in source or "updated" in source)
except Exception as e:
    check("session_close inspection", False, str(e))

# Stale initiatives cron
print("  — Stale Initiatives Cron —")
stale_script = AOS_DEV / "core" / "bin" / "stale-initiatives"
check("stale-initiatives is executable", os.access(stale_script, os.X_OK))

# Check crons.yaml includes stale-initiatives
crons_yaml = (AOS_DEV / "config" / "crons.yaml").read_text()
check("crons.yaml references stale-initiatives", "stale-initiatives" in crons_yaml)

# Work CLI initiatives command
print("  — Work CLI —")
result = subprocess.run(
    [sys.executable, str(AOS_DEV / "core" / "work" / "cli.py"), "initiatives"],
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
changes_file = AOS_DEV / "core" / "lib" / "CHANGES-initiative-pipeline.md"
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
