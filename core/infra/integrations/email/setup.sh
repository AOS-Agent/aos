#!/usr/bin/env bash
# ─── Email Integration Setup ──────────────────────────────
# Configures one or more email accounts for AOS.
# Supports multiple accounts stored in ~/.aos/config/email_accounts.yaml
#
# Usage:
#   setup.sh              — interactive setup (add accounts)
#   setup.sh --check      — verify existing accounts
#   setup.sh --add        — add another account

source "$(dirname "$0")/../_lib.sh"

NAME="email"
ACCOUNTS_FILE="$USER_DIR/config/email_accounts.yaml"

echo ""
echo "${BOLD}Email${RESET} — inbox reading and sending"
echo ""

errors=0

# ── Check Mode: Verify Existing Accounts ──────────────────

if $IS_CHECK; then
    if [[ ! -f "$ACCOUNTS_FILE" ]]; then
        _fail "No email accounts configured"
        exit 1
    fi

    count=$(python3 -c "
import yaml
from pathlib import Path
data = yaml.safe_load(Path('$ACCOUNTS_FILE').read_text()) or {}
print(len(data.get('accounts', [])))
" 2>/dev/null || echo "0")

    if [[ "$count" -eq 0 ]]; then
        _fail "No email accounts configured"
        exit 1
    fi

    _ok "$count email account(s) configured"

    # Check each account's credentials exist
    python3 -c "
import yaml
from pathlib import Path
import subprocess

data = yaml.safe_load(Path('$ACCOUNTS_FILE').read_text()) or {}
for acct in data.get('accounts', []):
    name = acct.get('name', 'unknown')
    key = acct.get('secret_key', '')
    result = subprocess.run(['$AGENT_SECRET', 'get', key],
        capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        print(f'  \033[32mok\033[0m  {name} — credentials stored')
    else:
        print(f'  \033[31mfail\033[0m  {name} — credentials missing')
" 2>/dev/null

    exit 0
fi

# ── Interactive: Add Account ──────────────────────────────

_info "AOS can read and send email via IMAP/SMTP."
_info "You can add multiple accounts (personal, work, etc.)"
echo ""

add_account() {
    echo ""
    _ask "Account name (e.g. personal, work, school):"
    read -r acct_name
    [[ -z "$acct_name" ]] && return 1

    # Sanitize for use as secret key
    local key_name
    key_name=$(echo "$acct_name" | tr '[:lower:]' '[:upper:]' | tr ' ' '_' | tr -cd '[:alnum:]_')

    _ask "Email address:"
    read -r email_addr
    [[ -z "$email_addr" ]] && { _warn "Skipped — no email"; return 1; }

    echo ""
    echo "  ${BOLD}Provider?${RESET}"
    echo "    1) Gmail"
    echo "    2) Outlook / Microsoft 365"
    echo "    3) iCloud"
    echo "    4) Custom IMAP/SMTP"
    echo ""
    _ask "Choice [1-4]:"
    read -r provider_choice

    local imap_host="" imap_port="993" smtp_host="" smtp_port="587"

    case "${provider_choice:-1}" in
        1)
            imap_host="imap.gmail.com"
            smtp_host="smtp.gmail.com"
            _info "For Gmail, use an App Password (not your regular password)."
            _info "Create one at: myaccount.google.com/apppasswords"
            ;;
        2)
            imap_host="outlook.office365.com"
            smtp_host="smtp.office365.com"
            ;;
        3)
            imap_host="imap.mail.me.com"
            smtp_host="smtp.mail.me.com"
            smtp_port="587"
            _info "For iCloud, use an App-Specific Password."
            _info "Create one at: appleid.apple.com > App-Specific Passwords"
            ;;
        4)
            _ask "IMAP host:"
            read -r imap_host
            _ask "IMAP port [993]:"
            read -r imap_port_in
            imap_port="${imap_port_in:-993}"
            _ask "SMTP host:"
            read -r smtp_host
            _ask "SMTP port [587]:"
            read -r smtp_port_in
            smtp_port="${smtp_port_in:-587}"
            ;;
    esac

    echo ""
    _ask "Password (or app password):"
    read -rs password
    echo ""

    if [[ -z "$password" ]]; then
        _warn "No password provided — skipping"
        return 1
    fi

    # Store credentials
    local secret_key="EMAIL_${key_name}_PASSWORD"
    secret_set "$secret_key" "$password"
    _ok "Credentials stored"

    # Write account config
    mkdir -p "$(dirname "$ACCOUNTS_FILE")"
    python3 -c "
import yaml
from pathlib import Path

path = Path('$ACCOUNTS_FILE')
if path.exists():
    data = yaml.safe_load(path.read_text()) or {}
else:
    data = {}

if 'accounts' not in data:
    data['accounts'] = []

# Remove existing account with same name
data['accounts'] = [a for a in data['accounts'] if a.get('name') != '$acct_name']

data['accounts'].append({
    'name': '$acct_name',
    'email': '$email_addr',
    'imap_host': '$imap_host',
    'imap_port': int('$imap_port'),
    'smtp_host': '$smtp_host',
    'smtp_port': int('$smtp_port'),
    'secret_key': '$secret_key',
})

path.write_text(yaml.dump(data, default_flow_style=False))
" 2>/dev/null

    _ok "Account '$acct_name' added ($email_addr)"
}

# Add first account
add_account || ((errors++))

# Offer to add more
while true; do
    echo ""
    _ask "Add another email account? [y/N]:"
    read -r more
    if [[ "${more:-n}" =~ ^[Yy]$ ]]; then
        add_account || true
    else
        break
    fi
done

# ── Result ────────────────────────────────────────────────

if [[ -f "$ACCOUNTS_FILE" ]]; then
    count=$(python3 -c "
import yaml
from pathlib import Path
data = yaml.safe_load(Path('$ACCOUNTS_FILE').read_text()) or {}
print(len(data.get('accounts', [])))
" 2>/dev/null || echo "0")

    if [[ "$count" -gt 0 ]]; then
        echo ""
        _ok "$count email account(s) configured"
        setup_complete "$NAME"
    else
        setup_failed "$NAME" "no accounts added"
    fi
else
    setup_failed "$NAME" "no accounts added"
fi
