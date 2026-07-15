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
from pathlib import Path

import pytest

CHECKER = (
    Path(__file__).parent.parent / "core" / "infra" / "service_import_check.py"
)


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
