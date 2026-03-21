#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AOS — Agentic Operating System
#  Bootstrap installer
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/<org>/aos/main/install.sh | bash
#    — or —
#    cd ~/aos && bash install.sh
#
#  Idempotent. Safe to re-run. Resumes from where it left off.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

# ── Version ──────────────────────────────────────────
AOS_VERSION="0.1.0"
AOS_REPO="https://github.com/agentalhadi/aos.git"
AOS_BRANCH="main"

# ── Paths ────────────────────────────────────────────
AOS_DIR="$HOME/aos"
USER_DIR="$HOME/.aos"
LOG_DIR="$USER_DIR/logs"
INSTALL_LOG="$LOG_DIR/install.log"
MACHINE_ID_FILE="$USER_DIR/.machine-id"

# ── Colors ──────────────────────────────────────────
if [[ -t 1 ]] && command -v tput &>/dev/null && [[ $(tput colors 2>/dev/null || echo 0) -ge 256 ]]; then
    # Rich palette for 256+ color terminals
    BRAND=$'\033[38;2;100;180;255m'    # AOS brand blue
    GREEN=$'\033[38;2;80;250;123m'     # soft green
    YELLOW=$'\033[38;2;255;200;50m'    # warm yellow
    RED=$'\033[38;2;255;85;85m'        # soft red
    CYAN=$'\033[38;2;100;220;255m'     # bright cyan
    MUTED=$'\033[38;2;108;112;134m'    # grey
    ACCENT=$'\033[38;2;180;130;255m'   # purple accent
    DIM=$(tput dim)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
elif [[ -t 1 ]] && command -v tput &>/dev/null && [[ $(tput colors 2>/dev/null || echo 0) -ge 8 ]]; then
    BRAND=$(tput setaf 4)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    RED=$(tput setaf 1)
    CYAN=$(tput setaf 6)
    MUTED=$(tput setaf 8 2>/dev/null || tput dim)
    ACCENT=$(tput setaf 5)
    DIM=$(tput dim)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
else
    BRAND="" GREEN="" YELLOW="" RED="" CYAN="" MUTED="" ACCENT="" DIM="" BOLD="" RESET=""
fi

# ── Timing ──────────────────────────────────────────
INSTALL_START=$(date +%s)
STEP_START=$INSTALL_START
_STEP_NUM=0
_TOTAL_STEPS=6

_timer_start() { STEP_START=$(date +%s); }
_timer_elapsed() {
    local now=$(date +%s)
    local elapsed=$((now - STEP_START))
    if [[ $elapsed -ge 60 ]]; then
        echo "$((elapsed / 60))m $((elapsed % 60))s"
    else
        echo "${elapsed}s"
    fi
}
_total_elapsed() {
    local now=$(date +%s)
    local elapsed=$((now - INSTALL_START))
    echo "$((elapsed / 60))m $((elapsed % 60))s"
}

# ── Spinner (braille dots) ──────────────────────────
_SPINNER_PID=""
_spinner_start() {
    local msg="${1:-Working}"
    [[ -t 1 ]] || return
    (
        local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
        local i=0
        while true; do
            printf "\r  ${CYAN}%s${RESET} ${MUTED}%s${RESET}" "${frames[$((i % 10))]}" "$msg"
            ((i++))
            sleep 0.08
        done
    ) &
    _SPINNER_PID=$!
    disown "$_SPINNER_PID" 2>/dev/null
}
_spinner_stop() {
    if [[ -n "${_SPINNER_PID:-}" ]]; then
        kill "$_SPINNER_PID" 2>/dev/null
        wait "$_SPINNER_PID" 2>/dev/null || true
        _SPINNER_PID=""
        printf "\r\033[K"
    fi
}

# Restore cursor on exit
trap 'tput cnorm 2>/dev/null' EXIT INT TERM

# ── Logging ──────────────────────────────────────────
_log_init() {
    mkdir -p "$LOG_DIR"
    echo "=== AOS Install — $(date -Iseconds) ===" >> "$INSTALL_LOG"
    echo "AOS_VERSION=$AOS_VERSION" >> "$INSTALL_LOG"
    echo "macOS=$(sw_vers -productVersion 2>/dev/null || echo unknown)" >> "$INSTALL_LOG"
    echo "arch=$(uname -m)" >> "$INSTALL_LOG"
    echo "" >> "$INSTALL_LOG"
}

_log() {
    echo "[$(date +%H:%M:%S)] $*" >> "$INSTALL_LOG"
}

# ── Output helpers ───────────────────────────────────
_ok()   { echo "  ${GREEN}✓${RESET} $*"; _log "OK: $*"; }
_skip() { echo "  ${MUTED}✓ $*${RESET}"; _log "SKIP: $*"; }
_warn() { echo "  ${YELLOW}!${RESET} $*"; _log "WARN: $*"; }
_fail() { echo "  ${RED}✗${RESET} $*"; _log "FAIL: $*"; }
_phase() {
    if [[ -n "${_PHASE_ACTIVE:-}" ]]; then
        echo "  ${MUTED}$(_timer_elapsed)${RESET}"
    fi
    _PHASE_ACTIVE=1
    ((_STEP_NUM++)) || true
    _timer_start
    echo ""
    echo "  ${BRAND}[${_STEP_NUM}/${_TOTAL_STEPS}]${RESET} ${BOLD}$*${RESET}"
    echo ""
    _log "PHASE: $*"
}
_step() {
    echo ""
    echo "  ${BOLD}$*${RESET}"
    _log "STEP: $*"
}
_info() { echo "  ${MUTED}$*${RESET}"; }

# ── Banner ──────────────────────────────────────────
_banner() {
    tput civis 2>/dev/null  # hide cursor during install

    echo ""
    echo "  ${MUTED}bismillah${RESET}"
    echo ""
    echo "${BRAND}${BOLD}"
    cat << 'BANNER'
       █████╗  ██████╗ ███████╗
      ██╔══██╗██╔═══██╗██╔════╝
      ███████║██║   ██║███████╗
      ██╔══██║██║   ██║╚════██║
      ██║  ██║╚██████╔╝███████║
      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
BANNER
    echo "${RESET}"
    echo "  ${MUTED}Agentic Operating System  v${AOS_VERSION}${RESET}"
    echo "  ${MUTED}$(uname -m) · macOS $(sw_vers -productVersion 2>/dev/null || echo '?') · $(date +%H:%M)${RESET}"
    echo ""
    echo "  ${MUTED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
}

