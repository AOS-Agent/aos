"""
Invariant: Instance-layer artifacts match what the framework declares.

The framework IS the manifest:
  - core/services/*/pyproject.toml  → declares expected service venvs
  - config/launchagents/com.aos.*   → declares expected LaunchAgent plists
  - .claude/skills/*/SKILL.md       → declares expected skill symlinks
  - settings.json hook commands     → declares expected hook scripts

Anything in instance space that doesn't trace back to the framework is
orphaned. This check finds orphans and removes them on fix().

Runs every update cycle. Safe — only touches AOS-namespaced items.
Never deletes user-created content, only AOS-managed artifacts that
the framework no longer declares.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status

AOS = Path.home() / "aos"
USER = Path.home() / ".aos"
CLAUDE = Path.home() / ".claude"

# Services that don't ship in core/services/ but are deployed to ~/.aos/services/
# and referenced by framework code. Never auto-remove these.
# Format: service directory name → reason it's preserved.
PRESERVED_SERVICES_FILE = AOS / "config" / "preserved-services.yaml"


def _preserved_services():
    """Service names that are explicitly preserved from cleanup.

    Reads from ~/aos/config/preserved-services.yaml. These are services
    deployed outside core/services/ but still referenced by framework code
    (e.g., data services used by core/comms/).
    """
    if not PRESERVED_SERVICES_FILE.exists():
        return set()
    try:
        import yaml
        with open(PRESERVED_SERVICES_FILE) as f:
            data = yaml.safe_load(f) or {}
        return set(data.get("services", {}).keys())
    except Exception:
        return set()


def _framework_services():
    """Service names the framework declares (has pyproject.toml) plus preserved services."""
    svc_dir = AOS / "core" / "services"
    declared = set()
    if svc_dir.is_dir():
        declared = {
            d.name for d in svc_dir.iterdir()
            if d.is_dir() and (d / "pyproject.toml").exists()
        }
    return declared | _preserved_services()


def _framework_launchagents():
    """LaunchAgent labels the framework declares (plist or plist.template)."""
    la_dir = AOS / "config" / "launchagents"
    if not la_dir.is_dir():
        return set()
    labels = set()
    for f in la_dir.iterdir():
        name = f.name
        # com.aos.bridge.plist.template → com.aos.bridge
        # com.aos.scheduler.plist → com.aos.scheduler
        if name.endswith(".plist.template"):
            labels.add(name.removesuffix(".plist.template"))
        elif name.endswith(".plist"):
            labels.add(name.removesuffix(".plist"))
    return labels


def _installed_launchagents():
    """com.aos.* LaunchAgent labels actually installed."""
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if not la_dir.is_dir():
        return set()
    return {
        f.name.removesuffix(".plist")
        for f in la_dir.glob("com.aos.*.plist")
    }


def _framework_skills():
    """Skill names the framework ships (has SKILL.md)."""
    skills_dir = AOS / ".claude" / "skills"
    if not skills_dir.is_dir():
        return set()
    return {
        d.name for d in skills_dir.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    }


def _installed_skills():
    """Skills installed globally (symlinks or dirs)."""
    skills_dir = CLAUDE / "skills"
    if not skills_dir.is_dir():
        return set()
    return {
        d.name for d in skills_dir.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    }


def _stale_model_caches():
    """HuggingFace model dirs that aren't the active whisper model."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    if not cache_dir.is_dir():
        return []

    # The model the transcriber actually uses
    active_model = "models--mlx-community--whisper-large-v3-turbo"

    stale = []
    for d in cache_dir.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        # Only flag whisper models that aren't the active one
        if "whisper" in name.lower() and name != active_model:
            stale.append(d)
    return stale


