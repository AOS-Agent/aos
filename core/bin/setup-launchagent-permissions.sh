#!/bin/bash
# ============================================================================
# setup-launchagent-permissions.sh
# ============================================================================
# Creates a temporary LaunchAgent that runs setup-permissions.sh
# from the launchd context, so permissions are granted to LaunchAgents.
#
# Usage: Run this script, then click Allow on all dialogs.
# The temporary LaunchAgent is removed after completion.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/com.agent.setup-permissions.plist"
LOG_PATH="$HOME/aos/logs/permissions-setup.log"

echo "Creating temporary LaunchAgent for permission setup..."

cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.setup-permissions</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT_DIR}/setup-permissions.sh</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_PATH}</string>
</dict>
</plist>
EOF

echo "Loading LaunchAgent..."
launchctl load "$PLIST_PATH" 2>/dev/null || true

echo "Triggering permission setup from LaunchAgent context..."
echo "→ Click 'Allow' on every dialog that appears."
echo ""
launchctl start com.agent.setup-permissions

# Wait for it to finish
sleep 15

echo ""
echo "Checking log output..."
echo "---"
cat "$LOG_PATH" 2>/dev/null || echo "(no output yet — may still be running)"
echo "---"

echo ""
echo "Cleaning up temporary LaunchAgent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
rm -f "$PLIST_PATH"

echo "Done. LaunchAgent permissions should now be granted."
echo "Verify in System Settings → Privacy & Security → Automation"
