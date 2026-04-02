"""
Invariant: Data directories live on the data drive per storage policy.

Reads ~/aos/config/storage.yaml and verifies that each declared relocation
is actually a symlink pointing to the data drive. Reports drift — does NOT
auto-fix, because moving data requires the operator's awareness (apps may
need to be quit, services stopped).

Fix command: `aos storage reconcile`
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

HOME = Path.home()
AOS = HOME / "aos"
STORAGE_CONFIG = AOS / "config" / "storage.yaml"

GB = 1024 ** 3


def _load_policy() -> dict:
    """Load storage policy from config."""
    if not STORAGE_CONFIG.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(STORAGE_CONFIG.read_text()) or {}
    except Exception:
        return {}


def _dir_size_gb(path: Path) -> float:
    """Quick size estimate via shutil (doesn't follow symlinks into subdirs)."""
    try:
        import subprocess
        result = subprocess.run(
            ["du", "-s", "-k", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return int(result.stdout.split()[0]) * 1024 / GB
    except Exception:
        pass
    return 0


def find_violations(policy: dict) -> list[dict]:
    """Check each declared relocation. Return list of violations."""
    data_drive = policy.get("data_drive", "")
    if not data_drive or not Path(data_drive).exists():
        return []  # No data drive — nothing to check

    relocations = policy.get("relocations", {})
    sip_protected = set(policy.get("sip_protected", []))
    violations = []

    for category, paths in relocations.items():
        if not isinstance(paths, list):
            continue
        for rel_path in paths:
            full_path = HOME / rel_path

            # Skip if path doesn't exist (not installed yet)
            if not full_path.exists() and not full_path.is_symlink():
                continue

            # Check if it's already a symlink to the data drive
            if full_path.is_symlink():
                target = str(full_path.resolve())
                if target.startswith(data_drive):
                    continue  # Correct — symlinked to data drive
                else:
                    violations.append({
                        "path": rel_path,
                        "category": category,
                        "issue": "symlink_wrong_target",
                        "detail": f"Points to {target}, expected {data_drive}",
                        "size_gb": 0,
                        "sip": rel_path in sip_protected,
                    })
                    continue

            # It's a local directory — should be on data drive
            is_sip = rel_path in sip_protected
            size_gb = _dir_size_gb(full_path) if not is_sip else 0

            violations.append({
                "path": rel_path,
                "category": category,
                "issue": "not_relocated",
                "detail": f"Local directory ({size_gb:.1f}GB)" if size_gb > 0.01 else "Local directory",
                "size_gb": size_gb,
                "sip": is_sip,
            })

    return violations


class StorageLayoutCheck(ReconcileCheck):
    name = "storage_layout"
    description = "Data directories relocated to data drive per storage policy"

    def check(self) -> bool:
        policy = _load_policy()
        if not policy:
            return True  # No policy = nothing to check
        data_drive = policy.get("data_drive", "")
        if not data_drive or not Path(data_drive).exists():
            return True  # Data drive not connected — skip
        violations = find_violations(policy)
        return len(violations) == 0

    def fix(self) -> CheckResult:
        policy = _load_policy()
        if not policy:
            return CheckResult(
                name=self.name,
                status=Status.SKIP,
                message="No storage policy found (config/storage.yaml)",
            )

        data_drive = policy.get("data_drive", "")
        if not data_drive or not Path(data_drive).exists():
            return CheckResult(
                name=self.name,
                status=Status.SKIP,
                message=f"Data drive not connected: {data_drive}",
            )

        violations = find_violations(policy)
        if not violations:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                message="All data directories correctly relocated",
            )

        total_gb = sum(v["size_gb"] for v in violations)
        lines = []
        for v in violations:
            sip_note = " [SIP]" if v["sip"] else ""
            lines.append(f"  ~/{v['path']}: {v['detail']}{sip_note}")

        detail = "\n".join(lines)
        detail += "\n\nRun `aos storage reconcile` to fix."

        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message=f"{len(violations)} directories not on data drive ({total_gb:.1f}GB local)",
            detail=detail,
            notify=True,
        )
