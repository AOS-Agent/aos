---
title: Framework Lockdown & Dev Access Control
type: spec
date: 2026-04-10
tags: [security, access-control, runtime, dev-workspace]
status: draft
---

# Framework Lockdown & Dev Access Control

## Problem

1. `~/aos/` (runtime) is writable — users can accidentally edit framework files instead of working in the dev workspace. Edits get overwritten on next update with no warning.
2. `~/project/aos/` (dev workspace) has no access control — anyone with machine access can read and modify the AOS source.

## Part 1: Runtime Lock (`~/aos/`)

### Mechanism

Use macOS `chflags uchg` (user immutable flag) on all files in `~/aos/` after every update. Files become read-only even to the owner. Editors show "read-only" errors. Requires explicit `chflags nouchg` to undo.

### Update flow

```
chflags -R nouchg ~/aos/          # unlock
git pull                           # update
python3 -m compileall ~/aos/       # pre-compile .pyc
run migrations                     # one-shot structural changes
run reconcile                      # fix drift
chflags -R uchg ~/aos/             # relock
```

### Gotchas & mitigations

| Concern | Mitigation |
|---------|------------|
| Git pull needs write access | Unlock before pull, relock after |
| Python writes `.pyc` at runtime | Pre-compile during update. Set `PYTHONDONTWRITEBYTECODE=1` in service LaunchAgent envs |
| Services need to read files | chflags only blocks writes, read access unaffected |
| Reconcile/migrations write files | They run inside the unlock window |
| User manually unlocks with `chflags nouchg` | Reconcile check detects unlocked state and re-locks on next cycle |
| `.git/` internals need write for git operations | Lock only tracked files, not `.git/` directory |

### Reconcile check

Add `checks/framework_lock.py`:
- Verify `~/aos/` files have `uchg` flag set
- If not, re-lock and log a warning
- Runs every update cycle

### User experience

- Attempting to edit `~/aos/` → editor shows read-only error
- Clear error message guides them to `~/project/aos/`
- `aos unlock` escape hatch for emergencies (requires confirmation)

## Part 2: Dev Workspace Access Control (`~/project/aos/`)

### Recommended approach: Encrypted sparsebundle + GitHub permissions

#### Local: Encrypted disk image

Create an encrypted APFS sparse bundle that mounts to `~/project/aos/`.

```bash
# One-time creation
hdiutil create -size 10g -type SPARSEBUNDLE -fs APFS \
  -encryption AES-256 -volname "AOS-Dev" \
  -stdinpass ~/project/.aos-dev.sparsebundle

# Mount (prompts for password)
aos dev open
# → hdiutil attach ~/project/.aos-dev.sparsebundle -mountpoint ~/project/aos/

# Unmount
aos dev close
# → hdiutil detach ~/project/aos/
```

- **Mounted**: full read/write access, normal git workflow
- **Unmounted**: directory is empty/gone, code is encrypted at rest
- Password stored nowhere — must be known by authorized developers
- Keychain integration optional (convenience vs security tradeoff)

#### Remote: GitHub private repo

- Private repository, team-based access
- Only approved GitHub accounts can clone or push
- Branch protection on `main`: require PR reviews, no force push
- Doesn't protect local files but controls distribution

#### CLI interface

```bash
aos dev open      # Mount encrypted volume, prompt for password
aos dev close     # Unmount, code disappears
aos dev status    # Show mount state
```

### Alternative approaches (considered, not recommended)

| Approach | Why not |
|----------|---------|
| macOS ACLs / separate user | Clunky user-switching, doesn't encrypt at rest |
| Git-crypt | Only encrypts marked files, not full directory protection |
| FileVault | Whole-disk, doesn't isolate AOS specifically |
| APFS encrypted volume | Heavier than sparsebundle, harder to back up |

## Implementation order

1. **Runtime lock** — wire `chflags` into `check-update`, add reconcile check, add `PYTHONDONTWRITEBYTECODE=1` to service envs
2. **Dev workspace encryption** — create sparsebundle, build `aos dev` CLI, migrate existing workspace into it
3. **GitHub permissions** — configure branch protection, audit team access

## Open questions

- Should `aos unlock` require a password or just confirmation?
- Keychain integration for dev workspace password — convenience vs security?
- How to handle CI/CD if we encrypt the dev workspace? (CI uses GitHub, so probably fine)
- Backup strategy for the encrypted sparsebundle
