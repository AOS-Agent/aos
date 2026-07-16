from .bridge_poll_liveness import BridgePollLivenessCheck
from .claude_defaults import ClaudeDefaultsCheck
from .claude_md import GlobalClaudeMdCheck, RootClaudeMdCheck
from .context_freshness import ContextFreshnessCheck
from .dead_code import DeadCodeCheck
from .deployment_health import DeploymentHealthCheck
from .dev_backend_plist import DevBackendPlistCheck
from .dev_browser import DevBrowserCheck
from .disk_smart import DiskSmartCheck
from .google_workspace import GoogleWorkspaceCheck
from .hooks import HooksPathCheck
from .initiatives import BridgeTopicsCheck, InitiativeDirectoriesCheck
from .instance_hygiene import InstanceHygieneCheck
from .launchagents import LaunchAgentPythonCheck
from .log_location import LogLocationCheck
from .mcp_location import McpLocationCheck
from .n8n import N8nServiceCheck
from .runtime_protection import RuntimeProtectionCheck
from .sentinel_plist import SentinelPlistDriftCheck
from .service_loaded import ServiceLoadedCheck
from .storage_layout import StorageLayoutCheck
from .symlinks import AgentSymlinkCheck, RuleSymlinkCheck, SkillSymlinkCheck
from .transcriber import TranscriberServiceCheck
from .vault_contract import VaultContractCheck

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

    # Services — deployed Sentinel plist matches its framework template
    # (catches drift back to hardcoded operator paths after a manual edit
    # or bad merge; complements LaunchAgentPythonCheck's binary-path check)
    SentinelPlistDriftCheck,

    # Integrations — Google Workspace gws CLI
    GoogleWorkspaceCheck,

    # Content — CLAUDE.md managed sections are current
    RootClaudeMdCheck,
    GlobalClaudeMdCheck,

    # Services — bridge Telegram poll loop is fetching, not silently wedged
    # (process-liveness watchdogs miss a stalled getUpdates; this restarts it)
    BridgePollLivenessCheck,

    # Services — transcriber running and healthy
    TranscriberServiceCheck,

    # Services — n8n automation engine running and healthy
    N8nServiceCheck,

    # Tooling — dev-browser binary for Chrome automation (blueprint,
    # clone-website, dissect, frontend-craft, harvest skills; reverser agent)
    DevBrowserCheck,

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

    # Vault inventory — refresh vault_inventory table, report drift.
    # Never mutates vault files; the bootstrap flow (Part 8) handles
    # operator-approved upgrades.
    VaultContractCheck,

    # Dev backend LaunchAgent — verifies qareen-dev is loaded under launchd
    # so the dev uvicorn on 4097 auto-restarts on crash. Notify-only; never
    # auto-installs (modifies ~/Library/LaunchAgents/).
    DevBackendPlistCheck,

    # Services — every deployed com.aos.*.plist has a loaded (and healthy)
    # launchd job. The generic net for the exact silent state that ate the
    # bridge and transcriber (plist intact, job unloaded). Runs AFTER the
    # per-service checks so they repair health first; this then catches any
    # still-unloaded/health-less service. periodic_fix=True — the one service
    # check allowed to repair between deploys.
    ServiceLoadedCheck,

    # Instance hygiene — diff framework declarations against instance state,
    # clean orphaned service venvs, stale LaunchAgents, broken symlinks,
    # old model caches, and excess log archives. Runs last.
    InstanceHygieneCheck,
]
