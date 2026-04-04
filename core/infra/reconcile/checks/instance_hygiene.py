"""
Invariant: Instance-layer artifacts match what the framework declares.

The framework IS the manifest:
  - core/services/*/pyproject.toml  → declares expected service venvs
  - config/launchagents/com.aos.*   → declares expected LaunchAgent plists
  - config/preserved-services.yaml  → instance services used by framework code
  - core/skills/*/SKILL.md          → declares expected skill symlinks

Anything in instance space that doesn't trace back to the framework is
potentially orphaned. This check REPORTS orphans — it never deletes.
Cleanup requires explicit operator approval via `aos hygiene --apply`.

Runs every update cycle. Only flags AOS-namespaced items.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

AOS = Path.home() / "aos"
USER = Path.home() / ".aos"
CLAUDE = Path.home() / ".claude"

PRESERVED_SERVICES_FILE = AOS / "config" / "preserved-services.yaml"
HYGIENE_STATE_FILE = USER / "config" / "hygiene-known.yaml"


def _load_preserved():
    """Load preserved artifacts from config.

    Returns dict with 'services' and 'launchagents' sets.
    """
    result = {"services": set(), "launchagents": set()}
    if not PRESERVED_SERVICES_FILE.exists():
        return result
    try:
        import yaml
        with open(PRESERVED_SERVICES_FILE) as f:
            data = yaml.safe_load(f) or {}
        result["services"] = set(data.get("services", {}).keys())
        result["launchagents"] = set(data.get("launchagents", {}).keys())
    except Exception:
        pass
    return result


def _known_orphans():
    """Orphans the operator has already reviewed and dismissed.

    Once the operator sees an orphan report and says "leave it",
    it gets written here so we don't nag every update cycle.
    """
    if not HYGIENE_STATE_FILE.exists():
        return set()
    try:
        import yaml
        with open(HYGIENE_STATE_FILE) as f:
            data = yaml.safe_load(f) or {}
        return set(data.get("dismissed", []))
    except Exception:
        return set()


def _framework_services():
    """Service names the framework declares plus preserved services."""
    svc_dir = AOS / "core" / "services"
    declared = set()
    if svc_dir.is_dir():
        declared = {
            d.name for d in svc_dir.iterdir()
            if d.is_dir() and (d / "pyproject.toml").exists()
        }
    return declared | _load_preserved()["services"]


def _framework_launchagents():
    """LaunchAgent labels the framework declares plus preserved LaunchAgents."""
    la_dir = AOS / "config" / "launchagents"
    labels = set()
    if la_dir.is_dir():
        for f in la_dir.iterdir():
            name = f.name
            if name.endswith(".plist.template"):
                labels.add(name.removesuffix(".plist.template"))
            elif name.endswith(".plist"):
                labels.add(name.removesuffix(".plist"))
    return labels | _load_preserved()["launchagents"]


def _installed_launchagents():
    """com.aos.* LaunchAgent labels actually installed."""
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if not la_dir.is_dir():
        return set()
    return {
        f.name.removesuffix(".plist")
        for f in la_dir.glob("com.aos.*.plist")
    }


def _stale_model_caches():
    """HuggingFace model dirs that aren't the active whisper model."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    if not cache_dir.is_dir():
        return []

    active_model = "models--mlx-community--whisper-large-v3-turbo"

    stale = []
    for d in cache_dir.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        if "whisper" in name.lower() and name != active_model:
            stale.append(d)
    return stale


def _format_size(total_bytes):
    if total_bytes > 1_073_741_824:
        return f"{total_bytes / 1_073_741_824:.1f} GB"
    elif total_bytes > 1_048_576:
        return f"{total_bytes / 1_048_576:.0f} MB"
    elif total_bytes > 1024:
        return f"{total_bytes / 1024:.0f} KB"
    return f"{total_bytes} bytes"


def _dir_size(path):
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:
        return 0


def find_orphans():
    """Returns dict of category → list of (path, label, size_bytes) orphans.

    Public so `aos hygiene` CLI can call it directly.
    """
    known = _known_orphans()
    orphans = {}

    # 1. Service venvs
    fw_services = _framework_services()
    instance_services = USER / "services"
    if instance_services.is_dir():
        svc_orphans = []
        for d in instance_services.iterdir():
            if d.is_dir() and d.name not in fw_services:
                if f"service:{d.name}" in known:
                    continue
                is_service = (
                    (d / ".venv").exists()
                    or (d / "pyproject.toml").exists()
                    or any(d.glob("*.py"))
                )
                if is_service:
                    svc_orphans.append((d, d.name, _dir_size(d)))
        if svc_orphans:
            orphans["services"] = svc_orphans

    # 2. LaunchAgents
    fw_agents = _framework_launchagents()
    inst_agents = _installed_launchagents()
    la_orphans = []
    for label in inst_agents - fw_agents:
        if f"launchagent:{label}" in known:
            continue
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.exists():
            la_orphans.append((plist, label, plist.stat().st_size))
    if la_orphans:
        orphans["launchagents"] = la_orphans

    # 3. Skills (only broken symlinks)
    skills_dir = CLAUDE / "skills"
    if skills_dir.is_dir():
        skill_orphans = []
        for d in skills_dir.iterdir():
            if d.is_symlink() and not d.resolve().exists():
                if f"skill:{d.name}" in known:
                    continue
                skill_orphans.append((d, d.name, 0))
        if skill_orphans:
            orphans["skills"] = skill_orphans

    # 4. Stale model caches
    stale_models = _stale_model_caches()
    model_orphans = []
    for m in stale_models:
        if f"model:{m.name}" in known:
            continue
        model_orphans.append((m, m.name, _dir_size(m)))
    if model_orphans:
        orphans["models"] = model_orphans

    # 5. Old rotated log archives (keep last 3 per base name)
    logs_dir = USER / "logs"
    if logs_dir.is_dir():
        from collections import defaultdict
        by_base = defaultdict(list)
        for gz in logs_dir.glob("**/*.gz"):
            base = gz.name.split(".gz")[0]
            parts = base.rsplit(".", 1)
            if len(parts) == 2 and parts[1].isdigit():
                base = parts[0]
            by_base[base].append(gz)

        log_orphans = []
        for base, files in by_base.items():
            files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
            if len(files) > 3:
                for old_file in files[:-3]:
                    key = f"log:{old_file.name}"
                    if key not in known:
                        log_orphans.append((old_file, old_file.name, old_file.stat().st_size))
        if log_orphans:
            orphans["logs"] = log_orphans

    return orphans


class InstanceHygieneCheck(ReconcileCheck):
    name = "instance_hygiene"
    description = "Instance artifacts match framework declarations"

    def check(self) -> bool:
        orphans = find_orphans()
        return len(orphans) == 0

    def fix(self) -> CheckResult:
        """Report orphans — NEVER auto-delete.

        Destructive cleanup requires operator approval. This check only
        surfaces what it found. Actual cleanup runs via `aos hygiene --apply`.
        """
        orphans = find_orphans()
        if not orphans:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                message="No orphaned instance artifacts"
            )

        total_items = sum(len(v) for v in orphans.values())
        total_bytes = sum(size for items in orphans.values() for _, _, size in items)

        lines = []
        for category, items in orphans.items():
            for path, label, size in items:
                size_str = f" ({_format_size(size)})" if size > 1024 else ""
                lines.append(f"  {category}: {label}{size_str}")

        detail = "\n".join(lines)
        detail += "\n\nRun `aos hygiene` to review and clean."

        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message=f"{total_items} orphaned artifacts found ({_format_size(total_bytes)})",
            detail=detail,
            notify=True
        )
