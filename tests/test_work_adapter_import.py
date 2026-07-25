"""The work adapter must import under the qareen SERVICE's path layout.

Regression for 2026-07-25: adapters/work.py did `from core.engine.work.
pipelines import ...`, but the qareen service runs with core/ as the import
root (top package `qareen`), so `core.*` threw ModuleNotFoundError → the work
adapter silently failed to register → every task/project/goal query returned
empty (the work board showed 0 tasks despite 1900+ tasks in the DB).
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_import_under_service_path_layout():
    # Fresh interpreter, ONLY core/ on the path — exactly the service's env.
    code = (
        "import sys; sys.path.insert(0, 'core'); "
        "from qareen.ontology.adapters.work import WorkAdapter, resolve_work_db_path; "
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, f"service-style import failed:\n{r.stderr}"
    assert "OK" in r.stdout
