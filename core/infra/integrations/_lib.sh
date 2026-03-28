#!/usr/bin/env bash
# ─── Integration Setup Library ────────────────────────────
# Shared functions for all setup.sh scripts.
# Source this at the top: source "$(dirname "$0")/../_lib.sh"

set -euo pipefail

AOS_DIR="$HOME/aos"
USER_DIR="$HOME/.aos"
AGENT_SECRET="$AOS_DIR/core/bin/cli/agent-secret"
INTEGRATION_STATE="$USER_DIR/config/integrations.yaml"

# Colors (minimal — works in any terminal)
if [[ -t 1 ]]; then
    GREEN=$'\033[32m' RED=$'\033[31m' YELLOW=$'\033[33m'
    CYAN=$'\033[36m' BOLD=$'\033[1m' DIM=$'\033[2m' RESET=$'\033[0m'
else
    GREEN="" RED="" YELLOW="" CYAN="" BOLD="" DIM="" RESET=""
fi

# ── Output ────────────────────────────────────────────────

_ok()   { echo "  ${GREEN}ok${RESET}  $1"; }
_fail() { echo "  ${RED}fail${RESET}  $1"; }
_warn() { echo "  ${YELLOW}warn${RESET}  $1"; }
_info() { echo "  ${DIM}$1${RESET}"; }
_ask()  { printf "  ${BOLD}$1${RESET} "; }

# ── Mode Detection ────────────────────────────────────────

IS_CHECK=false
IS_CONFIGURE=false
for arg in "$@"; do
    [[ "$arg" == "--check" ]] && IS_CHECK=true
    [[ "$arg" == "--configure" ]] && IS_CONFIGURE=true
done

# ── Secrets ───────────────────────────────────────────────

secret_get() {
    "$AGENT_SECRET" get "$1" 2>/dev/null || echo ""
}

secret_set() {
    "$AGENT_SECRET" set "$1" "$2" 2>/dev/null
}

secret_exists() {
    local val
    val=$(secret_get "$1")
    [[ -n "$val" ]]
}

# ── State Tracking ────────────────────────────────────────
# Records which integrations are configured in integrations.yaml

state_set() {
    local name="$1" status="$2"
    mkdir -p "$(dirname "$INTEGRATION_STATE")"

    python3 -c "
import yaml
from pathlib import Path

path = Path('$INTEGRATION_STATE')
if path.exists():
    data = yaml.safe_load(path.read_text()) or {}
else:
    data = {}

if 'integrations' not in data:
    data['integrations'] = {}

data['integrations']['$name'] = {
    'status': '$status',
    'configured': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
}

path.write_text(yaml.dump(data, default_flow_style=False))
" 2>/dev/null
}

state_get() {
    local name="$1"
    python3 -c "
import yaml
from pathlib import Path

path = Path('$INTEGRATION_STATE')
if not path.exists():
    print('unconfigured')
else:
    data = yaml.safe_load(path.read_text()) or {}
    status = data.get('integrations', {}).get('$name', {}).get('status', 'unconfigured')
    print(status)
" 2>/dev/null || echo "unconfigured"
}

# ── Prompts ───────────────────────────────────────────────

prompt_secret() {
    # Usage: prompt_secret "KEY_NAME" "Human-readable prompt" "help text"
    local key="$1" prompt="$2" help="${3:-}"

    if secret_exists "$key"; then
        _ok "$prompt (already stored)"
        return 0
    fi

    if $IS_CHECK; then
        _fail "$prompt (not stored)"
        return 1
    fi

    [[ -n "$help" ]] && _info "$help"
    _ask "$prompt:"
    read -r value

    if [[ -z "$value" ]]; then
        _warn "Skipped — no value provided"
        return 1
    fi

    secret_set "$key" "$value"
    _ok "$prompt stored"
}

prompt_value() {
    # Usage: prompt_value "prompt" "default"
    # Returns: the value (via stdout)
    local prompt="$1" default="${2:-}"

    if [[ -n "$default" ]]; then
        _ask "$prompt [$default]:"
    else
        _ask "$prompt:"
    fi
    read -r value
    echo "${value:-$default}"
}

# ── Health Checks ─────────────────────────────────────────

check_command() {
    # Usage: check_command "description" "command"
    local desc="$1" cmd="$2"
    if eval "$cmd" &>/dev/null; then
        _ok "$desc"
        return 0
    else
        _fail "$desc"
        return 1
    fi
}

check_service() {
    # Usage: check_service "description" "launchctl label or pgrep pattern"
    local desc="$1" pattern="$2"
    if launchctl list 2>/dev/null | grep -q "$pattern" || pgrep -f "$pattern" &>/dev/null; then
        _ok "$desc running"
        return 0
    else
        _fail "$desc not running"
        return 1
    fi
}

check_url() {
    # Usage: check_url "description" "url" "expected_content"
    local desc="$1" url="$2" expected="${3:-}"
    local response
    response=$(curl -s --max-time 5 "$url" 2>/dev/null || echo "")

    if [[ -z "$response" ]]; then
        _fail "$desc — no response"
        return 1
    fi

    if [[ -n "$expected" ]]; then
        if echo "$response" | grep -q "$expected"; then
            _ok "$desc"
            return 0
        else
            _fail "$desc — unexpected response"
            return 1
        fi
    else
        _ok "$desc"
        return 0
    fi
}

# ── Result ────────────────────────────────────────────────

setup_complete() {
    local name="$1"
    state_set "$name" "active"
    echo ""
    _ok "${BOLD}$name configured${RESET}"
}

setup_failed() {
    local name="$1" reason="${2:-setup incomplete}"
    state_set "$name" "failed"
    echo ""
    _fail "${BOLD}$name — $reason${RESET}"
}
