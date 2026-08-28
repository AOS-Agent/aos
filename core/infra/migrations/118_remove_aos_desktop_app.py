"""
Migration 118: remove the retired AOS desktop app from each machine
(operator-approved 2026-08-28).

The desktop app is gone from the framework in the same commit as this
migration: `apps/desktop/` and `core/bin/cli/release-app` are deleted, and the
app half of `/ship` (Step 2b) and of docs/release-channels.md with them. The
app's source is not lost — it lives on in this repo's history and, in fuller
form, in the archived project `~/project/_archive/aos-app`.

This migration cleans the two per-machine remnants:

  - /Applications/AOS.app          the installed bundle (CFBundleIdentifier
                                   am.hish.aos), last published 0.7.6
  - ~/Library/Caches/am.hish.aos   its cache directory

Both are removed only when they exist, and the bundle only when its
Info.plist actually identifies it as am.hish.aos — a differently-owned
/Applications/AOS.app is somebody else's application and is left alone.

macOS-only. On Linux, or on a Mac that never had the app, this is a no-op and
check() reports it as already applied.

╔══════════════════════════════════════════════════════════════════════════╗
║  DO NOT EXTEND THIS MIGRATION TO THE PORTAL DOOR.                        ║
║                                                                          ║
║  These four names look like they belong to the same retirement. They do  ║
║  not. They are the LIVE portal — the running door on this Mac and on     ║
║  faisal-mini, and the Cloudflare tunnel that Talk rides:                 ║
║                                                                          ║
║      am.hish.qren-door        LaunchAgent — the door process             ║
║      am.hish.qren-tunnel      LaunchAgent — the cloudflared tunnel       ║
║      ~/.aos/bin/portal-door   the door binary                            ║
║      ~/.aos/data/workspaces.db  the workspace records it serves          ║
║                                                                          ║
║  The `am.hish.` prefix is shared history, not shared lifecycle: the door ║
║  was built in the app's repo and outlived it. Booting out either agent,  ║
║  or deleting either file, takes the portal down for every signed-in      ║
║  surface and cannot be undone by re-running anything here.               ║
║                                                                          ║
║  Nothing in this file touches ~/Library/LaunchAgents, ~/.aos/bin or      ║
║  ~/.aos/data, and nothing added to it ever should.                       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

DESCRIPTION = "Remove the retired AOS desktop app (bundle + cache)"

import shutil
import sys
from pathlib import Path

HOME = Path.home()

APP = Path("/Applications/AOS.app")
CACHE = HOME / "Library" / "Caches" / "am.hish.aos"

BUNDLE_ID = b"am.hish.aos"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_our_app(app: Path) -> bool:
    """True when *app* is the AOS bundle this migration retires.

    The identifier is matched by scanning the Info.plist bytes rather than by
    parsing it: the string is stored literally in both the XML and the binary
    plist formats, and `plistlib` is not importable on every Python this runs
    under (the Homebrew 3.14 on the operator's Mac has a broken pyexpat, and a
    migration that cannot be imported cannot be skipped either).

    A bundle with no readable Info.plist is a half-written or half-deleted
    install of ours — the only way an /Applications/AOS.app gets into that
    state — so it counts. A bundle that reads cleanly and does not carry our
    identifier belongs to someone else and does not.
    """
    plist = app / "Contents" / "Info.plist"
    if not plist.is_file():
        return True
    try:
        return BUNDLE_ID in plist.read_bytes()
    except OSError:
        return True


def check() -> bool:
    """True when there is nothing left to do."""
    if not _is_macos():
        return True
    if CACHE.exists():
        return False
    if APP.exists() and _is_our_app(APP):
        return False
    return True


def up() -> bool:
    if not _is_macos():
        print("  not macOS — nothing to remove")
        return True

    removed = []

    if APP.exists():
        if _is_our_app(APP):
            shutil.rmtree(APP, ignore_errors=True)
            removed.append(str(APP))
        else:
            print(f"  {APP} is not {BUNDLE_ID.decode()} — left alone")
    if CACHE.exists():
        shutil.rmtree(CACHE, ignore_errors=True)
        removed.append(str(CACHE))

    print(f"  removed: {', '.join(removed) if removed else 'nothing (already clean)'}")
    return check()


def down() -> bool:
    """Not reversible, deliberately.

    Restoring the app would mean re-installing a signed bundle this repo no
    longer builds — there is no `release-app` any more and no `apps/desktop/`
    to build from. The source is recoverable (git history here; the archived
    aos-app project), a reinstall is not something a migration can do.
    """
    return False
