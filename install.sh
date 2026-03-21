#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AOS — Agentic Operating System
#  Bootstrap installer
#
#  Usage (one-liner):
#    curl -fsSL https://raw.githubusercontent.com/hishamalhadi/aos/main/install.sh | bash
#
#  Or manually:
#    git clone https://github.com/hishamalhadi/aos.git ~/aos
#    bash ~/aos/install.sh
#
#  Idempotent. Safe to re-run. Resumes from where it left off.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -uo pipefail
# No set -e — installer handles errors per-step, never exits on failure

# Don't run as root — Homebrew refuses it and files get wrong ownership
if [[ $EUID -eq 0 ]]; then
    echo "Don't run with sudo. Just:"
    echo ""
    echo "  bash ~/aos/install.sh"
    echo ""
    echo "It will ask for your password when it needs it."
    exit 1
fi

# Cache sudo upfront — one password prompt, then it's good for the whole install
echo "AOS needs admin access for Homebrew, SSH, and system config."
echo ""
if ! sudo true; then
    echo "  Failed to get admin access. Some steps may fail."
    echo ""
fi

# ── Version ──────────────────────────────────────────
AOS_VERSION="0.1.0"
AOS_REPO="https://github.com/hishamalhadi/aos.git"
AOS_BRANCH="main"

# ── Paths ────────────────────────────────────────────
AOS_DIR="$HOME/aos"
USER_DIR="$HOME/.aos"
LOG_DIR="$USER_DIR/logs"
INSTALL_LOG="$LOG_DIR/install.log"
MACHINE_ID_FILE="$USER_DIR/.machine-id"

# ── Ensure PATH is correct (critical for resume) ────
# On resume, prereqs are skipped but brew/bun paths are still needed.
if [[ -f /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -f /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
fi
export BUN_INSTALL="$HOME/.bun"
export PATH="$HOME/.local/bin:$BUN_INSTALL/bin:$PATH"

# ── Modes ──────────────────────────────────────────
DRY_RUN=false
CHECKPOINT_FILE="$HOME/.aos/.install-checkpoint"

for arg in "$@"; do
    case "$arg" in
        --dry-run)  DRY_RUN=true ;;
        --resume)   ;; # default behavior — checkpoints handle resume
        --clean)    rm -f "$HOME/.aos/.install-checkpoint" 2>/dev/null ;;
    esac
done

# Checkpoint helpers — track completed phases for resume
_checkpoint_done() {
    mkdir -p "$(dirname "$CHECKPOINT_FILE")"
    echo "$1" >> "$CHECKPOINT_FILE"
}
_checkpoint_skip() {
    [[ -f "$CHECKPOINT_FILE" ]] && grep -qx "$1" "$CHECKPOINT_FILE" 2>/dev/null
}

# Network check — fail fast if offline
_check_network() {
    if ! curl -sfm 5 https://brew.sh >/dev/null 2>&1; then
        _warn "No internet connection detected"
        _info "Some install steps require network access (Homebrew, pip, git clone)"
        printf "\n  Continue anyway? [y/N]: "
        read -r net_choice
        [[ "${net_choice:-n}" =~ ^[Yy]$ ]] || exit 1
    fi
}

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
_TOTAL_STEPS=7

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
    # sudo is already active from main() — NONINTERACTIVE skips Homebrew's own prompts
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

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
    # Always prefer Homebrew python over macOS system python
    # Put brew paths first so python3 resolves correctly for this session
    if [[ -d /opt/homebrew/bin ]]; then
        export PATH="/opt/homebrew/bin:$PATH"
    fi
    export PATH="$HOME/.local/bin:$PATH"

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

    # brew install python@3.13 creates python3.13 but may not create python3
    # Force the link so python3 points to brew's version, not macOS 3.9
    brew link --overwrite python@3.13 2>/dev/null || true

    # Find brew's python and make it the default for this session + future shells
    local brew_python=""
    for p in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 /usr/local/bin/python3.13 /usr/local/bin/python3; do
        if [[ -f "$p" ]]; then
            local pver
            pver=$("$p" --version 2>&1 | awk '{print $2}')
            local pminor
            pminor=$(echo "$pver" | cut -d. -f2)
            if [[ "$pminor" -ge 11 ]]; then
                brew_python="$p"
                break
            fi
        fi
    done

    if [[ -n "$brew_python" ]]; then
        _ok "Python $("$brew_python" --version 2>&1 | awk '{print $2}') ($brew_python)"
        # Symlink so python3 resolves to brew python everywhere
        mkdir -p "$HOME/.local/bin"
        ln -sf "$brew_python" "$HOME/.local/bin/python3"
        # Rehash so this session sees the new python3
        hash -r 2>/dev/null || true
    else
        _warn "Python 3.11+ not found — some features won't work"
    fi
}

