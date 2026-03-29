from .claude_md import RootClaudeMdCheck, GlobalClaudeMdCheck
from .mcp_location import McpLocationCheck
from .hooks import HooksPathCheck
from .launchagents import LaunchAgentPythonCheck
from .symlinks import AgentSymlinkCheck, SkillSymlinkCheck, RuleSymlinkCheck
from .log_location import LogLocationCheck
from .google_workspace import GoogleWorkspaceCheck
from .initiatives import InitiativeDirectoriesCheck, BridgeTopicsCheck
from .transcriber import TranscriberServiceCheck
from .disk_smart import DiskSmartCheck
from .context_freshness import ContextFreshnessCheck
from .claude_defaults import ClaudeDefaultsCheck
from .dead_code import DeadCodeCheck
from .runtime_protection import RuntimeProtectionCheck
from .deployment_health import DeploymentHealthCheck
from .instance_hygiene import InstanceHygieneCheck

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

    # Instance hygiene — diff framework declarations against instance state,
    # clean orphaned service venvs, stale LaunchAgents, broken symlinks,
    # old model caches, and excess log archives. Runs last.
    InstanceHygieneCheck,
]
