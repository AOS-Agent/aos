#!/usr/bin/env bash
# ─── Codex Integration Setup ──────────────────────────────
# OpenAI Codex CLI as an external model worker — overflow
# capacity when Claude usage limits are hit.
# Auth: ChatGPT plan OAuth. No API key. No per-token billing.
#
# Usage:
#   setup.sh              — interactive setup (spends ~16K plan tokens on round-trip test)
#   setup.sh --check      — verify install/auth/billing-safety/bridges (offline, zero tokens)

source "$(dirname "$0")/../_lib.sh"

NAME="codex"
echo ""
echo "${BOLD}Codex${RESET} — external model worker (OpenAI, ChatGPT plan)"
echo ""

errors=0

# ── Step 1: codex CLI ─────────────────────────────────────

check_command "codex CLI installed" "command -v codex" || {
    if ! $IS_CHECK; then
        _info "Installing @openai/codex via npm..."
        npm install -g @openai/codex 2>&1 | tail -2
        check_command "codex CLI installed" "command -v codex" || errors=$((errors+1))
    else
        errors=$((errors+1))
    fi
}

# ── Step 2: Authentication (ChatGPT OAuth) ────────────────

if codex login status &>/dev/null; then
    _ok "Codex authenticated"
    _info "$(codex login status 2>&1 | head -1)"
else
    if $IS_CHECK; then
        _fail "Codex not authenticated"
        errors=$((errors+1))
    else
        _info "Opening browser for ChatGPT sign-in..."
        _info "Sign in with the ChatGPT account — do NOT choose the API-key option."
        codex login 2>&1 || errors=$((errors+1))
        if codex login status &>/dev/null; then
            _ok "Codex authenticated"
        else
            _warn "Auth may have failed — run 'codex login' manually"
            errors=$((errors+1))
        fi
    fi
fi

# ── Step 3: Billing safety guard ──────────────────────────
# Plan-billed OAuth only. Any API key in auth.json or the
# environment risks silent per-token billing.

auth_json="$HOME/.codex/auth.json"
if [[ -f "$auth_json" ]]; then
    mode=$(python3 -c "
import json
d = json.load(open('$auth_json'))
m = d.get('auth_mode', 'unknown')
print(m + ('+apikey' if d.get('OPENAI_API_KEY') else ''))
" 2>/dev/null || echo "unreadable")
    if [[ "$mode" == "chatgpt" ]]; then
        _ok "auth mode: chatgpt (plan-billed, no per-token charges)"
    else
        _warn "auth mode is '$mode' — per-token API billing risk. Run 'codex logout && codex login' and sign in with ChatGPT."
        errors=$((errors+1))
    fi
else
    _fail "$HOME/.codex/auth.json missing"
    errors=$((errors+1))
fi

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    _warn "OPENAI_API_KEY is set in the environment — codex may use it and bill per-token. Unset it."
    errors=$((errors+1))
else
    _ok "no OPENAI_API_KEY in environment"
fi

# ── Step 4: AGENTS.md bridges ─────────────────────────────
# Codex reads AGENTS.md, not CLAUDE.md. Bridges keep one
# source of truth. Never overwrite a real file.

check_bridge() {
    local link="$1" target="$2"
    if [[ -L "$link" && -e "$link" ]]; then
        _ok "bridge: $link"
        return 0
    elif [[ -e "$link" ]]; then
        _warn "bridge: $link exists as a real file — not touching it"
        return 0
    fi
    if $IS_CHECK; then
        _fail "bridge missing: $link"
        return 1
    fi
    if [[ ! -e "$target" ]]; then
        _warn "bridge target missing: $target — skipping"
        return 1
    fi
    ln -s "$target" "$link" && _ok "bridge created: $link -> $target"
}

check_bridge "$HOME/.codex/AGENTS.md" "$HOME/.claude/CLAUDE.md" || errors=$((errors+1))
check_bridge "$HOME/AGENTS.md" "$HOME/CLAUDE.md" || errors=$((errors+1))
# Codex discovers user skills under ~/.agents/skills. Bridge the complete AOS
# skill directory so the filesystem remains the source of truth as skills change.
mkdir -p "$HOME/.agents"
check_bridge "$HOME/.agents/skills" "$HOME/.claude/skills" || errors=$((errors+1))
# Dev workspace bridge (only if the workspace exists on this machine)
if [[ -d "$HOME/project/aos" ]]; then
    check_bridge "$HOME/project/aos/AGENTS.md" "$HOME/project/aos/CLAUDE.md" || errors=$((errors+1))
fi

# ── Step 5: cmux lifecycle hooks (optional) ───────────────

if command -v cmux &>/dev/null; then
    if [[ -f "$HOME/.codex/hooks.json" ]]; then
        _ok "cmux hooks installed for codex"
    elif $IS_CHECK; then
        _warn "cmux present but codex hooks not installed (run: cmux hooks setup codex)"
    else
        _info "Installing cmux lifecycle hooks for codex..."
        yes | cmux hooks setup codex &>/dev/null && _ok "cmux hooks installed" \
            || _warn "cmux hook setup failed — run 'cmux hooks setup codex' manually"
    fi
fi

# ── Step 6: Round-trip test (interactive only — costs tokens) ──

if ! $IS_CHECK && [[ $errors -eq 0 ]]; then
    _info "Round-trip test (one cheap exec, ~16K plan tokens)..."
    if out=$(codex exec --json --ephemeral --skip-git-repo-check -s read-only \
             "Reply with exactly: OK" 2>/dev/null) \
       && echo "$out" | grep -q 'turn.completed'; then
        _ok "codex exec round-trip"
    else
        _fail "codex exec round-trip failed"
        errors=$((errors+1))
    fi
fi

# ── Result ────────────────────────────────────────────────

echo ""
if [[ $errors -eq 0 ]]; then
    setup_complete "$NAME"
else
    setup_failed "$NAME" "$errors issue(s)"
fi

exit $errors
