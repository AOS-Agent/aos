#!/usr/bin/env bash
# ─── Telegram Integration — Health Check ──────────────────
# Verifies Telegram integration is working.
# Actual setup is done by Sahib (onboard agent) via Chrome MCP.
#
# Usage:
#   setup.sh              — run all checks
#   setup.sh --check      — same (always non-destructive)

source "$(dirname "$0")/../_lib.sh"

NAME="telegram"
echo ""
echo "${BOLD}Telegram${RESET} — health check"
echo ""

errors=0

# ── Bot Token ─────────────────────────────────────────────

if secret_exists "TELEGRAM_BOT_TOKEN"; then
    _ok "Bot token stored"
    token=$(secret_get "TELEGRAM_BOT_TOKEN")
    check_url "Bot reachable" \
        "https://api.telegram.org/bot${token}/getMe" \
        '"ok":true' || ((errors++))
else
    _fail "Bot token not stored"
    ((errors++))
fi

# ── Chat ID ───────────────────────────────────────────────

if secret_exists "TELEGRAM_CHAT_ID"; then
    _ok "Chat ID stored"
else
    _fail "Chat ID not stored"
    ((errors++))
fi

# ── Bridge Service ────────────────────────────────────────

check_service "Bridge service" "com.agent.bridge" || ((errors++))

# ── Result ────────────────────────────────────────────────

echo ""
if [[ $errors -eq 0 ]]; then
    _ok "${BOLD}Telegram — all checks passed${RESET}"
    state_set "$NAME" "active"
else
    _fail "${BOLD}Telegram — $errors check(s) failed${RESET}"
    state_set "$NAME" "failed"
fi

exit $errors
