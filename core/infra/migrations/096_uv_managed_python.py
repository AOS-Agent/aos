"""
Migration 096: hand Python ownership to uv and install the runtime deps.

WHAT WAS WRONG
``core/engine/**`` and the cron scripts in ``core/bin/**`` import ten
third-party packages at runtime — pyyaml, rapidfuzz, phonenumbers, metaphone,
networkx, pyjwt, requests, httpx, feedparser, hijri_converter. Until this
change none of them were declared anywhere a machine would install from. They
appeared only in ``tests/requirements.txt``, which CI installs and no instance
ever reads.

So CI was green while production was broken, for months. Observed on the
reference Mac Mini before this migration was written:

  people-intel-refresh   0 ok / 8 failed   — every step died on
                                             ModuleNotFoundError: rapidfuzz
  ascbuild-sync          110 consecutive failures on `import jwt`
  morning-context        people-nudges silently returned [] (caught exception)
  feed-ingest            survives only because no feed source is active;
                         `import feedparser` is top-level and unguarded

The interpreter itself was Homebrew's. That caused three further problems:
it is "externally managed" (installing anything needs --break-system-packages),
``brew upgrade`` can move it under a running install, and macOS grants Full
Disk Access per *binary path* — so an incidental brew upgrade silently revokes
the iMessage ingest's access.

WHAT THIS DOES
Provisions the CPython pinned in ``.python-version`` through uv, syncs the
locked dependency set from ``uv.lock`` into ``~/.aos/python``, and points
``~/.aos/config/python`` at it. ``core/bin/internal/aos-python`` already reads
that file, so every AOS caller picks the new interpreter up with no further
change.

The pinned version is 3.13.12 — byte-identical to what Homebrew was already
providing on the reference machine. This migration is therefore a no-op on
*behaviour*: same version, same code paths. Only ownership of the interpreter
moves, from Homebrew to AOS.

WHAT IT DOES NOT DO
  - Does not uninstall or modify Homebrew's python@3.13. Other things on the
    machine (the operator's own project venvs, for one) are built against it
    and must keep working.
  - Does not touch the per-service venvs under ~/.aos/services/*/.venv. Those
    are deployed separately and pin their own interpreters; migrating them is
    a separate change.
  - Does not install pysqlcipher3 (Signal Desktop ingest). It is import-guarded,
    needs a system libsqlcipher, and the source is opt-in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

AOS_ROOT = Path.home() / "aos"
CONFIG_DIR = Path.home() / ".aos" / "config"
PYTHON_CONFIG = CONFIG_DIR / "python"
AOS_ENV = Path.home() / ".aos" / "python"

# Every module core/engine and core/bin import at runtime. Resolution succeeding
# is not the same as importing — native builds and shim packages can resolve
# cleanly and still fail here — so the migration verifies imports, not installs.
RUNTIME_MODULES = [
    "yaml",
    "rapidfuzz",
    "phonenumbers",
    "metaphone",
    "networkx",
    "jwt",
    "requests",
    "httpx",
    "feedparser",
    "hijri_converter",
]


def _notify(text: str) -> None:
    """Best-effort Telegram ping. Never fails the migration."""
    try:
        sys.path.insert(0, str(AOS_ROOT / "core"))
        from lib.notify import send_telegram

        send_telegram(text)
    except Exception as e:  # noqa: BLE001
        print(f"  (could not send Telegram notice: {e})")


def _run(cmd: list[str], env: dict | None = None, timeout: int = 600):
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=merged
    )


def _pinned_version() -> str | None:
    f = AOS_ROOT / ".python-version"
    try:
        v = f.read_text().strip()
        return v or None
    except OSError:
        return None


def _env_python() -> Path:
    return AOS_ENV / "bin" / "python"


def _imports_ok(python: Path) -> bool:
    """True when every runtime module imports under `python`."""
    if not python.exists():
        return False
    code = "import " + ", ".join(RUNTIME_MODULES)
    r = _run([str(python), "-c", code], timeout=120)
    return r.returncode == 0


SCHEDULER_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.aos.scheduler.plist"
ENV_BIN = str(AOS_ENV / "bin")


def _scheduler_path_patched() -> bool:
    """True when the scheduler's PATH already leads with the AOS env."""
    try:
        import plistlib

        d = plistlib.loads(SCHEDULER_PLIST.read_bytes())
    except Exception:  # noqa: BLE001
        return False
    path = d.get("EnvironmentVariables", {}).get("PATH", "")
    return path.split(":")[0] == ENV_BIN


