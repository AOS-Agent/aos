# AOS Changelog

## v0.2.0 — 2026-03-22

Added: onboarding v2 — conversation-first with Sahib
Added: morning ramble system — voice note → tasks
Added: 7-day learning drip via Telegram
Added: versioned releases with silent auto-updates
Changed: 35+ fixes from ship-readiness audit

## v0.2.0 — 2026-03-22

Added: onboarding v2 — conversation-first flow with Sahib personality, SuperWhisper ramble, and system education at each phase
Added: morning ramble system — Telegram sends personalized prompt, operator replies with voice note, system extracts tasks/ideas and saves to vault
Added: 7-day learning drip — daily Telegram tips teaching one system feature per day (vault, work system, agents, dashboard, etc.)
Added: agent renaming — `aos rename-agent <name>` creates symlink alias, persists in operator.yaml, works across cld and Telegram
Added: AirDrop connect script — generates personalized `connect-to-aos.sh` for operator's MacBook with SSH config, Screen Sharing shortcut, and dashboard bookmark
Added: screen sharing alongside SSH — enabled during install, desktop shortcut in connect script
Added: `aos repair` command — pulls latest, rebuilds all venvs, migrates secrets, reloads services in one shot
Added: `aos-release` command — tags version, merges dev to main, pushes with changelog, users auto-update at 4am
Added: ramble skill — conversational voice/text processor that accumulates tasks, ideas, thoughts across multiple messages with reclassification
Added: reboot recovery — scheduler detects reboot via kern.boottime, reloads LaunchAgents, sends Telegram notification
Added: file locking to work system — fcntl.flock + atomic temp+rename prevents corruption from concurrent sessions
Added: always-on Mac Mini config — no sleep, auto-restart on power loss, auto-login walkthrough, wake on LAN
Added: post-onboarding first session — Chief verifies Telegram, runs morning briefing, reminds about daily practice
Changed: voice transcription to auto-detect backend — tries mlx-whisper (Apple Silicon venv) first, falls back to faster-whisper
Changed: Telegram dispatch to use `--agent chief --chrome --allowedTools *` — full tool access matching interactive cld sessions
Changed: secrets from separate agent.keychain to login keychain — no more password prompts, ever
Changed: service venv creation to find Python 3.11+ (homebrew) and verify imports after installing
Changed: Chief agent tools from whitelist to `tools: "*"` — access to Chrome MCP, AskUserQuestion, WebSearch, everything
Changed: installer to pre-accept trust dialog in ~/.claude.json for ~ and ~/aos
Changed: bridge to read morning briefing and evening check-in times from operator.yaml instead of hardcoding
Fixed: SessionStart hook crash on Python 3.9 — engine.py uses str|None syntax, now caught with except Exception
Fixed: telemetry `local` keyword outside function — crashed bash on the status subcommand
Fixed: telemetry and session-recorder shell injection — replaced '''$var''' with os.environ.get()
Fixed: integration manifests referencing install.sh instead of setup.sh (telegram, whatsapp)
Fixed: specs referencing com.agent.* instead of com.aos.* and apps/ instead of core/services/
Fixed: hardcoded page_size in dashboard RAM calculation — now parsed from vm_stat header, works on Intel
Fixed: PII over-scrub regex — no longer matches git SHAs and UUIDs in feedback reports
Fixed: bridge restart after Telegram credentials stored during onboarding — services pick up new keychain entries
Fixed: hooks format — flat {command} wrapped in correct {hooks: [{type, command}]} structure
Fixed: scheduler shebang from hardcoded /opt/homebrew/bin/python3 to /usr/bin/env python3
Fixed: DB connection leaks in dashboard — added _db() context manager with timeout=10
Removed: NLTK from memory service — was never imported, phantom dependency causing install failures
Removed: evening check-in hardcoding — now configured per-operator during onboarding

## v0.1.0 — 2026-03-21

Initial release — install script, 3 system agents (Chief/Steward/Advisor), work system with project-scoped IDs, dashboard with real-time SSE, bridge (Telegram + Slack + voice transcription), listen job server, memory MCP, 15 default skills, 12+ scheduled cron jobs, knowledge vault with QMD search
