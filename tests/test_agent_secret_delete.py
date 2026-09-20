"""`agent-secret delete` must actually delete — in both stores.

A secret can live in two places: the login keychain under `aos.<NAME>`, and
the pre-migration `agent.keychain` under `<NAME>` (account `agent`). `get` and
`check` both fall back to the legacy copy, but `delete` only ever removed the
prefixed one — so it printed "Deleted: <NAME>", exited 0, and left the
credential fully readable. Found on 2026-09-20 while removing a retired bot's
token: `agent-secret get` returned it immediately afterwards.

That failure mode is worse than not deleting at all. A revoked credential is
revoked once; nobody goes back to check, and the operator believes a secret is
gone while it is still sitting on disk.

These tests run against a stub `security` on PATH that models both stores, so
they never touch the real Keychain.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "core" / "bin" / "cli" / "agent-secret"

STUB = r'''#!/usr/bin/env python3
"""Stub `security` modelling two keychains keyed by (keychain, account, service)."""
import sys, os, pathlib, hashlib

store = pathlib.Path(os.environ["FAKE_KEYCHAIN_STORE"])
store.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
if not args:
    sys.exit(2)
sub, i = args[0], 1
acct = svc = val = None
want_pw = False
rest = []
while i < len(args):
    a = args[i]
    if a == "-a":
        acct = args[i + 1]; i += 2
    elif a == "-s":
        svc = args[i + 1]; i += 2
    elif a == "-w":
        if sub.startswith("add"):
            val = args[i + 1]; i += 2
        else:
            want_pw = True; i += 1
    else:
        rest.append(a); i += 1

keychain = rest[-1] if rest else "login"
key = hashlib.sha256(f"{keychain}|{acct}|{svc}".encode()).hexdigest()
f = store / key

if sub == "add-generic-password":
    f.write_text(val or "")
    sys.exit(0)
if sub == "find-generic-password":
    if not f.exists():
        print("The specified item could not be found in the keychain.", file=sys.stderr)
        sys.exit(44)
    print(f.read_text() if want_pw else f'attributes:\n    "svce"<blob>="{svc}"')
    sys.exit(0)
if sub == "delete-generic-password":
    if not f.exists():
        print("The specified item could not be found in the keychain.", file=sys.stderr)
        sys.exit(44)
    f.unlink()
    sys.exit(0)
sys.exit(1)
'''


@pytest.fixture()
def secret_cli(tmp_path):
    """agent-secret wired to a stub `security`, plus helpers to plant/read."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "security"
    stub.write_text(STUB)
    stub.chmod(0o755)
    store = tmp_path / "store"

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_KEYCHAIN_STORE"] = str(store)

    def run(*args):
        return subprocess.run(["bash", str(SCRIPT), *args],
                              capture_output=True, text=True, env=env, timeout=30)

    def plant(name, value="v", *, legacy=False):
        if legacy:
            subprocess.run([str(stub), "add-generic-password", "-a", "agent",
                            "-s", name, "-w", value, "agent.keychain"],
                           env=env, check=True, timeout=30)
        else:
            subprocess.run([str(stub), "add-generic-password", "-a", "aos",
                            "-s", f"aos.{name}", "-w", value],
                           env=env, check=True, timeout=30)

    run.plant = plant
    return run


def test_delete_removes_the_legacy_copy_too(secret_cli):
    """The reference bug: both stores populated, only the prefixed one went."""
    secret_cli.plant("TOKEN", "live-value")
    secret_cli.plant("TOKEN", "live-value", legacy=True)
    assert secret_cli("get", "TOKEN").returncode == 0, "planted secret should read"

    r = secret_cli("delete", "TOKEN")
    assert r.returncode == 0, r.stderr
    assert "Deleted" in r.stdout

    after = secret_cli("get", "TOKEN")
    assert after.returncode != 0, (
        "delete reported success but the secret is still readable — "
        "exactly the failure this test exists for"
    )


def test_delete_works_when_only_the_legacy_copy_exists(secret_cli):
    """A secret that was never migrated must still be deletable."""
    secret_cli.plant("OLD_ONLY", legacy=True)
    assert secret_cli("get", "OLD_ONLY").returncode == 0

    assert secret_cli("delete", "OLD_ONLY").returncode == 0
    assert secret_cli("get", "OLD_ONLY").returncode != 0


def test_delete_works_when_only_the_prefixed_copy_exists(secret_cli):
    secret_cli.plant("NEW_ONLY")
    assert secret_cli("delete", "NEW_ONLY").returncode == 0
    assert secret_cli("get", "NEW_ONLY").returncode != 0


def test_deleting_a_missing_secret_fails_loudly(secret_cli):
    """It used to print an error and still exit 0, so a caller chaining on
    success could not tell a no-op from a deletion."""
    r = secret_cli("delete", "NEVER_EXISTED")
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_delete_leaves_other_secrets_alone(secret_cli):
    secret_cli.plant("KEEP_ME", "keep")
    secret_cli.plant("KEEP_ME_LEGACY", "keep", legacy=True)
    secret_cli.plant("DROP_ME", "drop")
    secret_cli.plant("DROP_ME", "drop", legacy=True)

    assert secret_cli("delete", "DROP_ME").returncode == 0

    assert secret_cli("get", "KEEP_ME").stdout.strip() == "keep"
    assert secret_cli("get", "KEEP_ME_LEGACY").stdout.strip() == "keep"
    assert secret_cli("get", "DROP_ME").returncode != 0


def test_set_purges_a_superseded_legacy_copy(secret_cli):
    """Rotating a credential must not leave the old one readable on disk."""
    secret_cli.plant("ROTATING", "old-leaked-value", legacy=True)

    assert secret_cli("set", "ROTATING", "new-value").returncode == 0
    assert secret_cli("get", "ROTATING").stdout.strip() == "new-value"

    # And the superseded copy is gone, not merely shadowed: deleting the new
    # prefixed entry must not resurrect the old value.
    assert secret_cli("delete", "ROTATING").returncode == 0
    assert secret_cli("get", "ROTATING").returncode != 0