# ── Error handling ───────────────────────────────────
_die() {
    _fail "$*"
    echo ""
    echo "  Install log: $INSTALL_LOG"
    echo "  Fix the issue above and re-run: bash ~/aos/install.sh"
    exit 1
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 1: Prerequisites
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

prereq_homebrew() {
    if command -v brew &>/dev/null; then
        _skip "Homebrew"
        return 0
    fi

    _step "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Add to PATH for this session (Apple Silicon vs Intel)
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi

    command -v brew &>/dev/null || _die "Homebrew installed but not on PATH"
    _ok "Homebrew"
}

prereq_python3() {
    if command -v python3 &>/dev/null; then
        local ver
        ver=$(python3 --version 2>&1 | awk '{print $2}')
        local major minor
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)

        if [[ "$major" -ge 3 ]] && [[ "$minor" -ge 11 ]]; then
            _skip "Python $ver"
            return 0
        fi
        _warn "Python $ver found but 3.11+ required"
    fi

    _step "Installing Python 3..."
    brew install python@3.13 2>&1 | tail -1
    command -v python3 &>/dev/null || _die "Python3 install failed"
    _ok "Python $(python3 --version 2>&1 | awk '{print $2}')"
}

prereq_pyyaml() {
    if python3 -c "import yaml" 2>/dev/null; then
        _skip "PyYAML"
        return 0
    fi

    _step "Installing PyYAML..."
    pip3 install --quiet pyyaml 2>&1
    python3 -c "import yaml" 2>/dev/null || _die "PyYAML install failed"
    _ok "PyYAML"
}

prereq_uv() {
    if command -v uv &>/dev/null; then
        _skip "uv"
        return 0
    fi

    _step "Installing uv..."
    brew install uv 2>&1 | tail -1
    command -v uv &>/dev/null || _die "uv install failed"
    _ok "uv"
}

prereq_bun() {
    if command -v bun &>/dev/null; then
        _skip "bun"
        return 0
    fi

    _step "Installing bun..."
    brew install oven-sh/bun/bun 2>&1 | tail -1
    command -v bun &>/dev/null || _die "bun install failed"
    _ok "bun"
}

prereq_qmd() {
    if command -v qmd &>/dev/null || [[ -f "$HOME/.bun/bin/qmd" ]]; then
        _skip "qmd"
        return 0
    fi

    _step "Installing qmd..."
    bun install -g qmd 2>&1 | tail -1
    [[ -f "$HOME/.bun/bin/qmd" ]] || _die "qmd install failed"
    _ok "qmd"
}

prereq_git() {
    if command -v git &>/dev/null; then
        _skip "git"
        return 0
    fi

    # Xcode CLT includes git — trigger install
    _step "Installing Xcode Command Line Tools (for git)..."
    xcode-select --install 2>/dev/null || true
    _warn "Xcode CLT installing — re-run this script when done"
    exit 0
}

prereq_gh() {
    if command -v gh &>/dev/null; then
        _skip "GitHub CLI"
        return 0
    fi

    _step "Installing GitHub CLI..."
    brew install gh 2>&1 | tail -1
    command -v gh &>/dev/null || _die "GitHub CLI install failed"
    _ok "GitHub CLI"
}

prereq_editor() {
    # Check if any supported editor is already installed
    local found=""
    command -v code &>/dev/null && found="VS Code"
    [[ -d "/Applications/Cursor.app" ]] && found="Cursor"
    [[ -d "/Applications/Antigravity.app" ]] && found="Antigravity"

    if [[ -n "$found" ]]; then
        _skip "$found"
        return 0
    fi

    echo ""
    echo "  ${BOLD}Which code editor would you like?${RESET}"
    echo ""
    echo "    1) VS Code          (free, most extensions)"
    echo "    2) Cursor            (AI-native, VS Code fork)"
    echo "    3) Antigravity       (lightweight, fast)"
    echo "    4) Skip              (install one yourself later)"
    echo ""
    printf "  Choice [1-4]: "
    read -r editor_choice

    case "${editor_choice:-1}" in
        1)
            _info "Installing VS Code..."
            brew install --cask visual-studio-code 2>&1 | tail -3
            command -v code &>/dev/null && _ok "VS Code" || _warn "VS Code install failed"
            ;;
        2)
            _info "Installing Cursor..."
            brew install --cask cursor 2>&1 | tail -3
            [[ -d "/Applications/Cursor.app" ]] && _ok "Cursor" || _warn "Cursor install failed"
            ;;
        3)
            _info "Antigravity is not available via Homebrew."
            _info "Download from: https://antigravity.app"
            _warn "Skipping editor — install Antigravity manually"
            ;;
        4|*)
            _info "Skipping editor install"
            ;;
    esac
}

prereq_obsidian() {
    if [[ -d "/Applications/Obsidian.app" ]]; then
        _skip "Obsidian"
        return 0
    fi

    echo ""
    echo "  ${BOLD}Install Obsidian?${RESET} (knowledge vault UI)"
    echo "  AOS uses ~/vault/ for knowledge — Obsidian gives you a visual interface."
    echo ""
    printf "  Install? [Y/n]: "
    read -r obs_choice

    case "${obs_choice:-y}" in
        [Yy]|"")
            _info "Installing Obsidian..."
            brew install --cask obsidian 2>&1 | tail -3
            [[ -d "/Applications/Obsidian.app" ]] && _ok "Obsidian" || _warn "Obsidian install failed"
            ;;
        *)
            _info "Skipping Obsidian"
            ;;
    esac
}

prereq_jq() {
    if command -v jq &>/dev/null; then
        _skip "jq"
        return 0
    fi

    _step "Installing jq..."
    brew install jq 2>&1 | tail -1
    command -v jq &>/dev/null || _die "jq install failed"
    _ok "jq"
}

prereq_claude() {
    # Claude Code — native install (arm64 binary)
    if command -v claude &>/dev/null; then
        # Verify it's the native binary, not npm
        local claude_path
        claude_path=$(which claude 2>/dev/null)
        local file_type
        file_type=$(file "$claude_path" 2>/dev/null || echo "")
        if echo "$file_type" | grep -q "Mach-O"; then
            _skip "Claude Code (native)"
        else
            _skip "Claude Code (non-native — consider reinstalling via native installer)"
        fi
        return 0
    fi

    _step "Installing Claude Code..."
    # Native install via Anthropic's official method
    if curl -fsSL https://claude.ai/install.sh | sh 2>&1 | tail -5; then
        if command -v claude &>/dev/null; then
            _ok "Claude Code (native)"
        else
            # May need PATH refresh
            export PATH="$HOME/.local/bin:$PATH"
            command -v claude &>/dev/null && _ok "Claude Code (native)" || _warn "Claude Code — installed but not on PATH yet"
        fi
    else
        _warn "Claude Code — auto-install failed"
        _info "Install manually: https://docs.anthropic.com/en/docs/claude-code"
        _info "The install will continue — Claude Code is needed for onboarding, not bootstrap."
    fi
}

