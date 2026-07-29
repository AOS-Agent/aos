"""
Migration 097: cmux is the only AOS terminal surface.

AOS used to install VS Code and drive it from `aos start` by writing a
`~/.vscode/tasks.json` task with `runOn: folderOpen` that launched `cld`. That
made VS Code a load-bearing dependency for the primary way in, and it left a
side effect on the machine: opening ANY folder in VS Code would auto-spawn a
Claude Code session.

`aos start` now drives cmux (`cmux new-workspace --cwd --command`). This
migration brings existing machines in line with that:

1. Install cmux if it is missing (non-fatal — the system is fully usable from
   any terminal via `cld`).
2. Repoint ~/.aos/config/editor from "code" to "cmux".
3. Remove the AOS-generated auto-run task from ~/.vscode/tasks.json so a
   leftover VS Code install stops spawning sessions behind the operator's back.

VS Code itself is NOT uninstalled — removing an app the operator may use for
other work is their call, not ours.
"""

DESCRIPTION = "cmux replaces VS Code as the AOS terminal surface"

import json
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
EDITOR_FILE = HOME / ".aos" / "config" / "editor"
CMUX_APP = Path("/Applications/cmux.app")
CMUX_BIN = CMUX_APP / "Contents" / "Resources" / "bin" / "cmux"
VSCODE_TASKS = HOME / ".vscode" / "tasks.json"

# The exact task `aos start` used to write. Matched on label + folderOpen so we
# only ever remove our own, never a task the operator authored.
AOS_TASK_LABEL = "Claude Code"


def _cmux_present() -> bool:
    return CMUX_BIN.exists() or shutil.which("cmux") is not None


def _aos_task(task: dict) -> bool:
    """True if this is the auto-run task AOS used to generate."""
    if not isinstance(task, dict):
        return False
    if task.get("label") != AOS_TASK_LABEL:
        return False
    run_on = (task.get("runOptions") or {}).get("runOn")
    if run_on != "folderOpen":
        return False
    # Belt and braces: it must actually be launching Claude Code.
    return "cld" in str(task.get("command", ""))


def _vscode_task_remaining() -> bool:
    """True if ~/.vscode/tasks.json still holds the AOS auto-run task."""
    if not VSCODE_TASKS.exists():
        return False
    try:
        data = json.loads(VSCODE_TASKS.read_text())
    except Exception:
        # Unparseable: not ours to interpret, so nothing left for us to remove.
        return False
    return any(_aos_task(t) for t in data.get("tasks", []))


def check() -> bool:
    """Applied when the editor config says cmux and no AOS VS Code task remains."""
    if not EDITOR_FILE.exists():
        return False
    if EDITOR_FILE.read_text().strip() != "cmux":
        return False
    return not _vscode_task_remaining()


def up() -> bool:
    # 1. Install cmux if absent. Non-fatal: `aos start` prints the brew command
    #    and falls back to running Claude Code in the current terminal.
    if _cmux_present():
        print("  cmux already installed")
    else:
        print("  Installing cmux...")
        try:
            result = subprocess.run(
                ["brew", "install", "--cask", "cmux"],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode == 0 and _cmux_present():
                print("  cmux installed")
            else:
                print("  WARNING: cmux install did not complete —")
                print("           run 'brew install --cask cmux' when convenient.")
        except FileNotFoundError:
            print("  WARNING: Homebrew not found — install cmux manually.")
        except subprocess.TimeoutExpired:
            print("  WARNING: cmux install timed out — run 'brew install --cask cmux'.")

    # 2. Repoint the editor config.
    EDITOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    previous = EDITOR_FILE.read_text().strip() if EDITOR_FILE.exists() else "(unset)"
    if previous != "cmux":
        EDITOR_FILE.write_text("cmux\n")
        print(f"  Editor: {previous} -> cmux")
    else:
        print("  Editor already set to cmux")

    # 3. Strip the AOS auto-run task from VS Code, leaving any operator-authored
    #    tasks untouched.
    if VSCODE_TASKS.exists():
        try:
            data = json.loads(VSCODE_TASKS.read_text())
            tasks = data.get("tasks", [])
            kept = [t for t in tasks if not _aos_task(t)]
            if len(kept) != len(tasks):
                if kept:
                    data["tasks"] = kept
                    VSCODE_TASKS.write_text(json.dumps(data, indent=2) + "\n")
                    print("  Removed the AOS auto-run task from ~/.vscode/tasks.json")
                else:
                    # The file existed only to hold our task.
                    VSCODE_TASKS.unlink()
                    print("  Removed ~/.vscode/tasks.json (held only the AOS task)")
            else:
                print("  No AOS task in ~/.vscode/tasks.json")
        except Exception as e:
            # Never fail the migration over a file we don't own.
            print(f"  WARNING: could not clean ~/.vscode/tasks.json: {e}")

    return True
