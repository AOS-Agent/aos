#!/usr/bin/env bash
# ─── Apple Native Apps Setup ──────────────────────────────
# Verifies access to macOS native apps: Calendar, Notes,
# Reminders, Messages, Mail, Contacts, Voice Memos.
#
# These apps need no credentials — just macOS permission grants.
# This script tests each one and reports what's accessible.
#
# Usage:
#   setup.sh              — test and report access
#   setup.sh --check      — same (always non-destructive)

source "$(dirname "$0")/../_lib.sh"

NAME="apple_native"
echo ""
echo "${BOLD}Apple Native Apps${RESET} — Calendar, Notes, Reminders, Messages, Mail"
echo ""
_info "These apps are already on your Mac. We just need to verify access."
_info "macOS may prompt you to grant permission — click Allow."
echo ""

errors=0
available=0

# ── Calendar ──────────────────────────────────────────────

if osascript -e 'tell application "Calendar" to get name of calendars' &>/dev/null; then
    count=$(osascript -e 'tell application "Calendar" to count calendars' 2>/dev/null || echo "?")
    _ok "Calendar ($count calendars)"
    ((available++))
else
    _warn "Calendar — no access (macOS may prompt you next time)"
    ((errors++))
fi

# ── Notes ─────────────────────────────────────────────────

if osascript -e 'tell application "Notes" to get name of every folder' &>/dev/null; then
    _ok "Notes"
    ((available++))
else
    _warn "Notes — no access"
    ((errors++))
fi

# ── Reminders ─────────────────────────────────────────────

if osascript -e 'tell application "Reminders" to get name of every list' &>/dev/null; then
    count=$(osascript -e 'tell application "Reminders" to count lists' 2>/dev/null || echo "?")
    _ok "Reminders ($count lists)"
    ((available++))
else
    _warn "Reminders — no access"
    ((errors++))
fi

# ── Messages (iMessage) ──────────────────────────────────

if osascript -e 'tell application "Messages" to get name of every chat' &>/dev/null 2>&1; then
    _ok "Messages (iMessage)"
    ((available++))
else
    # Messages is restricted — try a lighter check
    if [[ -f "$HOME/Library/Messages/chat.db" ]]; then
        _ok "Messages (database accessible)"
        ((available++))
    else
        _warn "Messages — no access (may need Full Disk Access in System Settings)"
        ((errors++))
    fi
fi

# ── Mail ──────────────────────────────────────────────────

if osascript -e 'tell application "Mail" to get name of every account' &>/dev/null; then
    count=$(osascript -e 'tell application "Mail" to count accounts' 2>/dev/null || echo "?")
    _ok "Mail ($count accounts)"
    ((available++))
else
    _warn "Mail — no access or no accounts configured"
    ((errors++))
fi

# ── Contacts ──────────────────────────────────────────────

if python3 "$HOME/aos/apps/messages/contacts_reader.py" --search "test" &>/dev/null 2>&1; then
    _ok "Contacts (via contacts_reader)"
    ((available++))
elif osascript -e 'tell application "Contacts" to get name of every person' &>/dev/null 2>&1; then
    _ok "Contacts (via AppleScript)"
    ((available++))
else
    _warn "Contacts — no access"
    ((errors++))
fi

# ── Voice Memos ───────────────────────────────────────────

VOICE_DIR="$HOME/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
if [[ -d "$VOICE_DIR" ]]; then
    count=$(ls "$VOICE_DIR"/*.m4a 2>/dev/null | wc -l | tr -d ' ')
    _ok "Voice Memos ($count recordings)"
    ((available++))
else
    _warn "Voice Memos — recordings folder not found"
    # Not a real error — they may just not have used it
fi

# ── Result ────────────────────────────────────────────────

echo ""
_ok "$available Apple apps accessible"
if [[ $errors -gt 0 ]]; then
    _info "$errors app(s) need permission. macOS will prompt when agents first access them."
    _info "Or grant manually: System Settings > Privacy & Security"
fi

# Apple native apps are always "configured" — they're on the Mac
state_set "$NAME" "active"
echo ""
_ok "${BOLD}Apple native apps configured${RESET}"