prereq_pyyaml() {
    if python3 -c "import yaml" 2>/dev/null; then
        _skip "PyYAML"
        return 0
    fi

    _step "Installing PyYAML..."
    # Use uv if available, fall back to pip. Target the active python3 explicitly.
    if command -v uv &>/dev/null; then
        uv pip install --python "$(which python3)" --quiet pyyaml 2>&1 || \
        python3 -m pip install --quiet --disable-pip-version-check --break-system-packages pyyaml 2>&1
    else
        python3 -m pip install --quiet --disable-pip-version-check --break-system-packages pyyaml 2>&1
    fi
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
    # Set global install dir — Homebrew bun doesn't set this by default
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"

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
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"

    if [[ -f "$HOME/.bun/bin/qmd" ]] && "$HOME/.bun/bin/qmd" --version &>/dev/null; then
        _skip "qmd"
        return 0
    fi

    _step "Installing qmd..."
    mkdir -p "$BUN_INSTALL/bin" "$BUN_INSTALL/install/global"

    # Install the real package — @tobilu/qmd (not the empty "qmd" shim)
    local qmd_out
    qmd_out=$(BUN_INSTALL="$HOME/.bun" bun install -g @tobilu/qmd 2>&1) || true
    _log "qmd install output: $qmd_out"
    hash -r 2>/dev/null || true

    if [[ -f "$HOME/.bun/bin/qmd" ]] && "$HOME/.bun/bin/qmd" --version &>/dev/null; then
        _ok "qmd"
    else
        _warn "qmd — install failed (vault search won't work until fixed: BUN_INSTALL=~/.bun bun install -g @tobilu/qmd)"
        _log "qmd not found after install attempt"
    fi
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
    local editor_cmd=""
    command -v code &>/dev/null && found="VS Code" && editor_cmd="code"
    [[ -d "/Applications/Cursor.app" ]] && found="Cursor" && editor_cmd="cursor"
    [[ -d "/Applications/Antigravity.app" ]] && found="Antigravity" && editor_cmd="antigravity"

    if [[ -n "$found" ]]; then
        _save_editor "$editor_cmd"
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
            if command -v code &>/dev/null; then
                _ok "VS Code"
                _save_editor "code"
            else
                _warn "VS Code install failed"
            fi
            ;;
        2)
            _info "Installing Cursor..."
            brew install --cask cursor 2>&1 | tail -3
            if [[ -d "/Applications/Cursor.app" ]]; then
                _ok "Cursor"
                _save_editor "cursor"
            else
                _warn "Cursor install failed"
            fi
            ;;
        3)
            _info "Opening Antigravity download page..."
            open "https://antigravity.app" 2>/dev/null || true
            echo ""
            echo "  ${MUTED}Install Antigravity, then press Enter to continue.${RESET}"
            read -r
            if [[ -d "/Applications/Antigravity.app" ]]; then
                _ok "Antigravity"
                _save_editor "antigravity"
            else
                _warn "Antigravity not found in /Applications — you can install it later"
            fi
            ;;
        4|*)
            _info "Skipping editor install"
            ;;
    esac
}

_save_editor() {
    # Persist editor choice so 'aos start' knows what to open
    local cmd="$1"
    mkdir -p "$USER_DIR/config"
    echo "$cmd" > "$USER_DIR/config/editor"
}

prereq_chrome() {
    # Google Chrome — required for browser automation via Claude-in-Chrome MCP
    if [[ -d "/Applications/Google Chrome.app" ]]; then
        _skip "Google Chrome"
    else
        _step "Installing Google Chrome..."
        brew install --cask google-chrome 2>&1 | tail -3
        [[ -d "/Applications/Google Chrome.app" ]] && _ok "Google Chrome" || _warn "Chrome install failed"
    fi

    # Ensure Chrome starts at login — MCP needs Chrome running
    local chrome_plist="$HOME/Library/LaunchAgents/com.agent.chrome.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    if [[ -f "$chrome_plist" ]]; then
        _skip "Chrome LaunchAgent"
    else
        cat > "$chrome_plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.chrome</string>
    <key>ProgramArguments</key>
    <array>
        <string>open</string>
        <string>-a</string>
        <string>Google Chrome</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST
        launchctl load "$chrome_plist" 2>/dev/null
        _ok "Chrome LaunchAgent (starts at login)"
    fi

    # Start Chrome now if not running
    if ! pgrep -x "Google Chrome" &>/dev/null; then
        open -a "Google Chrome" &>/dev/null
        _ok "Chrome started"
    fi

    # Chrome extension install deferred to onboarding agent
}

prereq_obsidian() {
    if [[ -d "/Applications/Obsidian.app" ]]; then
        _skip "Obsidian"
        return 0
    fi

    _step "Installing Obsidian..."
    brew install --cask obsidian 2>&1 | tail -3
    [[ -d "/Applications/Obsidian.app" ]] && _ok "Obsidian" || _warn "Obsidian install failed"
}

