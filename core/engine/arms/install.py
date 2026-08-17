"""Installation — converge a module toward `active`.

Declarative, not imperative. Every step knows how to answer "am I already
satisfied?", so `apply` converges rather than replays. That is what makes
install, repair and drift-correction the same code path: running it twice is
a no-op, and running it against a half-installed module finishes the job.

This matters because the alternative is already in this repo's history. n8n's
LaunchAgent was deployed while its binary never was, and nothing on the machine
was responsible for noticing the gap — a crash loop nobody owned. A converge
loop cannot produce that state: the plist step is not satisfied until the thing
it launches exists.

Steps deliberately NOT automated:
  * Full Disk Access and other TCC grants — macOS requires a human at the
    System Settings pane. The step reports what is needed and stops.
  * Anything requiring a secret that is not yet in the Keychain. Install stops
    with the name of what is missing rather than half-configuring a service.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import Manifest, Module, expand_path, load_manifest
from .probe import probe_module, venv_usable

AOS_DIR = Path.home() / "aos"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


class InstallRefused(Exception):
    pass


@dataclass
class Step:
    kind: str            # brew | dirs | launchagent | service | secret | manual
    target: str
    detail: str = ""
    satisfied: bool = False
    blocking: bool = False   # cannot be automated; needs the operator

    def __str__(self) -> str:
        mark = "✓" if self.satisfied else ("!" if self.blocking else " ")
        tail = f"  — {self.detail}" if self.detail else ""
        return f"  {mark} {self.kind:<12} {self.target}{tail}"


@dataclass
class Plan:
    module: str
    steps: list[Step] = field(default_factory=list)

    @property
    def pending(self) -> list[Step]:
        return [s for s in self.steps if not s.satisfied and not s.blocking]

    @property
    def blocked(self) -> list[Step]:
        return [s for s in self.steps if s.blocking and not s.satisfied]

    @property
    def already_done(self) -> bool:
        return not self.pending and not self.blocked


def _brew_installed(formula: str) -> bool:
    if not shutil.which("brew"):
        return False
    return subprocess.run(
        ["brew", "list", "--formula", formula], capture_output=True
    ).returncode == 0


def _secret_present(name: str) -> bool:
    helper = AOS_DIR / "core" / "bin" / "cli" / "agent-secret"
    if not helper.exists():
        return False
    # Never capture the value — only whether the lookup succeeds.
    return subprocess.run(
        [str(helper), "get", name], capture_output=True
    ).returncode == 0


def _template_for(label: str) -> Path | None:
    d = AOS_DIR / "config" / "launchagents"
    for cand in (d / f"{label}.plist.template", d / f"{label}.plist"):
        if cand.exists():
            return cand
    return None


def _render_plist(template: Path) -> str:
    text = template.read_text().replace("__HOME__", str(Path.home()))
    return text


def plan(module_id: str, manifest: Manifest | None = None, force: bool = False) -> Plan:
    manifest = manifest or load_manifest()
    mod: Module | None = manifest.get(module_id)
    if mod is None:
        raise InstallRefused(f"unknown module '{module_id}'")

    recipe = getattr(mod, "install", None) or {}
    p = Plan(module=mod.id)

    # 1. Secrets gate FIRST — never half-configure a service that cannot run.
    for name in mod.secrets:
        p.steps.append(Step(
            "secret", name,
            "present in Keychain" if _secret_present(name) else "MISSING — set with: agent-secret set " + name,
            satisfied=_secret_present(name),
            blocking=not _secret_present(name),
        ))

    for formula in recipe.get("brew", []):
        ok = _brew_installed(formula)
        p.steps.append(Step("brew", formula, "already installed" if ok else "brew install", satisfied=ok))

    for d in recipe.get("dirs", []):
        path = expand_path(d)
        p.steps.append(Step("dirs", str(path), "exists" if path.is_dir() else "create", satisfied=path.is_dir()))

    # 2. Service venv — and it must be USABLE, not merely present. A venv on a
    #    broken interpreter satisfies `exists` and can never take a package.
    svc = recipe.get("service")
    if svc:
        venv = Path.home() / ".aos" / "services" / svc / ".venv"
        usable = venv_usable(venv)
        ok = usable is True
        detail = (
            "venv healthy" if ok
            else "venv interpreter broken — will rebuild" if usable is False
            else "deploy service venv"
        )
        p.steps.append(Step("service", svc, detail, satisfied=ok))

    # 3. LaunchAgents last — a plist must never be loaded before the thing it
    #    launches exists. This ordering is the n8n crash-loop guard.
    #
    #    The goal is a WORKING module, not a module that matches the template.
    #    Several deployed plists legitimately differ from their templates: they
    #    exec ~/.aos/launchers/<Display Name> (so Login Items shows a real name)
    #    and carry EnvironmentVariables the template never had. Rewriting those
    #    from a stale template would strip the launcher and the env and restart
    #    a healthy service to make it worse. So: if the module already probes
    #    healthy, its plist is left alone unless the operator forces it.
    running_ok = probe_module(mod).status in ("active", "degraded")
    for label in mod.services:
        target = LAUNCH_AGENTS / f"{label}.plist"
        template = _template_for(label)

        if target.exists() and running_ok and not force:
            p.steps.append(Step(
                "launchagent", label, "in place and working — left untouched", satisfied=True
            ))
            continue

        if template is None:
            p.steps.append(Step(
                "launchagent", label,
                "no template in config/launchagents — cannot install",
                blocking=True,
            ))
            continue
        rendered = _render_plist(template)
        current = target.read_text() if target.exists() else None
        ok = current == rendered
        p.steps.append(Step(
            "launchagent", label,
            "up to date" if ok else ("REPLACE from template (forced)" if current else "install plist"),
            satisfied=ok,
        ))

    for note in recipe.get("manual", []):
        p.steps.append(Step("manual", note, "requires you — cannot be automated", blocking=True))

    return p


def apply(plan_obj: Plan, manifest: Manifest | None = None) -> tuple[list[str], list[str]]:
    """Execute only the unsatisfied, non-blocking steps. Returns (done, failed)."""
    manifest = manifest or load_manifest()
    mod = manifest.get(plan_obj.module)
    done: list[str] = []
    failed: list[str] = []

    if plan_obj.blocked:
        names = ", ".join(s.target for s in plan_obj.blocked)
        raise InstallRefused(
            f"blocked — these need you first: {names}. Nothing was changed."
        )

    for step in plan_obj.pending:
        try:
            if step.kind == "brew":
                r = subprocess.run(["brew", "install", step.target], capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    failed.append(f"brew {step.target}: {(r.stderr or '').strip()[:200]}")
                    continue
                done.append(f"installed {step.target}")

            elif step.kind == "dirs":
                Path(step.target).mkdir(parents=True, exist_ok=True)
                done.append(f"created {step.target}")

            elif step.kind == "service":
                aos = AOS_DIR / "core" / "bin" / "cli" / "aos"
                r = subprocess.run([str(aos), "deploy", step.target], capture_output=True, text=True, timeout=1800)
                if r.returncode != 0:
                    failed.append(f"deploy {step.target}: {(r.stderr or r.stdout or '').strip()[-200:]}")
                    continue
                done.append(f"deployed {step.target}")

            elif step.kind == "launchagent":
                template = _template_for(step.target)
                if template is None:
                    failed.append(f"{step.target}: template vanished")
                    continue
                rendered = _render_plist(template)
                # Refuse to write a plist with unresolved placeholders. install.sh
                # learned this the hard way: a literal __PLACEHOLDER__ as the
                # program path, loaded with KeepAlive, is a permanent crash loop
                # for a feature nobody enabled.
                import re
                if re.search(r"__[A-Z_]{2,}__", rendered):
                    failed.append(f"{step.target}: unresolved placeholders — not written")
                    continue
                LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
                target = LAUNCH_AGENTS / f"{step.target}.plist"
                uid = str(os.getuid())
                if target.exists():
                    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{step.target}"], capture_output=True)
                target.write_text(rendered)
                r = subprocess.run(
                    ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
                    capture_output=True, text=True,
                )
                if r.returncode != 0 and "already bootstrapped" not in (r.stderr or ""):
                    failed.append(f"bootstrap {step.target}: {(r.stderr or '').strip()[:200]}")
                    continue
                done.append(f"installed + started {step.target}")

        except Exception as exc:  # noqa: BLE001 — one bad step must not abort the rest
            failed.append(f"{step.kind} {step.target}: {exc}")

    # Verify by re-probing, not by assuming the steps worked.
    if mod is not None:
        state = probe_module(mod)
        done.append(f"verified: {mod.id} is now {state.status}" + (f" ({state.why})" if state.why else ""))
        if state.status in ("broken", "absent"):
            failed.append(f"{mod.id} did not come up — still {state.status}")

    return done, failed
