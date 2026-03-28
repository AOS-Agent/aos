"""
Invariant: Initiative pipeline infrastructure exists on the data layer.

Checks:
1. vault/knowledge/initiatives/ directory exists
2. vault/knowledge/expertise/ directory exists
4. bridge-topics.yaml exists (Bridge v2)

Auto-fixes by creating missing directories. Creates a minimal
bridge-topics.yaml scaffold if missing (the TopicManager will
populate topic IDs at runtime when Telegram is available).
"""

import subprocess
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
    PROJECTS_YAML = Path.home() / ".aos" / "config" / "projects.yaml"
    AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "agent-secret"

    def check(self) -> bool:
        return self.CONFIG_PATH.exists()

    def _get_forum_group_id(self) -> int | None:
        """Try to extract forum_group_id from projects.yaml."""
        if not self.PROJECTS_YAML.exists():
            return None
        try:
            import yaml
            with open(self.PROJECTS_YAML) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return None
            for proj in data.get("projects", {}).values():
                if isinstance(proj, dict):
                    tg = proj.get("telegram", {})
                    if isinstance(tg, dict) and tg.get("forum_group_id"):
                        return tg["forum_group_id"]
            system = data.get("system", {})
            if isinstance(system, dict):
                tg = system.get("telegram", {})
                if isinstance(tg, dict) and tg.get("forum_group_id"):
                    return tg["forum_group_id"]
        except Exception:
            pass
        return None

    def fix(self) -> CheckResult:
        """Create a minimal bridge-topics.yaml scaffold.

        The TopicManager will populate topic thread IDs at runtime
        when Telegram is available. This just ensures the file exists
        so the bridge can start without errors.
        """
        forum_group_id = self._get_forum_group_id()
        if forum_group_id is None:
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="bridge-topics.yaml missing and no forum_group_id in projects.yaml — configure Telegram first",
                notify=True,
            )

        try:
            import yaml
        except ImportError:
            return CheckResult(
                name=self.name,
                status=Status.NOTIFY,
                message="bridge-topics.yaml missing — pyyaml not available to auto-create",
                notify=True,
            )

        scaffold = {
            "forum_group_id": forum_group_id,
            "topics": {
                "daily": {"thread_id": None, "created": None, "pinned_message_id": None},
                "alerts": {"thread_id": None, "created": None, "pinned_message_id": None},
                "work": {"thread_id": None, "created": None, "pinned_message_id": None},
                "knowledge": {"thread_id": None, "created": None, "pinned_message_id": None},
                "system": {"thread_id": None, "created": None, "pinned_message_id": None},
            },
        }

        try:
            self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CONFIG_PATH, "w") as f:
                yaml.dump(scaffold, f, default_flow_style=False, sort_keys=False)
            return CheckResult(
                name=self.name,
                status=Status.FIXED,
                message=f"Created bridge-topics.yaml scaffold (forum_group_id={forum_group_id}, topics pending)",
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=f"Failed to create bridge-topics.yaml: {e}",
            )
