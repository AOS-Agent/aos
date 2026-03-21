#!/usr/bin/env bash
# ─── GitHub Integration Setup ─────────────────────────────
# Authenticates gh CLI for repo/PR/issue operations.
#
# Usage:
#   setup.sh              — interactive setup
#   setup.sh --check      — verify auth

source "$(dirname "$0")/../_lib.sh"

NAME="github"
echo ""
echo "${BOLD}GitHub${RESET} — repos, PRs, and issues"
echo ""

errors=0

# ── Step 1: gh CLI ────────────────────────────────────────

check_command "gh CLI installed" "command -v gh" || {
    if ! $IS_CHECK; then
        _info "Installing gh..."
        brew install gh 2>&1 | tail -3
        check_command "gh CLI installed" "command -v gh" || ((errors++))
    else
        ((errors++))
    fi
}

# ── Step 2: Authentication ────────────────────────────────

if gh auth status &>/dev/null; then
    _ok "GitHub authenticated"
    user=$(gh api user --jq '.login' 2>/dev/null || echo "unknown")
    _info "Logged in as: $user"
else
    if $IS_CHECK; then
        _fail "GitHub not authenticated"
        ((errors++))
    else
        _info "Opening browser for GitHub sign-in..."
        gh auth login --web 2>&1 || ((errors++))
        if gh auth status &>/dev/null; then
            _ok "GitHub authenticated"
        else
            _warn "Auth may have failed — run 'gh auth login' manually"
            ((errors++))
        fi
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
