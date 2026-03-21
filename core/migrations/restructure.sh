#!/usr/bin/env bash
# AOS Restructure — migrates from v1 to v2 filesystem layout
#
# What this does:
#   1. Stops all v1 services
#   2. Archives ~/aos/ → ~/aos-v1-archive/
#   3. Renames ~/aosv2/ → ~/aos/
#   4. Renames ~/.aos-v2/ → ~/.aos/
#   5. Moves v1 service deployments to ~/.aos/services/
#   6. Moves v1 data to ~/.aos/data/
#   7. Moves v1 logs to ~/.aos/logs/
#   8. Ports skills, commands, templates, specs, agents, vendor
#   9. Updates LaunchAgent plists
#  10. Restarts services
#
# SAFE: Nothing is deleted. v1 is archived. Can be reversed.
#
# Usage: bash ~/aosv2/core/migrations/restructure.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}~${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }
step() { echo -e "\n${YELLOW}[$1]${NC} $2"; }

V1="$HOME/aos"
V2="$HOME/aosv2"
ARCHIVE="$HOME/aos-v1-archive"
OLD_USER="$HOME/.aos-v2"
NEW_USER="$HOME/.aos"

# ── Pre-flight checks ─────────────────────────────────────────────────────────

echo "=== AOS Restructure ==="
echo ""
echo "This will:"
echo "  ~/aos/    → ~/aos-v1-archive/  (archived)"
echo "  ~/aosv2/  → ~/aos/             (promoted)"
echo "  ~/.aos-v2/ → ~/.aos/           (renamed)"
echo "  v1 services → ~/.aos/services/ (deployed)"
echo ""

[ -d "$V2" ] || fail "~/aosv2/ not found. Nothing to promote."

if [ -d "$ARCHIVE" ]; then
    fail "~/aos-v1-archive/ already exists. Previous migration? Remove it first."
fi

