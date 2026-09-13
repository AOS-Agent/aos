"""
Tests for core/bin/internal/service-import-check.py — the ship-check helper that
flags a service's third-party imports missing from its pyproject.toml.

The check exists to catch a genuinely undeclared third-party dependency (which
would ImportError on a remote machine's freshly built venv). It must NOT flag
noise: stdlib modules, the service's own local modules, other AOS-internal
modules reached via sys.path, or relative imports. These tests pin both
directions — a real gap fires, everything else stays quiet.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
CHECKER = REPO_ROOT / "core" / "infra" / "service_import_check.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("service_import_check", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sic():
    return _load_checker()


def _make_service(root: Path, name: str, deps: list[str], files: dict[str, str]) -> Path:
    """Build a fake AOS tree: <root>/core/services/<name>/ with a pyproject and
    the given {filename: source} python files. Returns the service dir. The
    three-levels-up layout matters — the checker derives the AOS root from it."""
    svc = root / "core" / "services" / name
    svc.mkdir(parents=True)
    dep_block = ",\n    ".join(f'"{d}"' for d in deps)
    (svc / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
        f"dependencies = [\n    {dep_block}\n]\n"
    )
    for fname, src in files.items():
        (svc / fname).write_text(src)
    return svc


def test_flags_a_genuinely_undeclared_third_party_import(tmp_path, sic):
    svc = _make_service(
        tmp_path,
        "faker",
        deps=["httpx>=0.27"],
        files={
            "main.py": (
                "import os\n"                       # stdlib — must be ignored
                "import httpx\n"                    # declared — must be ignored
                "import nonexistent_pkg_zzz\n"      # THE real gap
                "from . import helper\n"            # relative — must be ignored
                "from config import load\n"         # local sibling — must be ignored
            ),
            "config.py": "def load():\n    return {}\n",
        },
    )
    gaps = sic.find_import_gaps(svc)
    assert gaps == ["nonexistent_pkg_zzz"], gaps


def test_clean_service_reports_no_gaps(tmp_path, sic):
    svc = _make_service(
        tmp_path,
        "clean",
        deps=["httpx>=0.27", "pyyaml>=6.0"],
        files={
            "main.py": (
                "import os, sys, json\n"            # stdlib
                "import httpx\n"                    # declared
                "from worker import run\n"          # local sibling
            ),
            "worker.py": "def run():\n    pass\n",
        },
    )
    assert sic.find_import_gaps(svc) == []


def test_extras_bearing_dep_is_not_truncated(tmp_path, sic):
    # `uvicorn[standard]` must be parsed as a declared dep — the old regex stopped
    # at the extras bracket and dropped every dependency after it.
    svc = _make_service(
        tmp_path,
        "extras",
        deps=["uvicorn[standard]>=0.30", "starlette>=0.37"],
        files={"main.py": "import uvicorn\nimport starlette\n"},
    )
    assert sic.find_import_gaps(svc) == []


def test_aos_internal_module_is_not_flagged(tmp_path, sic):
    # A shared internal module living elsewhere in the AOS tree (reached via a
    # sys.path insert at runtime) must not be mistaken for a third-party dep.
    (tmp_path / "core" / "infra" / "lib").mkdir(parents=True)
    (tmp_path / "core" / "infra" / "lib" / "log.py").write_text("def get_logger():\n    pass\n")
    svc = _make_service(
        tmp_path,
        "usesinternal",
        deps=["httpx>=0.27"],
        files={"main.py": "import httpx\nfrom log import get_logger\n"},
    )
    assert sic.find_import_gaps(svc) == []


# ---------------------------------------------------------------------------
# Regression: bridge's aiohttp dependency (aos#236.3)
#
# Dangling-wires audit (2026-09-13): "Bridge API :4098 — missing aiohttp
# dependency ... 'No module aiohttp' x40 restarts." Investigation found
# aiohttp>=3.9.0 was already declared in core/services/bridge/pyproject.toml
# and pinned in requirements.lock (fixed 2026-03-28, commit f3c2170,
# aos#131-era) — both the manifest and the installed .venv already have it.
# What was missing was a test locking that fact in, run through the same
# venv-aware path ship-check uses (core/infra/service_import_check.py run
# with the SERVICE's own interpreter, not pytest's) — so a future edit that
# drops the dependency line fails the suite instead of waiting to be
# discovered as restart-loop noise in production.
# ---------------------------------------------------------------------------

BRIDGE_DIR = REPO_ROOT / "core" / "services" / "bridge"
BRIDGE_VENV_PY = Path.home() / ".aos" / "services" / "bridge" / ".venv" / "bin" / "python"


def _run_checker(python: Path, svc_dir: Path) -> str:
    result = subprocess.run(
        [str(python), str(CHECKER), str(svc_dir)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"checker crashed: {result.stderr}"
    return result.stdout.strip()


@pytest.mark.skipif(
    not BRIDGE_VENV_PY.exists(),
    reason="bridge .venv not built on this machine — instance state, not framework state",
)
def test_bridge_service_has_no_import_gaps_in_its_own_venv():
    """The exact check ship-check runs, against the exact venv bridge runs in.

    aiohttp is the named regression (it is what api_server.py imports for the
    :4098 health/SSE endpoints), but this asserts zero gaps overall — any
    undeclared third-party import in the bridge service is the same failure
    class (ImportError on a freshly rebuilt remote venv).
    """
    gaps = _run_checker(BRIDGE_VENV_PY, BRIDGE_DIR).splitlines()
    assert gaps == [], (
        f"bridge service has undeclared third-party imports: {gaps} — "
        f"add them to {BRIDGE_DIR / 'pyproject.toml'}"
    )


@pytest.mark.skipif(
    not BRIDGE_VENV_PY.exists(),
    reason="bridge .venv not built on this machine — instance state, not framework state",
)
def test_bridge_import_check_would_catch_a_dropped_aiohttp(tmp_path):
    """Proves the regression guard has teeth: copy the real bridge service,
    strip the aiohttp dependency line, and confirm the checker (run with the
    real bridge venv, so aiohttp IS importable there) still flags it as a
    declared-dependency gap rather than silently passing."""
    import shutil

    fake_root = tmp_path / "aos"
    fake_svc = fake_root / "core" / "services" / "bridge"
    shutil.copytree(BRIDGE_DIR, fake_svc, ignore=shutil.ignore_patterns(".venv", "__pycache__"))

    pyproject = fake_svc / "pyproject.toml"
    stripped = "\n".join(
        line for line in pyproject.read_text().splitlines()
        if "aiohttp" not in line
    )
    pyproject.write_text(stripped)

    gaps = _run_checker(BRIDGE_VENV_PY, fake_svc).splitlines()
    assert "aiohttp" in gaps, (
        f"stripping aiohttp from pyproject.toml should surface it as a gap, got: {gaps}"
    )