prereq_ssh() {
    # SSH / Remote Login — essential for headless Mac Mini access
    # Check if Remote Login is enabled
    local status
    status=$(sudo systemsetup -getremotelogin 2>/dev/null | grep -i "on" || echo "")

    if [[ -n "$status" ]]; then
        _skip "SSH (Remote Login)"
        return 0
    fi

    # Try to enable (requires sudo — may prompt)
    _step "Enabling SSH (Remote Login)..."
    if sudo systemsetup -setremotelogin on 2>/dev/null; then
        _ok "SSH (Remote Login)"
    else
        _warn "SSH — could not enable Remote Login (enable manually in System Settings > General > Sharing)"
        _log "SSH enable failed — may need manual setup"
    fi
}

prereq_tailscale() {
    # Tailscale — overlay network for remote access without port forwarding
    if command -v tailscale &>/dev/null; then
        local ts_status
        ts_status=$(tailscale status --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('BackendState',''))" 2>/dev/null || echo "unknown")
        if [[ "$ts_status" == "Running" ]]; then
            _skip "Tailscale (connected)"
        else
            _skip "Tailscale (installed, state: $ts_status)"
            _info "Run 'tailscale up' to connect to your tailnet"
        fi
        return 0
    fi

    _step "Installing Tailscale..."
    if brew install tailscale 2>&1 | tail -3; then
        _ok "Tailscale installed"
        _info "Open Tailscale.app and sign in to connect to your tailnet"
        _info "Then: tailscale up --ssh  (enables Tailscale SSH)"
    else
        _warn "Tailscale — install failed (install manually from https://tailscale.com/download)"
    fi
}

prereq_claude_remote() {
    # Claude Code remote control — allows agents to reach this machine's Claude
    local script="$AOS_DIR/core/bin/claude-remote-start"
    local plist_template="$AOS_DIR/config/launchagents/com.aos.claude-remote.plist.template"

    if ! command -v claude &>/dev/null; then
        _info "Claude Remote — skipped (Claude Code not installed)"
        return 0
    fi

    if [[ ! -f "$script" ]]; then
        _info "Claude Remote — script not found, skipping"
        return 0
    fi

    if launchctl list 2>/dev/null | grep -q "claude-remote"; then
        _skip "Claude Remote"
    else
        _info "Claude Remote — will be configured during onboarding"
    fi
}

