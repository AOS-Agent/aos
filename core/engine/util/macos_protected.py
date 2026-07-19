"""macOS Full Disk Access (FDA) helpers.

Several AOS ingesters need to read files in TCC-protected directories:

  ~/Library/Messages/chat.db                 (iMessage)
  ~/Library/Mail/V*/MailData/Envelope Index  (Apple Mail)
  ~/Library/Application Support/AddressBook  (Contacts)
  ~/Library/Calendars                        (Calendar)

macOS shows a permission prompt the first time the responsible *binary*
touches one of these. The grant lives per-binary in the user's TCC
database. Once granted, it persists. If the user dismisses the prompt
without granting, the access fails silently AND macOS will re-prompt on
subsequent attempts — which is what makes the popup feel "stuck".

This module provides three things:

  1. ``has_full_disk_access(path)`` — silent probe; returns True/False
     by attempting to open the file. Does NOT trigger a fresh prompt
     if access has been previously granted.

  2. ``safe_snapshot(src, cache_key, max_age_sec=300)`` — copies a
     protected SQLite database (plus its WAL/SHM sidecars) to a stable
     /tmp path. Caches by mtime so repeated calls within the freshness
     window do NOT re-copy and do NOT re-trigger any TCC interaction.
     /tmp is not TCC-protected, so reading the snapshot is free.

  3. ``print_grant_instructions(reason)`` — prints a clear, one-time
     message explaining how to grant FDA, including the exact path to
     the current Python binary, and offers to open the System Settings
     pane via ``open``.

Convention: tools that need FDA call ``ensure_access(path, friendly)``
at startup. If access is missing, the helper prints instructions and
returns False so the caller can exit cleanly without retrying.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Marker file used to suppress repeated FDA-instruction printouts within
# a single shell session. The marker is written when the user is shown
# the instructions; deleted when access is detected as working.
_FDA_NAG_MARKER = Path.home() / ".aos" / "state" / "fda-nag-shown"

# Default cache TTL for snapshots — 5 minutes is enough for back-to-back
# CLI calls to dedupe but short enough that "live" data is fresh.
DEFAULT_SNAPSHOT_TTL_SEC = 300


def _ensure_marker_dir() -> None:
    _FDA_NAG_MARKER.parent.mkdir(parents=True, exist_ok=True)


def has_full_disk_access(path: Path) -> bool:
    """Return True if the current process can read ``path``.

    Silent probe — does not raise. The probe opens the file in binary
    mode and reads 1 byte. macOS will not pop a fresh prompt if the
    user has previously denied access (it just returns EPERM).
    """
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except (PermissionError, OSError):
        return False


def python_binary() -> str:
    """Return the resolved path to the running Python binary."""
    return os.path.realpath(sys.executable)


def print_grant_instructions(reason: str) -> None:
    """Print a clear, one-shot message on how to grant Full Disk Access.

    Idempotent within a session: if the marker file exists, the message
    is suppressed (so the user isn't nagged repeatedly).
    """
    _ensure_marker_dir()
    if _FDA_NAG_MARKER.exists():
        return

    binary = python_binary()
    msg = f"""
╭───────────────────────────────────────────────────────────────────╮
│  Full Disk Access required                                        │
├───────────────────────────────────────────────────────────────────┤
│ {reason:<65} │
│                                                                   │
│ The current Python binary needs Full Disk Access to read this:    │
│                                                                   │
│   {binary[:65]:<65} │
│                                                                   │
│ One-time setup:                                                   │
│   1. Open System Settings → Privacy & Security → Full Disk Access │
│   2. Click the + button                                           │
│   3. Press ⌘⇧G and paste the python path above                   │
│   4. Click Open, then enable the toggle next to python3.14        │
│   5. Re-run the AOS command                                       │
│                                                                   │
│ Or run: open "x-apple.systempreferences:com.apple.preference.\\    │
│           security?Privacy_AllFiles"                              │
│                                                                   │
│ Once granted, the prompt will not appear again. AOS will skip     │
│ this source until access is granted, but won't re-trigger TCC.    │
╰───────────────────────────────────────────────────────────────────╯
"""
    print(msg, file=sys.stderr)
    _FDA_NAG_MARKER.write_text(str(int(time.time())))


def clear_nag_marker() -> None:
    """Remove the nag marker. Called when access starts working."""
    try:
        _FDA_NAG_MARKER.unlink()
    except FileNotFoundError:
        pass


def open_fda_settings() -> bool:
    """Open the Full Disk Access pane in System Settings. Returns True on success."""
    try:
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"],
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def ensure_access(path: Path, friendly_name: str) -> bool:
    """Check FDA for ``path``. Print instructions if missing.

    Returns True if accessible, False otherwise. Callers should exit
    cleanly on False rather than attempting reads that would trigger
    further TCC interaction.
    """
    if not path.exists():
        return False
    if has_full_disk_access(path):
        clear_nag_marker()
        return True
    print_grant_instructions(f"AOS cannot read {friendly_name}")
    return False


# ── Snapshot cache ──────────────────────────────────────────────────────


def _cache_dir() -> Path:
    """Per-user cache for protected-file snapshots."""
    d = Path(tempfile.gettempdir()) / f"aos-snapshots-{os.getuid()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_age_sec(snap_path: Path) -> float:
    try:
        return time.time() - snap_path.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


def safe_snapshot(
    src: Path,
    cache_key: str,
    max_age_sec: int = DEFAULT_SNAPSHOT_TTL_SEC,
    include_sidecars: bool = True,
) -> Path | None:
    """Copy a TCC-protected SQLite DB to /tmp with mtime caching.

    Args:
        src: Path to the protected source database (e.g. chat.db).
        cache_key: Stable identifier for this snapshot (e.g. ``imessage``).
            Determines the snapshot filename. Multiple call sites can
            share the same key to dedupe.
        max_age_sec: If a cached snapshot is younger than this, return
            the cached path without re-copying. Set to 0 to force fresh.
        include_sidecars: Also copy ``-wal`` and ``-shm`` files if they
            exist next to the source (required for consistent SQLite
            reads on databases with WAL journaling).

    Returns:
        Path to the cached snapshot in /tmp, or None if copy failed
        (FDA missing). On None, ``ensure_access`` instructions will
        have been printed already.

    The snapshot is shared across all callers via cache_key, so calling
    this from different scripts within the same TTL window is free.
    """
    if not has_full_disk_access(src):
        print_grant_instructions(f"AOS cannot snapshot {cache_key} ({src.name})")
        return None

    cache = _cache_dir()
    snap = cache / f"{cache_key}.sqlite"

    # Re-use cached snapshot if it's fresh enough
    if snap.exists() and _snapshot_age_sec(snap) < max_age_sec:
        logger.debug("Reusing cached snapshot %s (age=%.0fs)",
                     snap, _snapshot_age_sec(snap))
        return snap

    # Copy fresh
    try:
        shutil.copy2(src, snap)
        if include_sidecars:
            for ext in ("-wal", "-shm"):
                sidecar = Path(str(src) + ext)
                if sidecar.exists():
                    try:
                        shutil.copy2(sidecar, str(snap) + ext)
                    except (PermissionError, OSError):
                        pass  # WAL not always present; ignore
        clear_nag_marker()
        return snap
    except (PermissionError, OSError) as e:
        logger.warning("Snapshot copy failed for %s: %s", src, e)
        print_grant_instructions(f"AOS cannot copy {cache_key} ({src.name})")
        return None


def cleanup_snapshots() -> int:
    """Delete all cached snapshots (e.g. on logout). Returns count removed."""
    cache = _cache_dir()
    n = 0
    for f in cache.glob("*"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n
