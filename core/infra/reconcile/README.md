# Reconcile

Self-healing system. Runs 19 checks every 2 hours to verify AOS health: services are up, symlinks intact, configs present, LaunchAgents loaded, disk space sufficient. Each failed check attempts a fix; unresolvable failures are surfaced as alerts.

## Quick Reference
- **Port**: N/A (cron-style runner)
- **Restart**: `launchctl kickstart -k gui/$(id -u)/com.aos.reconcile`
- **Logs**: `~/.aos/logs/reconcile.log`
- **Config**: `~/.aos/config/reconcile.yaml`

## Key Files
- `runner.py` — Loads and executes all checks, aggregates results, triggers alerts
- `base.py` — Base class for checks: `name`, `check()`, `fix()` interface
- `checks/` — One file per check (19 checks total)

## Debugging
- Check if running: `pgrep -f "reconcile/runner.py"`
- Tail logs: `tail -f ~/.aos/logs/reconcile.log`
