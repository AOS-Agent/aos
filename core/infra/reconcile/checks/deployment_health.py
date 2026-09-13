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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

HOME = Path.home()
AOS = HOME / "aos"
INSTANCE = HOME / ".aos"


class DeploymentHealthCheck(ReconcileCheck):
    name = "deployment_health"
    description = "Verify shipped components are deployed and functional"

    # The QMD collection this check keeps registered over the vault.
    QMD_COLLECTION_NAME = "vault"

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

    @staticmethod
    def _issue_id(issue: dict):
        """Stable identity for an issue, used to tell whether a specific
        gap survived a fix() pass. Two issues of the same kind (e.g. two
        services missing a venv) are different issues and must not be
        conflated."""
        kind = issue["kind"]
        if kind == "missing_venv":
            return (kind, issue.get("service"))
        if kind == "missing_cron_script":
            return (kind, issue.get("job"))
        if kind == "missing_git_hook":
            return (kind, issue.get("target"))
        return (kind, issue.get("message"))

    def fix(self) -> CheckResult:
        """Apply remedies, then re-run the detector and judge every remedy
        by whether its issue actually disappeared (aos#2345). A remedy
        must never self-report success — `_fix_qmd_collection` used to
        append to `self.fixed` whenever the `qmd` subprocess calls didn't
        raise, regardless of whether the vault collection came into
        existence, which produced a false "Fixed 1 deployment gap(s)" on
        every run forever."""
        before = list(self.issues)

        for issue in before:
            kind = issue["kind"]
            if kind == "missing_venv":
                self._fix_venv(issue)
            elif kind == "missing_cron_script":
                pass  # Can't auto-fix missing scripts — notify
            elif kind == "missing_git_hook":
                self._fix_git_hook(issue)
            elif kind == "missing_qmd_collection":
                self._fix_qmd_collection(issue)

        # The only trustworthy signal that a remedy took: re-run the same
        # detector that found the gap in the first place.
        self.check()
        after_ids = {self._issue_id(i) for i in self.issues}
        self.fixed = [i for i in before if self._issue_id(i) not in after_ids]
        remaining = list(self.issues)

        if remaining:
            if self.fixed:
                detail_fixed = "\n".join(f"  ✓ {i['message']}" for i in self.fixed)
                detail_remain = "\n".join(f"  ✗ {i['message']}" for i in remaining)
                detail = f"{detail_fixed}\n{detail_remain}"
            else:
                detail = "\n".join(f"  - {i['message']}" for i in remaining)
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message=f"Fixed {len(self.fixed)}, {len(remaining)} remain"
                if self.fixed
                else f"{len(remaining)} deployment gap(s) need attention",
                detail=detail,
                notify=True,
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
            # Nothing was wrong (fix() called directly against an already
            # healthy state) — never claim a fix that never happened.
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

    def _qmd_collection_exists(self, qmd: Path) -> bool:
        """The single source of truth for whether the vault collection is
        registered: `qmd collection show <name>` (aos#2345). `qmd status`
        aggregates every collection into one summary, so it stayed green
        while the specific collection this check owns was missing or had
        never been added under this name — a check that can't name what it
        verified isn't verifying it."""
        try:
            result = subprocess.run(
                [str(qmd), "collection", "show", self.QMD_COLLECTION_NAME],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            return False
        return result.returncode == 0

    def _check_qmd_collections(self):
        """Verify QMD vault collection is registered."""
        qmd = HOME / ".bun" / "bin" / "qmd"
        if not qmd.exists():
            return  # QMD not installed — skip

        if not self._qmd_collection_exists(qmd):
            self.issues.append({
                "kind": "missing_qmd_collection",
                "message": f"QMD has no '{self.QMD_COLLECTION_NAME}' collection — vault search is broken",
            })

    def _fix_qmd_collection(self, issue):
        """Bootstrap the vault collection.

        Deliberately does NOT record success itself — `fix()` re-runs
        `check()` (which re-verifies via `qmd collection show`) and decides
        from that whether this remedy actually took. `qmd` can exit 0 while
        refusing the action outright ("Collection 'vault' already exists.");
        trusting the exit code here is exactly the bug aos#2345 fixed.
        """
        qmd = HOME / ".bun" / "bin" / "qmd"
        vault = HOME / "vault"
        if not qmd.exists() or not vault.exists():
            return

        try:
            subprocess.run(
                [str(qmd), "collection", "add", self.QMD_COLLECTION_NAME, str(vault), "--pattern", "**/*.md"],
                capture_output=True, timeout=30,
            )
            subprocess.run(
                [str(qmd), "embed"],
                capture_output=True, timeout=120,
            )
        except Exception:
            pass