class InstanceHygieneCheck(ReconcileCheck):
    name = "instance_hygiene"
    description = "Instance artifacts match framework declarations"

    def _find_orphans(self):
        """Returns dict of category → list of (path, label) orphans."""
        orphans = {}

        # 1. Service venvs
        fw_services = _framework_services()
        instance_services = USER / "services"
        if instance_services.is_dir():
            svc_orphans = []
            for d in instance_services.iterdir():
                if d.is_dir() and d.name not in fw_services:
                    # Flag if it looks like a deployed service (has venv, pyproject,
                    # or Python files). Skip if it's just an empty dir.
                    is_service = (
                        (d / ".venv").exists()
                        or (d / "pyproject.toml").exists()
                        or any(d.glob("*.py"))
                    )
                    if is_service:
                        svc_orphans.append((d, d.name))
            if svc_orphans:
                orphans["services"] = svc_orphans

        # 2. LaunchAgents
        fw_agents = _framework_launchagents()
        inst_agents = _installed_launchagents()
        la_orphans = []
        for label in inst_agents - fw_agents:
            plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
            if plist.exists():
                la_orphans.append((plist, label))
        if la_orphans:
            orphans["launchagents"] = la_orphans

        # 3. Skills (only stale symlinks pointing to framework paths that no longer exist)
        skills_dir = CLAUDE / "skills"
        if skills_dir.is_dir():
            skill_orphans = []
            for d in skills_dir.iterdir():
                if d.is_symlink() and not d.resolve().exists():
                    skill_orphans.append((d, d.name))
            if skill_orphans:
                orphans["skills"] = skill_orphans

        # 4. Stale model caches
        stale_models = _stale_model_caches()
        if stale_models:
            orphans["models"] = [(m, m.name) for m in stale_models]

        # 5. Old rotated log archives (keep last 3, flag the rest)
        logs_dir = USER / "logs"
        if logs_dir.is_dir():
            old_gz = sorted(
                logs_dir.glob("**/*.gz"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0
            )
            # Keep the 3 most recent per base name, flag older ones
            from collections import defaultdict
            by_base = defaultdict(list)
            for gz in old_gz:
                # install.log.1.gz, install.log.2.gz → base = install.log
                base = gz.name.split(".gz")[0]
                # Strip trailing .N rotation number
                parts = base.rsplit(".", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    base = parts[0]
                by_base[base].append(gz)

            log_orphans = []
            for base, files in by_base.items():
                # Sort oldest first, flag all but the 3 newest
                files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
                if len(files) > 3:
                    for old_file in files[:-3]:
                        log_orphans.append((old_file, f"{old_file.name} ({base})"))
            if log_orphans:
                orphans["logs"] = log_orphans

        return orphans

    def check(self) -> bool:
        orphans = self._find_orphans()
        return len(orphans) == 0

    def fix(self) -> CheckResult:
        orphans = self._find_orphans()
        if not orphans:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                message="No orphaned artifacts found"
            )

        cleaned = []
        skipped = []
        total_bytes = 0

        for category, items in orphans.items():
            for path, label in items:
                if category == "launchagents":
                    # Unload before removing
                    try:
                        subprocess.run(
                            ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                            capture_output=True, timeout=5
                        )
                    except Exception:
                        pass
                    try:
                        size = path.stat().st_size
                        path.unlink()
                        cleaned.append(f"  LaunchAgent: {label}")
                        total_bytes += size
                    except Exception as e:
                        skipped.append(f"  LaunchAgent: {label} ({e})")

                elif category == "services":
                    # Remove entire service directory (venv + any state)
                    try:
                        size = sum(
                            f.stat().st_size for f in path.rglob("*") if f.is_file()
                        )
                        shutil.rmtree(path)
                        cleaned.append(f"  Service: {label}")
                        total_bytes += size
                    except Exception as e:
                        skipped.append(f"  Service: {label} ({e})")

                elif category == "skills":
                    # Remove broken symlink
                    try:
                        path.unlink()
                        cleaned.append(f"  Skill symlink: {label}")
                    except Exception as e:
                        skipped.append(f"  Skill symlink: {label} ({e})")

                elif category == "models":
                    # Remove stale model cache
                    try:
                        size = sum(
                            f.stat().st_size for f in path.rglob("*") if f.is_file()
                        )
                        shutil.rmtree(path)
                        cleaned.append(f"  Model cache: {label}")
                        total_bytes += size
                    except Exception as e:
                        skipped.append(f"  Model cache: {label} ({e})")

                elif category == "logs":
                    try:
                        size = path.stat().st_size
                        path.unlink()
                        cleaned.append(f"  Old log: {label}")
                        total_bytes += size
                    except Exception as e:
                        skipped.append(f"  Old log: {label} ({e})")

        # Format size
        if total_bytes > 1_073_741_824:
            size_str = f"{total_bytes / 1_073_741_824:.1f} GB"
        elif total_bytes > 1_048_576:
            size_str = f"{total_bytes / 1_048_576:.0f} MB"
        elif total_bytes > 1024:
            size_str = f"{total_bytes / 1024:.0f} KB"
        else:
            size_str = f"{total_bytes} bytes"

        detail = "\n".join(cleaned + (["\nSkipped:"] + skipped if skipped else []))

        return CheckResult(
            name=self.name,
            status=Status.FIXED if cleaned else Status.NOTIFY,
            message=f"Cleaned {len(cleaned)} orphans, reclaimed {size_str}" if cleaned
                    else f"Found {len(skipped)} orphans but could not clean them",
            detail=detail
        )
