#!/usr/bin/env bash
# ─── Obsidian / Vault Integration Setup ───────────────────
# Verifies the knowledge vault and QMD search are working.
# Vault is created by install.sh — this script validates.
#
# Usage:
#   setup.sh              — verify and report
#   setup.sh --check      — same (always non-destructive)

source "$(dirname "$0")/../_lib.sh"

NAME="obsidian"
echo ""
echo "${BOLD}Obsidian Vault${RESET} — knowledge layer"
echo ""

errors=0

# ── Vault directory ───────────────────────────────────────

VAULT_PATH="${HOME}/vault"

if [[ -d "$VAULT_PATH" ]]; then
    note_count=$(find "$VAULT_PATH" -name "*.md" -maxdepth 3 2>/dev/null | wc -l | tr -d ' ')
    _ok "Vault exists at $VAULT_PATH ($note_count notes)"
else
    if $IS_CHECK; then
        _fail "Vault directory not found at $VAULT_PATH"
        ((errors++))
    else
        mkdir -p "$VAULT_PATH"/{daily,knowledge,log,ops/sessions,reviews}
        _ok "Created vault at $VAULT_PATH"
    fi
fi

# ── Vault structure ───────────────────────────────────────

for subdir in daily knowledge log; do
    if [[ -d "$VAULT_PATH/$subdir" ]]; then
        _ok "Vault/$subdir"
    else
        if ! $IS_CHECK; then
            mkdir -p "$VAULT_PATH/$subdir"
            _ok "Created vault/$subdir"
        else
            _warn "Missing vault/$subdir"
            ((errors++))
        fi
    fi
done

# ── QMD search ────────────────────────────────────────────

QMD_BIN="$HOME/.bun/bin/qmd"

if [[ -f "$QMD_BIN" ]] || command -v qmd &>/dev/null; then
    _ok "QMD installed"

    # Test a query
    if "$QMD_BIN" query "test" -n 1 &>/dev/null 2>&1; then
        _ok "QMD search working"
    else
        _warn "QMD installed but query failed (may need: cd ~/vault && qmd index)"
        ((errors++))
    fi
else
    _fail "QMD not installed (run: bun install -g qmd)"
    ((errors++))
fi

# ── Obsidian app (optional) ──────────────────────────────

if [[ -d "/Applications/Obsidian.app" ]]; then
    _ok "Obsidian app installed"
else
    _info "Obsidian app not installed (optional — vault works without it)"
fi

# ── Config file ───────────────────────────────────────────

OBSIDIAN_CONFIG="$USER_DIR/config/obsidian.yaml"

if [[ -f "$OBSIDIAN_CONFIG" ]]; then
    _ok "Config exists"
else
    if ! $IS_CHECK; then
        mkdir -p "$(dirname "$OBSIDIAN_CONFIG")"
        cat > "$OBSIDIAN_CONFIG" << YAML
vault_path: ~/vault/
daily_notes_folder: daily/
YAML
        _ok "Config created"
    else
        _warn "Config missing at $OBSIDIAN_CONFIG"
        ((errors++))
    fi
fi

# ── Result ────────────────────────────────────────────────

echo ""
if [[ $errors -eq 0 ]]; then
    _ok "${BOLD}Obsidian vault — all checks passed${RESET}"
    state_set "$NAME" "active"
else
    _fail "${BOLD}Obsidian vault — $errors check(s) failed${RESET}"
    state_set "$NAME" "failed"
fi

exit $errors