prereq_superwhisper() {
    # SuperWhisper — voice-to-text transcription (local Whisper model)
    if [[ -d "/Applications/superwhisper.app" ]]; then
        _skip "SuperWhisper"
    else
        _step "Installing SuperWhisper..."
        brew install --cask superwhisper 2>&1 | tail -3
        [[ -d "/Applications/superwhisper.app" ]] && _ok "SuperWhisper" || _warn "SuperWhisper install failed"
    fi

    # Configure defaults: default mode, always show mini recorder, minimized
    if [[ -d "/Applications/superwhisper.app" ]]; then
        defaults write com.superduper.superwhisper activeModeKey -string "default"
        defaults write com.superduper.superwhisper alwaysShowMiniRecorder -bool true
        defaults write com.superduper.superwhisper isMinimized -bool true
        _ok "SuperWhisper configured (default mode, mini recorder)"
    fi

    # Ensure SuperWhisper starts at login
    mkdir -p "$HOME/Library/LaunchAgents"
    local sw_plist="$HOME/Library/LaunchAgents/com.agent.superwhisper.plist"
    if [[ -f "$sw_plist" ]]; then
        _skip "SuperWhisper LaunchAgent"
    elif [[ -d "/Applications/superwhisper.app" ]]; then
        cat > "$sw_plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.superwhisper</string>
    <key>ProgramArguments</key>
    <array>
        <string>open</string>
        <string>-a</string>
        <string>superwhisper</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST
        launchctl load "$sw_plist" 2>/dev/null
        _ok "SuperWhisper LaunchAgent (starts at login)"
    fi

    # Start SuperWhisper now if not running
    if [[ -d "/Applications/superwhisper.app" ]] && ! pgrep -x "superwhisper" &>/dev/null; then
        open -a "superwhisper" &>/dev/null
        _ok "SuperWhisper started"
    fi
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

prereq_ffmpeg() {
    if command -v ffmpeg &>/dev/null; then
        _skip "ffmpeg"
        return 0
    fi

    _step "Installing ffmpeg..."
    brew install ffmpeg 2>&1 | tail -3
    command -v ffmpeg &>/dev/null || _die "ffmpeg install failed"
    _ok "ffmpeg"
}

prereq_mlx_whisper() {
    # mlx-whisper — Apple Silicon optimized local transcription
    # Only relevant on Apple Silicon. Installed into its own venv to avoid polluting system python.
    if [[ "$(uname -m)" != "arm64" ]]; then
        _skip "mlx-whisper (Intel — not applicable)"
        return 0
    fi

    local mlx_venv="$HOME/.aos/services/mlx-whisper/.venv"
    if [[ -f "$mlx_venv/bin/python" ]] && "$mlx_venv/bin/python" -c "import mlx_whisper" 2>/dev/null; then
        _skip "mlx-whisper"
        return 0
    fi

    _step "Installing mlx-whisper (Apple Silicon transcription)..."
    mkdir -p "$(dirname "$mlx_venv")"
    python3 -m venv "$mlx_venv" 2>/dev/null || uv venv "$mlx_venv" 2>/dev/null
    if [[ -f "$mlx_venv/bin/pip" ]]; then
        "$mlx_venv/bin/pip" install --quiet mlx-whisper 2>&1 | tail -3
        if "$mlx_venv/bin/python" -c "import mlx_whisper" 2>/dev/null; then
            _ok "mlx-whisper"
        else
            _warn "mlx-whisper — install failed (non-critical, voice transcription unavailable)"
        fi
    else
        _warn "mlx-whisper — could not create venv (non-critical)"
    fi
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

prereq_claude_auth() {
    # Claude Code handles its own auth on first launch — nothing to do here.
    # When cld runs at the end of install, Claude will prompt for sign-in if needed.
    return 0
}

prereq_ssh() {
    # SSH / Remote Login — check status only, don't try to enable
    # Enabling requires Full Disk Access on macOS 15+ and often fails in scripts.
    # Onboarding will walk the operator through enabling it manually if needed.
    local status
    status=$(sudo -n systemsetup -getremotelogin 2>/dev/null | grep -i "on" || echo "")

    if [[ -n "$status" ]]; then
        _skip "SSH (Remote Login)"
        return 0
    fi

    _info "SSH (Remote Login) is off — onboarding will help you enable it"
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
    prereq_uv
    prereq_pyyaml
    prereq_bun
    prereq_qmd
    prereq_jq
    prereq_ffmpeg
    prereq_mlx_whisper
    prereq_gh
    prereq_editor
    prereq_chrome
    prereq_superwhisper
    prereq_obsidian
    prereq_claude
    prereq_claude_auth

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
        # Get the operator's real name — try multiple sources
        local op_name=""
        # 1. macOS contact card (most reliable for real name)
        op_name=$(id -F 2>/dev/null || echo "")
        # 2. Fall back to git config
        if [[ -z "$op_name" ]] || [[ "$op_name" == "$(whoami)" ]]; then
            op_name=$(git config --global user.name 2>/dev/null || echo "")
        fi
        # 3. Ask if we still don't have it
        if [[ -z "$op_name" ]]; then
            echo ""
            printf "  ${BOLD}What's your name?${RESET} "
            read -r op_name
        fi
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
    if python3 "$AOS_DIR/core/migrations/runner.py" migrate 2>&1 | sed 's/^/    /'; then
        _ok "Migrations complete"
    else
        _warn "Some migrations failed — system may need manual fixes"
    fi

    # ── Ensure critical files exist (fallback if migrations missed them) ──

    # settings.json — Claude Code config with hooks
    local settings_file="$HOME/.claude/settings.json"
    if [[ ! -f "$settings_file" ]]; then
        _info "Creating Claude Code settings..."
        cat > "$settings_file" << 'SETTINGS'
{
  "agent": "chief",
  "permissions": {
    "allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)", "Glob(*)", "Grep(*)"],
    "deny": []
  },
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
    "CLAUDE_CODE_TEAMMATE_MODE": "in-process"
  },
  "hooks": {}
}
SETTINGS
        _ok "settings.json created"
    else
        # Patch existing settings.json — ensure agent=chief and teams env vars
        python3 -c "
import json
from pathlib import Path
p = Path.home() / '.claude' / 'settings.json'
s = json.loads(p.read_text())
changed = False
if not s.get('agent'):
    s['agent'] = 'chief'
    changed = True
if 'env' not in s:
    s['env'] = {}
if 'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS' not in s.get('env', {}):
    s['env']['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'] = '1'
    changed = True
if 'CLAUDE_CODE_TEAMMATE_MODE' not in s.get('env', {}):
    s['env']['CLAUDE_CODE_TEAMMATE_MODE'] = 'in-process'
    changed = True
if changed:
    p.write_text(json.dumps(s, indent=2) + '\n')
    print('patched')
else:
    print('ok')
" 2>/dev/null
    fi

    # Repair hooks — fix flat format ({"command": "..."}) to correct nested format ({"hooks": [{"type": "command", ...}]})
    python3 -c "
import json
from pathlib import Path

p = Path('$settings_file')
if not p.exists():
    exit()
s = json.loads(p.read_text())
hooks = s.get('hooks', {})
changed = False

for event in list(hooks.keys()):
    entries = hooks[event]
    if not isinstance(entries, list):
        continue
    fixed = []
    for entry in entries:
        if isinstance(entry, dict) and 'command' in entry and 'hooks' not in entry:
            # Flat format — wrap it
            hook_obj = {'type': 'command', 'command': entry['command']}
            if entry.get('statusMessage'):
                hook_obj['statusMessage'] = entry['statusMessage']
            if entry.get('async'):
                hook_obj['async'] = True
            fixed.append({'hooks': [hook_obj]})
            changed = True
        else:
            fixed.append(entry)
    hooks[event] = fixed

if changed:
    s['hooks'] = hooks
    p.write_text(json.dumps(s, indent=2) + '\n')
    print('repaired')
else:
    print('ok')
" 2>/dev/null

    # Wire hooks if missing — run migration 005 directly
    if ! python3 -c "
import json
with open('$settings_file') as f:
    s = json.load(f)
assert s.get('hooks', {}).get('SessionStart')
" 2>/dev/null; then
        _info "Wiring work system hooks..."
        python3 "$AOS_DIR/core/migrations/005_wire_hooks.py" 2>/dev/null && _ok "Hooks wired" || _warn "Hooks — wire manually later"
    fi

    # mcp.json — MCP server config (memory service)
    local mcp_file="$HOME/.claude/mcp.json"
    if [[ ! -f "$mcp_file" ]]; then
        _info "Creating MCP config..."
        cat > "$mcp_file" << MCPJSON
{
  "mcpServers": {}
}
MCPJSON
        _ok "mcp.json created"
    fi

    # projects dir — Claude Code per-project memory
    mkdir -p "$HOME/.claude/projects"

    echo ""

    # Sync agents (system agents: chief, steward, advisor)
    _step "Syncing agents..."
    echo ""
    bash "$AOS_DIR/core/bin/aos" sync-agents 2>&1 | sed 's/^/  /'

    # Activate onboard agent from catalog
    if [[ ! -f "$HOME/.claude/agents/onboard.md" ]]; then
        bash "$AOS_DIR/core/bin/activate-agent" onboard 2>&1 | sed 's/^/  /'
    else
        echo "  ✓ Onboard agent already active"
    fi

    # Initialize trust config if not present
    if [[ ! -f "$USER_DIR/config/trust.yaml" ]]; then
        _step "Initializing trust configuration..."
        cp "$AOS_DIR/config/defaults/trust.yaml" "$USER_DIR/config/trust.yaml" 2>/dev/null || {
            # No default template — create minimal trust config
            cat > "$USER_DIR/config/trust.yaml" << 'TRUST'
# Trust Configuration — Per-capability trust levels
# Levels: 0=SHADOW, 1=APPROVAL, 2=SEMI-AUTO, 3=FULL-AUTO
agents: {}
graduation:
  0_to_1:
    min_observations: 20
    accuracy_threshold: 0.80
    requires_human_approval: true
  1_to_2:
    min_weighted_score: 30
    max_revert_rate: 0.05
    requires_human_approval: true
  2_to_3:
    min_autonomous_actions: 50
    max_revert_rate: 0.02
    requires_human_approval: true
always_escalate:
  - financial_commitment
  - delete_production_data
  - external_communication_new_contact
promotions: []
TRUST
        }
        _ok "Trust configuration initialized"
    else
        _skip "Trust configuration"
    fi

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

            # Each service deploy is best-effort — don't kill the install
            if uv venv "$dst/.venv" --quiet 2>/dev/null && \
               uv pip install --quiet -p "$dst/.venv/bin/python" \
                 -r <(python3 -c "
import re, sys
toml_path = '$src_dir/pyproject.toml'
try:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with open(toml_path, 'rb') as f:
        deps = tomllib.load(f).get('project', {}).get('dependencies', [])
except Exception:
    deps, in_deps = [], False
    for line in open(toml_path):
        s = line.strip()
        if s == 'dependencies = [': in_deps = True; continue
        if in_deps and s == ']': break
        if in_deps:
            m = re.search(r'\"(.+?)\"', line)
            if m: deps.append(m.group(1))
print('\n'.join(deps))
"
               ) 2>&1 | tail -3 >> "$INSTALL_LOG"; then
                _ok "Service $name"
            else
                _warn "Service $name — deploy failed (run 'aos deploy $name' later)"
            fi
        fi
    done

    # NLTK removed — memory service doesn't use it

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

    # ── Always-On Configuration ────────────────────────────
    # Mac Mini should never sleep, auto-restart on power loss, and stay logged in
    _step "Configuring always-on settings..."

    if sudo -n true 2>/dev/null; then
        # Prevent sleep (display can sleep, but system stays awake)
        sudo pmset -a sleep 0 2>/dev/null && _ok "System sleep disabled" || _warn "Could not disable sleep"

        # Prevent disk sleep
        sudo pmset -a disksleep 0 2>/dev/null

        # Wake on network access (Wake on LAN)
        sudo pmset -a womp 1 2>/dev/null && _ok "Wake on LAN enabled" || true

        # Auto restart on power failure
        sudo pmset -a autorestart 1 2>/dev/null && _ok "Auto-restart on power loss" || _warn "Could not set auto-restart"

        # Start up automatically after power failure (hardware level)
        sudo nvram AutoBoot=%01 2>/dev/null || true

        # Disable screen saver lock (headless machine, no one to unlock)
        defaults write com.apple.screensaver askForPassword -int 0 2>/dev/null
        _ok "Screen lock disabled"

        # Disable auto-logout (System Settings > Security > Advanced)
        sudo defaults write /Library/Preferences/.GlobalPreferences com.apple.autologout.AutoLogOutDelay -int 0 2>/dev/null
        _ok "Auto-logout disabled"

        # Disable display sleep on AC power (keep it awake for screen sharing)
        sudo pmset -a displaysleep 0 2>/dev/null
        _ok "Display sleep disabled"
    else
        _info "Always-on settings require sudo — configure manually:"
        _info "  sudo pmset -a sleep 0 displaysleep 0 disksleep 0"
        _info "  sudo pmset -a autorestart 1 womp 1"
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

hook_defs = {
    'SessionStart': [
        {'hooks': [{'type': 'command', 'command': 'python3 ~/aos/core/work/inject_context.py', 'statusMessage': 'Loading work context...'}]},
    ],
    'PostCompact': [
        {'hooks': [{'type': 'command', 'command': 'python3 ~/aos/core/work/inject_context.py', 'statusMessage': 'Reloading work context...'}]},
    ],
    'Stop': [
        {'hooks': [{'type': 'command', 'command': 'python3 ~/aos/core/work/reconcile.py', 'async': True}]},
    ],
    'SessionEnd': [
        {'hooks': [
            {'type': 'command', 'command': 'python3 ~/aos/core/work/session_close.py', 'async': True},
            {'type': 'command', 'command': 'python3 ~/aos/core/bin/reconcile-sessions --hook --quiet', 'async': True},
        ]},
    ],
}

for event, hook_entries in hook_defs.items():
    if event not in s['hooks'] or not s['hooks'][event]:
        s['hooks'][event] = hook_entries
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
    # ── Scorecard: structured health verification ──────────────
    # Every check is categorized. The final scorecard shows pass/warn/fail counts
    # and tells you exactly what needs attention.

    local pass=0 warn=0 fail=0
    local warnings=() failures=()

    _check() {
        # Usage: _check "label" "test command" [critical]
        # critical = "critical" means failure blocks onboarding
        local label="$1" cmd="$2" severity="${3:-warn}"
        if eval "$cmd" 2>/dev/null; then
            _ok "$label"
            ((pass++))
        elif [[ "$severity" == "critical" ]]; then
            _fail "$label"
            ((fail++))
            failures+=("$label")
        else
            _warn "$label"
            ((warn++))
            warnings+=("$label")
        fi
    }

    # ── Core data ──────────────────────────────────────────────
    _step "Core data"
    _check "User data dir"      "[[ -d '$USER_DIR' ]]"                 critical
    _check "Machine ID"         "[[ -f '$USER_DIR/.machine-id' ]]"     critical
    _check "Migrations applied" "[[ -f '$USER_DIR/.version' ]]"        critical
    _check "Event bus"          "[[ -f '$USER_DIR/events.jsonl' ]]"    critical
    _check "Work system"        "[[ -f '$USER_DIR/work/work.yaml' ]]"  critical

    # ── Context files ──────────────────────────────────────────
    _step "Context files"
    _check "Root CLAUDE.md"     "[[ -f '$HOME/CLAUDE.md' ]]"           critical
    _check "Global CLAUDE.md"   "[[ -f '$HOME/.claude/CLAUDE.md' ]]"   critical
    _check "Operator profile"   "[[ -f '$USER_DIR/config/operator.yaml' ]]"
    _check "Knowledge vault"    "[[ -d '$HOME/vault' ]]"               critical
    _check "Projects directory" "[[ -d '$HOME/project' ]]"

    # ── Git config ─────────────────────────────────────────────
    _step "Git config"
    _check "Git name"           "[[ -n \"\$(git config --global user.name 2>/dev/null)\" ]]"
    _check "Git email"          "[[ -n \"\$(git config --global user.email 2>/dev/null)\" ]]"

    # ── Settings.json ──────────────────────────────────────────
    _step "Claude Code settings"
    _check "settings.json exists" "[[ -f '$HOME/.claude/settings.json' ]]" critical
    if [[ -f "$HOME/.claude/settings.json" ]]; then
        _check "Agent = Chief" "python3 -c \"
import json
with open('$HOME/.claude/settings.json') as f:
    s = json.load(f)
assert s.get('agent') == 'chief'
\"" critical
        _check "Agent teams enabled" "python3 -c \"
import json
with open('$HOME/.claude/settings.json') as f:
    s = json.load(f)
assert 'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS' in s.get('env', {})
\""
        for hook_name in SessionStart PostCompact Stop SessionEnd; do
            _check "Hook: $hook_name" "python3 -c \"
import json
with open('$HOME/.claude/settings.json') as f:
    s = json.load(f)
assert s.get('hooks', {}).get('$hook_name')
\"" critical
        done
    fi

    # ── Agents ─────────────────────────────────────────────────
    _step "Agents"
    for agent in chief steward advisor; do
        _check "Agent $agent" "[[ -f '$HOME/.claude/agents/${agent}.md' ]]" critical
    done
    _check "Onboard agent" "[[ -f '$HOME/.claude/agents/onboard.md' ]]"

    # ── Skills ─────────────────────────────────────────────────
    _step "Skills"
    local default_skills="recall work review step-by-step obsidian-cli extract telegram-admin bridge-ops marketing diagram session-analysis frontend-design architect skill-creator skill-scanner"
    local skill_count=0 skill_missing=0
    for skill_name in $default_skills; do
        if [[ -L "$HOME/.claude/skills/$skill_name" ]]; then
            ((skill_count++))
        else
            ((skill_missing++))
        fi
    done
    if [[ "$skill_missing" -eq 0 ]]; then
        _ok "All $skill_count default skills linked"
        ((pass++))
    else
        _fail "$skill_missing of $((skill_count + skill_missing)) skills missing"
        ((fail++))
        failures+=("$skill_missing skills missing")
    fi

    if [[ -f "$USER_DIR/config/developer-mode" ]]; then
        local dev_count=0
        for skill_name in systematic-debugging verification-before-completion requesting-code-review receiving-code-review executing-plans writing-plans dispatching-parallel-agents writing-skills autonomous-execution; do
            [[ -L "$HOME/.claude/skills/$skill_name" ]] && ((dev_count++))
        done
        _ok "$dev_count developer skills"
        ((pass++))
    fi

    # ── Services ───────────────────────────────────────────────
    _step "Services"
    for svc in bridge dashboard listen memory; do
        _check "Service $svc venv" "[[ -f '$USER_DIR/services/$svc/.venv/bin/python' ]]" critical
    done

    # NLTK removed — memory service doesn't use it

    # ── LaunchAgents ───────────────────────────────────────────
    _step "LaunchAgents"
    for la in com.aos.scheduler com.aos.bridge com.aos.dashboard com.aos.listen; do
        _check "LaunchAgent $la" "launchctl list 2>/dev/null | grep -q '$la'"
    done

    # ── LaunchAgent path validation ─────────────────────────────
    # Detect when launchd has cached stale paths that don't match the plist on disk
    local la_drift=0
    for la in com.aos.scheduler com.aos.bridge com.aos.dashboard com.aos.listen; do
        local plist_file="$HOME/Library/LaunchAgents/${la}.plist"
        if [[ -f "$plist_file" ]]; then
            # Get the path launchd is actually using
            local loaded_args
            loaded_args=$(launchctl print "gui/$(id -u)/$la" 2>/dev/null | grep -A2 "arguments" | tail -1 | xargs 2>/dev/null || true)
            if [[ -n "$loaded_args" ]] && [[ ! -f "$loaded_args" ]]; then
                _warn "LaunchAgent $la has stale path: $loaded_args"
                ((la_drift++))
                ((warn++))
                warnings+=("$la has stale cached path — run: launchctl bootout gui/\$(id -u)/$la && launchctl bootstrap gui/\$(id -u) $plist_file")
            fi
        fi
    done
    if [[ "$la_drift" -eq 0 ]]; then
        _ok "LaunchAgent paths match plist files"
        ((pass++))
    fi

    # ── Cron scripts ───────────────────────────────────────────
    _step "Scheduled jobs"
    _check "crons.yaml" "[[ -f '$AOS_DIR/config/crons.yaml' ]]"

    # Validate every enabled cron job references a script that exists
    local cron_errors=0
    if [[ -f "$AOS_DIR/config/crons.yaml" ]]; then
        while IFS= read -r cmd_line; do
            [[ -z "$cmd_line" ]] && continue
            # Find the first path-like argument (contains /)
            local script_path=""
            for word in $cmd_line; do
                if [[ "$word" == */* ]]; then
                    script_path="$word"
                    break
                fi
            done
            # Expand ~ to $HOME
            script_path="${script_path/#\~/$HOME}"
            if [[ -n "$script_path" ]] && [[ ! -f "$script_path" ]]; then
                ((cron_errors++))
                _log "CRON MISSING: $script_path (from: $cmd_line)"
            fi
        done < <(python3 -c "
import yaml
with open('$AOS_DIR/config/crons.yaml') as f:
    data = yaml.safe_load(f)
for name, job in (data.get('jobs') or {}).items():
    if job.get('enabled', True) is not False:
        print(job.get('command', ''))
" 2>/dev/null)
    fi
    if [[ "$cron_errors" -eq 0 ]]; then
        _ok "All cron scripts exist"
        ((pass++))
    else
        _warn "$cron_errors cron script(s) missing — check install log"
        ((warn++))
        warnings+=("$cron_errors cron scripts missing")
    fi

    # Make all bin scripts executable
    chmod +x "$AOS_DIR/core/bin/"* 2>/dev/null
    _ok "Bin scripts executable"
    ((pass++))

    # ── Tools ──────────────────────────────────────────────────
    _step "Tools & dependencies"
    _check "Homebrew"       "command -v brew"           critical
    _check "Python 3.11+"   "python3 -c 'import sys; assert sys.version_info >= (3, 11)'" critical
    _check "uv"             "command -v uv"             critical
    _check "bun"            "command -v bun"
    _check "jq"             "command -v jq"
    _check "ffmpeg"         "command -v ffmpeg"
    _check "gh"             "command -v gh"
    _check "QMD"            "[[ -f '$HOME/.bun/bin/qmd' ]] || command -v qmd"
    _check "Claude Code"    "command -v claude"         critical
    if [[ "$(uname -m)" == "arm64" ]]; then
        _check "mlx-whisper"    "[[ -f '$USER_DIR/services/mlx-whisper/.venv/bin/python' ]] && '$USER_DIR/services/mlx-whisper/.venv/bin/python' -c 'import mlx_whisper'"
    fi

    # ── Apps ───────────────────────────────────────────────────
    _step "Applications"
    _check "Google Chrome"  "[[ -d '/Applications/Google Chrome.app' ]]"
    _check "SuperWhisper"   "[[ -d '/Applications/superwhisper.app' ]]"
    _check "Obsidian"       "[[ -d '/Applications/Obsidian.app' ]]"

    # ── Remote access ──────────────────────────────────────────
    _step "Remote access"
    _check "SSH"            "sudo -n systemsetup -getremotelogin 2>/dev/null | grep -qi on"
    _check "Tailscale"      "command -v tailscale"
    _check "Claude Remote"  "launchctl list 2>/dev/null | grep -q claude-remote"

    # ── Scorecard ──────────────────────────────────────────────
    echo ""
    echo "  ${MUTED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
    echo "  ${BOLD}Scorecard${RESET}"
    echo ""
    printf "    ${GREEN}%-6s${RESET} %d passed\n" "PASS" "$pass"
    if [[ "$warn" -gt 0 ]]; then
        printf "    ${YELLOW}%-6s${RESET} %d warnings\n" "WARN" "$warn"
        for w in "${warnings[@]}"; do
            echo "    ${MUTED}  - $w${RESET}"
        done
    fi
    if [[ "$fail" -gt 0 ]]; then
        printf "    ${RED}%-6s${RESET} %d failures\n" "FAIL" "$fail"
        for f in "${failures[@]}"; do
            echo "    ${MUTED}  - $f${RESET}"
        done
    fi
    echo ""

    # Save scorecard to install log
    _log "SCORECARD: pass=$pass warn=$warn fail=$fail"

    # Write structured report for Chief to read during onboarding
    local report_file="$HOME/.aos/config/install-report.yaml"
    {
        echo "install_date: '$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
        echo "aos_version: '$AOS_VERSION'"
        echo "macos: '$(sw_vers -productVersion 2>/dev/null || echo unknown)'"
        echo "arch: '$(uname -m)'"
        echo "pass: $pass"
        echo "warn: $warn"
        echo "fail: $fail"
        if [[ ${#warnings[@]} -gt 0 ]]; then
            echo "warnings:"
            for w in "${warnings[@]}"; do
                echo "  - \"$w\""
            done
        fi
        if [[ ${#failures[@]} -gt 0 ]]; then
            echo "failures:"
            for f in "${failures[@]}"; do
                echo "  - \"$f\""
            done
        fi
    } > "$report_file"

    if [[ "$fail" -gt 0 ]]; then
        _fail "$fail critical failure(s) — fix with: bash ~/aos/install.sh"
        echo ""
    elif [[ "$warn" -gt 0 ]]; then
        _ok "System operational ($warn non-critical warning(s))"
        echo ""
        return 0
    else
        _ok "All checks passed — system fully operational"
        echo ""
        return 0
    fi
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

    if command -v cld &>/dev/null || command -v claude &>/dev/null; then
        echo "  ${BOLD}Get started:${RESET}"
        echo ""
        echo "    ${BRAND}${BOLD}\$ aos start${RESET}"
        echo ""
        echo "  ${MUTED}Opens your editor with Claude Code ready.${RESET}"
        echo "  ${MUTED}Sahib will walk you through the rest.${RESET}"
    else
        echo "  ${BOLD}Next:${RESET} Install Claude Code, then run ${BRAND}aos start${RESET}"
        echo "  ${MUTED}https://docs.anthropic.com/en/docs/claude-code${RESET}"
    fi

    echo ""
    echo "  ${MUTED}────────────────────────────────────────────────────${RESET}"
    echo ""
    echo "  ${MUTED}aos status        check migration status${RESET}"
    echo "  ${MUTED}aos self-test     verify system health${RESET}"
    echo "  ${MUTED}aos update        pull latest + migrate${RESET}"
    echo ""
    echo "  ${MUTED}Installer options:${RESET}"
    echo "  ${MUTED}  --dry-run       preview what would be installed${RESET}"
    echo "  ${MUTED}  --clean         ignore checkpoints, full re-install${RESET}"
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

    # Dry-run mode — show what would happen
    if [[ "$DRY_RUN" == true ]]; then
        echo "  ${YELLOW}DRY RUN${RESET} — showing what would be installed"
        echo ""
        echo "  ${BOLD}Phase 1:${RESET} Prerequisites (Homebrew, Python, uv, bun, jq, ffmpeg, gh, mlx-whisper)"
        echo "  ${BOLD}Phase 2:${RESET} Repository clone/update, PATH setup, git config"
        echo "  ${BOLD}Phase 3:${RESET} User data bootstrap, migrations, agents, skills"
        echo "  ${BOLD}Phase 4:${RESET} Service venvs (bridge, dashboard, listen, memory)"
        echo "  ${BOLD}Phase 5:${RESET} macOS provisioning (dock, desktop, terminal, preferences)"
        echo "  ${BOLD}Phase 6:${RESET} Apps (Chrome, SuperWhisper, Obsidian, Claude Code)"
        echo "  ${BOLD}Phase 7:${RESET} Health scorecard — verify everything works"
        echo ""
        if [[ -f "$CHECKPOINT_FILE" ]]; then
            local completed
            completed=$(wc -l < "$CHECKPOINT_FILE" | tr -d ' ')
            echo "  ${MUTED}Resume point: $completed phase(s) already completed${RESET}"
            echo "  ${MUTED}Completed: $(tr '\n' ', ' < "$CHECKPOINT_FILE" | sed 's/,$//')${RESET}"
        fi
        echo ""
        echo "  ${MUTED}Run without --dry-run to install.${RESET}"
        echo ""
        exit 0
    fi

    # Network check — fail fast
    _check_network

    # Keep sudo alive — install can take 20+ minutes, ticket expires in 5
    ( while true; do sudo -n true 2>/dev/null; sleep 50; done ) &
    SUDO_KEEPALIVE_PID=$!
    trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null" EXIT

    # Part 1: Prerequisites
    if _checkpoint_skip "prereqs"; then
        _phase "Prerequisites"
        _skip "Already completed (resuming)"
    else
        _phase "Prerequisites"
        run_prereqs
        _checkpoint_done "prereqs"
    fi

    # Part 2: Repo & PATH
    if _checkpoint_skip "repo"; then
        _phase "Repository & PATH"
        _skip "Already completed (resuming)"
    else
        _phase "Repository & PATH"
        setup_repo
        setup_path
        setup_git_config
        _checkpoint_done "repo"
    fi

    # Part 3: Bootstrap (migrations)
    if _checkpoint_skip "bootstrap"; then
        _phase "Bootstrap"
        _skip "Already completed (resuming)"
    else
        _phase "Bootstrap"
        run_bootstrap
        _checkpoint_done "bootstrap"
    fi

    # Part 4: Services
    if _checkpoint_skip "services"; then
        _phase "Services"
        _skip "Already completed (resuming)"
    else
        _phase "Services"
        deploy_services
        _checkpoint_done "services"
    fi

    # Part 5: macOS provisioning
    if _checkpoint_skip "provisioning"; then
        _phase "System configuration"
        _skip "Already completed (resuming)"
    else
        _phase "System configuration"
        run_provisioning
        _checkpoint_done "provisioning"
    fi

    # Part 6: Discovery
    _phase "Discovery"
    run_discovery

    # Part 7: Health scorecard + Handoff
    _phase "Health scorecard"
    run_health_gate
    print_handoff

    # Clean checkpoint on success — next run starts fresh
    rm -f "$CHECKPOINT_FILE" 2>/dev/null
    _log "Install complete"

    # Launch aos start — drops the operator into onboarding with Chief
    if command -v claude &>/dev/null; then
        echo ""
        echo "  ${BOLD}Launching AOS...${RESET}"
        echo ""
        sleep 1
        exec aos start
    fi
}

main "$@"
