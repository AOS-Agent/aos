# Domain-Specific Examples

Reference examples for how step-by-step decomposition looks across different task types. Read this file when you need inspiration for how to scope, structure parts, write acceptance criteria, and flag research needs.

---

## Infrastructure / System Config

**Task:** "Set up Redis with persistence and monitoring"

```
  SCOPE ─── Redis Setup
  ─────────────────────────────────

  1  Install & configure            S
  2  LaunchAgent                    S   ← 1
  3  Health endpoint                S   ← 2
  4  Dashboard widget               S   ← 3

  📋 4 parts  ·  as we go
```

**Example acceptance criteria for Part 1:**
- `redis-cli ping` returns `PONG`
- `cat /opt/homebrew/etc/redis.conf | grep appendonly` shows `yes`
- `redis-cli CONFIG GET save` returns non-empty save schedule
- Data survives `brew services restart redis`

**Example readiness signals:**
- Part 1: ⚡ Ready (standard Homebrew install)
- Part 4: 🔍 Needs research (how does the dashboard widget system work? need to check existing widget pattern)

**Example backward verification after Part 3:**
- Re-check Part 1: `redis-cli ping` → PONG ✅ (health endpoint config didn't break Redis)
- Re-check Part 2: `launchctl list | grep redis` → running ✅

---

## Code / Refactoring

**Task:** "Refactor the bridge to support multiple messaging platforms"

```
  SCOPE ─── Bridge Multi-Platform Refactor
  ─────────────────────────────────

  1  Extract interface              M
  2  Adapter pattern                M   ← 1
  3  WhatsApp adapter               L   ← 2  ⚠️ may split
  4  Router                         M   ← 2,3
  5  Tests                          M   ← 4

  📋 5 parts  ·  as we go
```

**Example acceptance criteria for Part 1:**
- `MessageHandler` protocol defined in `apps/bridge/protocols.py`
- Protocol has `send()`, `receive()`, `health_check()` methods
- Existing Telegram code still passes: `python -m pytest tests/test_bridge.py`
- No direct Telegram imports in `bridge_main.py` (all behind interface)

**Example backward verification after Part 3:**
- Re-check Part 2: `python -m pytest tests/test_telegram_adapter.py` still passes ✅
- Re-check Part 1: `grep "class MessageHandler" apps/bridge/protocols.py` still exists ✅

---

## Business Strategy

**Task:** "Build out the Nuchay launch plan for Q2"

```
  SCOPE ─── Nuchay Q2 Launch Plan
  ─────────────────────────────────

  1  Market positioning             M
  2  Channel strategy               M
  3  Content pipeline               M   ← 1,2
  4  Launch timeline                S   ← 1,2,3
  5  Success metrics                S   ← 4

  📋 5 parts  ·  plan first
```

**Example acceptance criteria for Part 1:**
- Target segment defined with demographics and pain points (written, not just discussed)
- Top 3 differentiators articulated in one sentence each
- Pricing model documented with at least 2 tiers
- Saved to `~/nuchay/docs/positioning.md`

**Readiness signals:**
- Part 1: 🔍 Needs research (competitor pricing, market size data)
- Part 4: ⚡ Ready (just synthesizing decisions from Parts 1-3)

**Note:** Business tasks rarely need backward verification — decisions build on each other but don't break each other. Skip backward checks unless a later decision invalidates an earlier assumption.

---

## Setup / Migration

**Task:** "Migrate from ClickUp to Plane for project management"

```
  SCOPE ─── ClickUp → Plane Migration
  ─────────────────────────────────

  1  Plane instance                 S
  2  Data audit                     M   (parallel with 1)
  3  Schema mapping                 M   ← 1,2
  4  Migration script               L   ← 3  ⚠️ may split
  5  Verification                   M   ← 4
  6  Cutover                        S   ← 5

  📋 6 parts  ·  as we go
```

**Example acceptance criteria for Part 4:**
- Script runs without errors: `python migrate.py --dry-run` exits 0
- Dry run report shows N projects, M tasks matched
- `python migrate.py --execute` completes in < 10 minutes
- Spot-check: 3 random ClickUp tasks found in Plane with correct status and assignee

**Example backward verification after Part 5:**
- Re-check Part 1: `curl http://127.0.0.1:8880/health` → 200 ✅ (migration load didn't crash Plane)
- Re-check Part 4: re-run `python migrate.py --dry-run` → same counts ✅ (verification didn't corrupt data)

---

## App Development

**Task:** "Add push notifications to the Chief iOS app"

```
  SCOPE ─── Push Notifications for Chief
  ─────────────────────────────────

  1  APNs setup                     M
  2  Server integration             M   ← 1
  3  Client handling                M   ← 1
  4  Notification types             M   ← 2,3
  5  Testing                        S   ← 4

  📋 5 parts  ·  as we go
```

**Example acceptance criteria for Part 1:**
- Push Notifications capability enabled in Xcode project
- `*.entitlements` file contains `aps-environment` key
- APNs key uploaded and key ID stored in `agent-secret`
- Test push via `curl` to APNs sandbox returns 200

**Readiness signals:**
- Part 1: 🔍 Needs research (which APNs auth method — key vs certificate? Check Apple's current recommendation)
- Part 3: ⚡ Ready (standard UNUserNotificationCenter pattern)

**Goal-backward check example:**
After all 5 parts done — send a real push from AOS to the phone. Does the notification appear? Does tapping it open the right screen? This is the goal check that catches wiring gaps between server (Part 2) and client (Part 3).
