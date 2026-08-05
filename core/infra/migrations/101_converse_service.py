"""
Migration 101: Converse service registration (Wave 2 / T3).

NUMBERING NOTE: PLAN.md (~/.aos/tmp/sessions-build/PLAN.md §8 Phase B) calls
this `100_converse_service.py`. Migration 100 landed first as
`100_converse_init.py` (Wave 0/T1 — see that migration's own numbering
note: 099 was already taken by 099_cmux_socket_control.py by the time this
build ran, so every PLAN.md Phase B/C/D/E migration shifts up by one). This
is that ripple: 101 here, and 102/103/104 for the later phases
(101_envoy_to_converse.py -> 102, 102_sentinel_on_converse.py -> 103,
103_converse_cleanup.py -> 104) when those waves are built.

Registers the Converse supervisor daemon (core/services/converse) the same
way migration 092 registered work-runner: additive, and DELIBERATELY DOES
NOT LOAD THE LAUNCHAGENT. Converse is feature-complete as of this build
(T3) but ships OFF until the operator runs the Sana cutover (PLAN.md §8
Phase B / Wave 3 T5) — the component-lifecycle rule ("what ships, what
configures, what happens at runtime") is honored by making that inertness
structural, not a config toggle alone:

1. Deploys the rendered plist to ~/Library/LaunchAgents/com.aos.converse.plist
   from core/services/converse/com.aos.converse.plist.template — but with
   RunAtLoad=false and Disabled=true baked into the template itself, and
   this migration never calls `launchctl bootstrap`/`load`. A plist present
   on disk but never bootstrapped is invisible to launchd entirely; even if
   some future step bootstraps it, RunAtLoad=false + Disabled=true mean it
   still won't start without an explicit kickstart. Verified safe against
   ServiceLoadedCheck (core/infra/reconcile/checks/service_loaded.py): the
   service.yaml below declares status=optional, and that check's own
   docstring is explicit — "optional: if loaded, held to the same health
   bar; if absent, that is fine (never restarted)" — so an unbootstrapped
   optional plist is never auto-started by reconcile.
2. core/services/converse/service.yaml already ships with this code (T3) —
   nothing to write; this migration just confirms it resolves through the
   registry (core/infra/lib/service_registry.py) so a schema regression is
   caught at migration time, not silently at the next reconcile pass.
3. ~/.aos/logs/converse/ (service + per-turn logs) — already created by
   migration 100, ensured here again for a machine that skipped 100's dir
   step somehow (defensive, not authoritative).

Everything else (schema, config, work/log dirs) was migration 100's job —
this migration is purely "the daemon now exists, here is its dormant
LaunchAgent," matching PLAN.md §8's "Sentinel never down" incremental
posture: zero behavior change for any existing user until an operator
explicitly flips this on.

Idempotent: deterministic plist re-render + write; check() confirms the
deployed plist matches the template and is NOT loaded (a machine where an
operator has since done the Wave-3 cutover and loaded it manually is left
alone — check() only asserts non-drift of file content, not launchd state,
so a manual cutover doesn't make this migration re-run and fight it).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DESCRIPTION = "Register Converse supervisor service (LaunchAgent installed disabled — ships OFF)"

HOME = Path.home()
AOS_ROOT = HOME / "aos"

SERVICE_DIR = AOS_ROOT / "core" / "services" / "converse"
TEMPLATE_PATH = SERVICE_DIR / "com.aos.converse.plist.template"
SERVICE_MANIFEST = SERVICE_DIR / "service.yaml"

PLIST_NAME = "com.aos.converse"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"

LOG_DIR = HOME / ".aos" / "logs" / "converse"


def _render() -> str | None:
    if not TEMPLATE_PATH.exists():
        return None
    return TEMPLATE_PATH.read_text().replace("__HOME__", str(HOME))


def _is_loaded() -> bool:
    """True if launchd currently knows about com.aos.converse at all. Used
    only by check()'s belt-and-suspenders assertion that this migration
    never itself loads the job — NOT used to decide whether to act (this
    migration never bootstraps/kickstarts, full stop)."""
    try:
        result = subprocess.run(
            ["launchctl", "list", PLIST_NAME], capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0


def _load_service_registry():
    """Import core/infra/lib/service_registry.py by locating it relative to
    this migration file, same convention as migration 100's converse/db.py
    loader — works whether this runs from ~/aos or the dev worktree."""
    core_dir = next((p for p in Path(__file__).resolve().parents if p.name == "core"), None)
    if core_dir is None:
        return None
    lib_dir = core_dir / "infra" / "lib"
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))
    try:
        import service_registry  # type: ignore

        return service_registry
    except Exception as e:
        print(f"       WARNING: could not import service_registry: {e}")
        return None


def check() -> bool:
    """Applied when: the deployed plist exists and matches the rendered
    (disabled) template, the log dir exists, and the service manifest is
    present and parses through the registry. Does NOT check launchd load
    state — see module docstring."""
    if not LOG_DIR.exists():
        return False
    if not SERVICE_MANIFEST.exists():
        return False

    expected = _render()
    if expected is None:
        # Template missing from this checkout — nothing this migration can
        # do; treat as not-yet-applied so a re-run surfaces the problem
        # loudly via up() rather than silently reporting success.
        return False
    if not PLIST_PATH.exists():
        return False
    if PLIST_PATH.read_text() != expected:
        return False

    registry = _load_service_registry()
    if registry is not None:
        try:
            reg = registry.load_registry()
        except registry.ManifestError as e:
            print(f"       WARNING: service registry invalid: {e}")
            return False
        manifest = reg.by_name("converse")
        if manifest is None or manifest.status != "optional":
            return False

    return True


def up() -> bool:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"       Dir:    {LOG_DIR}")

    content = _render()
    if content is None:
        print(f"  ✗ Converse plist template not found at {TEMPLATE_PATH}")
        return False

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(content)
    print(f"  ✓ Deployed {PLIST_PATH} (RunAtLoad=false, Disabled=true — NOT bootstrapped)")

    if not SERVICE_MANIFEST.exists():
        print(f"  ✗ Missing {SERVICE_MANIFEST} (should ship with this code change)")
        return False

    registry = _load_service_registry()
    if registry is not None:
        try:
            reg = registry.load_registry()
        except registry.ManifestError as e:
            print(f"  ✗ Service registry failed to validate with converse's manifest present: {e}")
            return False
        manifest = reg.by_name("converse")
        if manifest is None:
            print("  ✗ converse manifest did not resolve through the registry after install")
            return False
        print(f"  ✓ Registered in service registry: status={manifest.status} label={manifest.label}")
    else:
        print("  ⚠ service_registry module unavailable — skipped registry validation (manifest file is still in place)")

    if _is_loaded():  # pragma: no cover - only true if an operator already cut over by hand
        print("  ⚠ com.aos.converse is already loaded in launchd (manual cutover?) — left untouched")
    else:
        print("  ✓ com.aos.converse is NOT loaded — daemon ships off, as intended")

    return True


if __name__ == "__main__":
    if check():
        print("Migration 101 already applied")
    else:
        print("Done" if up() else "Failed")
