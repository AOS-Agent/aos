"""install.sh's fresh-install hook_defs (aos#236.4).

install.sh bootstraps ~/.claude/settings.json's baseline hooks for a fresh
machine (a separate code path from the reconcile check and from one-shot
migrations — see core/infra/reconcile/checks/hooks.py and
core/infra/migrations/123_trust_log_dispatch_hook.py, which cover existing
installs). This locks in that the automatic trust-log-dispatch PostToolUse
hook is part of that fresh-install bootstrap too, not just the drift-repair
paths — so a brand new machine gets chief.md's catalog-dispatch mandate
enforced from the first session, matching how SessionStart/SessionEnd are
already handled here.
"""

import ast
import re
from pathlib import Path

INSTALL_SH = Path(__file__).parent.parent / "install.sh"


def _hook_defs() -> dict:
    text = INSTALL_SH.read_text()
    m = re.search(r"hook_defs = (\{.*?\n\})\n", text, re.DOTALL)
    assert m, "install.sh's hook_defs block was not found — did its shape change?"
    return ast.literal_eval(m.group(1))


def test_hook_defs_is_valid_python_literal():
    defs = _hook_defs()
    assert isinstance(defs, dict) and defs


def test_post_tool_use_trust_log_dispatch_is_bootstrapped_on_fresh_install():
    defs = _hook_defs()
    assert "PostToolUse" in defs, "fresh installs never get the trust-log-dispatch hook"

    blocks = defs["PostToolUse"]
    matching = [
        inner
        for block in blocks
        for inner in block.get("hooks", [])
        if inner.get("command") == "python3 ~/aos/core/hooks/trust_log_dispatch.py"
    ]
    assert matching, f"trust_log_dispatch.py not wired in install.sh PostToolUse: {blocks}"

    owning_block = next(
        b for b in blocks
        if any(i.get("command") == "python3 ~/aos/core/hooks/trust_log_dispatch.py" for i in b.get("hooks", []))
    )
    assert owning_block.get("matcher") == "Agent", (
        "trust-log-dispatch hook must be scoped to the Agent tool via matcher"
    )
