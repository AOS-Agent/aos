"""
Migration 134: harden Google OAuth credential files (aos#2326) and finish
the workspace-mcp -> gws consolidation migration 059 started.

Background
----------
aos#2326 found the Google OAuth refresh token — a live, long-lived
credential granting Gmail/Drive/Calendar access — sitting in a plaintext
JSON file. The house rule is "secrets in macOS Keychain only"
(agent-secret get/set), so this looked like a straightforward violation.

It isn't, quite. Tracing every consumer (core/infra/integrations/
google-workspace/manifest.yaml, core/bin/internal/gws-account, migration
059, core/infra/reconcile/checks/google_workspace.py):

  - `gws` — the third-party Homebrew CLI that actually talks to Google — is
    invoked by `gws-account` with `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`
    pointed at `~/.aos/config/google/credentials/<email>.json`. gws reads
    (and on token refresh, rewrites) that file itself, in its own format.
    We do not control gws's internals, so the refresh token cannot move
    into Keychain without breaking gws outright.

So the honest fix is the file, not Keychain: mode 0600, excluded from
backup/sync, and a reconcile check (google_workspace.py, this same release)
that NOTIFIES — never silently re-chmods — on drift.

This migration:

  1. Finishes what 059 started. 059's own docstring says the old
     `~/.google_workspace_mcp/credentials/` directory was "left in place
     for one update cycle as a rollback safety net" — but no later
     migration ever removed it, so a machine that ran 059 could be
     carrying two on-disk copies of the same refresh token indefinitely.
     Any legacy token not yet present in the new location is converted
     (same client_id/client_secret/refresh_token -> authorized_user shape
     059 already uses); the legacy directory is then removed.
  2. Hardens every credential file in the new location to 0600, however
     it got there (059's conversion, this migration's conversion, or the
     operator's own manual `gws auth login` step from the integration's
     setup instructions).
  3. Best-effort excludes the credentials directory from Time Machine via
     the `com.apple.metadata:com_apple_backup_excludeItem` xattr. Never
     fatal: a machine without `xattr` (Linux CI, a stripped-down sandbox)
     just skips this — there is nothing else in this tree that backs up
     `~/.aos/config` wholesale (config/storage.yaml's relocations list and
     backup-comms both leave it alone), so this is defense in depth, not a
     fix for an active leak.

Not shredded in the sense of a secure multi-pass overwrite — APFS is
copy-on-write, so that guarantee doesn't really exist on this filesystem
even if you write the code for it. It's `shutil.rmtree`, matching every
other migration's file-removal (see 133).

Idempotent: re-running is a no-op once nothing needs converting and every
credential file is already 0600. Reversible: down() cannot safely re-create
a shredded plaintext secret out of nothing — returns False, same as 131's
archive step and 133's revert.
"""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

DESCRIPTION = (
    "Harden Google OAuth credential files (0600, Time-Machine-excluded); "
    "finish the workspace-mcp -> gws directory consolidation migration 059 started"
)

TM_EXCLUDE_ATTR = "com.apple.metadata:com_apple_backup_excludeItem"


# Resolved on every call, never captured at import — a module-level
# `Path.home()` here would freeze whichever machine (or sandboxed test HOME)
# happened to import this module first, for the rest of the process (see
# migration 133's own note on this same pattern).
def _old_creds_dir() -> Path:
    return Path.home() / ".google_workspace_mcp" / "credentials"


def _new_creds_dir() -> Path:
    return Path.home() / ".aos" / "config" / "google" / "credentials"


def _is_hardened(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) == 0o600


def _harden_file(path: Path) -> None:
    path.chmod(0o600)


def _exclude_from_backup(dir_path: Path) -> None:
    """Best-effort Time Machine exclusion. Never raises — a machine without
    `xattr` (non-macOS CI, a stripped sandbox) just skips it."""
    try:
        subprocess.run(
            ["xattr", "-w", TM_EXCLUDE_ATTR, "1", str(dir_path)],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass


def check() -> bool:
    old_dir = _old_creds_dir()
    new_dir = _new_creds_dir()

    if old_dir.exists():
        return False  # still has (or is) the superseded legacy directory

    if new_dir.is_dir():
        for f in new_dir.glob("*.json"):
            if not _is_hardened(f):
                return False

    return True


def up() -> bool:
    old_dir = _old_creds_dir()
    new_dir = _new_creds_dir()
    results = []
    failed = False

    # 1. Convert any legacy token not already present in the new location.
    if old_dir.is_dir():
        new_dir.mkdir(parents=True, exist_ok=True)
        for token_file in sorted(old_dir.glob("*.json")):
            dest = new_dir / token_file.name
            if dest.exists():
                results.append(f"Skipped {token_file.stem} (already converted)")
                continue
            try:
                data = json.loads(token_file.read_text())
                gws_cred = {
                    "client_id": data["client_id"],
                    "client_secret": data["client_secret"],
                    "refresh_token": data["refresh_token"],
                    "type": "authorized_user",
                }
                dest.write_text(json.dumps(gws_cred, indent=2) + "\n")
                dest.chmod(0o600)
                results.append(f"Converted {token_file.stem} from legacy workspace-mcp store")
            except (KeyError, json.JSONDecodeError) as e:
                results.append(f"Failed to convert {token_file.stem}: {e}")
                failed = True

    # 2. Harden every credential file that ended up in the new location —
    #    whether via 059, step 1 above, or a manual `gws auth login` import.
    if new_dir.is_dir():
        for f in sorted(new_dir.glob("*.json")):
            if not _is_hardened(f):
                _harden_file(f)
                results.append(f"chmod 0600 {f.name}")

        try:
            new_dir.chmod(0o700)
        except OSError:
            pass

        if any(new_dir.glob("*.json")):
            _exclude_from_backup(new_dir)
            results.append("requested Time Machine exclusion for credentials dir")

    # 3. Remove the now-fully-superseded legacy directory. Only once every
    #    token in it converted cleanly — a failed conversion must never lose
    #    data, so the legacy copy stays until it's fixed and re-run.
    if old_dir.exists() and not failed:
        shutil.rmtree(old_dir)
        parent = old_dir.parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
        results.append(f"removed legacy {old_dir}")

    print("; ".join(results) if results else "No changes needed")
    return not failed


def down() -> bool:
    return False


if __name__ == "__main__":
    print("Migration 134 already applied" if check() else ("Done" if up() else "Failed"))