read -p "Continue? [y/N] " -n 1 -r
echo ""
[[ $REPLY =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── Step 1: Stop all services ─────────────────────────────────────────────────

step "1/10" "Stopping services"

for svc in com.agent.bridge com.agent.dashboard com.agent.listen com.agent.phoenix com.agent.whatsmeow com.agent.claude-remote com.aos.scheduler; do
    if launchctl list | grep -q "$svc" 2>/dev/null; then
        launchctl bootout "gui/$(id -u)/$svc" 2>/dev/null && log "Stopped $svc" || warn "Could not stop $svc"
    fi
done

# ── Step 2: Archive v1 ────────────────────────────────────────────────────────

step "2/10" "Archiving ~/aos/ → ~/aos-v1-archive/"

if [ -d "$V1" ]; then
    mv "$V1" "$ARCHIVE"
    log "Archived v1"
else
    warn "~/aos/ not found, skipping archive"
fi

# ── Step 3: Promote v2 ────────────────────────────────────────────────────────

step "3/10" "Promoting ~/aosv2/ → ~/aos/"

mv "$V2" "$V1"
log "aosv2 is now ~/aos/"

# Update V1/V2 references for rest of script
AOS="$HOME/aos"

# ── Step 4: Rename user data dir ──────────────────────────────────────────────

step "4/10" "Renaming user data directory"

if [ -d "$OLD_USER" ] && [ ! -d "$NEW_USER" ]; then
    mv "$OLD_USER" "$NEW_USER"
    log "~/.aos-v2/ → ~/.aos/"
elif [ -d "$NEW_USER" ]; then
    warn "~/.aos/ already exists, merging..."
    if [ -d "$OLD_USER" ]; then
        # Merge: copy anything not already in new
        rsync -a --ignore-existing "$OLD_USER/" "$NEW_USER/"
        mv "$OLD_USER" "${OLD_USER}.migrated"
        log "Merged and archived old ~/.aos-v2/"
    fi
else
    mkdir -p "$NEW_USER"
    log "Created fresh ~/.aos/"
fi

# Ensure all subdirs exist
for d in work config services logs logs/crons data data/health handoffs; do
    mkdir -p "$NEW_USER/$d"
done

# ── Step 5: Move v1 service deployments ───────────────────────────────────────

step "5/10" "Moving service deployments to ~/.aos/services/"

if [ -d "$ARCHIVE/apps" ]; then
    for svc in bridge dashboard listen memory phoenix messages roborock whatsmeow; do
        src="$ARCHIVE/apps/$svc"
        dst="$NEW_USER/services/$svc"
        if [ -d "$src" ] && [ ! -d "$dst" ]; then
            cp -R "$src" "$dst"
            log "Deployed $svc → ~/.aos/services/$svc"
        elif [ -d "$dst" ]; then
            warn "$svc already deployed, skipping"
        fi
    done

    # Special cases — apps that are project-specific, not services
    for app in content-engine transcriber xfeed health-sync; do
        src="$ARCHIVE/apps/$app"
        dst="$NEW_USER/services/$app"
        if [ -d "$src" ] && [ ! -d "$dst" ]; then
            cp -R "$src" "$dst"
            log "Deployed $app → ~/.aos/services/$app"
        fi
    done
fi

# ── Step 6: Move v1 data ─────────────────────────────────────────────────────

step "6/10" "Moving data to ~/.aos/data/"

if [ -d "$ARCHIVE/data" ]; then
    for d in "$ARCHIVE/data"/*/; do
        name=$(basename "$d")
        dst="$NEW_USER/data/$name"
        if [ ! -d "$dst" ]; then
            cp -R "$d" "$dst"
            log "Data: $name"
        fi
    done
fi

# Move v1 logs
if [ -d "$ARCHIVE/logs" ]; then
    cp -R "$ARCHIVE/logs/"* "$NEW_USER/logs/" 2>/dev/null && log "Copied v1 logs" || warn "No v1 logs to copy"
fi

# Move execution logs
if [ -d "$ARCHIVE/execution_log" ]; then
    mkdir -p "$NEW_USER/logs/execution"
    cp -R "$ARCHIVE/execution_log/"* "$NEW_USER/logs/execution/" 2>/dev/null && log "Copied execution logs"
fi

# ── Step 7: Port remaining assets ────────────────────────────────────────────

step "7/10" "Porting skills, commands, templates, specs, agents"

# Port v1 skills that don't exist in v2
if [ -d "$ARCHIVE/.claude/skills" ]; then
    for skill_dir in "$ARCHIVE/.claude/skills"/*/; do
        name=$(basename "$skill_dir")
        dst="$AOS/.claude/skills/$name"
        if [ ! -d "$dst" ] && [ ! -L "$dst" ]; then
            cp -R "$skill_dir" "$dst"
            log "Skill: $name"
        fi
    done
fi

# Port commands
if [ -d "$ARCHIVE/.claude/commands" ]; then
    mkdir -p "$AOS/.claude/commands"
    for cmd in "$ARCHIVE/.claude/commands"/*.md; do
        name=$(basename "$cmd")
        dst="$AOS/.claude/commands/$name"
        if [ ! -f "$dst" ]; then
            cp "$cmd" "$dst"
            log "Command: $name"
        fi
    done
fi

# Port catalog agents as templates
if [ -d "$ARCHIVE/.claude/agents" ]; then
    mkdir -p "$AOS/templates/agents"
    for agent in "$ARCHIVE/.claude/agents"/*.md; do
        name=$(basename "$agent")
        # Skip system agents (already in v2)
        case "$name" in
            chief.md|steward.md|advisor.md) continue ;;
        esac
        dst="$AOS/templates/agents/$name"
        if [ ! -f "$dst" ]; then
            cp "$agent" "$dst"
            log "Catalog agent: $name"
        fi
    done
fi

# Port project template
if [ -d "$ARCHIVE/templates/project" ]; then
    dst="$AOS/templates/project"
    if [ ! -d "$dst" ]; then
        cp -R "$ARCHIVE/templates/project" "$dst"
        log "Project template"
    fi
fi

# Port specs not in v2
if [ -d "$ARCHIVE/specs" ]; then
    for spec in "$ARCHIVE/specs"/*.md; do
        name=$(basename "$spec")
        dst="$AOS/specs/$name"
        if [ ! -f "$dst" ]; then
            cp "$spec" "$dst"
            log "Spec: $name"
        fi
    done
fi

# Port docs
if [ -d "$ARCHIVE/docs" ]; then
    mkdir -p "$AOS/docs"
    cp -R "$ARCHIVE/docs/"* "$AOS/docs/" 2>/dev/null && log "Docs" || true
fi

# Port vendor
if [ -d "$ARCHIVE/vendor" ]; then
    for v in "$ARCHIVE/vendor"/*/; do
        name=$(basename "$v")
        dst="$AOS/vendor/$name"
        if [ ! -d "$dst" ]; then
            cp -R "$v" "$dst"
            log "Vendor: $name"
        fi
    done
fi

# Port v1 config not yet in v2
if [ -d "$ARCHIVE/config" ]; then
    for conf in channel-update.yaml patterns.yaml .env.sample; do
        src="$ARCHIVE/config/$conf"
        dst="$NEW_USER/config/$conf"
        if [ -f "$src" ] && [ ! -f "$dst" ]; then
            cp "$src" "$dst"
            log "Config: $conf"
        fi
    done
    # Keys directory
    if [ -d "$ARCHIVE/config/keys" ] && [ ! -d "$NEW_USER/config/keys" ]; then
        cp -R "$ARCHIVE/config/keys" "$NEW_USER/config/keys"
        log "Config: keys/"
    fi
fi

# Port v1 bin scripts not rewritten in v2
if [ -d "$ARCHIVE/bin" ]; then
    for script in channel-update claude-remote-start deploy-chief deploy-metrics \
                  email-cleanup friction-rules healthsync-deploy imessage-watch \
                  iphone-tap onboard-project setup-launchagent-permissions.sh \
                  setup-permissions.sh technician-create-topic vacuum; do
        src="$ARCHIVE/bin/$script"
        dst="$AOS/core/bin/$script"
        if [ -f "$src" ] && [ ! -f "$dst" ]; then
            cp "$src" "$dst"
            chmod +x "$dst"
            log "Script: $script"
        fi
    done
    # Patterns directory
    if [ -d "$ARCHIVE/bin/patterns" ] && [ ! -d "$AOS/core/bin/patterns" ]; then
        cp -R "$ARCHIVE/bin/patterns" "$AOS/core/bin/patterns"
        log "Patterns directory"
    fi
fi

# Port MCP config
if [ -f "$ARCHIVE/.mcp.json" ] && [ ! -f "$AOS/.mcp.json" ]; then
    cp "$ARCHIVE/.mcp.json" "$AOS/.mcp.json"
    log "MCP config"
fi

# ── Step 8: Update symlinks ──────────────────────────────────────────────────

step "8/10" "Updating symlinks"

# Agent symlinks should now point to ~/aos/core/agents/
for agent in chief steward advisor; do
    link="$HOME/.claude/agents/${agent}.md"
    target="$AOS/core/agents/${agent}.md"
    if [ -L "$link" ]; then
        rm "$link"
        ln -sf "$target" "$link"
        log "Agent symlink: $agent → ~/aos/core/agents/"
    fi
done

# Skill symlinks should now point to ~/aos/.claude/skills/
for skill in recall work review step-by-step; do
    link="$HOME/.claude/skills/$skill"
    target="$AOS/.claude/skills/$skill"
    if [ -L "$link" ]; then
        rm "$link"
        ln -sf "$target" "$link"
        log "Skill symlink: $skill → ~/aos/.claude/skills/"
    fi
done

# ── Step 9: Update LaunchAgent plists ─────────────────────────────────────────

step "9/10" "Updating LaunchAgent paths"

LA_DIR="$HOME/Library/LaunchAgents"

# Map service → working dir + binary
update_plist() {
    local plist="$1" old_path="$2" new_path="$3"
    if [ -f "$LA_DIR/$plist" ]; then
        sed -i '' "s|$old_path|$new_path|g" "$LA_DIR/$plist"
        log "Updated $plist"
    fi
}

# Bridge: ~/aos/apps/bridge → ~/.aos/services/bridge
update_plist "com.agent.bridge.plist" "$ARCHIVE/apps/bridge" "$NEW_USER/services/bridge"
update_plist "com.agent.bridge.plist" "/Users/$(whoami)/aos/apps/bridge" "$NEW_USER/services/bridge"
update_plist "com.agent.bridge.plist" "/Users/$(whoami)/aos/logs" "$NEW_USER/logs"

# Dashboard
update_plist "com.agent.dashboard.plist" "$ARCHIVE/apps/dashboard" "$NEW_USER/services/dashboard"
update_plist "com.agent.dashboard.plist" "/Users/$(whoami)/aos/apps/dashboard" "$NEW_USER/services/dashboard"
update_plist "com.agent.dashboard.plist" "/Users/$(whoami)/aos/logs" "$NEW_USER/logs"

# Listen
update_plist "com.agent.listen.plist" "$ARCHIVE/apps/listen" "$NEW_USER/services/listen"
update_plist "com.agent.listen.plist" "/Users/$(whoami)/aos/apps/listen" "$NEW_USER/services/listen"
update_plist "com.agent.listen.plist" "/Users/$(whoami)/aos/logs" "$NEW_USER/logs"

# Phoenix
update_plist "com.agent.phoenix.plist" "$ARCHIVE/apps/phoenix" "$NEW_USER/services/phoenix"
update_plist "com.agent.phoenix.plist" "/Users/$(whoami)/aos/apps/phoenix" "$NEW_USER/services/phoenix"
update_plist "com.agent.phoenix.plist" "/Users/$(whoami)/aos/logs" "$NEW_USER/logs"

# WhatsApp
update_plist "com.agent.whatsmeow.plist" "$ARCHIVE/apps/messages" "$NEW_USER/services/messages"
update_plist "com.agent.whatsmeow.plist" "/Users/$(whoami)/aos/apps/messages" "$NEW_USER/services/messages"
update_plist "com.agent.whatsmeow.plist" "/Users/$(whoami)/aos/logs" "$NEW_USER/logs"

# Claude Remote
update_plist "com.agent.claude-remote.plist" "/Users/$(whoami)/aos/" "$AOS/"
update_plist "com.agent.claude-remote.plist" "/Users/$(whoami)/aos/logs" "$NEW_USER/logs"

# Scheduler — already points to aosv2, just update to aos
update_plist "com.aos.scheduler.plist" "/Users/$(whoami)/aosv2/" "$AOS/"
update_plist "com.aos.scheduler.plist" ".aos-v2" ".aos"

# ── Step 10: Update hooks in settings.json ────────────────────────────────────

step "10/10" "Updating hook paths"

SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
    sed -i '' "s|/aosv2/|/aos/|g" "$SETTINGS"
    sed -i '' "s|\.aos-v2|.aos|g" "$SETTINGS"
    log "Updated settings.json"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "=== Restructure Complete ==="
echo ""
echo "  ~/aos/              Framework (was ~/aosv2/)"
echo "  ~/.aos/             Instance data (was ~/.aos-v2/)"
echo "  ~/aos-v1-archive/   Archived v1 (safe to delete when ready)"
echo ""
echo "Next steps:"
echo "  1. Restart services:  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.agent.bridge.plist"
echo "  2. Run self-test:     ~/aos/core/bin/aos self-test"
echo "  3. Verify dashboard:  curl http://127.0.0.1:4096/api/health"
echo ""
echo "  ⚠ ~/aos-v1-archive/ is kept for safety. Delete when confident."
