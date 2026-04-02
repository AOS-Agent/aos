"""
Invariant: All NVMe disks report healthy SMART status with no critical warnings.

Auto-discovers every physical NVMe disk on the machine and checks each one.
This is a NOTIFY-only check — hardware issues cannot be auto-repaired.

Requires: smartctl (brew install smartmontools)
"""

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status


@dataclass
class DiskReport:
    """Parsed SMART data for a single disk."""
    device: str
    model: str = "unknown"
    passed: bool = True
    temperature: Optional[int] = None
    available_spare: Optional[int] = None
    spare_threshold: Optional[int] = None
    percentage_used: Optional[int] = None
    media_errors: int = 0
    critical_warning: int = 0
    concerns: list = field(default_factory=list)


def _discover_nvme_disks() -> list[str]:
    """Discover all physical NVMe disks on the system via smartctl --scan."""
    try:
        result = subprocess.run(
            ["smartctl", "--scan-open", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        devices = []
        for dev in data.get("devices", []):
            # Only include NVMe devices
            if dev.get("type") == "nvme":
                devices.append(dev["name"])
        return devices
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    # Fallback: Apple Silicon Macs always have disk0 as internal NVMe
    return ["/dev/disk0"]


def _parse_smartctl_json(device: str) -> Optional[DiskReport]:
    """Run smartctl --json and parse the output for an NVMe disk."""
    try:
        result = subprocess.run(
            ["smartctl", "-a", "--json", device],
            capture_output=True, text=True, timeout=30,
        )
        # smartctl returns non-zero for minor issues (log read failures etc.)
        # We care about the JSON payload, not the exit code.
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None

    report = DiskReport(device=device)

    # Model
    report.model = data.get("model_name", "unknown")

    # Overall health
    smart_status = data.get("smart_status", {})
    report.passed = smart_status.get("passed", True)

    # NVMe health info
    health = data.get("nvme_smart_health_information_log", {})
    if health:
        report.temperature = health.get("temperature")
        report.available_spare = health.get("available_spare")
        report.spare_threshold = health.get("available_spare_threshold")
        report.percentage_used = health.get("percentage_used")
        report.media_errors = health.get("media_and_data_integrity_errors", 0)
        report.critical_warning = health.get("critical_warning", 0)

    # Evaluate concerns
    if not report.passed:
        report.concerns.append("SMART self-assessment: FAILED")

    if report.critical_warning != 0:
        report.concerns.append(f"Critical warning flag: 0x{report.critical_warning:02x}")

    if report.media_errors > 0:
        report.concerns.append(f"Media/data integrity errors: {report.media_errors}")

    if report.available_spare is not None and report.spare_threshold is not None:
        if report.available_spare <= report.spare_threshold:
            report.concerns.append(
                f"Available spare ({report.available_spare}%) at or below "
                f"threshold ({report.spare_threshold}%)"
            )

    if report.percentage_used is not None and report.percentage_used >= 80:
        report.concerns.append(f"Drive wear at {report.percentage_used}% — approaching end of life")

    if report.temperature is not None and report.temperature >= 70:
        report.concerns.append(f"Temperature {report.temperature}°C — overheating risk")

    return report


class DiskSmartCheck(ReconcileCheck):
    name = "disk_smart_health"
    description = "Disk SMART health — detects early failure signs on all NVMe drives"

    def check(self) -> bool:
        # No smartctl = can't check, skip gracefully
        if not shutil.which("smartctl"):
            return True

        self._reports = []

        # Auto-discover and check every NVMe disk on the machine
        for device in _discover_nvme_disks():
            report = _parse_smartctl_json(device)
            if report:
                self._reports.append(report)

        # Pass if no reports have concerns
        return all(len(r.concerns) == 0 for r in self._reports)

    def fix(self) -> CheckResult:
        if not shutil.which("smartctl"):
            return CheckResult(
                self.name, Status.SKIP,
                "smartctl not installed — run: brew install smartmontools",
            )

        lines = []

        # Report disk concerns
        for report in self._reports:
            if report.concerns:
                lines.append(f"\n{report.model} ({report.device}):")
                for concern in report.concerns:
                    lines.append(f"  - {concern}")

        if not lines:
            return CheckResult(self.name, Status.OK, "All disks healthy")

        detail = "\n".join(lines)

        # Determine severity
        has_critical = any(
            not r.passed or r.media_errors > 0 or r.critical_warning != 0
            for r in self._reports
        )

        message = (
            "Disk health issue detected — potential hardware problem"
            if has_critical
            else "Disk health warning — review recommended"
        )

        return CheckResult(
            self.name, Status.NOTIFY,
            message,
            detail=detail,
            notify=True,
        )
