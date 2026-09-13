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
- `checks/` — One file per check
- `alert_copy.py` — Humanizes a finding into the one-line Telegram/inbox copy
- `inbox_sink.py` — Gives a NOTIFY a consumer (aos#239): syncs it into the work
  inbox as one deduplicated, auto-closing row instead of a log line nobody
  reads. See its module docstring for the dedup/fingerprint contract.

## The inbox sink

A NOTIFY that only reaches `~/.aos/logs/reconcile.jsonl` has no consumer —
several checks (`dead_code`, `storage_layout`, `vault_contract`,
`instance_hygiene`, `arms_coverage`, `context_freshness`) measured NOTIFY on
essentially every run for months with nobody ever acting on one. `run_all()`
now calls `inbox_sink.sync(results)` after logging: every `Status.NOTIFY`
becomes (or updates) one `work inbox` row tagged `[reconcile]`, keyed by check
name plus a stable fingerprint of the humanized finding — a repeat NOTIFY bumps
that row's count/last_seen instead of adding another, and a check that
resolves (OK or auto-FIXED) has its row dropped. This is best-effort: a broken
`work.db` never takes a reconcile run down with it. `~/.aos/logs/reconcile.jsonl`
and the Telegram path are unchanged.

## Debugging
- Check if running: `pgrep -f "reconcile/runner.py"`
- Tail logs: `tail -f ~/.aos/logs/reconcile.log`
