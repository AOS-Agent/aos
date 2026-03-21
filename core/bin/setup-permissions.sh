#!/bin/bash
# ============================================================================
# setup-permissions.sh — One-time macOS automation permission setup
# ============================================================================
# Run this script from EACH context that will use AppleScript/osascript:
#   1. Terminal.app (covers Terminal + SSH sessions)
#   2. VS Code terminal (covers VS Code)
#   3. LaunchAgent context (run via: launchctl kickstart gui/$(id -u)/com.agent.permissions)
#
# Each app will trigger a permission dialog. Click "Allow" for each one.
# This only needs to happen ONCE per context — macOS remembers the choice.
# ============================================================================

set -e

APPS=(
    "Finder"
    "Notes"
    "Safari"
    "Google Chrome"
    "Calendar"
    "Mail"
    "Messages"
    "System Settings"
    "Terminal"
    "Reminders"
    "Music"
    "Preview"
)

echo "============================================"
echo "  Mac Mini Agent — Permission Setup"
echo "============================================"
echo ""
echo "This will trigger macOS permission dialogs"
echo "for each app the agent needs to control."
echo ""
echo "→ Click 'Allow' on EVERY dialog that appears."
echo "→ If no dialog appears, permission is already granted."
echo ""
echo "Running from: $(ps -p $PPID -o comm= 2>/dev/null || echo "unknown")"
echo "User: $(whoami)"
echo "Context: ${TERM_PROGRAM:-terminal}"
echo ""
echo "============================================"
echo ""

GRANTED=0
TOTAL=${#APPS[@]}

for app in "${APPS[@]}"; do
    echo -n "  Requesting: $app ... "

    # First activate the app (triggers basic permission)
    if osascript -e "tell application \"$app\" to activate" 2>/dev/null; then
        sleep 0.5

        # Then try to read something (triggers full AppleEvents permission)
        osascript -e "tell application \"$app\" to get name" 2>/dev/null && {
            echo "✓ granted"
            GRANTED=$((GRANTED + 1))
        } || {
            echo "⚠ dialog shown — click Allow"
            GRANTED=$((GRANTED + 1))
        }
    else
        echo "✗ app not found (skipping)"
    fi
done

echo ""
echo "============================================"
echo "  Automation permissions: $GRANTED / $TOTAL apps"
echo "============================================"
echo ""

# Now request Accessibility + Screen Recording awareness
echo "MANUAL STEPS (if not already done):"
echo ""
echo "  System Settings → Privacy & Security → Accessibility"
echo "    → Add: Terminal, Visual Studio Code"
echo ""
echo "  System Settings → Privacy & Security → Screen Recording"
echo "    → Add: Terminal, Visual Studio Code"
echo ""
echo "  System Settings → Privacy & Security → Automation"
echo "    → Verify all apps show as allowed"
echo ""

# Close apps we opened (except Finder which is always running)
echo "Closing apps that were opened for permission triggers..."
for app in "${APPS[@]}"; do
    if [ "$app" != "Finder" ] && [ "$app" != "Terminal" ] && [ "$app" != "System Settings" ]; then
        osascript -e "tell application \"$app\" to quit" 2>/dev/null || true
    fi
done

echo ""
echo "Done. Run this script again from other contexts"
echo "(Terminal, SSH, LaunchAgent) to cover all access paths."
echo ""