def _patch_scheduler_path() -> bool:
    """Put the AOS env first on the scheduler's PATH, then reload it.

    Seventeen cron scripts begin with ``#!/usr/bin/env python3``. A shebang
    cannot call the aos-python resolver, so PATH order is the only lever that
    decides which interpreter they run under. The framework template carries
    this too, but templates are only rendered at install time — an existing
    machine needs the live plist patched.
    """
    import plistlib

    if not SCHEDULER_PLIST.exists():
        print("  ! No scheduler plist found — skipping PATH patch")
        return True  # nothing to patch is not a failure

    try:
        d = plistlib.loads(SCHEDULER_PLIST.read_bytes())
        env = d.setdefault("EnvironmentVariables", {})
        parts = [p for p in env.get("PATH", "").split(":") if p and p != ENV_BIN]
        env["PATH"] = ":".join([ENV_BIN] + parts)
        SCHEDULER_PLIST.write_bytes(plistlib.dumps(d))
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ Could not patch scheduler PATH: {e}")
        return False

    uid = os.getuid()
    _run(["launchctl", "bootout", f"gui/{uid}/com.aos.scheduler"], timeout=30)
    r = _run(["launchctl", "bootstrap", f"gui/{uid}", str(SCHEDULER_PLIST)], timeout=30)
    if r.returncode != 0:
        print(f"  ! Scheduler reload returned {r.returncode}: {r.stderr.strip()[:200]}")
        print("    PATH is patched; it takes effect on the next scheduler start.")
    else:
        print("  ✓ Scheduler PATH patched and reloaded")
    return True


def check() -> bool:
    """Already applied when the AOS env exists, is wired up, and imports."""
    if not PYTHON_CONFIG.exists():
        return False
    try:
        configured = PYTHON_CONFIG.read_text().strip()
    except OSError:
        return False
    if configured != str(_env_python()):
        return False
    if not _scheduler_path_patched():
        return False
    return _imports_ok(_env_python())


def up() -> bool:
    pinned = _pinned_version()
    if not pinned:
        print("  ✗ No .python-version in the framework — cannot pin an interpreter")
        return False

    if not _run(["which", "uv"]).returncode == 0:
        print("  ✗ uv not installed. Install it first: brew install uv")
        return False

    print(f"  Provisioning CPython {pinned} via uv...")
    r = _run(["uv", "python", "install", pinned])
    if r.returncode != 0:
        print(f"  ✗ uv python install failed: {r.stderr.strip()[:300]}")
        return False

    print("  Syncing locked runtime dependencies...")
    r = _run(
        ["uv", "sync", "--frozen", "--project", str(AOS_ROOT)],
        env={"UV_PROJECT_ENVIRONMENT": str(AOS_ENV)},
    )
    if r.returncode != 0:
        print(f"  ✗ uv sync failed: {r.stderr.strip()[:300]}")
        return False

    # Verify before rewiring. A half-built environment that aos-python already
    # points at is worse than the Homebrew interpreter we are replacing.
    if not _imports_ok(_env_python()):
        print("  ✗ Runtime environment built but not importable — leaving config alone")
        return False

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    previous = PYTHON_CONFIG.read_text().strip() if PYTHON_CONFIG.exists() else "(unset)"
    PYTHON_CONFIG.write_text(str(_env_python()) + "\n")

    if not _patch_scheduler_path():
        return False

    print(f"  ✓ Python {pinned} — {_env_python()}")
    print(f"    (was: {previous})")
    print(f"  ✓ {len(RUNTIME_MODULES)} runtime dependencies installed and importable")

    _notify(
        "🐍 Fixed the Mac Mini's Python setup.\n\n"
        "Some nightly jobs had been failing for months — the contact sync, the "
        "App Store build sync, and the people reminders in your morning brief. "
        "They were all missing packages that nobody had ever actually installed; "
        "the tests only looked green because the test runner installed them "
        "separately.\n\n"
        "AOS now manages its own Python and installs exactly what it needs. "
        "Same version as before, so nothing else changes.\n\n"
        "The contact sync still needs Full Disk Access from you before it can "
        "read Messages — ask me and I'll walk you through it. 👍"
    )
    return True


if __name__ == "__main__":
    if check():
        print("Migration 096 already applied")
    else:
        print("Done" if up() else "Failed")
