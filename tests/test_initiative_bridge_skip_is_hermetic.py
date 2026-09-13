"""
Hermeticity test for the pytest-skip guard in tests/test_initiative_bridge.py.

test_initiative_bridge.py is a standalone dev-machine verification script, not
a set of pytest tests — its filename just happens to match `test_*.py`, so
pytest imports it on every collection. Its guard builds a human-readable skip
reason from `Path.home() / "project" / "aos"` before calling
`pytest.skip(..., allow_module_level=True)`. That read happens at module exec
time, on every real pytest run, on whatever machine is running the suite —
before any fixture or monkeypatch in a *calling* test could ever apply, since
collection-time module exec runs before any test function starts. The actual
risk isn't that one read (it's harmless and read-only): it's that AOS_USER
(~/.aos) and VAULT (~/vault) are built unconditionally a few lines further
down, and would be read the moment execution ever continued past the guard.

This test re-imports the file with `Path.home()` patched *before* exec — the
same pattern `load_migration()` in test_migrations_108_117_idempotency.py uses
for migrations that resolve HOME at module scope — proving: (1) the skip
reason reflects the sandboxed home, not whatever machine happens to be running
the suite; (2) the guard's one check creates nothing under that sandbox; and
(3) execution genuinely halts at the skip and never reaches AOS_USER/VAULT.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "tests" / "test_initiative_bridge.py"


def _import_with_home(home: Path, tag: str):
    """Exec test_initiative_bridge.py fresh, with Path.home() -> home.

    Bypasses sys.modules entirely (spec_from_file_location + exec_module, not
    a normal `import`), so every call is a genuinely fresh execution under
    whichever Path.home() is active at that moment — nothing from a previous
    call can leak in as a stale cached value.

    Returns (module, skip_exception_or_None). The module object exists even
    when exec raises partway through (module_from_spec creates it before
    exec_module runs), so callers can assert on how far execution got.
    """
    real_home = Path.home
    Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
    try:
        spec = importlib.util.spec_from_file_location(
            f"_initiative_bridge_reimport_{tag}", TARGET
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            skipped = None
        except pytest.skip.Exception as exc:  # noqa: BLE001
            skipped = exc
        return mod, skipped
    finally:
        Path.home = real_home  # type: ignore[method-assign]


def test_skip_reason_reflects_the_sandboxed_home_not_a_dev_box(tmp_path):
    home = tmp_path / "operator-home"
    home.mkdir()

    mod, skipped = _import_with_home(home, "operator")

    assert skipped is not None, "must skip under pytest regardless of machine"
    assert "not a dev machine" in str(skipped)

    # Read-only: the guard's one check (~/project/aos) creates nothing.
    assert list(home.iterdir()) == []

    # The real risk: AOS_USER/VAULT (~/.aos, ~/vault) are assigned a few lines
    # after the guard, unconditionally. Prove execution never got there.
    assert not hasattr(mod, "AOS_USER")
    assert not hasattr(mod, "VAULT")
    assert not hasattr(mod, "PASSED")


def test_skip_reason_when_sandbox_has_a_dev_workspace(tmp_path):
    home = tmp_path / "dev-home"
    (home / "project" / "aos").mkdir(parents=True)

    mod, skipped = _import_with_home(home, "dev")

    assert skipped is not None
    reason = str(skipped)
    assert "not a dev machine" not in reason
    assert "run it directly" in reason

    # Still read-only, and execution still halts at the guard.
    assert list((home / "project" / "aos").iterdir()) == []
    assert not hasattr(mod, "AOS_USER")
    assert not hasattr(mod, "VAULT")
