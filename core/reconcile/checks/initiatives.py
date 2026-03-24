"""
Invariant: Initiative pipeline infrastructure exists on the data layer.

Checks:
1. vault/knowledge/initiatives/ directory exists
2. vault/knowledge/expertise/ directory exists
3. vault/ideas/ directory exists
4. bridge-topics.yaml exists (Bridge v2)

Auto-fixes by creating missing directories. Config files are
created by their respective migrations — this just verifies.
"""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class InitiativeDirectoriesCheck(ReconcileCheck):
    name = "initiative_directories"
    description = "Initiative pipeline vault directories exist"

    VAULT = Path.home() / "vault"
    REQUIRED_DIRS = [
        VAULT / "knowledge" / "initiatives",
        VAULT / "knowledge" / "expertise",
        VAULT / "ideas",
    ]

    def check(self) -> bool:
        # If vault doesn't exist at all (drive unmounted), skip
        if not self.VAULT.exists():
            return True  # can't fix, skip silently
        return all(d.exists() for d in self.REQUIRED_DIRS)

    def fix(self) -> CheckResult:
        if not self.VAULT.exists():
            return CheckResult(
                name=self.name,
                status=Status.SKIP,
                message="Vault not mounted — cannot create directories"
            )

        created = []
        for d in self.REQUIRED_DIRS:
            if not d.exists():
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    created.append(str(d.relative_to(Path.home())))
                except Exception as e:
                    return CheckResult(
                        name=self.name,
                        status=Status.ERROR,
                        message=f"Failed to create {d}: {e}"
                    )

        if created:
            return CheckResult(
                name=self.name,
                status=Status.FIXED,
                message=f"Created directories: {', '.join(created)}"
            )
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message="All initiative directories exist"
        )


class BridgeTopicsCheck(ReconcileCheck):
    name = "bridge_topics_config"
    description = "Bridge v2 topics config exists"

    CONFIG_PATH = Path.home() / ".aos" / "config" / "bridge-topics.yaml"

    def check(self) -> bool:
        return self.CONFIG_PATH.exists()

    def fix(self) -> CheckResult:
        # Don't auto-create — migration 017 handles this with Telegram API calls
        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message="bridge-topics.yaml missing — run migration 017 or restart bridge",
            notify=True,
        )
