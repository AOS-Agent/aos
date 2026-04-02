"""
Deployment Health — verify that shipped components are actually deployed.

Catches the "shipped but never deployed" class of bugs:
- Services with plists but no venv
- Cron jobs referencing missing scripts
- Git hooks shipped but not installed
- QMD collections declared but not registered
"""

import shutil
import subprocess
from pathlib import Path

from ..base import CheckResult, ReconcileCheck, Status

HOME = Path.home()
AOS = HOME / "aos"
INSTANCE = HOME / ".aos"


class DeploymentHealthCheck(ReconcileCheck):
    name = "deployment_health"
    description = "Verify shipped components are deployed and functional"

    def __init__(self):
        self.issues = []
        self.fixed = []

    def check(self) -> bool:
        self.issues = []
        self._check_service_venvs()
        self._check_cron_commands()
        self._check_git_hooks()
        self._check_qmd_collections()
        return len(self.issues) == 0

    def fix(self) -> CheckResult:
        self.fixed = []

        for issue in list(self.issues):
            kind = issue["kind"]
            if kind == "missing_venv":
                self._fix_venv(issue)
            elif kind == "missing_cron_script":
                pass  # Can't auto-fix missing scripts — notify
            elif kind == "missing_git_hook":
                self._fix_git_hook(issue)
            elif kind == "missing_qmd_collection":
                self._fix_qmd_collection(issue)

        remaining = [i for i in self.issues if i not in self.fixed]

        if remaining and not self.fixed:
            detail = "\n".join(f"  - {i['message']}" for i in remaining)
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message=f"{len(remaining)} deployment gap(s) need attention",
                detail=detail,
                notify=True,
            )
        elif remaining:
            detail_fixed = "\n".join(f"  ✓ {i['message']}" for i in self.fixed)
            detail_remain = "\n".join(f"  ✗ {i['message']}" for i in remaining)
            return CheckResult(
                name=self.name,
                status=Status.FIXED,
                message=f"Fixed {len(self.fixed)}, {len(remaining)} remain",
                detail=f"{detail_fixed}\n{detail_remain}",
                notify=bool(remaining),
            )
        elif self.fixed:
            detail = "\n".join(f"  ✓ {i['message']}" for i in self.fixed)
            return CheckResult(
                name=self.name,
                status=Status.FIXED,
                message=f"Fixed {len(self.fixed)} deployment gap(s)",
                detail=detail,
            )
        else:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                message="All shipped components are deployed",
            )

    # ── Service venvs ────────────────────────────────────────────────────

    def _check_service_venvs(self):
        """For each service with a LaunchAgent plist, verify venv exists."""
        la_dir = HOME / "Library" / "LaunchAgents"
        if not la_dir.exists():
            return

        for plist in la_dir.glob("com.aos.*.plist"):
            svc_name = plist.stem.replace("com.aos.", "")

            # Check if this service has a pyproject.toml in framework
            svc_framework = AOS / "core" / "services" / svc_name
            if not (svc_framework / "pyproject.toml").exists():
                continue  # Not a Python service

            # Check instance venv
            svc_instance = INSTANCE / "services" / svc_name
            venv = svc_instance / ".venv"
            if not venv.exists() or not (venv / "bin" / "python3").exists():
                self.issues.append({
                    "kind": "missing_venv",
                    "service": svc_name,
                    "framework": str(svc_framework),
                    "instance": str(svc_instance),
                    "message": f"Service '{svc_name}' has LaunchAgent but no venv",
                })

    def _fix_venv(self, issue):
        """Create venv and install deps for a service."""
        issue["service"]
        svc_framework = Path(issue["framework"])
        svc_instance = Path(issue["instance"])

        svc_instance.mkdir(parents=True, exist_ok=True)

        # Copy pyproject.toml if not present
        dst_pyproject = svc_instance / "pyproject.toml"
        src_pyproject = svc_framework / "pyproject.toml"
        if not dst_pyproject.exists() and src_pyproject.exists():
            shutil.copy2(src_pyproject, dst_pyproject)

        # Create venv
        venv = svc_instance / ".venv"
        result = subprocess.run(
            ["python3", "-m", "venv", str(venv)],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            return

        # Install deps
        pip = venv / "bin" / "pip"
        result = subprocess.run(
            [str(pip), "install", "-e", str(svc_framework), "--quiet"],
            capture_output=True, timeout=120,
        )
        if result.returncode == 0:
            self.fixed.append(issue)

    # ── Cron commands ────────────────────────────────────────────────────

    def _check_cron_commands(self):
        """Verify each cron job's command script exists."""
        crons_yaml = AOS / "config" / "crons.yaml"
        if not crons_yaml.exists():
            return

        try:
            import yaml
            with open(crons_yaml) as f:
                data = yaml.safe_load(f)
            jobs = data.get("jobs", {})
        except Exception:
            return

        for name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            if not job.get("enabled", True):
                continue

            command = job.get("command", "").strip()
            if not command:
                continue

            # Extract the script path from "bash ~/aos/..." or "python3 ~/aos/..."
            parts = command.split()
            script_path = None
            for part in parts:
                expanded = part.replace("~/", str(HOME) + "/").replace("$HOME/", str(HOME) + "/")
                if expanded.startswith(str(HOME)) and not expanded.startswith(str(HOME) + "/."):
                    script_path = expanded
                    break

            if script_path and not Path(script_path).exists():
                self.issues.append({
                    "kind": "missing_cron_script",
                    "job": name,
                    "script": script_path,
                    "message": f"Cron '{name}' references missing script: {script_path}",
                })

    # ── Git hooks ────────────────────────────────────────────────────────

    def _check_git_hooks(self):
        """Verify shipped git hooks are installed."""
        # The pre-push hook source
        hook_source = AOS / ".git" / "hooks" / "pre-push"
        dev_hooks = HOME / "project" / "aos" / ".git" / "hooks" / "pre-push"

        # Check if we have a hook to install
        shipped_hook = AOS / "core" / "hooks" / "pre-push"
        if not shipped_hook.exists():
            return  # No hook shipped — nothing to check

        if not hook_source.exists():
            self.issues.append({
                "kind": "missing_git_hook",
                "target": str(hook_source),
                "source": str(shipped_hook),
                "message": "Pre-push hook not installed in ~/aos/.git/hooks/",
            })

        if (HOME / "project" / "aos" / ".git").exists() and not dev_hooks.exists():
            self.issues.append({
                "kind": "missing_git_hook",
                "target": str(dev_hooks),
                "source": str(shipped_hook),
                "message": "Pre-push hook not installed in ~/project/aos/.git/hooks/",
            })

    def _fix_git_hook(self, issue):
        """Copy hook to .git/hooks/ and make executable."""
        source = Path(issue["source"])
        target = Path(issue["target"])

        if not source.exists():
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o755)
        self.fixed.append(issue)

    # ── QMD collections ──────────────────────────────────────────────────

    def _check_qmd_collections(self):
        """Verify QMD vault collection is registered."""
        qmd = HOME / ".bun" / "bin" / "qmd"
        if not qmd.exists():
            return  # QMD not installed — skip

        try:
            result = subprocess.run(
                [str(qmd), "status"],
                capture_output=True, text=True, timeout=10,
            )
            if "Total:    0 files" in result.stdout or "No collections" in result.stdout:
                self.issues.append({
                    "kind": "missing_qmd_collection",
                    "message": "QMD has no collections — vault search is broken",
                })
        except Exception:
            pass

    def _fix_qmd_collection(self, issue):
        """Bootstrap the vault collection."""
        qmd = HOME / ".bun" / "bin" / "qmd"
        vault = HOME / "vault"
        if not qmd.exists() or not vault.exists():
            return

        try:
            subprocess.run(
                [str(qmd), "collection", "add", "vault", str(vault), "--pattern", "**/*.md"],
                capture_output=True, timeout=30,
            )
            subprocess.run(
                [str(qmd), "embed"],
                capture_output=True, timeout=120,
            )
            self.fixed.append(issue)
        except Exception:
            pass
