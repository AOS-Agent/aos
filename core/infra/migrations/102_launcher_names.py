"""
Migration 102: Clean Login Items names for every AOS LaunchAgent.

macOS Background Task Management (System Settings → General → Login Items &
Extensions) displays a launchd item by its EXECUTABLE's filename, not its
label — so a machine running fifteen AOS services shows an unreadable wall
of "python3", "bash", and "node" entries with no way to tell the bridge
from the transcriber.

The canonical transform lives in core/infra/lib/launchers.py: each deployed
plist is rewritten to point at a small exec-wrapper in ~/.aos/launchers/
whose FILENAME is the display name ("AOS Bridge"). The wrapper `exec`s the
real command, so launchd still supervises the true process — KeepAlive,
ThrottleInterval, and exit-status semantics are unchanged — while BTM shows
the clean name.

This migration applies the transform to every registry-declared service
whose plist is deployed on this machine, then reloads each job through the
guarded service_ctl choke-point so BTM re-registers the item under its new
name. Going forward the launcher_naming reconcile check (periodic_fix)
enforces the invariant for services installed after this migration ran.

Scope: registry services only. Personal/operator LaunchAgents outside the
registry are the operator's own and are left untouched.

Idempotent: ensure_launcher() is a no-op on an already-wrapped plist, and
check() reports done when no registry plist still execs a bare interpreter.
"""

import sys
from pathlib import Path


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process.
def _home() -> Path:
    return Path.home()


def _la_dir() -> Path:
    return _home() / "Library" / "LaunchAgents"


# One-time, import-time sys.path bootstrap — see migration 119's identical
# comment: this runs once, right now, and is never consulted again by
# check()/up() after a test's sandbox patch has expired.
sys.path.insert(0, str(_home() / "aos"))


def _targets():
    """Every AOS-managed plist: deployed com.aos.* plists plus every
    registry-declared label (covers other prefixes like com.agent.*)."""
    from core.infra.lib import launchers
    from core.infra.lib.service_registry import load_registry

    names = {}
    try:
        for svc in load_registry():
            names[svc.label] = svc.display_name
    except Exception:
        pass
    la_dir = _la_dir()
    labels = set(names) | {p.stem for p in la_dir.glob("com.aos.*.plist")}
    out = []
    for label in sorted(labels):
        plist = la_dir / f"{label}.plist"
        if not plist.exists():
            continue
        name = names.get(label) or launchers.derive_display_name(label)
        out.append((label, plist, name))
    return out


def _unwrapped():
    from core.infra.lib import launchers

    bad = []
    for label, plist, name in _targets():
        try:
            pl = launchers.read_plist(plist)
        except Exception:
            continue
        args = launchers.effective_args(pl)
        if not args or launchers.is_wrapped(pl):
            continue
        if launchers.is_ugly_arg0(args[0]):
            bad.append((label, plist, name))
    return bad


def check() -> bool:
    """Done when no deployed registry plist still execs a bare interpreter."""
    if not _la_dir().exists():
        return True  # nothing deployed on this machine — nothing to rename
    return not _unwrapped()


def up() -> bool:
    from core.infra.lib import launchers
    from core.infra.lib.service_ctl import restart_launchagent

    failures = []
    for label, plist, name in _unwrapped():
        try:
            changed, display = launchers.ensure_launcher(label, plist, name)
            if changed:
                # Full bootout/bootstrap so BTM re-registers the display name.
                restart_launchagent(label, plist, actor="migration:102")
            print(f"  {label} → {display}")
        except Exception as e:
            failures.append(f"{label}: {e}")
            print(f"  FAILED {label}: {e}")
    if failures:
        print(f"  {len(failures)} service(s) not renamed — reconcile "
              "launcher_naming will retry")
    return not failures
