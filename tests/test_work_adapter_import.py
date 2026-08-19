"""The vendored work adapter must import standalone from the repo root.

History: this file used to pin the 2026-07-25 regression where the adapter
(then living in core/qareen/ontology) failed to import under the qareen
service's path layout and every work query silently returned empty. Qareen
was decommissioned (aos#208) and the adapter now lives in
core/engine/work/ontology; the invariant that survives is: a fresh
interpreter with only the repo root on sys.path can import the adapter —
no service, no side effects, no path walking required.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_import_from_repo_root():
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "from core.engine.work.ontology.work import WorkAdapter, resolve_work_db_path; "
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, f"repo-root import failed:\n{r.stderr}"
    assert "OK" in r.stdout


def test_import_under_work_engine_path_layout():
    # cli.py imports backend flat with core/engine/work on sys.path; backend
    # then needs the repo root for core.engine.work.ontology. Prove the
    # bootstrap in backend.py still finds it from a fresh interpreter.
    code = (
        "import sys; sys.path.insert(0, 'core/engine/work'); "
        "import backend; print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, f"engine-style import failed:\n{r.stderr}"
    assert "OK" in r.stdout
