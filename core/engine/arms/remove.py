"""Removal — converge a module to `absent`.

Removal is a plan you can read before it runs. `plan()` returns the exact steps;
`apply()` executes them. Nothing here runs implicitly: the CLI requires --apply,
because a settings panel that quietly rips services off a Mac is not a feature.

Deliberate boundaries:
  * core-tier modules are never removable (see Module.removable)
  * `foreign` entries are never touched — AOS observes them, it does not own them
  * user DATA is never deleted by a removal; only service artifacts are.
    Vaults, databases and models survive. `--purge` is a separate, explicit ask.
  * Keychain secrets are reported, never auto-deleted. A token you can re-paste
    is cheap; a token you cannot recover is not.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .intent import SERVICES_CONFIG, module_service_names, set_disabled
from .manifest import Manifest, Module, load_manifest


@dataclass
class Step:
    action: str        # unload | delete | intent | note
    target: str
    detail: str = ""
    destructive: bool = True

    def __str__(self) -> str:
        mark = " " if not self.destructive else "!"
        return f"  {mark} {self.action:<7} {self.target}{'  — ' + self.detail if self.detail else ''}"


class RemovalRefused(Exception):
    pass


def _uid() -> str:
    return str(os.getuid())


def _loaded(label: str) -> bool:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    return any(line.split("\t")[-1] == label for line in out.splitlines()[1:])


def _launcher_for(plist: Path) -> Path | None:
    """Services are started via a launcher file whose NAME is the Login Items
    display string. Removing the plist without it leaves an orphan behind."""
    try:
        data = plistlib.loads(plist.read_bytes())
    except (OSError, ValueError):
        return None
    args = data.get("ProgramArguments") or []
    if not args:
        return None
    p = Path(str(args[0]))
    return p if p.parent == Path.home() / ".aos" / "launchers" else None


def plan(module_id: str, manifest: Manifest | None = None, purge: bool = False) -> list[Step]:
    manifest = manifest or load_manifest()
    mod: Module | None = manifest.get(module_id)
    if mod is None:
        if any(f.label == module_id or f.name == module_id for f in manifest.foreign):
            raise RemovalRefused(
                f"'{module_id}' is a foreign agent — observed on this machine but not "
                "owned by AOS. It is shown read-only and must be removed by whoever "
                "installed it."
            )
        raise RemovalRefused(f"unknown module '{module_id}'")

    if not mod.removable:
        raise RemovalRefused(
            f"'{mod.id}' is tier core — the spine of the system. Core modules are not "
            "removable from the arms panel."
        )

    steps: list[Step] = []
    agents = Path.home() / "Library" / "LaunchAgents"

    for label in mod.services:
        plist = agents / f"{label}.plist"
        if _loaded(label):
            steps.append(Step("unload", label, f"launchctl bootout gui/{_uid()}"))
        if plist.exists():
            launcher = _launcher_for(plist)
            steps.append(Step("delete", str(plist)))
            if launcher and launcher.exists():
                steps.append(Step("delete", str(launcher), "Login Items launcher"))

    if purge:
        svc = Path.home() / ".aos" / "services" / mod.id.replace("-", "_")
        if svc.is_dir():
            steps.append(Step("delete", str(svc), "service venv + instance files"))

    for secret in mod.secrets:
        steps.append(
            Step("note", secret, "Keychain secret left in place — remove manually if desired", destructive=False)
        )

    names = module_service_names(mod)
    if names:
        steps.append(Step(
            "intent", str(SERVICES_CONFIG),
            "disabled: " + ", ".join(names),
            destructive=False,
        ))

    if not any(s.destructive for s in steps):
        steps.insert(0, Step("note", mod.id, "nothing installed to remove", destructive=False))
    return steps


def apply(steps: list[Step]) -> tuple[list[str], list[str]]:
    """Execute a plan. Returns (done, failed). Never raises on a single failure —
    a half-removed module must still report what it managed to do."""
    done: list[str] = []
    failed: list[str] = []

    for step in steps:
        try:
            if step.action == "unload":
                r = subprocess.run(
                    ["launchctl", "bootout", f"gui/{_uid()}/{step.target}"],
                    capture_output=True, text=True,
                )
                msg = (r.stderr or "").strip()
                # bootout on an already-stopped job is not a failure
                if r.returncode != 0 and "No such process" not in msg:
                    failed.append(f"unload {step.target}: {msg}")
                else:
                    done.append(f"unloaded {step.target}")

            elif step.action == "delete":
                p = Path(step.target)
                if p.is_dir():
                    subprocess.run(["rm", "-rf", str(p)], check=True)
                elif p.exists():
                    p.unlink()
                done.append(f"deleted {step.target}")

            elif step.action == "intent":
                # Written to the file reconcile already reads, so the panel and
                # the reconciler can never hold different opinions.
                _, _, names = step.detail.partition("disabled: ")
                wanted = [n.strip() for n in names.split(",") if n.strip()]
                now = set_disabled(wanted, disabled=True)
                done.append(f"recorded opt-out in services.yaml ({', '.join(wanted)})")
                if now:
                    done.append(f"disabled services now: {', '.join(now)}")

        except Exception as exc:  # noqa: BLE001 — a failed step must not abort the rest
            failed.append(f"{step.action} {step.target}: {exc}")

    return done, failed
