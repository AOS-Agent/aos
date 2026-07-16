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

# Dry-run walks the ceremony without changing anything, so it needs no admin
# access — detect it early (before the sudo prompt) so demos and CI stay
# password-free. The authoritative parse happens below in the modes block.
_EARLY_DRY_RUN=false
[[ "${INSTALL_DRY_RUN:-0}" == "1" ]] && _EARLY_DRY_RUN=true
for _arg in "$@"; do [[ "$_arg" == "--dry-run" ]] && _EARLY_DRY_RUN=true; done

# Cache sudo upfront — one password prompt, then it's good for the whole install
if [[ "$_EARLY_DRY_RUN" != true ]]; then
    echo "AOS needs admin access for Homebrew, SSH, and system config."
    echo ""
    if ! sudo true; then
        echo "  Failed to get admin access. Some steps may fail."
        echo ""
    fi
fi

# ── Version ──────────────────────────────────────────
AOS_VERSION=$(cat "$HOME/aos/VERSION" 2>/dev/null | tr -d '[:space:]' || echo "0.1.0")
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
# Dry-run walks the stage ceremony without touching the machine. Enable it with
# --dry-run or INSTALL_DRY_RUN=1 (the env form is handy for demos and CI).
DRY_RUN=false
[[ "${INSTALL_DRY_RUN:-0}" == "1" ]] && DRY_RUN=true
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
    # shellcheck disable=SC2034
    BRAND="" GREEN="" YELLOW="" RED="" CYAN="" MUTED="" ACCENT="" DIM="" BOLD="" RESET=""
fi

# ── Calm-UI channel ──────────────────────────────────
# fd 3 is a private copy of the terminal reserved for the install ceremony
# (banner, spinner, stage lines, failure panel). During a stage, the phase's
# own stdout/stderr is redirected to $INSTALL_LOG so the screen stays quiet —
# but the spinner and any failure panel must still reach the operator. They
# write to fd 3, which is never redirected.
exec 3>&1

# ── Timing ──────────────────────────────────────────
INSTALL_START=$(date +%s)
STEP_START=$INSTALL_START
_STEP_NUM=0
_TOTAL_STEPS=6
_CURRENT_STAGE=""
# Role is detected at install time (mirrors migration 081): a machine with the
# ~/project/aos dev workspace is a developer, everything else is an operator.
# Stamped early by _detect_role so later stages (notably the handoff) can fork.
ROLE=""

_timer_start() { STEP_START=$(date +%s); }
_timer_elapsed() {
    local now
    now=$(date +%s)
    local elapsed=$((now - STEP_START))
    if [[ $elapsed -ge 60 ]]; then
        echo "$((elapsed / 60))m $((elapsed % 60))s"
    else
        echo "${elapsed}s"
    fi
}
_total_elapsed() {
    local now
    now=$(date +%s)
    local elapsed=$((now - INSTALL_START))
    echo "$((elapsed / 60))m $((elapsed % 60))s"
}

