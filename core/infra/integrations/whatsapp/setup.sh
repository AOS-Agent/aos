#!/usr/bin/env bash
# ─── WhatsApp Integration Setup ───────────────────────────
# Verifies and configures the whatsmeow bridge for WhatsApp.
#
# Usage:
#   setup.sh              — interactive setup
#   setup.sh --check      — health check only

source "$(dirname "$0")/../_lib.sh"

NAME="whatsapp"
echo ""
echo "${BOLD}WhatsApp${RESET} — read and send messages"
echo ""

errors=0

# ── Step 1: Whatsmeow Service ─────────────────────────────

WHATSMEOW_DIR="$HOME/.aos/services/whatsmeow"
WHATSMEOW_PORT=7601

check_service "Whatsmeow service" "whatsmeow" || {
    if ! $IS_CHECK; then
        _info "The whatsmeow bridge needs to be deployed first."
        _info "Run: aos deploy whatsmeow"
    fi
    ((errors++))
}

# ── Step 2: Service Health ────────────────────────────────

check_url "WhatsApp API" "http://127.0.0.1:${WHATSMEOW_PORT}/status" || {
    if ! $IS_CHECK; then
        _warn "Whatsmeow not responding on port $WHATSMEOW_PORT"
        _info "It may need to be started: launchctl load ~/Library/LaunchAgents/com.agent.whatsmeow.plist"
    fi
    ((errors++))
}

# ── Step 3: Pairing Status ────────────────────────────────

if curl -s --max-time 3 "http://127.0.0.1:${WHATSMEOW_PORT}/status" 2>/dev/null | grep -q "connected"; then
    _ok "WhatsApp paired and connected"
else
    if ! $IS_CHECK; then
        echo ""
        _warn "WhatsApp not paired or not connected"
        _info "Pairing requires scanning a QR code with your phone:"
        _info "  1. Check the whatsmeow logs for a QR code"
        _info "  2. Open WhatsApp on your phone > Settings > Linked Devices"
        _info "  3. Scan the QR code"
        echo ""
        _info "Logs: tail -f ~/.aos/services/whatsmeow/whatsmeow.log"
    else
        _fail "WhatsApp not connected"
    fi
    ((errors++))
fi

# ── Step 4: Database ──────────────────────────────────────

DB_PATH="$WHATSMEOW_DIR/messages.db"
if [[ -f "$DB_PATH" ]]; then
    count=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
print(conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0])
conn.close()
" 2>/dev/null || echo "0")
    _ok "Message database ($count messages)"
else
    _warn "No message database yet — will be created after pairing"
fi

# ── Step 5: Send Test ─────────────────────────────────────

if [[ $errors -eq 0 ]] && ! $IS_CHECK; then
    _ask "Send a test message to yourself? [y/N]:"
    read -r test_choice
    if [[ "${test_choice:-n}" =~ ^[Yy]$ ]]; then
        _ask "Your phone number (with country code, e.g. 15551234567):"
        read -r phone
        if [[ -n "$phone" ]]; then
            response=$(curl -s -X POST "http://127.0.0.1:${WHATSMEOW_PORT}/send" \
                -H "Content-Type: application/json" \
                -d "{\"to\":\"$phone\",\"text\":\"AOS connected. WhatsApp integration is live.\"}" 2>/dev/null)
            if echo "$response" | grep -qi "sent\|success\|ok"; then
                _ok "Test message sent"
            else
                _warn "Test message may have failed — check your phone"
            fi
        fi
    fi
fi

# ── Result ────────────────────────────────────────────────

echo ""
if [[ $errors -eq 0 ]]; then
    setup_complete "$NAME"
else
    if $IS_CHECK; then
        _fail "WhatsApp — $errors check(s) failed"
    else
        setup_failed "$NAME" "$errors issue(s)"
    fi
fi

exit $errors
