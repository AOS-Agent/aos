# Initiative Pipeline + Bridge v2 — Change Manifest

Tracks every file added or modified for both specs.
Used by migration/reconcile system to ensure all AOS machines get these changes.

## Files Modified

| File | Change | Status |
|------|--------|--------|
| `core/work/engine.py` | Added `source_ref` param to `add_task()` + conditional write (2 lines) | ✅ verified |
| `core/work/cli.py` | Added `source_ref` display in `cmd_show`, added `cmd_initiatives` command | ✅ verified |
| `~/.aos/config/operator.yaml` | Added `initiatives:` config block | ✅ verified |
| `core/work/inject_context.py` | Initiative scanning + merged session-context.json write | ✅ verified |
| `core/work/session_close.py` | Initiative doc update with surgical regex (not yaml.dump) + atomic os.replace | ✅ verified |
| `core/services/bridge/daily_briefing.py` | Bridge v2 BLUF rewrite + initiative section | ✅ verified |
| `core/services/bridge/evening_checkin.py` | Bridge v2 conversational wrap format | ✅ verified |
| `core/services/bridge/intent_classifier.py` | Bridge v2 quick commands: vault search, project-scoped IDs, colon syntax | ✅ verified |
| `core/services/bridge/telegram_channel.py` | Wired quick command intercept before Claude dispatch | ✅ verified |
| `core/services/bridge/message_renderer.py` | Rate-limit message now references quick commands | ✅ verified |
| `core/services/bridge/pyproject.toml` | Updated py-modules list with all bridge modules | ✅ done |
| `config/crons.yaml` | Added stale-initiatives cron job at 09:00 | ✅ verified |
| `core/reconcile/checks/__init__.py` | Added InitiativeDirectoriesCheck + BridgeTopicsCheck | ✅ verified |

## Files Added

| File | Purpose | Status |
|------|---------|--------|
| `core/lib/__init__.py` | Package init for shared libs | ✅ done |
| `core/lib/notify.py` | Shared Telegram notification helper (splitting, backoff, fallback) | ✅ verified |
| `core/lib/CHANGES-initiative-pipeline.md` | This manifest | ✅ done |
| `core/services/bridge/shared_context.py` | Cross-session decision store (Bridge v2) | ✅ verified |
| `core/services/bridge/topic_manager.py` | Progressive forum topic management (Bridge v2) | ✅ verified |
| `core/migrations/017_bridge_topics.py` | Migration: bootstrap bridge-topics.yaml + daily topic | ✅ created |
| `core/migrations/018_initiative_infrastructure.py` | Migration: vault dirs + initiatives config in operator.yaml | ✅ created |
| `core/reconcile/checks/initiatives.py` | Reconcile checks for initiative dirs + bridge topics | ✅ created |
| `core/bin/stale-initiatives` | Cron script: scan stale initiatives, Telegram nudge | ✅ created |

## Directories Created

| Path | Purpose | Status |
|------|---------|--------|
| `core/lib/` | Shared library modules | ✅ done |
| `~/vault/knowledge/expertise/` | Expertise accumulation (data layer) | ✅ done |
| `~/vault/ideas/` | Idea capture (data layer) | ✅ done |

## Config Files Created (user data, never in git)

| Path | Purpose | Status |
|------|---------|--------|
| `~/.aos/config/bridge-topics.yaml` | Forum topic thread IDs | Created by migration 017 |
| `~/.aos/data/bridge/shared-context.json` | Cross-session decision store | Created on first use |

## Migration/Reconcile Needed

| Type | # | Description | Status |
|------|---|-------------|--------|
| Migration | 017 | Bootstrap bridge-topics.yaml from projects.yaml, create daily topic | ✅ created |
| Migration | 018 | Create vault dirs (expertise, ideas), create core/lib, add initiatives to operator.yaml | ✅ created |
| Reconcile | initiative_directories | Verify initiative directories exist on data layer | ✅ created |
| Reconcile | bridge_topics_config | Verify bridge-topics.yaml exists and is valid | ✅ created |
| Cron | stale-initiatives | Scan stale initiatives daily at 09:00, Telegram nudge | ✅ created |

## Key Design Decisions

1. **session_close uses regex, not yaml.dump** — yaml.dump destroys frontmatter formatting. Surgical `re.sub` on the `updated:` line only.
2. **inject_context merges initiative_ids into single .session-context.json write** — prevents BUG 1 (double-write overwrite).
3. **inject_context uses find() not index()** — prevents BUG 2 (ValueError on malformed frontmatter).
4. **shared_context.py uses tempfile + os.replace()** — atomic writes, never corrupts on crash.
5. **topic_manager creates topics progressively** — only `daily` on first setup, others on-demand.
6. **notify.py is stdlib-only** — uses urllib.request, no httpx dependency. Can be called from hooks.