run_prereqs() {
    prereq_git
    prereq_homebrew
    prereq_python3
    prereq_pyyaml
    prereq_uv
    prereq_bun
    prereq_qmd
    prereq_jq
    prereq_gh
    prereq_editor
    prereq_obsidian
    prereq_claude

    _step "Remote access"

    prereq_ssh
    prereq_tailscale
    prereq_claude_remote
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 2: Clone repo & PATH setup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

setup_repo() {
    _step "Setting up AOS repository..."
    echo ""

    if [[ -d "$AOS_DIR/.git" ]]; then
        _skip "Repository exists at $AOS_DIR"
        # Only pull if clean working tree (don't clobber local changes)
        local current_branch
        current_branch=$(git -C "$AOS_DIR" branch --show-current 2>/dev/null || echo "")
        local has_upstream
        has_upstream=$(git -C "$AOS_DIR" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || echo "")
        if [[ -n "$has_upstream" ]] && [[ "$current_branch" == "$AOS_BRANCH" ]] && git -C "$AOS_DIR" diff --quiet 2>/dev/null && git -C "$AOS_DIR" diff --cached --quiet 2>/dev/null; then
            _info "Pulling latest..."
            git -C "$AOS_DIR" pull --ff-only 2>&1 | sed 's/^/    /' >> "$INSTALL_LOG"
            _ok "Updated to latest"
        elif [[ -z "$has_upstream" ]]; then
            _info "No remote tracking — skipping pull"
        else
            _info "Local changes detected — skipping pull (use 'aos update' later)"
        fi
    else
        _info "Cloning $AOS_REPO..."
        git clone --branch "$AOS_BRANCH" "$AOS_REPO" "$AOS_DIR" 2>&1 | sed 's/^/    /'
        _ok "Cloned to $AOS_DIR"
    fi
}

setup_path() {
    _step "Setting up PATH..."
    echo ""

    local aos_bin="$AOS_DIR/core/bin/aos"
    local link_target="$HOME/.local/bin/aos"

    # Ensure ~/.local/bin exists and is on PATH
    mkdir -p "$HOME/.local/bin"

    if [[ -L "$link_target" ]] && [[ "$(readlink "$link_target")" == "$aos_bin" ]]; then
        _skip "aos on PATH"
    elif [[ -f "$link_target" ]] || [[ -L "$link_target" ]]; then
        # Something else is there — replace it
        ln -sf "$aos_bin" "$link_target"
        _ok "aos symlinked (replaced existing)"
    else
        ln -s "$aos_bin" "$link_target"
        _ok "aos symlinked to $link_target"
    fi

    # Ensure ~/.local/bin is in shell profile
    local shell_rc
    if [[ -f "$HOME/.zshrc" ]]; then
        shell_rc="$HOME/.zshrc"
    elif [[ -f "$HOME/.bashrc" ]]; then
        shell_rc="$HOME/.bashrc"
    else
        shell_rc="$HOME/.zshrc"
    fi

    if ! grep -q '\.local/bin' "$shell_rc" 2>/dev/null; then
        echo '' >> "$shell_rc"
        echo '# AOS' >> "$shell_rc"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_rc"
        _ok "Added ~/.local/bin to PATH in $(basename "$shell_rc")"
    else
        _skip "PATH entry in $(basename "$shell_rc")"
    fi

    # Make aos executable
    chmod +x "$aos_bin"

    # Symlink cld (claude with bypassed permissions)
    local cld_bin="$AOS_DIR/core/bin/cld"
    local cld_target="$HOME/.local/bin/cld"
    chmod +x "$cld_bin" 2>/dev/null

    if [[ -L "$cld_target" ]] && [[ "$(readlink "$cld_target")" == "$cld_bin" ]]; then
        _skip "cld on PATH"
    else
        ln -sf "$cld_bin" "$cld_target"
        _ok "cld on PATH (claude --dangerously-skip-permissions)"
    fi

    # Also add to current session
    export PATH="$HOME/.local/bin:$PATH"
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 3: User data bootstrap (migrations)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

setup_git_config() {
    _step "Checking git configuration..."
    echo ""

    local name
    name=$(git config --global user.name 2>/dev/null || echo "")
    if [[ -z "$name" ]]; then
        echo ""
        printf "  ${BOLD}Your name${RESET} (for git commits): "
        read -r git_name
        if [[ -n "$git_name" ]]; then
            git config --global user.name "$git_name"
            _ok "Git name: $git_name"
        else
            _warn "Git name not set — set later with: git config --global user.name \"Your Name\""
        fi
    else
        _skip "Git name: $name"
    fi

    local email
    email=$(git config --global user.email 2>/dev/null || echo "")
    if [[ -z "$email" ]]; then
        printf "  ${BOLD}Your email${RESET} (for git commits): "
        read -r git_email
        if [[ -n "$git_email" ]]; then
            git config --global user.email "$git_email"
            _ok "Git email: $git_email"
        else
            _warn "Git email not set — set later with: git config --global user.email \"you@example.com\""
        fi
    else
        _skip "Git email: $email"
    fi
}

run_bootstrap() {
    _step "Bootstrapping user data..."
    echo ""

    # Ensure minimum structure exists for migration runner and services
    mkdir -p "$USER_DIR/logs"
    mkdir -p "$USER_DIR/logs/crons/locks"
    mkdir -p "$USER_DIR/config"

    # Generate machine ID if not present
    if [[ -f "$MACHINE_ID_FILE" ]]; then
        _skip "Machine ID"
    else
        local machine_id
        machine_id="aos-$(uname -n | tr '[:upper:]' '[:lower:]' | tr ' ' '-')-$(date +%s | shasum | head -c 8)"
        echo "$machine_id" > "$MACHINE_ID_FILE"
        _ok "Machine ID: $machine_id"
    fi

    # Create project directory
    local project_dir="$HOME/project"
    if [[ -d "$project_dir" ]]; then
        _skip "Projects directory"
    else
        mkdir -p "$project_dir"
        _ok "Created ~/project/"
    fi

    # Create knowledge vault with standard structure
    local vault_dir="$HOME/vault"
    if [[ -d "$vault_dir" ]]; then
        _skip "Knowledge vault"
    else
        mkdir -p "$vault_dir"/{daily,knowledge,log,ops/sessions,reviews,sessions}
        _ok "Created ~/vault/ with standard structure"
    fi

    # Scaffold operator profile if not present
    local operator_yaml="$USER_DIR/config/operator.yaml"
    if [[ -f "$operator_yaml" ]]; then
        _skip "Operator profile"
    else
        local op_name
        op_name=$(git config --global user.name 2>/dev/null || echo "")
        local op_tz
        op_tz=$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' || echo "UTC")
        cat > "$operator_yaml" << OPERATOR
# Operator Profile
# Chief reads this at session start to personalize behavior.
# This file is user data — never committed to the system repo.

name: ${op_name:-Operator}
timezone: $op_tz

# Communication preferences
communication:
  style: concise           # concise | detailed | conversational
  questions: one-at-a-time # never batch questions
  language: en             # primary language

# Schedule blocks (Chief respects these, won't interrupt)
schedule:
  blocks: []
  # Example:
  #   - name: Focus time
  #     days: [mon, tue, wed, thu, fri]
  #     start: "09:00"
  #     end: "12:00"

# Daily loop timing
daily_loop:
  morning_briefing: "07:00"
  evening_checkin: "21:00"

# Trust preferences
trust:
  default_level: 1          # 0=SHADOW, 1=APPROVAL, 2=SEMI-AUTO, 3=FULL-AUTO
  escalation: always        # always ask before destructive actions
OPERATOR
        _ok "Operator profile scaffolded (name: ${op_name:-Operator}, tz: $op_tz)"
        _info "Edit ~/.aos/config/operator.yaml to customize"
    fi

    # Run migrations
    _info "Running migrations..."
    echo ""
    python3 "$AOS_DIR/core/migrations/runner.py" migrate 2>&1 | sed 's/^/    /'
    echo ""
    _ok "Migrations complete"

    # Sync agents
    _step "Syncing agents..."
    echo ""
    bash "$AOS_DIR/core/bin/aos" sync-agents 2>&1 | sed 's/^/  /'

    # Sync skills — prompt for developer mode
    _step "Installing skills..."
    echo ""
    _info "14 default skills will be installed (work, recall, review, etc.)"
    echo ""
    echo "  ${BOLD}Also install developer skills?${RESET}"
    echo "  (debugging, code review, execution plans — 9 extra skills)"
    echo ""
    printf "  Install developer skills? [y/N]: "
    read -r dev_choice

    case "${dev_choice:-n}" in
        [Yy])
            touch "$USER_DIR/config/developer-mode"
            bash "$AOS_DIR/core/bin/aos" sync-skills --all 2>&1 | sed 's/^/  /'
            ;;
        *)
            bash "$AOS_DIR/core/bin/aos" sync-skills 2>&1 | sed 's/^/  /'
            ;;
    esac
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 4: Service deployment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

deploy_services() {
    _step "Deploying services..."
    echo ""

    local services_src="$AOS_DIR/core/services"
    local services_dst="$USER_DIR/services"

    for src_dir in "$services_src"/*/; do
        local name
        name=$(basename "$src_dir")
        [[ -f "$src_dir/pyproject.toml" ]] || continue

        local dst="$services_dst/$name"

        if [[ -d "$dst/.venv" ]] && [[ -f "$dst/.venv/bin/python" ]]; then
            _skip "Service $name"
        else
            _info "Deploying $name..."
            mkdir -p "$dst"
            uv venv "$dst/.venv" --quiet 2>/dev/null
            uv pip install --quiet -p "$dst/.venv/bin/python" -r <(
                python3 -c "
import tomllib
with open('$src_dir/pyproject.toml', 'rb') as f:
    deps = tomllib.load(f).get('project', {}).get('dependencies', [])
print('\n'.join(deps))
"
            ) 2>&1 | tail -3 >> "$INSTALL_LOG"
            _ok "Service $name"
        fi
    done

    # Install LaunchAgents from templates
    install_launchagents
}

install_launchagents() {
    _step "Setting up LaunchAgents..."
    echo ""

    local la_dir="$HOME/Library/LaunchAgents"
    mkdir -p "$la_dir"

    local templates_dir="$AOS_DIR/config/launchagents"

    # Handle static plists (e.g., com.aos.scheduler.plist)
    for plist_file in "$templates_dir"/*.plist; do
        [[ -f "$plist_file" ]] || continue
        local name
        name=$(basename "$plist_file")
        local target="$la_dir/$name"

        local temp_plist
        temp_plist=$(mktemp)
        sed "s|__HOME__|$HOME|g" "$plist_file" > "$temp_plist"

        if [[ -f "$target" ]] && diff -q "$temp_plist" "$target" &>/dev/null; then
            _skip "LaunchAgent $name"
            rm "$temp_plist"
        else
            launchctl unload "$target" 2>/dev/null || true
            mv "$temp_plist" "$target"
            launchctl load "$target" 2>/dev/null || true
            _ok "LaunchAgent $name"
        fi
    done

    # Handle template plists (e.g., com.aos.bridge.plist.template)
    for template in "$templates_dir"/*.plist.template; do
        [[ -f "$template" ]] || continue
        local name
        name=$(basename "$template" .template)  # com.aos.bridge.plist
        local target="$la_dir/$name"

        # Generate from template — substitute __HOME__ placeholder
        local temp_plist
        temp_plist=$(mktemp)
        sed "s|__HOME__|$HOME|g" "$template" > "$temp_plist"

        if [[ -f "$target" ]] && diff -q "$temp_plist" "$target" &>/dev/null; then
            _skip "LaunchAgent $name"
            rm "$temp_plist"
        else
            launchctl unload "$target" 2>/dev/null || true
            mv "$temp_plist" "$target"
            launchctl load "$target" 2>/dev/null || true
            _ok "LaunchAgent $name"
        fi
    done
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 5: macOS provisioning
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

configure_dock() {
    _step "Configuring Dock..."
    echo ""

    local changed=0

    # Auto-hide dock
    local current_autohide
    current_autohide=$(defaults read com.apple.dock autohide 2>/dev/null || echo "0")
    if [[ "$current_autohide" != "1" ]]; then
        defaults write com.apple.dock autohide -bool true
        ((changed++))
        _ok "Dock auto-hide enabled"
    else
        _skip "Dock auto-hide"
    fi

    # Zero delay on show
    local current_delay
    current_delay=$(defaults read com.apple.dock autohide-delay 2>/dev/null || echo "not set")
    if [[ "$current_delay" != "0" ]]; then
        defaults write com.apple.dock autohide-delay -float 0
        ((changed++))
        _ok "Dock show delay: 0ms"
    else
        _skip "Dock show delay"
    fi

    # Zero animation time
    local current_anim
    current_anim=$(defaults read com.apple.dock autohide-time-modifier 2>/dev/null || echo "not set")
    if [[ "$current_anim" != "0" ]]; then
        defaults write com.apple.dock autohide-time-modifier -float 0
        ((changed++))
        _ok "Dock animation: 0ms"
    else
        _skip "Dock animation"
    fi

    # Clear dock — keep only essential apps
    # Essential: Finder (always there), Terminal, VS Code, System Settings
    local app_count
    app_count=$(defaults read com.apple.dock persistent-apps 2>/dev/null | grep -c "tile-data" || echo "0")
    if [[ "$app_count" -gt 5 ]]; then
        # Clear all persistent apps
        defaults write com.apple.dock persistent-apps -array

        # Add back essentials
        for app in "/System/Applications/Utilities/Terminal.app" \
                   "/Applications/Visual Studio Code.app" \
                   "/System/Applications/System Settings.app"; do
            if [[ -d "$app" ]]; then
                defaults write com.apple.dock persistent-apps -array-add \
                    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$app</string><key>_CFURLStringType</key><integer>0</integer></dict></dict></dict>"
            fi
        done
        ((changed++))
        _ok "Dock cleared — kept Terminal, VS Code, System Settings"
    else
        _skip "Dock apps (already minimal)"
    fi

    # Restart dock if changes were made
    if [[ "$changed" -gt 0 ]]; then
        killall Dock 2>/dev/null || true
        _info "Dock restarted"
    fi
}

configure_desktop() {
    _step "Configuring desktop..."
    echo ""

    # Set solid black wallpaper
    # Create a 1x1 black PNG if it doesn't exist
    local wallpaper="$AOS_DIR/config/wallpaper-black.png"
    if [[ ! -f "$wallpaper" ]]; then
        # Generate a small black PNG using Python
        python3 -c "
import struct, zlib
def create_black_png(path, w=64, h=64):
    raw = b''
    for _ in range(h):
        raw += b'\x00' + b'\x00\x00\x00' * w
    compressed = zlib.compress(raw)
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', compressed))
        f.write(chunk(b'IEND', b''))
create_black_png('$wallpaper')
" 2>/dev/null
        _ok "Created black wallpaper"
    else
        _skip "Black wallpaper asset"
    fi

    # Apply wallpaper to all desktops
    if [[ -f "$wallpaper" ]]; then
        osascript -e "
            tell application \"System Events\"
                tell every desktop
                    set picture to \"$wallpaper\"
                end tell
            end tell
        " 2>/dev/null && _ok "Desktop wallpaper set to black" || _warn "Could not set wallpaper (set manually in System Settings)"
    fi
}

configure_terminal() {
    _step "Configuring Terminal.app..."
    echo ""

    local profile="AOS"
    local current_default
    current_default=$(defaults read com.apple.Terminal "Default Window Settings" 2>/dev/null || echo "")

    if [[ "$current_default" == "$profile" ]]; then
        _skip "Terminal profile '$profile'"
        return 0
    fi

    # Create AOS terminal profile — dark, clean, good font
    osascript -e "
        tell application \"Terminal\"
            -- Duplicate existing dark profile as base
            set baseProfile to \"Basic\"
            set profileNames to name of every settings set
            if \"$profile\" is not in profileNames then
                -- Create new profile based on Basic
                set newProfile to make new settings set with properties {name:\"$profile\"}
            end if

            set targetProfile to settings set \"$profile\"

            -- Dark background (pure black)
            set background color of targetProfile to {0, 0, 0}
            -- Light text
            set normal text color of targetProfile to {57344, 57344, 57344}
            -- Green cursor
            set cursor color of targetProfile to {0, 52224, 0}
            -- Font: Menlo 13pt
            set font name of targetProfile to \"MenloRegular\"
            set font size of targetProfile to 13
            -- No window title bar clutter
            set title displays shell path of targetProfile to false
            set title displays window size of targetProfile to false
            set title displays device name of targetProfile to false
            -- Columns and rows
            set number of columns of targetProfile to 120
            set number of rows of targetProfile to 35
        end tell
    " 2>/dev/null

    # Set as default
    defaults write com.apple.Terminal "Default Window Settings" -string "$profile"
    defaults write com.apple.Terminal "Startup Window Settings" -string "$profile"

    _ok "Terminal profile '$profile' — black bg, Menlo 13pt, green cursor"
}

configure_macos() {
    _step "Configuring macOS system preferences..."
    echo ""

    # Disable Notification Center (less noise on headless machine)
    # Show battery percentage
    # Disable auto-correct (interferes with agent typing)
    local prefs_changed=0

    # Disable auto-correct
    local autocorrect
    autocorrect=$(defaults read NSGlobalDomain NSAutomaticSpellingCorrectionEnabled 2>/dev/null || echo "1")
    if [[ "$autocorrect" != "0" ]]; then
        defaults write NSGlobalDomain NSAutomaticSpellingCorrectionEnabled -bool false
        ((prefs_changed++))
        _ok "Auto-correct disabled"
    else
        _skip "Auto-correct"
    fi

    # Disable auto-capitalization
    local autocaps
    autocaps=$(defaults read NSGlobalDomain NSAutomaticCapitalizationEnabled 2>/dev/null || echo "1")
    if [[ "$autocaps" != "0" ]]; then
        defaults write NSGlobalDomain NSAutomaticCapitalizationEnabled -bool false
        ((prefs_changed++))
        _ok "Auto-capitalization disabled"
    else
        _skip "Auto-capitalization"
    fi

    # Disable smart quotes (breaks code pasting)
    local smartquotes
    smartquotes=$(defaults read NSGlobalDomain NSAutomaticQuoteSubstitutionEnabled 2>/dev/null || echo "1")
    if [[ "$smartquotes" != "0" ]]; then
        defaults write NSGlobalDomain NSAutomaticQuoteSubstitutionEnabled -bool false
        ((prefs_changed++))
        _ok "Smart quotes disabled"
    else
        _skip "Smart quotes"
    fi

    # Disable smart dashes (breaks code)
    local smartdashes
    smartdashes=$(defaults read NSGlobalDomain NSAutomaticDashSubstitutionEnabled 2>/dev/null || echo "1")
    if [[ "$smartdashes" != "0" ]]; then
        defaults write NSGlobalDomain NSAutomaticDashSubstitutionEnabled -bool false
        ((prefs_changed++))
        _ok "Smart dashes disabled"
    else
        _skip "Smart dashes"
    fi

    # Faster key repeat
    local keyrepeat
    keyrepeat=$(defaults read NSGlobalDomain KeyRepeat 2>/dev/null || echo "6")
    if [[ "$keyrepeat" -gt 2 ]]; then
        defaults write NSGlobalDomain KeyRepeat -int 2
        defaults write NSGlobalDomain InitialKeyRepeat -int 15
        ((prefs_changed++))
        _ok "Fast key repeat"
    else
        _skip "Key repeat speed"
    fi

    # Expand save panel by default
    local savepanel
    savepanel=$(defaults read NSGlobalDomain NSNavPanelExpandedStateForSaveMode 2>/dev/null || echo "0")
    if [[ "$savepanel" != "1" ]]; then
        defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode -bool true
        defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode2 -bool true
        ((prefs_changed++))
        _ok "Expanded save panels"
    else
        _skip "Save panel expansion"
    fi

    if [[ "$prefs_changed" -gt 0 ]]; then
        _info "Some preferences may require logout to take full effect"
    fi
}

setup_statusline() {
    _step "Setting up Claude Code statusline..."
    echo ""

    local statusline_script="$HOME/.claude/statusline.sh"
    local statusline_source="$AOS_DIR/config/statusline.sh"

    # Ship the statusline script with AOS
    if [[ -f "$statusline_script" ]]; then
        _skip "Statusline script"
    elif [[ -f "$statusline_source" ]]; then
        cp "$statusline_source" "$statusline_script"
        chmod +x "$statusline_script"
        _ok "Statusline script installed"
    else
        # Create default statusline
        cat > "$statusline_script" << 'STATUSLINE'
#!/bin/bash
input=$(cat)

MODEL=$(echo "$input" | jq -r '.model.display_name // "?"' | sed 's/Opus 4.6 (1M context)/O4.6/' | sed 's/Sonnet 4.6/S4.6/' | sed 's/Haiku 4.5/H4.5/')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
COST=$(printf "%.2f" "$(echo "$input" | jq -r '.cost.total_cost_usd // 0')")
DUR_MS=$(echo "$input" | jq -r '.cost.total_duration_ms // 0')
LINES_ADD=$(echo "$input" | jq -r '.cost.total_lines_added // 0')
LINES_DEL=$(echo "$input" | jq -r '.cost.total_lines_removed // 0')

# Context bar
FILLED=$((PCT * 15 / 100))
EMPTY=$((15 - FILLED))
BAR=$(printf "%${FILLED}s" | tr ' ' '▓')$(printf "%${EMPTY}s" | tr ' ' '░')

# Color context percentage based on usage
if [ "$PCT" -ge 80 ]; then
  CLR="\033[31m"  # red
elif [ "$PCT" -ge 50 ]; then
  CLR="\033[33m"  # yellow
else
  CLR="\033[32m"  # green
fi
RST="\033[0m"

# Duration
DUR_SEC=$((DUR_MS / 1000))
MINS=$((DUR_SEC / 60))
SECS=$((DUR_SEC % 60))

GRN="\033[32m"
RED="\033[31m"

printf "${CLR}%s${RST} %s ${CLR}%s%%${RST}  \$%s  %dm%02ds  ${GRN}+%s${RST} ${RED}-%s${RST}" \
  "$MODEL" "$BAR" "$PCT" "$COST" "$MINS" "$SECS" "$LINES_ADD" "$LINES_DEL"
STATUSLINE
        chmod +x "$statusline_script"
        _ok "Statusline script created"
    fi

    # Wire statusline into settings.json if not already set
    local has_statusline
    has_statusline=$(python3 -c "
import json
with open('$HOME/.claude/settings.json') as f:
    s = json.load(f)
print('yes' if 'statusLine' in s else 'no')
" 2>/dev/null || echo "no")

    if [[ "$has_statusline" == "no" ]]; then
        python3 -c "
import json
with open('$HOME/.claude/settings.json') as f:
    s = json.load(f)
s['statusLine'] = {
    'type': 'command',
    'command': '~/.claude/statusline.sh',
    'padding': 2
}
with open('$HOME/.claude/settings.json', 'w') as f:
    json.dump(s, f, indent=2)
    f.write('\n')
" 2>/dev/null
        _ok "Statusline wired into settings.json"
    else
        _skip "Statusline in settings.json"
    fi

    # Ensure all required settings.json keys exist (agent, env, hooks)
    # This is a backstop — migrations handle this too, but install.sh
    # should guarantee the minimum config for a working system.
    python3 -c "
import json
from pathlib import Path

settings_path = Path.home() / '.claude' / 'settings.json'
settings_path.parent.mkdir(parents=True, exist_ok=True)

if settings_path.exists():
    with open(settings_path) as f:
        s = json.load(f)
else:
    s = {}

changed = []

# Default agent: Chief
if not s.get('agent'):
    s['agent'] = 'chief'
    changed.append('agent=chief')

# Agent teams env vars
if 'env' not in s:
    s['env'] = {}
if 'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS' not in s['env']:
    s['env']['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'] = '1'
    changed.append('agent-teams')
if 'CLAUDE_CODE_TEAMMATE_MODE' not in s['env']:
    s['env']['CLAUDE_CODE_TEAMMATE_MODE'] = 'in-process'
    changed.append('teammate-mode')

# Work system hooks
if 'hooks' not in s:
    s['hooks'] = {}

aos_home = Path.home() / 'aos'

hook_defs = {
    'SessionStart': {
        'command': f'python3 {aos_home}/core/work/inject_context.py',
        'statusMessage': 'Loading work context...',
    },
    'PostCompact': {
        'command': f'python3 {aos_home}/core/work/inject_context.py',
        'statusMessage': 'Reloading work context...',
    },
    'Stop': {
        'command': f'python3 {aos_home}/core/work/reconcile.py',
        'async': True,
    },
    'SessionEnd': {
        'command': f'python3 {aos_home}/core/work/session_close.py',
        'async': True,
    },
}

for event, hook_props in hook_defs.items():
    if event not in s['hooks'] or not s['hooks'][event]:
        hook_entry = {'hooks': [{'type': 'command', **hook_props}]}
        s['hooks'][event] = [hook_entry]
        changed.append(f'hook:{event}')

if changed:
    with open(settings_path, 'w') as f:
        json.dump(s, f, indent=2)
        f.write('\n')
    print('CHANGED:' + ','.join(changed))
else:
    print('OK')
" 2>/dev/null
    local result=$?
    if [[ $result -eq 0 ]]; then
        local output
        output=$(python3 -c "
import json
from pathlib import Path
settings_path = Path.home() / '.claude' / 'settings.json'
if settings_path.exists():
    with open(settings_path) as f:
        s = json.load(f)
    checks = []
    if s.get('agent') == 'chief': checks.append('chief')
    if 'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS' in s.get('env', {}): checks.append('teams')
    hooks = s.get('hooks', {})
    for h in ['SessionStart', 'PostCompact', 'Stop', 'SessionEnd']:
        if hooks.get(h): checks.append(h)
    print(','.join(checks))
" 2>/dev/null)
        _ok "Settings verified: $output"
    fi
}

run_provisioning() {
    configure_dock
    configure_desktop
    configure_terminal
    configure_macos
    setup_statusline
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 6: Health gate & handoff
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

run_discovery() {
    _step "Running discovery scan..."
    echo ""
    python3 "$AOS_DIR/core/migrations/runner.py" discover 2>&1 | sed 's/^/    /'
    echo ""
}

run_health_gate() {
    _step "Running health check..."
    echo ""

    local errors=0

    # Core checks
    [[ -d "$USER_DIR" ]]                   && _ok "User data dir"          || { _fail "User data dir missing"; ((errors++)); }
    [[ -f "$USER_DIR/.version" ]]          && _ok "Migrations applied"     || { _fail "Migrations not run"; ((errors++)); }
    [[ -f "$USER_DIR/.machine-id" ]]       && _ok "Machine ID"             || { _fail "No machine ID"; ((errors++)); }
    [[ -f "$USER_DIR/events.jsonl" ]]      && _ok "Event bus"              || { _fail "Event bus missing"; ((errors++)); }
    [[ -f "$USER_DIR/work/work.yaml" ]]    && _ok "Work system"            || { _fail "work.yaml missing"; ((errors++)); }

    # Context files
    [[ -f "$HOME/CLAUDE.md" ]]             && _ok "Root CLAUDE.md"         || { _fail "~/CLAUDE.md missing"; ((errors++)); }
    [[ -f "$HOME/.claude/CLAUDE.md" ]]     && _ok "Global CLAUDE.md"       || { _fail "~/.claude/CLAUDE.md missing"; ((errors++)); }

    # Vault & projects
    [[ -d "$HOME/vault" ]]                 && _ok "Knowledge vault"        || { _fail "~/vault/ missing"; ((errors++)); }
    [[ -d "$HOME/project" ]]               && _ok "Projects directory"     || { _warn "~/project/ missing"; }

    # Git config
    local git_name git_email
    git_name=$(git config --global user.name 2>/dev/null || echo "")
    git_email=$(git config --global user.email 2>/dev/null || echo "")
    [[ -n "$git_name" ]]                   && _ok "Git name: $git_name"    || { _warn "Git name not set"; }
    [[ -n "$git_email" ]]                  && _ok "Git email: $git_email"  || { _warn "Git email not set"; }

    # Settings — agent, env vars, hooks
    if [[ -f "$HOME/.claude/settings.json" ]]; then
        local settings_check
        settings_check=$(python3 -c "
import json
with open('$HOME/.claude/settings.json') as f:
    s = json.load(f)
issues = []
if s.get('agent') != 'chief': issues.append('agent not chief')
env = s.get('env', {})
if 'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS' not in env: issues.append('no agent-teams env')
hooks = s.get('hooks', {})
for h in ['SessionStart', 'PostCompact', 'Stop', 'SessionEnd']:
    if not hooks.get(h): issues.append(f'no {h} hook')
print('|'.join(issues) if issues else 'OK')
" 2>/dev/null || echo "parse error")

        if [[ "$settings_check" == "OK" ]]; then
            _ok "Settings: agent=chief, teams, hooks (3/3)"
        else
            IFS='|' read -ra issues <<< "$settings_check"
            for issue in "${issues[@]}"; do
                _warn "Settings: $issue"
            done
        fi
    else
        _fail "settings.json missing"; ((errors++))
    fi

    # Operator profile
    [[ -f "$USER_DIR/config/operator.yaml" ]] && _ok "Operator profile" || { _warn "Operator profile missing"; }

    # Log directories
    [[ -d "$USER_DIR/logs/crons" ]]           && _ok "Cron log directory" || { _warn "Cron log dir missing"; }

    # Agent symlinks
    for agent in chief steward advisor; do
        local link="$HOME/.claude/agents/${agent}.md"
        if [[ -L "$link" ]] || [[ -f "$link" ]]; then
            _ok "Agent $agent"
        else
            _fail "Agent $agent not installed"
            ((errors++))
        fi
    done

    # Skills — check default skills are symlinked
    local default_skills="recall work review step-by-step obsidian-cli extract telegram-admin bridge-ops marketing diagram session-analysis frontend-design architect skill-creator skill-scanner"
    local skill_count=0
    local skill_missing=0
    for skill_name in $default_skills; do
        local link="$HOME/.claude/skills/$skill_name"
        if [[ -L "$link" ]]; then
            ((skill_count++))
        else
            _fail "Skill $skill_name not linked"
            ((skill_missing++))
            ((errors++))
        fi
    done
    if [[ "$skill_missing" -eq 0 ]]; then
        _ok "All $skill_count default skills linked"
    fi

    # Check developer skills if opted in
    if [[ -f "$USER_DIR/config/developer-mode" ]]; then
        local dev_count=0
        for skill_name in systematic-debugging verification-before-completion requesting-code-review receiving-code-review executing-plans writing-plans dispatching-parallel-agents writing-skills autonomous-execution; do
            local link="$HOME/.claude/skills/$skill_name"
            [[ -L "$link" ]] && ((dev_count++))
        done
        _ok "$dev_count developer skills linked"
    fi

    # Services
    for svc in bridge dashboard listen memory; do
        local venv="$USER_DIR/services/$svc/.venv/bin/python"
        if [[ -f "$venv" ]]; then
            _ok "Service $svc"
        else
            _warn "Service $svc — no venv"
        fi
    done

    # LaunchAgents — verify scheduler and core services are loaded
    local la_loaded=0
    for la in com.aos.scheduler com.aos.bridge com.aos.dashboard com.aos.listen; do
        if launchctl list 2>/dev/null | grep -q "$la"; then
            ((la_loaded++))
        else
            _warn "LaunchAgent $la — not loaded"
        fi
    done
    [[ "$la_loaded" -eq 4 ]] && _ok "All $la_loaded core LaunchAgents loaded"

    # Scheduler — verify crons.yaml exists and scheduler can find it
    [[ -f "$AOS_DIR/config/crons.yaml" ]]    && _ok "Cron definitions (crons.yaml)" || { _warn "crons.yaml missing"; }

    # QMD — vault search
    if [[ -f "$HOME/.bun/bin/qmd" ]] || command -v qmd &>/dev/null; then
        _ok "QMD (vault search)"
    else
        _warn "QMD — not installed (vault search unavailable)"
    fi

    # Remote access
    if sudo systemsetup -getremotelogin 2>/dev/null | grep -qi "on"; then
        _ok "SSH (Remote Login)"
    else
        _warn "SSH — Remote Login not enabled"
    fi

    if command -v tailscale &>/dev/null; then
        _ok "Tailscale installed"
    else
        _warn "Tailscale — not installed"
    fi

    if launchctl list 2>/dev/null | grep -q "claude-remote"; then
        _ok "Claude Remote"
    else
        _warn "Claude Remote — not running"
    fi

    echo ""
    if [[ "$errors" -eq 0 ]]; then
        _ok "All health checks passed"
    else
        _fail "$errors check(s) failed — see above"
    fi

    return "$errors"
}

print_handoff() {
    local total=$(_total_elapsed)
    local hostname=$(scutil --get ComputerName 2>/dev/null || hostname -s)
    local machine_id=$(cat "$MACHINE_ID_FILE" 2>/dev/null || echo "unknown")
    local op_name=$(python3 -c "
import yaml
try:
    with open('$USER_DIR/config/operator.yaml') as f:
        print(yaml.safe_load(f).get('name', 'Operator'))
except: print('Operator')
" 2>/dev/null || echo "Operator")

    tput cnorm 2>/dev/null  # restore cursor

    echo ""
    echo "  ${MUTED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
    echo "  ${GREEN}${BOLD}AOS v${AOS_VERSION} installed successfully.${RESET}"
    echo ""
    printf "  ${MUTED}%-12s${RESET}%s\n" "Machine" "$hostname"
    printf "  ${MUTED}%-12s${RESET}%s\n" "ID" "$machine_id"
    printf "  ${MUTED}%-12s${RESET}%s\n" "Operator" "$op_name"
    printf "  ${MUTED}%-12s${RESET}%s\n" "Duration" "$total"
    echo ""
    echo "  ${MUTED}────────────────────────────────────────────────────${RESET}"
    echo ""

    if command -v claude &>/dev/null; then
        echo "  ${BOLD}Get started:${RESET}"
        echo ""
        echo "    ${BRAND}${BOLD}\$ claude${RESET}"
        echo ""
        echo "  ${MUTED}Chief is your default agent. Open a terminal,${RESET}"
        echo "  ${MUTED}type claude, and Chief handles the rest.${RESET}"
    else
        echo "  ${BOLD}Next:${RESET} Install Claude Code, then run ${BRAND}claude${RESET}"
        echo "  ${MUTED}https://docs.anthropic.com/en/docs/claude-code${RESET}"
    fi

    echo ""
    echo "  ${MUTED}────────────────────────────────────────────────────${RESET}"
    echo ""
    echo "  ${MUTED}aos status      check migration status${RESET}"
    echo "  ${MUTED}aos self-test   verify system health${RESET}"
    echo "  ${MUTED}aos update      pull latest + migrate${RESET}"
    echo ""
    echo "  ${MUTED}Log: $INSTALL_LOG${RESET}"
    echo ""
    echo "  ${MUTED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
    echo "  ${MUTED}alhamdulillah${RESET}"
    echo ""
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

main() {
    # Init logging (needs minimum dir structure)
    mkdir -p "$LOG_DIR"
    _log_init

    # Show banner
    _banner

    # Part 1: Prerequisites
    _phase "Prerequisites"
    run_prereqs

    # Part 2: Repo & PATH
    _phase "Repository & PATH"
    setup_repo
    setup_path
    setup_git_config

    # Part 3: Bootstrap (migrations)
    _phase "Bootstrap"
    run_bootstrap

    # Part 4: Services
    _phase "Services"
    deploy_services

    # Part 5: macOS provisioning
    _phase "System configuration"
    run_provisioning

    # Part 6: Health gate + Handoff
    _phase "Health check"
    run_discovery
    run_health_gate
    print_handoff

    _log "Install complete"
}

main "$@"
