"""
Regression test for VaultContractCheck (core/infra/reconcile/checks/vault_contract.py).

Dangling-wires audit (2026-09-13) flagged this check as a "relative-import
crash" that "has never run real logic". Investigation for aos#236.1 found the
import was already fixed by commit 8984622 ("vault_contract resolves core.*
via AOS root on sys.path", aos#167, 2026-07-14) — `~/.aos/logs/reconcile.jsonl`
shows real "Vault inventory refreshed: N docs" rows going back to 2026-07-15,
including today. No regression test locked that fix in, though, so a future
refactor of the reconcile checks/ layout could silently reopen the crash
without anything noticing. This test is that lock.

Runs in a subprocess with HOME pointed at a scratch directory containing a
minimal knowledge/references/ doc and a pre-seeded vault_inventory schema (the
same CREATE TABLE migration 075 writes), so the assertion is end-to-end: the
check resolves `core.engine.intelligence.inventory`, scans a real (if tiny)
vault, and reports non-crash stats — never a bare False from a swallowed
ModuleNotFoundError.
"""

import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).parent.parent
MIGRATION_075 = REPO / "core" / "infra" / "migrations" / "075_vault_inventory.py"

DOC = """\
---
title: "Test reference doc"
type: reference
date: "2026-09-13"
---

# Test reference doc

Body text, no wikilinks, no violations expected beyond the contract's own
rules for the reference type.
"""

PROBE = """
    import sys
    from pathlib import Path
    REPO = Path(sys.argv[1])

    # Exactly the sys.path layout runner.py sets up when it loads checks/
    # from a fresh process -- this is the layout the original bug depended on.
    sys.path.insert(0, str(REPO / "core" / "infra" / "reconcile"))
    sys.path.insert(0, str(REPO / "core" / "infra" / "reconcile" / "checks"))

    from vault_contract import VaultContractCheck

    check = VaultContractCheck()
    ok = check.check()
    result = check.fix()

    print("CHECK_RAN_OK:" + str(ok))
    print("STATUS:" + result.status.value)
    print("MESSAGE:" + result.message)
"""


def _seed_vault_inventory_schema(fake_home: Path) -> None:
    """Create ~/.aos/data/qareen.db with the vault_inventory table, the way
    migration 075 does on a real install. scan_vault() upserts into this
    table but never creates it -- that's the migration's job."""
    spec = importlib.util.spec_from_file_location("m075_vault_inventory", MIGRATION_075)
    m075 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m075)

    db_dir = fake_home / ".aos" / "data"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "qareen.db"))
    try:
        conn.execute(m075.CREATE_SQL)
        for idx in m075.INDEX_SQL:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


def _run_probe(fake_home: Path) -> dict:
    script = textwrap.dedent(PROBE)
    result = subprocess.run(
        [sys.executable, "-c", script, str(REPO)],
        capture_output=True, text=True, timeout=60,
        # cwd deliberately NOT the repo root: `python -c` puts '' (cwd) on
        # sys.path, and when cwd happens to be the repo root that silently
        # papers over a missing sys.path.insert for the AOS root -- exactly
        # how the original bug hid on a real machine (reconcile invoked as
        # `python3 runner.py`, whose sys.path[0] is the SCRIPT's directory,
        # not the caller's cwd).
        cwd=str(fake_home),
        env={**os.environ, "HOME": str(fake_home)},
    )
    assert result.returncode == 0, (
        f"VaultContractCheck crashed in a fresh process "
        f"(this is the exact failure mode the audit described):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    parsed = {}
    for line in result.stdout.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            parsed[key] = value
    return parsed


def test_vault_contract_check_runs_real_logic_against_temp_vault():
    """check()/fix() must actually scan the vault, not crash on import."""
    with tempfile.TemporaryDirectory(prefix="vault-contract-test-") as tmp:
        fake_home = Path(tmp)
        refs_dir = fake_home / "vault" / "knowledge" / "references"
        refs_dir.mkdir(parents=True)
        (refs_dir / "test-doc.md").write_text(DOC)
        _seed_vault_inventory_schema(fake_home)

        out = _run_probe(fake_home)

        # The old bug: check() silently returned False forever because the
        # `from .contract import ...` relative import inside scan_vault's
        # module raised ModuleNotFoundError, which check() caught and turned
        # into a bare `return False` -- indistinguishable from "vault has
        # issues". Proving real logic ran means proving the stats reflect the
        # one doc we planted, not an empty/failed scan.
        assert out.get("STATUS") == "notify", out
        assert "1 docs" in out.get("MESSAGE", ""), (
            f"expected the scan to see the 1 planted doc, got: {out}"
        )


def test_vault_contract_check_handles_missing_vault_without_crashing():
    """No ~/vault at all (fresh install) must degrade to SKIP, not crash."""
    with tempfile.TemporaryDirectory(prefix="vault-contract-test-empty-") as tmp:
        fake_home = Path(tmp)
        _seed_vault_inventory_schema(fake_home)
        out = _run_probe(fake_home)
        assert out.get("STATUS") == "skip", out
