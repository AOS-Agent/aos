# Domain examples

How scope, criteria, readiness, and the backward check look across six domains. Read when scoping a task in a domain you have not decomposed before. Criteria are shown in the form they are stored — `sbs criteria --set` lines — so the runnable shape is the default.

---

## Infrastructure / system config

**Task:** "Set up Redis with persistence and monitoring"

```
## Scope: Redis Setup
1. **Install & configure** (S) — Homebrew install, AOF on, save schedule
2. **LaunchAgent** (S) — survives login · depends on 1
3. **Health endpoint** (S) — /health reports PONG + persistence · depends on 2
4. **Dashboard widget** (S) — status tile · depends on 3
```

Part 1 criteria:
```
--set '$ redis-cli ping :: PONG'
--set '$ redis-cli CONFIG GET appendonly :: yes'
--set '$ brew services restart redis && sleep 2 && redis-cli GET smoke :: 1'
```

Readiness: Part 1 ⚡ Ready (standard install) · Part 4 🔍 Needs research (how do existing widgets register? read one first).

Backward check after Part 3: `sbs verify <parent>` re-runs `redis-cli ping` and the LaunchAgent check — the health endpoint did not disturb either.

---

## Code / refactoring

**Task:** "Refactor the bridge to support multiple messaging platforms"

```
## Scope: Bridge Multi-Platform
1. **Extract interface** (M) — MessageHandler protocol
2. **Telegram adapter** (M) — existing code behind the interface · depends on 1
3. **WhatsApp adapter** (M) — split from an L: transport now, media later · depends on 1
4. **Router** (M) — dispatch by platform · depends on 2, 3
5. **Tests** (S) — cross-platform suite · depends on 4
```

Part 1 criteria:
```
--set '$ grep -c "class MessageHandler(Protocol)" apps/bridge/protocols.py :: 1'
--set '$ grep -c "def send\|def receive\|def health_check" apps/bridge/protocols.py :: 3'
--set '$ python -m pytest tests/test_bridge.py -q :: passed'
--set '$ grep -c "import telegram" apps/bridge/bridge_main.py :: 0'
```

Parts 2 and 3 share no edge — dispatch both to background agents in worktrees while the operator reads the Part 4 brief.

---

## Business strategy

**Task:** "Build out the Nuchay launch plan for Q2"

```
## Scope: Nuchay Q2 Launch
1. **Market positioning** (M) — segment, differentiators, pricing tiers
2. **Channel strategy** (M) — where the segment already is
3. **Content pipeline** (M) — cadence and owners · depends on 1, 2
4. **Launch timeline** (S) — dated milestones · depends on 3
5. **Success metrics** (S) — targets per channel · depends on 4
```

Part 1 criteria — mostly manual gates, with the artefact check runnable:
```
--set '$ test -s ~/vault/knowledge/nuchay/positioning.md'
--set '$ grep -c "^## Tier" ~/vault/knowledge/nuchay/positioning.md :: 2'
--set 'target segment named with demographics and the pain it pays to remove'
--set 'three differentiators, one sentence each'
```

Rhythm: suggest *plan first* — decisions here build on each other, and the operator wants the whole shape before committing. Backward checks rarely apply; a later decision invalidating an earlier assumption is the one case.

---

## Setup / migration

**Task:** "Migrate from ClickUp to Plane"

```
## Scope: ClickUp → Plane
1. **Plane instance** (S) — running, reachable
2. **Data audit** (M) — what exists, what maps · parallel with 1
3. **Schema mapping** (M) — field by field · depends on 1, 2
4. **Migration script** (M) — split from an L: dry-run now, execute in 5 · depends on 3
5. **Execute + verify** (M) — spot-checks · depends on 4
6. **Cutover** (S) — redirect, archive · depends on 5
```

Part 4 criteria:
```
--set '$ python migrate.py --dry-run :: exit 0'
--set '$ python migrate.py --dry-run | grep -c "projects:" :: 1'
--set 'dry-run counts match the audit from Part 2'
```

Backward check after Part 5: `curl http://127.0.0.1:8880/health :: 200` (the load did not crash Plane) and the dry-run counts are unchanged (verification did not mutate data).

---

## App development

**Task:** "Add push notifications to the Chief iOS app"

```
## Scope: Push Notifications
1. **APNs setup** (M) — capability, key, entitlement
2. **Server integration** (M) — send from AOS · depends on 1
3. **Client handling** (M) — UNUserNotificationCenter, deep link · depends on 1
4. **Notification types** (M) — payload contract · depends on 2, 3
5. **Device test** (S) — real push, tap opens the right screen · depends on 4
```

Part 1 criteria:
```
--set '$ grep -c aps-environment Chief/Chief.entitlements :: 1'
--set '$ agent-secret get APNS_KEY_ID | wc -c | xargs test 0 -lt'
--set '$ curl -s -o /dev/null -w "%{http_code}" --http2 https://api.sandbox.push.apple.com/3/device/TEST :: 400'
```

Readiness: Part 1 🔍 Needs research (key vs certificate — check Apple's current guidance) · Part 3 ⚡ Ready.

Goal-backward at POLISH: send a real push from AOS to the phone and tap it. This is what catches a wiring gap between Parts 2 and 3 that every per-part criterion passed.

---

## Design

**Task:** "Design the client shell for the Qren app — v1"

```
## Scope: Qren Client Shell v1
1. **Frame** (S) — viewport, nav model, density rules
2. **Design language** (M) — type scale, colour tokens, spacing · depends on 1
3. **Shell screens** (M) — home, switcher, settings IA · depends on 2
4. **States** (S) — empty, loading, error per screen · depends on 3
5. **Handoff doc** (S) — one file a builder can start from · depends on 4
```

Part 2 criteria — artefacts are checkable even when taste is not:
```
--set '$ test -s design/tokens.css'
--set '$ grep -c "^  --" design/tokens.css | xargs test 24 -le'
--set '$ grep -c "font-size" design/tokens.css | xargs test 5 -le'
--set 'type scale reads at 390px without a six-line wrap on the headline'
--set 'colour tokens pass contrast on both themes (checked in the browser)'
```

Readiness: Part 3 🔍 Needs research — pull two or three reference apps (`mobbin-search`) before the brief, so the approach names a pattern rather than inventing one.
