from .claude_md import RootClaudeMdCheck, GlobalClaudeMdCheck
from .mcp_location import McpLocationCheck
from .hooks import HooksPathCheck
from .launchagents import LaunchAgentPythonCheck
from .symlinks import AgentSymlinkCheck, SkillSymlinkCheck
from .log_location import LogLocationCheck
from .google_workspace import GoogleWorkspaceCheck
from .initiatives import InitiativeDirectoriesCheck, BridgeTopicsCheck
from .transcriber import TranscriberServiceCheck

# Add new checks here — they run in this order on every update cycle.
ALL_CHECKS = [
    # Structural — file locations
    McpLocationCheck,
    LogLocationCheck,

    # Symlinks — agents and skills point to framework
    AgentSymlinkCheck,
    SkillSymlinkCheck,

    # Config — settings.json hooks have valid paths
    HooksPathCheck,

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
]
