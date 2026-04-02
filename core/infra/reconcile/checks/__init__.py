from .claude_defaults import ClaudeDefaultsCheck
from .claude_md import GlobalClaudeMdCheck, RootClaudeMdCheck
from .context_freshness import ContextFreshnessCheck
from .dead_code import DeadCodeCheck
from .deployment_health import DeploymentHealthCheck
from .disk_smart import DiskSmartCheck
from .google_workspace import GoogleWorkspaceCheck
from .hooks import HooksPathCheck
from .initiatives import BridgeTopicsCheck, InitiativeDirectoriesCheck
from .instance_hygiene import InstanceHygieneCheck
from .launchagents import LaunchAgentPythonCheck
from .log_location import LogLocationCheck
from .mcp_location import McpLocationCheck
from .runtime_protection import RuntimeProtectionCheck
from .storage_layout import StorageLayoutCheck
from .symlinks import AgentSymlinkCheck, RuleSymlinkCheck, SkillSymlinkCheck
from .transcriber import TranscriberServiceCheck

# Add new checks here — they run in this order on every update cycle.
ALL_CHECKS = [
    # Runtime protection — must run FIRST to unblock git pull
    RuntimeProtectionCheck,

    # Structural — file locations
    McpLocationCheck,
    LogLocationCheck,

    # Symlinks — agents, skills, and rules point to framework
    AgentSymlinkCheck,
    SkillSymlinkCheck,
    RuleSymlinkCheck,

    # Config — settings.json hooks have valid paths
    HooksPathCheck,

    # Config — ~/.claude.json always-on defaults (remote control, chrome)
    ClaudeDefaultsCheck,

    # Services — LaunchAgent plists reference existing Python
    LaunchAgentPythonCheck,

    # Integrations — MCP servers for external services
    GoogleWorkspaceCheck,

    # Content — CLAUDE.md managed sections are current
    RootClaudeMdCheck,
    GlobalClaudeMdCheck,

    # Services — transcriber running and healthy
    TranscriberServiceCheck,

    # Initiative pipeline + Bridge v2 infrastructure
    InitiativeDirectoriesCheck,
    BridgeTopicsCheck,

    # Hardware — disk SMART health monitoring
    DiskSmartCheck,

    # Context — CLAUDE.md dynamic content matches system state
    ContextFreshnessCheck,

    # Hygiene — detect orphaned scripts and stale module refs
    DeadCodeCheck,

    # Deployment health — verify shipped components are actually deployed
    # (venvs exist, cron scripts exist, git hooks installed, QMD collections up)
    DeploymentHealthCheck,

    # Storage layout — verify data dirs are on the data drive per policy.
    # Reports drift but never auto-moves (operator awareness required).
    StorageLayoutCheck,

    # Instance hygiene — diff framework declarations against instance state,
    # clean orphaned service venvs, stale LaunchAgents, broken symlinks,
    # old model caches, and excess log archives. Runs last.
    InstanceHygieneCheck,
]
