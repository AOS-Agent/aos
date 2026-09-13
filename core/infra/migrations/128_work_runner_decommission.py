"""
Migration 128: remove the work runner from the instance — the code is gone.

v0.7.7 deletes the work runner from the framework: `core/engine/work/runner.py`
(905 lines), `core/engine/work/proc_group.py`, `core/services/work_runner/`
(manifest + LaunchAgent template + main.py), `config/defaults/work-runner.yaml`,
the `work-runner` entry in `config/modules.yaml`, and the four CLI subcommands
(`work runner status|cancel|enable|disable`). `task_runs` held **zero rows on
every machine audited, ever** — built, deployed, KeepAlive'd, never once used to
delegate a task (work-engine audits 2026-08-20 §5/§7 and 2026-09-13 §6.5).

Migration 111 already switched it off fleet-wide, so why this one:

  1. **111 respects an explicit opt-in.** A machine where the operator wrote
     `work-runner` under `enabled:` in services.yaml was deliberately left
     running. That was right while the code existed. It no longer does — the
     plist would exec a `main.py` that is not in the tree, and launchd would
     retry it on a KeepAlive loop forever. An opt-in to something that cannot
     run is not a preference this migration can honour, so it is cleared, and
     the log says so rather than doing it quietly.
  2. **111's removal was conditional.** It only unlinked the plist on the path
     where it disabled the service. This one removes the deployed plist and the
     `~/.aos/launchers/` wrapper unconditionally.
  3. **The declaration outlived the service.** `work-runner` sat in
     `lib/default_off.py`'s DEFAULT_OFF, so the `default_off_services` reconcile
     check would have kept re-adding the name to `disabled:` every cycle — a
     permanent statement that a service which does not exist is switched off.
     The name leaves DEFAULT_OFF in the same commit; this migration removes it
     from `disabled:` and `enabled:` on machines that recorded it.

What it does NOT touch: `~/.aos/logs/work-runner/` and
`~/.aos/work/runner/worktrees/` (operator data — if anything was ever written
there it is the operator's to read and delete), and the empty `task_runs` table
in work.db (dropping a table is a schema change with no upside; it costs
nothing to leave an empty table behind a removed feature).

Not reversible: the service is gone from the framework, so there is nothing to
re-deploy. The code lives on in git history.

Idempotent: check() passes once no plist, no wrapper, no config file and no
services.yaml entry remain.
"""

from __future__ import annotations

DESCRIPTION = "Remove the work-runner LaunchAgent + instance config (service deleted in 0.7.7)"

import os
import subprocess
from pathlib import Path

LABEL = "com.aos.work-runner"
SERVICE = "work-runner"


# Resolved on every call, never captured at import — see default_off.py's own
# docstring (core/infra/lib/default_off.py) for why a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process. This one
# matters twice over: _clear_declaration() writes the same services.yaml that
# default_off.disable_service() does, and that helper already re-resolves.
def _plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _launcher() -> Path:
    return Path.home() / ".aos" / "launchers" / SERVICE


def _runner_config() -> Path:
    return Path.home() / ".aos" / "config" / "work-runner.yaml"


def _services_config() -> Path:
    return Path.home() / ".aos" / "config" / "services.yaml"


def _loaded() -> bool:
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _declared_names() -> set[str]:
    """`work-runner` wherever services.yaml still mentions it."""
    if not _services_config().exists():
        return set()
    try:
        import yaml
        raw = yaml.safe_load(_services_config().read_text())
    except Exception:  # noqa: BLE001
        return set()
    if not isinstance(raw, dict):
        return set()
    found = set()
    for key in ("enabled", "disabled"):
        values = raw.get(key)
        if isinstance(values, list) and SERVICE in {str(v).strip() for v in values}:
            found.add(key)
    return found


def _clear_declaration() -> list[str]:
    """Drop `work-runner` from both lists. Returns the keys it was removed from."""
    keys = _declared_names()
    if not keys:
        return []
    try:
        import yaml
        raw = yaml.safe_load(_services_config().read_text())
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, dict):
        return []
    header_lines = []
    for line in _services_config().read_text().splitlines():
        if line.startswith("#") or not line.strip():
            header_lines.append(line)
        else:
            break
    for key in keys:
        raw[key] = [v for v in raw[key] if str(v).strip() != SERVICE]
    try:
        body = yaml.safe_dump(raw, sort_keys=False, default_flow_style=False)
    except Exception:  # noqa: BLE001
        return []
    prefix = "\n".join(header_lines).rstrip("\n")
    _services_config().write_text((prefix + "\n\n" if prefix else "") + body)
    return sorted(keys)


def check() -> bool:
    """Applied when no trace of the service is left on this machine."""
    if _plist().exists() or _launcher().exists() or _runner_config().exists():
        return False
    if _loaded():
        return False
    return not _declared_names()


def up() -> bool:
    if _loaded():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, timeout=30,
        )
        print(f"  ✓ Booted out {LABEL}")

    for path, what in ((_plist(), "LaunchAgent plist"), (_launcher(), "launcher wrapper"),
                       (_runner_config(), "instance config")):
        if path.exists():
            try:
                path.unlink()
                print(f"  ✓ Removed {what}: {path}")
            except OSError as e:
                print(f"  ✗ Could not remove {path} ({e})")
                return False

    cleared = _clear_declaration()
    if cleared:
        where = " and ".join(f"`{k}:`" for k in cleared)
        print(f"  ✓ Cleared {SERVICE} from {where} in {_services_config()}")
        if "enabled" in cleared:
            print("     (it was an explicit opt-in — the service no longer exists "
                  "in the framework, so there is nothing left to opt in to)")

    if check():
        print("  · The work runner is fully decommissioned (0 task_runs rows, ever)")
        return True
    return False


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 128 already applied" if check() else ("Done" if up() else "Failed"))
