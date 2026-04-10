"""Git snapshot helper — takes a pre-bootstrap commit of the vault.

Clean, reversible. If the vault isn't a git repo, falls back to a
tarball at ~/.aos/backups/vault-bootstrap-<run_id>.tar.gz and returns a
synthetic ref indicating the tarball location.

The goal is ALWAYS to have an exact reference point the operator can
roll back to if compilation produces bad output.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_VAULT_DIR = Path.home() / "vault"
BACKUPS_DIR = Path.home() / ".aos" / "backups"


def _run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=60,
        )
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def take_snapshot(
    run_id: str,
    vault_dir: Path | None = None,
) -> dict[str, str | None]:
    """Take a pre-bootstrap snapshot of the vault.

    Returns:
        {
            "method": "git" | "tarball",
            "git_ref": "abc123..." or None,
            "git_branch": "main" or None,
            "tarball_path": "..." or None,
            "timestamp": "...",
        }

    Never raises — returns a dict even on failure, with method=None and
    an error field. Callers should check method before trusting the snapshot.
    """
    vault = vault_dir or DEFAULT_VAULT_DIR
    ts = datetime.now(timezone.utc).isoformat()

    if not vault.is_dir():
        return {
            "method": None,
            "error": f"vault dir not found: {vault}",
            "timestamp": ts,
        }

    # Try git first
    git_dir = vault / ".git"
    if git_dir.exists():
        return _take_git_snapshot(run_id, vault, ts)

    # Fall back to tarball
    return _take_tarball_snapshot(run_id, vault, ts)


def _take_git_snapshot(run_id: str, vault: Path, ts: str) -> dict[str, str | None]:
    """Git commit the current vault state with a bootstrap tag."""
    # Stage everything (this captures any working-tree changes as a safety net)
    code, _, err = _run(["git", "add", "-A"], vault)
    if code != 0:
        logger.warning("git add failed in %s: %s", vault, err)
        # Fall through to tarball as a fallback
        return _take_tarball_snapshot(run_id, vault, ts)

    # Check if there's anything to commit
    code, stdout, _ = _run(["git", "status", "--porcelain"], vault)
    has_changes = bool(stdout.strip())

    if has_changes:
        msg = f"pre-bootstrap snapshot (run {run_id}) {ts}"
        code, _, err = _run(["git", "commit", "-m", msg], vault)
        if code != 0:
            logger.warning("git commit failed in %s: %s", vault, err)

    # Get the current HEAD ref either way (even if nothing to commit, we want
    # the hash so rollback is possible)
    code, rev, _ = _run(["git", "rev-parse", "HEAD"], vault)
    if code != 0:
        return _take_tarball_snapshot(run_id, vault, ts)

    code, branch, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], vault)

    return {
        "method": "git",
        "git_ref": rev.strip(),
        "git_branch": branch.strip() or None,
        "tarball_path": None,
        "timestamp": ts,
    }


def _take_tarball_snapshot(
    run_id: str, vault: Path, ts: str,
) -> dict[str, str | None]:
    """Create a tarball of vault/knowledge/ for non-git vaults."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    safe_ts = ts.replace(":", "").replace("-", "").split(".")[0]
    tarball = BACKUPS_DIR / f"vault-bootstrap-{run_id}-{safe_ts}.tar.gz"

    # Only tar knowledge/ — the bootstrap never touches log/ or other folders
    knowledge = vault / "knowledge"
    if not knowledge.is_dir():
        return {
            "method": None,
            "error": "vault/knowledge dir not found",
            "timestamp": ts,
        }

    code, _, err = _run(
        ["tar", "-czf", str(tarball), "-C", str(vault), "knowledge"],
        vault,
    )
    if code != 0:
        return {
            "method": None,
            "error": f"tar failed: {err}",
            "timestamp": ts,
        }

    return {
        "method": "tarball",
        "git_ref": None,
        "git_branch": None,
        "tarball_path": str(tarball),
        "timestamp": ts,
    }
