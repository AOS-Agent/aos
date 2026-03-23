from .claude_md import RootClaudeMdCheck, GlobalClaudeMdCheck
from .mcp_location import McpLocationCheck
from .hooks import HooksPathCheck
from .launchagents import LaunchAgentPythonCheck
from .symlinks import AgentSymlinkCheck, SkillSymlinkCheck
from .log_location import LogLocationCheck
from .model_config import ModelConfigCheck

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

    # Model — Claude Code uses the right model
    ModelConfigCheck,

    # Services — LaunchAgent plists reference existing Python
    LaunchAgentPythonCheck,

    # Content — CLAUDE.md managed sections are current
    RootClaudeMdCheck,
    GlobalClaudeMdCheck,
]
