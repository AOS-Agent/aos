"""
Invariant: every AOS-managed LaunchAgent shows a clean, readable name in
System Settings → General → Login Items & Extensions.

macOS Background Task Management displays a launchd item by its EXECUTABLE's
filename, not its label. A plist that launches /bin/bash, a venv python, or
node renders as "bash" / "python" / "node" — with twenty services that's an
unreadable wall of interpreters. The canonical fix (lib/launchers.py) points
each plist at a named exec-wrapper in ~/.aos/launchers/ ("AOS Bridge") whose
filename becomes the display name; the wrapper `exec`s the real command so
KeepAlive semantics are unchanged.

This check is the enforcement layer: any service that lands with a bare
interpreter as arg0 — a new install, a hand-written plist, a redeploy that
overwrote the transform — gets wrapped and its job reloaded, so future
services inherit clean names without their installers having to remember.

Scope: every deployed com.aos.* plist plus every registry-declared label
(covers services shipped under other prefixes, e.g. com.agent.*). Personal
agents outside both sets are left alone — they belong to the operator.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib import launchers
from lib.service_ctl import restart_launchagent
from lib.service_registry import load_registry

LA_DIR = Path.home() / "Library" / "LaunchAgents"


class LauncherNamingCheck(ReconcileCheck):
    name = "launcher_naming"
    description = "AOS LaunchAgents show readable names in Login Items (named launchers)"
    periodic_fix = True

    def precondition(self) -> bool:
        return LA_DIR.exists()

    def _targets(self) -> list[tuple[str, Path, str]]:
        """(label, plist_path, display_name) for every AOS-managed plist.

        Scope is the union of every deployed com.aos.* plist (services ship
        under that prefix even when not yet registry-declared) and every
        registry-declared label (covers other prefixes like com.agent.*).
        Registry display_name wins; otherwise derived from the label.
        """
        names: dict[str, str | None] = {}
        try:
            for svc in load_registry():
                names[svc.label] = svc.display_name
        except Exception:
            pass
        labels = set(names) | {
            p.stem for p in LA_DIR.glob("com.aos.*.plist")
        }
        out = []
        for label in sorted(labels):
            plist = LA_DIR / f"{label}.plist"
            if not plist.exists():
                continue
            name = names.get(label) or launchers.derive_display_name(label)
            out.append((label, plist, name))
        return out

    def _unwrapped(self) -> list[tuple[str, Path, str]]:
        bad = []
        for label, plist, name in self._targets():
            try:
                pl = launchers.read_plist(plist)
            except Exception:
                continue  # malformed plist is another check's problem
            args = launchers.effective_args(pl)
            if not args:
                continue
            if launchers.is_wrapped(pl):
                if not Path(args[0]).exists():
                    bad.append((label, plist, name))  # launcher file went missing
                continue
            if launchers.is_ugly_arg0(args[0]):
                bad.append((label, plist, name))
        return bad

    def check(self) -> bool:
        if not self.precondition():
            return False
        return not self._unwrapped()

    def fix(self) -> CheckResult:
        if not self.precondition():
            return CheckResult(
                self.name, Status.SKIP,
                "~/Library/LaunchAgents missing — nothing to verify",
            )
        fixed, failed = [], []
        for label, plist, name in self._unwrapped():
            try:
                changed, display = launchers.ensure_launcher(label, plist, name)
                if changed:
                    restart_launchagent(label, plist, actor="reconcile:launcher_naming")
                fixed.append(f"{label} → {display}")
            except Exception as e:
                failed.append(f"{label}: {e}")
        if failed:
            return CheckResult(
                self.name, Status.NOTIFY,
                f"wrapped {len(fixed)}, failed {len(failed)}",
                detail="; ".join(failed), notify=True,
            )
        return CheckResult(
            self.name, Status.FIXED,
            f"named launchers applied: {', '.join(fixed)}",
        )