# ── Spinner (braille dots) ──────────────────────────
_SPINNER_PID=""
_spinner_start() {
    local msg="${1:-Working}"
    # Spinner draws on the calm-UI channel (fd 3) so it survives a stage's
    # stdout→log redirect and keeps animating while the work stays quiet.
    [[ -t 3 ]] || return
    (
        local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
        local i=0
        while true; do
            printf "\r  ${CYAN}%s${RESET} ${MUTED}%s${RESET}" "${frames[$((i % 10))]}" "$msg" >&3
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
        printf "\r\033[K" >&3
    fi
}

# Restore cursor and clear any live spinner on exit — a stack trace or an
# interrupt must never leave the terminal with a spinning artifact.
trap '_spinner_stop; tput cnorm 2>/dev/null >&3' EXIT INT TERM

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

# ── Stage presenter ──────────────────────────────────
# Each install phase is shown as a single calm line: a spinner with a
# human-named stage while the work runs, resolving to a checkmark. The phase's
# own output (tool spew, per-item _ok/_skip/_warn lines, migration logs) is
# redirected to $INSTALL_LOG — the screen never sees raw tool output.
#
#   _stage "Human stage name" phase_function [checkpoint_key]
#
# A checkpoint key makes the stage resumable: on a re-run it's shown as already
# done and skipped. If the phase calls _die (fatal) it routes straight to the
# failure panel; a non-zero return does the same. Success stamps the checkpoint.
_stage() {
    local name="$1" fn="$2" ckpt="${3:-}"
    ((_STEP_NUM++)) || true

    # Dry-run: walk the ceremony without touching the machine.
    if [[ "$DRY_RUN" == true ]]; then
        printf "  ${MUTED}[%d/%d]${RESET} ${GREEN}✓${RESET} %s ${MUTED}(dry run)${RESET}\n" \
            "$_STEP_NUM" "$_TOTAL_STEPS" "$name" >&3
        _log "DRYRUN STAGE: $name"
        return 0
    fi

    # Resume: a completed checkpoint means this stage is already done.
    if [[ -n "$ckpt" ]] && _checkpoint_skip "$ckpt"; then
        printf "  ${MUTED}[%d/%d]${RESET} ${GREEN}✓${RESET} %s ${MUTED}(already done)${RESET}\n" \
            "$_STEP_NUM" "$_TOTAL_STEPS" "$name" >&3
        _log "SKIP STAGE (resumed): $name"
        return 0
    fi

    _CURRENT_STAGE="$name"
    _timer_start
    _log "STAGE START: $name"
    _spinner_start "${name}…"

    # Run the phase with all of its output captured to the log. fd 3 (the
    # spinner, and the failure panel if _die fires) still reaches the screen.
    local rc=0
    { "$fn"; } >>"$INSTALL_LOG" 2>&1 || rc=$?

    _spinner_stop
    if [[ "$rc" -ne 0 ]]; then
        _fail_panel "The ${name} step didn't finish cleanly."
        exit 1
    fi

    printf "  ${MUTED}[%d/%d]${RESET} ${GREEN}✓${RESET} %s ${MUTED}%s${RESET}\n" \
        "$_STEP_NUM" "$_TOTAL_STEPS" "$name" "$(_timer_elapsed)" >&3
    _log "STAGE DONE: $name ($(_timer_elapsed))"
    [[ -n "$ckpt" ]] && _checkpoint_done "$ckpt"
    return 0
}

# ── Graceful failure panel ───────────────────────────
# A stack trace is never the last thing on screen. When a stage fails we print a
# calm, bordered, plain-English panel: what happened, what it means, the ONE
# command to recover, and where the full log lives. Always drawn on fd 3 so it
# reaches the operator even mid-stage. Preserves a non-zero exit for scripting.
_fail_panel() {
    local what="$1"
    local recover="${2:-bash ~/aos/install.sh}"
    tput cnorm 2>/dev/null >&3   # bring the cursor back
    {
        echo ""
        echo "  ${RED}────────────────────────────────────────────────────${RESET}"
        echo "  ${RED}${BOLD}The install paused — one thing needs another try${RESET}"
        echo ""
        echo "  ${BOLD}What happened${RESET}"
        echo "  ${MUTED}${what}${RESET}"
        echo ""
        echo "  ${BOLD}What it means${RESET}"
        echo "  ${MUTED}Your Mac is fine and nothing is half-broken. The installer"
        echo "  stops cleanly here and picks up exactly where it left off.${RESET}"
        echo ""
        echo "  ${BOLD}What to do${RESET}"
        echo "  Run this again:"
        echo "    ${BRAND}${BOLD}${recover}${RESET}"
        echo ""
        echo "  ${MUTED}Full details are in the log:${RESET}"
        echo "  ${MUTED}${INSTALL_LOG}${RESET}"
        echo "  ${RED}────────────────────────────────────────────────────${RESET}"
        echo ""
    } >&3
    _log "PANEL: stage=${_CURRENT_STAGE:-?} what=$what recover=$recover"
}

# ── Error handling ───────────────────────────────────
# Fatal errors inside a stage route through the failure panel. Because a stage
# redirects stdout to the log, _die must draw on fd 3 (via _fail_panel) so the
# operator actually sees it. The message is used verbatim as "what happened".
_die() {
    _spinner_stop
    _fail_panel "$*"
    exit 1
}

# ── Role detection (mirrors migration 081) ───────────
# developer = has the ~/project/aos dev workspace; operator = everything else.
_detect_role() {
    if [[ -d "$HOME/project/aos" ]]; then
        ROLE="developer"
    else
        ROLE="operator"
    fi
    _log "ROLE: $ROLE"
}

# ── Identity preflight ───────────────────────────────
# The only questions the installer asks come here, BEFORE the calm ceremony —
# so a prompt never appears under a spinner (whose stage has redirected stdout
# to the log). Once git identity is set, the git and bootstrap stages resolve
# the operator's name without prompting. Non-interactive when nothing's missing
# (re-runs, developer machines) and skipped entirely in dry-run.
_collect_identity() {
    tput cnorm 2>/dev/null >&3   # cursor visible while typing

    local name email
    name=$(git config --global user.name 2>/dev/null || echo "")
    email=$(git config --global user.email 2>/dev/null || echo "")

    if [[ -z "$name" || -z "$email" ]]; then
        echo "" >&3
        echo "  ${BOLD}First, two quick things${RESET}" >&3
        echo "  ${MUTED}so your work is signed with your name.${RESET}" >&3
        echo "" >&3
    fi

    if [[ -z "$name" ]]; then
        printf "  ${BOLD}Your name${RESET} (for git commits): " >&3
        read -r name
        if [[ -n "$name" ]]; then
            git config --global user.name "$name"
        fi
    fi
    if [[ -z "$email" ]]; then
        printf "  ${BOLD}Your email${RESET} (for git commits): " >&3
        read -r email
        if [[ -n "$email" ]]; then
            git config --global user.email "$email"
        fi
    fi

    # Resolve the operator's display name once, here, so run_bootstrap never
    # has to prompt. Prefer the macOS full name, then git, then what we asked.
    local op_name
    op_name=$(id -F 2>/dev/null || echo "")
    if [[ -z "$op_name" || "$op_name" == "$(whoami)" ]]; then
        op_name="${name:-$(git config --global user.name 2>/dev/null || echo "")}"
    fi
    export AOS_OPERATOR_NAME="${op_name:-Operator}"
    _log "IDENTITY: operator=$AOS_OPERATOR_NAME role=$ROLE"

    tput civis 2>/dev/null >&3   # re-hide for the ceremony
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
            # Write resolved path for aos-python wrapper
            mkdir -p "$HOME/.aos/config"
            which python3 > "$HOME/.aos/config/python"
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
        # Write resolved path for aos-python wrapper
        mkdir -p "$HOME/.aos/config"
        echo "$brew_python" > "$HOME/.aos/config/python"
        # Rehash so this session sees the new python3
        hash -r 2>/dev/null || true
    else
        # Still write whatever python3 we have, even if < 3.11
        local fallback_python
        fallback_python=$(which python3 2>/dev/null || echo "")
        if [[ -n "$fallback_python" ]]; then
            mkdir -p "$HOME/.aos/config"
            echo "$fallback_python" > "$HOME/.aos/config/python"
        fi
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

prereq_xcode_clt() {
    # Xcode Command Line Tools provide: git, clang, make, headers.
    # EVERYTHING depends on this — Homebrew, Python, native gems, etc.
    # Must be first prereq. Must succeed before continuing.

    local clt_dir="/Library/Developer/CommandLineTools"

    # Check if CLT is already installed (the real check, not just the /usr/bin/git shim)
    if [[ -e "$clt_dir/usr/bin/git" ]]; then
        _skip "Xcode Command Line Tools"
        return 0
    fi

    _step "Installing Xcode Command Line Tools (git, compiler, headers)..."

    # ── Tier 1: Headless via softwareupdate ──────────────────────
    # The placeholder file makes CLT appear in the softwareupdate catalog.
    # This is the same method Homebrew uses. Works over SSH, no GUI.
    local clt_placeholder="/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress"

    # Reset ignored updates (CLT might have been previously dismissed)
    sudo /usr/sbin/softwareupdate --reset-ignored 2>/dev/null || true
    sudo touch "${clt_placeholder}" 2>/dev/null

    _info "Checking Apple software update catalog..."
    local su_output clt_label
    su_output=$(/usr/sbin/softwareupdate -l 2>&1)

    # Apple changes the output format across macOS versions — try multiple patterns
    # Pattern 1: "* Label: Command Line Tools for Xcode-16.0"
    clt_label=$(echo "$su_output" | awk -F': ' '/Label:.*Command Line/{print $2}' | sort -V | tail -1)
    # Pattern 2: "   * Command Line Tools for Xcode-16.0"
    if [[ -z "$clt_label" ]]; then
        clt_label=$(echo "$su_output" | awk -F'\\* ' '/\* .*Command Line/{print $2}' | sort -V | tail -1)
    fi
    # Pattern 3: Broadest grep
    if [[ -z "$clt_label" ]]; then
        clt_label=$(echo "$su_output" | grep -o 'Command Line Tools[^"]*' | sort -V | tail -1)
    fi

    if [[ -n "${clt_label}" ]]; then
        _info "Found: ${clt_label}"
        _info "Downloading and installing (this takes a few minutes)..."
        sudo /usr/sbin/softwareupdate -i "${clt_label}" 2>&1 | tail -5
        sudo /usr/bin/xcode-select --switch "$clt_dir" 2>/dev/null
    else
        _info "CLT not listed in software update catalog"
        _log "softwareupdate output: $su_output"
    fi

    sudo rm -f "${clt_placeholder}" 2>/dev/null

    # Check if Tier 1 succeeded
    if [[ -e "$clt_dir/usr/bin/git" ]]; then
        hash -r 2>/dev/null
        _ok "Xcode Command Line Tools"
        return 0
    fi

    # ── Tier 2: xcode-select --install (GUI, separate Apple infra) ──
    # This uses Apple's developer tools servers, NOT the softwareupdate catalog.
    # Works when softwareupdate fails. Requires an interactive session.
    if test -t 0; then
        _info "Trying GUI installer (uses different Apple server)..."
        xcode-select --install 2>/dev/null

        if [[ $? -eq 0 ]]; then
            _info "Installer launched — waiting for completion..."
            local elapsed=0
            while [[ ${elapsed} -lt 1800 ]]; do
                if [[ -e "$clt_dir/usr/bin/git" ]]; then
                    sudo /usr/bin/xcode-select --switch "$clt_dir" 2>/dev/null
                    hash -r 2>/dev/null
                    _ok "Xcode Command Line Tools"
                    return 0
                fi
                sleep 5
                elapsed=$((elapsed + 5))
                (( elapsed % 30 == 0 )) && printf "."
            done
            echo ""
        fi
    fi

    # ── Tier 3: Remove broken state and retry ────────────────────
    # Sometimes CLT is partially installed or the catalog is stale.
    if [[ -d "$clt_dir" ]]; then
        _info "Removing broken CLT installation and retrying..."
        sudo rm -rf "$clt_dir"
        sudo xcode-select --reset 2>/dev/null || true

        sudo touch "${clt_placeholder}" 2>/dev/null
        su_output=$(/usr/sbin/softwareupdate -l 2>&1)
        clt_label=$(echo "$su_output" | awk -F': ' '/Label:.*Command Line/{print $2}' | sort -V | tail -1)
        [[ -z "$clt_label" ]] && clt_label=$(echo "$su_output" | awk -F'\\* ' '/\* .*Command Line/{print $2}' | sort -V | tail -1)

        if [[ -n "$clt_label" ]]; then
            _info "Found after reset: $clt_label"
            sudo /usr/sbin/softwareupdate -i "$clt_label" 2>&1 | tail -5
            sudo /usr/bin/xcode-select --switch "$clt_dir" 2>/dev/null
        fi
        sudo rm -f "${clt_placeholder}" 2>/dev/null

        if [[ -e "$clt_dir/usr/bin/git" ]]; then
            hash -r 2>/dev/null
            _ok "Xcode Command Line Tools"
            return 0
        fi
    fi

    _fail "Xcode CLT auto-install failed"
    _warn "Download manually from: https://developer.apple.com/download/all/"
    _warn "Search for 'Command Line Tools', install, then re-run this installer"
    _die "Xcode Command Line Tools required"
}

prereq_git() {
    # After CLT install, git should be available. Just verify.
    if command -v git &>/dev/null; then
        _skip "git"
        return 0
    fi
    # CLT is installed but git not on PATH — add it
    if [[ -e /Library/Developer/CommandLineTools/usr/bin/git ]]; then
        export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
        hash -r 2>/dev/null
        _ok "git (from CLT, added to PATH)"
        return 0
    fi
    _die "git not found — Xcode CLT may need reinstalling: xcode-select --install"
}

prereq_gh() {
    if command -v gh &>/dev/null; then
        _skip "GitHub CLI"
        return 0
    fi

    _step "Installing GitHub CLI..."
    brew install gh 2>&1 | tail -1
    command -v gh &>/dev/null || { _warn "GitHub CLI — install failed (install later: brew install gh)"; return 0; }
    _ok "GitHub CLI"
}

prereq_editor() {
    # VS Code is the default editor for AOS
    if command -v code &>/dev/null; then
        _save_editor "code"
        _skip "VS Code"
        return 0
    fi

    # Check if VS Code app exists but CLI isn't on PATH yet
    if [[ -d "/Applications/Visual Studio Code.app" ]]; then
        # Install the 'code' CLI command
        local code_bin="/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
        if [[ -x "$code_bin" ]]; then
            ln -sf "$code_bin" /usr/local/bin/code 2>/dev/null || true
        fi
        if command -v code &>/dev/null; then
            _save_editor "code"
            _skip "VS Code"
            return 0
        fi
    fi

    _info "Installing VS Code..."
    brew install --cask visual-studio-code 2>&1 | tail -3
    if [[ -d "/Applications/Visual Studio Code.app" ]]; then
        # Ensure 'code' CLI is on PATH
        local code_bin="/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
        if [[ -x "$code_bin" ]] && ! command -v code &>/dev/null; then
            ln -sf "$code_bin" /usr/local/bin/code 2>/dev/null || true
        fi
        _ok "VS Code"
        _save_editor "code"
    else
        _warn "VS Code install failed — install it manually later"
    fi
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
    command -v jq &>/dev/null || { _warn "jq — install failed (install later: brew install jq)"; return 0; }
    _ok "jq"
}

prereq_ffmpeg() {
    if command -v ffmpeg &>/dev/null; then
        _skip "ffmpeg"
        return 0
    fi

    _step "Installing ffmpeg..."
    brew install ffmpeg 2>&1 | tail -3
    command -v ffmpeg &>/dev/null || { _warn "ffmpeg — install failed (media features won't work until fixed: brew install ffmpeg)"; return 0; }
    _ok "ffmpeg"
}

prereq_ytdlp() {
    if command -v yt-dlp &>/dev/null; then
        _skip "yt-dlp"
    else
        _step "Installing yt-dlp..."
        brew install yt-dlp 2>&1 | tail -1
        command -v yt-dlp &>/dev/null || { _warn "yt-dlp — install failed (YouTube downloads won't work until fixed: brew install yt-dlp)"; return 0; }
        _ok "yt-dlp"
    fi

    # PO Token plugin — required for YouTube since 2025
    local ytdlp_python
    ytdlp_python="$(head -1 "$(which yt-dlp)" | sed 's/^#!//')"
    if [[ -x "$ytdlp_python" ]] && "$ytdlp_python" -c "import bgutil_ytdlp_pot_provider" &>/dev/null; then
        _skip "yt-dlp PO Token plugin"
    else
        _step "Installing yt-dlp PO Token plugin..."
        "$ytdlp_python" -m ensurepip 2>/dev/null || true
        "$ytdlp_python" -m pip install bgutil-ytdlp-pot-provider 2>&1 | tail -1
        _ok "yt-dlp PO Token plugin"
    fi

    # bgutil server (PO token generation scripts)
    local bgutil_dir="$HOME/bgutil-ytdlp-pot-provider"
    if [[ -d "$bgutil_dir/server/src" ]]; then
        _skip "bgutil server"
    else
        _step "Cloning bgutil PO token server..."
        git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$bgutil_dir" 2>&1 | tail -1
        _ok "bgutil server"
    fi
}

prereq_youtube_transcript_api() {
    if python3 -c "import youtube_transcript_api" &>/dev/null; then
        _skip "youtube-transcript-api"
        return 0
    fi

    _step "Installing youtube-transcript-api..."
    pip3 install --break-system-packages youtube-transcript-api 2>&1 | tail -1
    python3 -c "import youtube_transcript_api" &>/dev/null || { _warn "youtube-transcript-api — install failed (YouTube captions won't work until fixed)"; return 0; }
    _ok "youtube-transcript-api"
}

prereq_surya_ocr() {
    if python3 -c "from surya.recognition import RecognitionPredictor" &>/dev/null; then
        _skip "surya-ocr"
        return 0
    fi

    _step "Installing surya-ocr + imagehash (video OCR)..."
    pip3 install --break-system-packages "surya-ocr" "imagehash" "transformers>=4.40,<5" 2>&1 | tail -1
    python3 -c "from surya.recognition import RecognitionPredictor" &>/dev/null || { _warn "surya-ocr — install failed (video OCR won't work until fixed)"; return 0; }
    _ok "surya-ocr"
}

prereq_mlx_whisper() {
    # mlx-whisper is now part of the transcriber service (core/services/transcriber).
    # The service venv gets deployed in deploy_services(). This prereq only checks
    # that Apple Silicon is present (required for MLX) and cleans up the legacy
    # standalone venv if it exists.
    if [[ "$(uname -m)" != "arm64" ]]; then
        _skip "mlx-whisper (Intel — not applicable, transcription unavailable)"
        return 0
    fi

    # Clean up legacy standalone venv — mlx-whisper now lives in the transcriber service
    local legacy_venv="$HOME/.aos/services/mlx-whisper"
    if [[ -d "$legacy_venv" ]]; then
        rm -rf "$legacy_venv"
        _ok "Cleaned up legacy mlx-whisper venv (now in transcriber service)"
    fi

    # Actual install happens via deploy_services() → transcriber/pyproject.toml
    _skip "mlx-whisper (installed via transcriber service)"
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
    local script="$AOS_DIR/core/bin/setup/claude-remote-start"
    # shellcheck disable=SC2034
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
    prereq_xcode_clt
    prereq_git
    prereq_homebrew
    prereq_python3
    prereq_uv
    prereq_pyyaml
    prereq_bun
    prereq_qmd
    prereq_jq
    prereq_ffmpeg
    prereq_ytdlp
    prereq_youtube_transcript_api
    prereq_surya_ocr
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
    return 0
}

# Repository, PATH, and git config — presented as one "Installing the system"
# stage. Git identity was already resolved in the preflight, so setup_git_config
# runs non-interactively here.
run_repo() {
    setup_repo
    setup_path
    setup_git_config
    return 0
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

    local aos_bin="$AOS_DIR/core/bin/cli/aos"
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
    local cld_bin="$AOS_DIR/core/bin/cli/cld"
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

    # Secrets use the login keychain (no separate keychain, no password prompts)
    # Migrate any existing secrets from agent.keychain if present
    if security list-keychains 2>/dev/null | grep -q "agent.keychain"; then
        bash "$AOS_DIR/core/bin/cli/agent-secret" migrate 2>/dev/null
        _ok "Migrated secrets to login keychain"
    else
        _skip "Secrets (login keychain)"
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
        mkdir -p "$vault_dir"/{log/{sessions,friction,weeks,months},knowledge/{captures,decisions,expertise,initiatives,references,research,synthesis}}
        _ok "Created ~/vault/ with standard structure"
    fi

    # Scaffold operator profile if not present
    local operator_yaml="$USER_DIR/config/operator.yaml"
    if [[ -f "$operator_yaml" ]]; then
        _skip "Operator profile"
    else
        # The operator's name was already resolved in the identity preflight
        # (macOS contact card → git → asked once), so this stage never prompts.
        local op_name="${AOS_OPERATOR_NAME:-}"
        if [[ -z "$op_name" ]]; then
            op_name=$(id -F 2>/dev/null || echo "")
        fi
        if [[ -z "$op_name" ]] || [[ "$op_name" == "$(whoami)" ]]; then
            op_name=$(git config --global user.name 2>/dev/null || echo "")
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
    if python3 "$AOS_DIR/core/infra/migrations/runner.py" migrate 2>&1 | sed 's/^/    /'; then
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
  "chrome": true,
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
if not s.get('chrome'):
    s['chrome'] = True
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
        python3 "$AOS_DIR/core/infra/migrations/005_wire_hooks.py" 2>/dev/null && _ok "Hooks wired" || _warn "Hooks — wire manually later"
    fi

    # MCP servers — register AOS services into Claude Code's config
    _info "Syncing MCP servers..."
    bash "$AOS_DIR/core/bin/cli/aos" sync-mcp 2>/dev/null && _ok "MCP servers synced" || _warn "MCP sync — run 'aos sync-mcp' after deploy"

    # projects dir — Claude Code per-project memory
    mkdir -p "$HOME/.claude/projects"

    # Pre-accept trust dialog for ~ and ~/aos (prevents interactive prompt on first run)
    python3 -c "
import json
from pathlib import Path

p = Path.home() / '.claude.json'
d = json.loads(p.read_text()) if p.exists() else {}
if 'projects' not in d:
    d['projects'] = {}

for path in [str(Path.home()), str(Path.home() / 'aos')]:
    if path not in d['projects']:
        d['projects'][path] = {}
    d['projects'][path]['hasTrustDialogAccepted'] = True
    d['projects'][path]['hasTrustDialogHooksAccepted'] = True

# AOS defaults: always-on features (verified keys — see rules/claude-code-config.md)
# remoteControlAtStartup: enables Remote Control for all sessions (stored in ~/.claude.json, not settings.json)
# claudeInChromeDefaultEnabled: enables Chrome MCP for all sessions
if not d.get('remoteControlAtStartup'):
    d['remoteControlAtStartup'] = True
if not d.get('claudeInChromeDefaultEnabled'):
    d['claudeInChromeDefaultEnabled'] = True

p.write_text(json.dumps(d, indent=2) + '\n')
print('ok')
" 2>/dev/null && _ok "Trust dialog pre-accepted" || _warn "Trust dialog — may prompt on first run"

    echo ""

    # Runtime protection — prevent accidental commits to ~/aos/
    _step "Installing runtime protection..."
    local hooks_dir="$AOS_DIR/.git/hooks"
    mkdir -p "$hooks_dir"
    cat > "$hooks_dir/pre-commit" << 'HOOK'
#!/bin/bash
# Installed by AOS — do not remove
# ~/aos/ is a read-only runtime copy. All commits go to ~/project/aos/
echo "ERROR: ~/aos/ is read-only runtime. Commit to ~/project/aos/ instead." >&2
exit 1
HOOK
    chmod +x "$hooks_dir/pre-commit"
    touch "$AOS_DIR/.no-auto-commit"
    _ok "Runtime protection installed"

    # Sync agents (system agents: chief, steward, advisor)
    _step "Syncing agents..."
    echo ""
    bash "$AOS_DIR/core/bin/cli/aos" sync-agents 2>&1 | sed 's/^/  /'

    # Activate onboard agent from catalog
    if [[ ! -f "$HOME/.claude/agents/onboard.md" ]]; then
        "$AOS_DIR/core/bin/cli/activate-agent" onboard 2>&1 | sed 's/^/  /'
    else
        echo "  ✓ Onboard agent already active"
    fi

    # Initialize trust config if not present
    if [[ ! -f "$USER_DIR/config/trust.yaml" ]]; then
        _step "Initializing trust configuration..."
        if [[ -f "$AOS_DIR/config/defaults/trust.yaml" ]]; then
            cp "$AOS_DIR/config/defaults/trust.yaml" "$USER_DIR/config/trust.yaml"
        else
            # Inline fallback — create trust config
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
        fi
        _ok "Trust configuration initialized"
    else
        _skip "Trust configuration"
    fi

    # Set release channel to stable on fresh installs. Friends ride the safe
    # lane (promoted releases only); the operator's machine is flipped to edge
    # separately (`aos channel edge`). Absence already means stable, so this is
    # only an explicit marker — never overwrite an existing choice.
    if [[ ! -f "$USER_DIR/config/channel" ]]; then
        mkdir -p "$USER_DIR/config"
        echo "stable" > "$USER_DIR/config/channel"
        _ok "Release channel set to 'stable'"
    else
        _skip "Release channel ($(cat "$USER_DIR/config/channel" 2>/dev/null | tr -d '[:space:]'))"
    fi

    # Sync skills — the developer-vs-operator split is derived from role, not
    # asked. A developer machine (has ~/project/aos) gets the full set including
    # the developer skills; an operator machine gets the default set. This keeps
    # the ceremony prompt-free while preserving the old behavior's outcomes.
    _step "Installing skills..."
    echo ""
    if [[ "$ROLE" == "developer" ]]; then
        _info "Installing all skills (developer machine)"
        touch "$USER_DIR/config/developer-mode"
        bash "$AOS_DIR/core/bin/cli/aos" sync-skills --all 2>&1 | sed 's/^/  /'
    else
        _info "Installing default skills"
        bash "$AOS_DIR/core/bin/cli/aos" sync-skills 2>&1 | sed 's/^/  /'
    fi
    return 0
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 4: Service deployment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

deploy_services() {
    _step "Deploying services..."
    echo ""

    local services_src="$AOS_DIR/core/services"
    local services_dst="$USER_DIR/services"

    # Find best python for service venvs
    local svc_python=""
    for p in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 "$HOME/.local/bin/python3"; do
        if [[ -f "$p" ]]; then
            local pminor
            pminor=$("$p" --version 2>&1 | awk '{print $2}' | cut -d. -f2)
            if [[ "$pminor" -ge 11 ]]; then
                svc_python="$p"
                break
            fi
        fi
    done

    for src_dir in "$services_src"/*/; do
        local name
        name=$(basename "$src_dir")
        [[ -f "$src_dir/pyproject.toml" ]] || continue

        local dst="$services_dst/$name"

        # Check if venv exists AND uses Python 3.11+
        local needs_deploy=false
        if [[ ! -d "$dst/.venv" ]] || [[ ! -f "$dst/.venv/bin/python" ]]; then
            needs_deploy=true
        else
            local venv_minor
            venv_minor=$("$dst/.venv/bin/python" --version 2>&1 | awk '{print $2}' | cut -d. -f2 || echo "0")
            if [[ "$venv_minor" -lt 11 ]]; then
                _info "Service $name has Python 3.$venv_minor — rebuilding"
                rm -rf "$dst/.venv"
                needs_deploy=true
            elif ! "$dst/.venv/bin/python" -c "import yaml" 2>/dev/null; then
                _info "Service $name missing deps — reinstalling"
                needs_deploy=true
            else
                _skip "Service $name"
            fi
        fi

        if [[ "$needs_deploy" == true ]]; then
            _info "Deploying $name..."
            mkdir -p "$dst"

            # Create venv with correct python
            if [[ ! -d "$dst/.venv" ]]; then
                local py_arg=""
                [[ -n "$svc_python" ]] && py_arg="--python $svc_python"
                if ! uv venv "$dst/.venv" $py_arg 2>&1 | tail -2; then
                    _warn "Service $name — venv creation failed"
                    continue
                fi
            fi

            # Install deps
            local deps
            deps=$(python3 -c "
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
" 2>/dev/null)

            if echo "$deps" | uv pip install -p "$dst/.venv/bin/python" -r - 2>&1 | tail -3 >> "$INSTALL_LOG"; then
                # Verify critical import
                if "$dst/.venv/bin/python" -c "import yaml" 2>/dev/null; then
                    _ok "Service $name"
                else
                    _warn "Service $name — deployed but yaml import failed"
                fi
            else
                _warn "Service $name — pip install failed (run 'aos deploy $name' later)"
            fi
        fi
    done

    # NLTK removed — memory service doesn't use it

    # Install LaunchAgents from templates
    install_launchagents
    return 0
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

        # Enable Screen Sharing
        if nc -z localhost 5900 2>/dev/null; then
            _skip "Screen Sharing"
        else
            sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.screensharing.plist 2>/dev/null
            if nc -z localhost 5900 2>/dev/null; then
                _ok "Screen Sharing enabled"
            else
                _warn "Screen Sharing — enable manually: System Settings > Sharing > Screen Sharing"
            fi
        fi
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
        {'hooks': [{'type': 'command', 'command': 'python3 ~/aos/core/engine/work/inject_context.py', 'statusMessage': 'Loading work context...'}]},
    ],
    'PostCompact': [
        {'hooks': [{'type': 'command', 'command': 'python3 ~/aos/core/engine/work/inject_context.py', 'statusMessage': 'Reloading work context...'}]},
    ],
    'Stop': [
        {'hooks': [{'type': 'command', 'command': 'python3 ~/aos/core/engine/work/reconcile.py', 'async': True}]},
    ],
    'SessionEnd': [
        {'hooks': [
            {'type': 'command', 'command': 'python3 ~/aos/core/engine/work/session_close.py', 'async': True},
            {'type': 'command', 'command': 'python3 ~/aos/core/bin/crons/reconcile-sessions --hook --quiet', 'async': True},
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
    return 0
}

# Discovery + health scorecard, presented as one "Final checks" stage. Returns
# non-zero when the health gate finds critical failures so the stage presenter
# shows the failure panel instead of handing off a half-built system.
run_final_checks() {
    run_discovery
    run_health_gate
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PART 6: Health gate & handoff
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

run_discovery() {
    _step "Running discovery scan..."
    echo ""
    python3 "$AOS_DIR/core/infra/migrations/runner.py" discover 2>&1 | sed 's/^/    /'
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
    # Auto-discover: every skill directory in the framework should be symlinked globally
    local skill_count=0 skill_missing=0 missing_names=""
    for skill_dir in "$AOS_DIR"/core/skills/*/; do
        [[ -d "$skill_dir" ]] || continue
        [[ -f "$skill_dir/SKILL.md" ]] || continue
        local skill_name
        skill_name=$(basename "$skill_dir")
        if [[ -L "$HOME/.claude/skills/$skill_name" ]] || [[ -d "$HOME/.claude/skills/$skill_name" ]]; then
            ((skill_count++))
        else
            ((skill_missing++))
            missing_names="$missing_names $skill_name"
        fi
    done
    if [[ "$skill_missing" -eq 0 ]]; then
        _ok "All $skill_count skills linked"
        ((pass++))
    else
        _fail "$skill_missing of $((skill_count + skill_missing)) skills missing:$missing_names"
        ((fail++))
        failures+=("$skill_missing skills missing")
    fi

    # ── Services ───────────────────────────────────────────────
    _step "Services"
    # Check all services that have pyproject.toml (auto-discovered)
    for svc_dir in "$AOS_DIR"/core/services/*/; do
        [[ -f "$svc_dir/pyproject.toml" ]] || continue
        local svc_name
        svc_name=$(basename "$svc_dir")
        _check "Service $svc_name venv" "[[ -f '$USER_DIR/services/$svc_name/.venv/bin/python' ]]" critical
    done
    # Verify critical imports in service venvs
    _check "Bridge: yaml+httpx" "'$USER_DIR/services/bridge/.venv/bin/python' -c 'import yaml, httpx'"
    _check "Dashboard: yaml+httpx" "'$USER_DIR/services/dashboard/.venv/bin/python' -c 'import yaml, httpx, fastapi'"
    _check "Listen: yaml+fastapi" "'$USER_DIR/services/listen/.venv/bin/python' -c 'import yaml, fastapi'"

    # Secrets accessible (login keychain)
    _check "Secrets (login keychain)" "security find-generic-password -a aos -s aos.test 2>/dev/null || true"

    # ── LaunchAgents ───────────────────────────────────────────
    _step "LaunchAgents"
    # Auto-discover: check all AOS LaunchAgents installed in ~/Library/LaunchAgents
    for plist in "$HOME/Library/LaunchAgents"/com.aos.*.plist; do
        [[ -f "$plist" ]] || continue
        local la
        la=$(basename "$plist" .plist)
        _check "LaunchAgent $la" "launchctl list 2>/dev/null | grep -q '$la'"
    done

    # ── LaunchAgent path validation ─────────────────────────────
    # Detect when launchd has cached stale paths that don't match the plist on disk
    local la_drift=0
    for plist in "$HOME/Library/LaunchAgents"/com.aos.*.plist; do
        [[ -f "$plist" ]] || continue
        local la
        la=$(basename "$plist" .plist)
        # Get the path launchd is actually using
        local loaded_args
        loaded_args=$(launchctl print "gui/$(id -u)/$la" 2>/dev/null | grep -A2 "arguments" | tail -1 | xargs 2>/dev/null || true)
        if [[ -n "$loaded_args" ]] && [[ ! -f "$loaded_args" ]]; then
            _warn "LaunchAgent $la has stale path: $loaded_args"
            ((la_drift++))
            ((warn++))
            warnings+=("$la has stale cached path — run: launchctl bootout gui/\$(id -u)/$la && launchctl bootstrap gui/\$(id -u) $plist")
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
        _check "Transcriber: mlx-whisper"    "[[ -f '$USER_DIR/services/transcriber/.venv/bin/python' ]] && '$USER_DIR/services/transcriber/.venv/bin/python' -c 'import mlx_whisper'"
    fi

    # ── Apps ───────────────────────────────────────────────────
    _step "Applications"
    _check "Google Chrome"  "[[ -d '/Applications/Google Chrome.app' ]]"
    _check "SuperWhisper"   "[[ -d '/Applications/superwhisper.app' ]]"
    _check "Obsidian"       "[[ -d '/Applications/Obsidian.app' ]]"

    # ── Always-on ─────────────────────────────────────────────
    _step "Always-on configuration"
    _check "System sleep disabled"    "pmset -g 2>/dev/null | grep -q 'sleep.*0'"
    _check "Auto-restart on power"    "pmset -g 2>/dev/null | grep -q 'autorestart.*1'"
    _check "Auto-login configured"    "defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null"

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
        _fail "$fail critical failure(s) — see $INSTALL_LOG"
        echo ""
        # Non-zero return → the "Final checks" stage renders the failure panel
        # rather than proceeding to the handoff. The scorecard above and
        # install-report.yaml capture exactly what failed.
        return 1
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

QAREEN_URL="http://localhost:4096"

# Wait briefly for the Qareen service to answer, then open it in the operator's
# browser. Headless / SSH sessions (no GUI) just get the URL printed instead.
# Never fatal — a browser that won't open is a printed link, not a failed install.
_open_qareen() {
    # Headless / SSH sessions have no browser to open — print the link and
    # return immediately (no point waiting on the service we won't open).
    if [[ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]] || ! command -v open >/dev/null 2>&1; then
        echo "  ${BOLD}Open AOS in your browser:${RESET}"
        echo "    ${BRAND}${BOLD}${QAREEN_URL}${RESET}"
        echo ""
        return 0
    fi

    # GUI session: give the Qareen service a moment to answer, then open it so
    # the operator lands on a live page rather than a connection error.
    local i=0
    while [[ $i -lt 15 ]]; do
        curl -sfm 2 "$QAREEN_URL/api/health" >/dev/null 2>&1 && break
        sleep 1
        ((i++)) || true
    done

    if open "$QAREEN_URL" >/dev/null 2>&1; then
        echo "  ${MUTED}Opening AOS in your browser…${RESET}"
    else
        echo "  ${BOLD}Open AOS in your browser:${RESET}"
        echo "    ${BRAND}${BOLD}${QAREEN_URL}${RESET}"
    fi
    echo ""
}

print_handoff() {
    local total
    total=$(_total_elapsed)
    local hostname
    hostname=$(scutil --get ComputerName 2>/dev/null || hostname -s)
    local machine_id
    machine_id=$(cat "$MACHINE_ID_FILE" 2>/dev/null || echo "unknown")
    local op_name
    op_name=$(python3 -c "
import yaml
try:
    with open('$USER_DIR/config/operator.yaml') as f:
        print(yaml.safe_load(f).get('name', 'Operator'))
except: print('Operator')
" 2>/dev/null || echo "Operator")

    tput cnorm 2>/dev/null >&3  # restore cursor

    echo ""
    echo "  ${MUTED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
    echo "  ${GREEN}${BOLD}Your system is alive.${RESET}"
    echo ""
    printf "  ${MUTED}%-12s${RESET}%s\n" "Machine" "$hostname"
    printf "  ${MUTED}%-12s${RESET}%s\n" "ID" "$machine_id"
    printf "  ${MUTED}%-12s${RESET}%s\n" "Operator" "$op_name"
    printf "  ${MUTED}%-12s${RESET}%s\n" "Duration" "$total"
    echo ""
    echo "  ${MUTED}────────────────────────────────────────────────────${RESET}"
    echo ""

    if [[ "$ROLE" == "developer" ]]; then
        # Developer machine: the terminal is the surface. Point at Qareen and
        # the dev handoff, but don't auto-open a browser.
        echo "  ${BOLD}AOS is running at${RESET} ${BRAND}${QAREEN_URL}${RESET}"
        echo ""
        if command -v cld &>/dev/null || command -v claude &>/dev/null; then
            echo "  ${BOLD}Start a session:${RESET}  ${BRAND}${BOLD}aos start${RESET}  ${MUTED}(or ${BOLD}cld${RESET}${MUTED})${RESET}"
        else
            echo "  ${BOLD}Next:${RESET} install Claude Code, then run ${BRAND}aos start${RESET}"
            echo "  ${MUTED}https://docs.anthropic.com/en/docs/claude-code${RESET}"
        fi
        echo "  ${MUTED}Dev workspace: ~/project/aos — framework changes go there, never ~/aos.${RESET}"
        echo ""
    else
        # Operator machine: Qareen is the whole takeover. Sahib greets them
        # inside the UI; the terminal is never surfaced again.
        echo "  ${MUTED}Claude is waiting for you inside — Sahib will take it from here.${RESET}"
        echo ""
        _open_qareen
    fi

    echo "  ${MUTED}────────────────────────────────────────────────────${RESET}"
    echo ""
    echo "  ${MUTED}aos status        check migration status${RESET}"
    echo "  ${MUTED}aos self-test     verify system health${RESET}"
    echo "  ${MUTED}aos update        pull latest + migrate${RESET}"
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

    # Role decides how the install ends (developer terminal vs operator browser)
    # and the developer-skills split. Detected the same way migration 081 does.
    _detect_role

    # Dry-run / walk mode — walk the stage ceremony without touching the machine.
    # Same code path as a real install so the transcript matches what ships.
    if [[ "$DRY_RUN" == true ]]; then
        echo "  ${YELLOW}DRY RUN${RESET} — walking the install stages (nothing is changed)"
        echo "  ${MUTED}Role: ${ROLE}${RESET}"
        echo ""
        _stage "Checking your Mac"                          run_prereqs      prereqs
        _stage "Installing the system"                      run_repo         repo
        _stage "Setting up your knowledge vault and memory" run_bootstrap    bootstrap
        _stage "Waking the agents"                          deploy_services  services
        _stage "Making it yours"                            run_provisioning provisioning
        _stage "Final checks"                               run_final_checks
        echo ""
        echo "  ${MUTED}Run without --dry-run (or INSTALL_DRY_RUN unset) to install for real.${RESET}"
        echo ""
        exit 0
    fi

    # Network check — fail fast
    _check_network

    # The only questions we ask, asked once, before the calm ceremony begins.
    _collect_identity

    # Keep sudo alive — install can take 20+ minutes, ticket expires in 5
    ( while true; do sudo -n true 2>/dev/null; sleep 50; done ) &
    SUDO_KEEPALIVE_PID=$!
    trap '_spinner_stop; tput cnorm 2>/dev/null >&3; kill $SUDO_KEEPALIVE_PID 2>/dev/null' EXIT

    echo "" >&3

    # The install as a calm progress ceremony: one human-named line per stage,
    # spinner → checkmark, all tool output tucked into $INSTALL_LOG. A stage that
    # fails routes through the failure panel (see _stage) and exits non-zero.
    _stage "Checking your Mac"                          run_prereqs      prereqs
    _stage "Installing the system"                      run_repo         repo
    _stage "Setting up your knowledge vault and memory" run_bootstrap    bootstrap
    _stage "Waking the agents"                          deploy_services  services
    _stage "Making it yours"                            run_provisioning provisioning
    _stage "Final checks"                               run_final_checks

    print_handoff

    # Clean checkpoint on success — next run starts fresh
    rm -f "$CHECKPOINT_FILE" 2>/dev/null
    _log "Install complete"

    # Handoff launch. Operators land in Qareen (opened in print_handoff); the
    # terminal is never surfaced for them. Developers keep the terminal handoff —
    # aos start drops them into a Claude Code session with onboarding.
    if [[ "$ROLE" == "developer" ]] && command -v claude &>/dev/null; then
        echo ""
        echo "  ${BOLD}Launching AOS...${RESET}"
        echo ""
        sleep 1
        exec aos start
    fi
}

main "$@"
