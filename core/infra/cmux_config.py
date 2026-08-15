"""
Provision cmux so AOS can drive it from outside a cmux window.

Why this exists
---------------
cmux's socket API defaults to ``socketControlMode: "cmuxOnly"`` — it only
accepts commands from a caller that is already inside cmux (one that has
``$CMUX_WORKSPACE_ID`` / ``$CMUX_SURFACE_ID`` set).

``aos start`` is, by definition, always called from *outside* cmux: at the end
of an install it runs in Terminal.app. Under the default mode every socket call
it makes is refused, so it fell through to running Claude Code in Terminal. On a
fresh Mac that failure was not intermittent — it was guaranteed. The operator
watched cmux launch and then got onboarded in the wrong window.

Nothing in AOS ever wrote this setting, so no install has ever had it. This
module is the single place that fixes it, called from both ``install.sh``
(fresh installs) and migration 099 (existing machines).

cmux.json is JSONC — comments are legal and operators write them. So reads strip
comments to parse, but writes are surgical text edits that leave every existing
comment, key, and bit of formatting intact.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "cmux"
CONFIG_FILE = CONFIG_DIR / "cmux.json"
CMUX_BIN = Path("/Applications/cmux.app/Contents/Resources/bin/cmux")

# What AOS needs: a mode that accepts socket commands from outside cmux.
DESIRED_MODE = "automation"

# Modes that already permit external control. If the operator has chosen any of
# these we leave their choice alone — "password" and the open modes are all at
# least as permissive as what we need, and silently rewriting an operator's
# security posture is not ours to do.
SUFFICIENT_MODES = {
    "automation",
    "password",
    "allowAll",
    "openAccess",
    "fullOpenAccess",
    "full",
}

_SEED = """// cmux configuration
//
// AOS sets socketControlMode so `aos start` can open a workspace and launch
// Claude Code from outside cmux (e.g. from Terminal at the end of an install).
// cmux's default, "cmuxOnly", refuses socket commands from non-cmux callers,
// which left onboarding stranded in Terminal.
{
  "$schema": "https://raw.githubusercontent.com/manaflow-ai/cmux/main/web/data/cmux.schema.json",
  "automation": {
    "socketControlMode": "automation"
  }
}
"""


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments that fall outside string literals."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        out.append(ch)
        i += 1
    stripped = "".join(out)
    # Trailing commas are legal in JSONC, fatal in json.loads.
    return re.sub(r",(\s*[}\]])", r"\1", stripped)


def current_mode() -> str | None:
    """The configured socketControlMode, or None if unset/unreadable."""
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(_strip_jsonc(CONFIG_FILE.read_text()))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    automation = data.get("automation")
    if not isinstance(automation, dict):
        return None
    mode = automation.get("socketControlMode")
    return mode if isinstance(mode, str) else None


def is_satisfied() -> bool:
    return current_mode() in SUFFICIENT_MODES


def _backup() -> None:
    """cmux's own agent guidance: back up cmux.json before editing it."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(CONFIG_FILE, CONFIG_FILE.with_suffix(f".json.{stamp}.bak"))


def _rewrite_existing_key(text: str) -> str | None:
    """Point an existing socketControlMode at DESIRED_MODE, in place."""
    pattern = re.compile(
        r'("socketControlMode"\s*:\s*)"[^"]*"'
    )
    new_text, count = pattern.subn(rf'\g<1>"{DESIRED_MODE}"', text, count=1)
    return new_text if count else None


def _insert_into_automation(text: str) -> str | None:
    """Add socketControlMode to an existing automation block."""
    match = re.search(r'("automation"\s*:\s*\{)', text)
    if not match:
        return None
    insert_at = match.end()
    addition = (
        "\n    // AOS: allows `aos start` to drive cmux from outside cmux."
        f'\n    "socketControlMode": "{DESIRED_MODE}",'
    )
    return text[:insert_at] + addition + text[insert_at:]


def _add_automation_block(text: str) -> str | None:
    """Add a whole automation block to an existing config object."""
    close = text.rfind("}")
    if close == -1:
        return None
    head = text[:close].rstrip()
    # Only add a separating comma if there is already a key in the object.
    sep = "," if head.rstrip().endswith(("}", "]", '"')) or re.search(
        r'[\d\w"]\s*$', head
    ) else ""
    block = (
        f"{sep}\n"
        "  // AOS: allows `aos start` to drive cmux from outside cmux.\n"
        '  "automation": {\n'
        f'    "socketControlMode": "{DESIRED_MODE}"\n'
        "  }\n"
    )
    return head + block + text[close:]


def ensure(reload: bool = True) -> tuple[bool, str]:
    """
    Make sure cmux accepts socket control from outside cmux.

    Returns (changed, human_readable_status).
    """
    existing = current_mode()
    if existing in SUFFICIENT_MODES:
        return False, f"cmux socket control already usable ({existing})"

    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(_SEED)
        _maybe_reload(reload)
        return True, f"cmux socket control set to {DESIRED_MODE} (new config)"

    text = CONFIG_FILE.read_text()
    updated = (
        _rewrite_existing_key(text)
        or _insert_into_automation(text)
        or _add_automation_block(text)
    )
    if updated is None:
        return False, "cmux config could not be edited safely — left untouched"

    # Never write something cmux would reject. If our surgical edit produced
    # invalid JSONC, back out rather than break the operator's terminal.
    try:
        json.loads(_strip_jsonc(updated))
    except json.JSONDecodeError:
        return False, "cmux config edit would have been invalid — left untouched"

    _backup()
    CONFIG_FILE.write_text(updated)
    _maybe_reload(reload)
    was = existing or "unset (default cmuxOnly)"
    return True, f"cmux socket control: {was} -> {DESIRED_MODE}"


def _maybe_reload(reload: bool) -> None:
    """Ask a running cmux to pick the change up; harmless if it isn't running."""
    if not reload or not CMUX_BIN.exists():
        return
    try:
        subprocess.run(
            [str(CMUX_BIN), "reload-config"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        pass


if __name__ == "__main__":
    changed, status = ensure()
    print(status)
