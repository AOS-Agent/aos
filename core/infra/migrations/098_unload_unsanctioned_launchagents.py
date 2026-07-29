"""
Migration 098: unload LaunchAgents the installer should never have loaded.

`install_launchagents` used to glob `config/launchagents/*` and `launchctl load`
everything it found. A glob can see filenames but not the service registry's
`status`, so every install force-loaded:

  - `listen`  — status: retired. core/services/README.md: "must **not** be loaded."
  - `qareen-tunnel` — a Cloudflare tunnel nobody opted into, rendered with its
    `__CLOUDFLARED__` placeholder unsubstituted (the installer only replaced
    `__HOME__`; the real renderer is core/qareen/services/tunnel_manager.py).
    The result is a plist whose program path is the literal string
    "__CLOUDFLARED__", loaded with KeepAlive — a permanent crash loop.

The installer is now registry-driven. This cleans up machines that already ran
the old one.

Deliberately conservative:
  - Only `retired` services are removed outright.
  - `optional` services (mesh, companion, crawler, memory, work-runner) are left
    exactly as they are — if one is loaded, the operator opted in on purpose
    (e.g. `work runner enable`) and this migration must not rip that out.
  - Only touches labels the framework ships in config/launchagents/. Instance
    LaunchAgents (envoy, ios-deploy, qareen-deploy, slack-watch, …) are never
    considered.
"""

DESCRIPTION = "Unload retired + placeholder-broken LaunchAgents"

import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
LA_DIR = HOME / "Library" / "LaunchAgents"
TEMPLATES_DIR = AOS_ROOT / "config" / "launchagents"

# Rendered with a placeholder the installer never substituted, and owned by an
# opt-in wizard rather than the installer.
TUNNEL_LABEL = "com.aos.qareen-tunnel"

PLACEHOLDER = "__CLOUDFLARED__"


def _framework_labels() -> set[str]:
    """Labels the framework ships a plist for — the only ones we may touch."""
    labels = set()
    if not TEMPLATES_DIR.is_dir():
        return labels
    for path in TEMPLATES_DIR.iterdir():
        name = path.name
        if name.endswith(".plist.template"):
            labels.add(name[: -len(".plist.template")])
        elif name.endswith(".plist"):
            labels.add(name[: -len(".plist")])
    return labels


def _retired_labels() -> set[str]:
    """Labels of services the registry marks `retired`."""
    sys.path.insert(0, str(AOS_ROOT / "core" / "infra" / "lib"))
    try:
        from service_registry import load_registry
    except Exception:
        return set()
    try:
        return {m.label for m in load_registry() if m.is_retired}
    except Exception:
        return set()


def _targets() -> list[str]:
    """Labels that should not be loaded on this machine."""
    framework = _framework_labels()
    targets = {label for label in _retired_labels() if label in framework}

    # The tunnel: remove only if it was rendered broken. A machine where the
    # operator genuinely enabled the tunnel has a fully-substituted plist
    # written by tunnel_manager, and must be left alone.
    tunnel_plist = LA_DIR / f"{TUNNEL_LABEL}.plist"
    if tunnel_plist.exists():
        try:
            if PLACEHOLDER in tunnel_plist.read_text():
                targets.add(TUNNEL_LABEL)
        except Exception:
            pass

    return sorted(targets)


def _still_present(label: str) -> bool:
    """True if the plist exists on disk or the job is registered with launchd."""
    if (LA_DIR / f"{label}.plist").exists():
        return True
    result = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True, timeout=15
    )
    return any(line.endswith(label) for line in result.stdout.splitlines())


def check() -> bool:
    """Applied when nothing that shouldn't be loaded is still around."""
    try:
        return not any(_still_present(label) for label in _targets())
    except Exception:
        return False


def up() -> bool:
    targets = _targets()
    if not targets:
        print("  Nothing to clean — no retired or broken LaunchAgents present")
        return True

    uid = os.getuid()
    for label in targets:
        plist = LA_DIR / f"{label}.plist"

        # bootout covers modern registration; unload covers legacy. Both are
        # best-effort: "not loaded" is the desired end state, not an error.
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{label}"],
            capture_output=True, text=True, timeout=15,
        )
        if plist.exists():
            subprocess.run(
                ["launchctl", "unload", "-w", str(plist)],
                capture_output=True, text=True, timeout=15,
            )
            try:
                plist.unlink()
                print(f"  Removed {label}")
            except Exception as e:
                print(f"  WARNING: could not remove {plist}: {e}")
        else:
            print(f"  Unloaded {label}")

    return True
