"""
Invariant: Claude Code is configured to use the correct model.

AOS ships a default model. Users who haven't explicitly chosen a different
model get upgraded. Users who made a deliberate choice are left alone.
"""

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from base import ReconcileCheck, CheckResult, Status


class ModelConfigCheck(ReconcileCheck):
    name = "model_config"
    description = "Claude Code configured to use Opus 4"

    SETTINGS = Path.home() / ".claude" / "settings.json"

    # The model AOS ships as default
    TARGET_MODEL = "claude-opus-4-20250514"

    # Models we consider "old defaults" — safe to upgrade automatically.
    # If the user is on one of these, we shipped it — upgrade them.
    OLD_DEFAULTS = {
        None,                           # never set
        "",                             # empty string
        "claude-sonnet-4-20250514",     # previous default
        "claude-sonnet-3.5-20241022",   # older default
        "claude-sonnet-3-5-sonnet-20241022",
        "sonnet",                       # alias
    }

    # If the user explicitly chose one of these, leave them alone.
    # (anything NOT in OLD_DEFAULTS is considered a deliberate choice)

    def check(self) -> bool:
        if not self.SETTINGS.exists():
            return False
        try:
            settings = json.loads(self.SETTINGS.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        return settings.get("model") == self.TARGET_MODEL

    def fix(self) -> CheckResult:
        if not self.SETTINGS.exists():
            return CheckResult(
                self.name, Status.SKIP,
                "settings.json does not exist — will be created by install"
            )

        try:
            settings = json.loads(self.SETTINGS.read_text())
        except json.JSONDecodeError:
            return CheckResult(
                self.name, Status.NOTIFY,
                "settings.json is malformed — cannot safely set model",
                notify=True,
            )

        current = settings.get("model")

        if current in self.OLD_DEFAULTS:
            # Old default or unset — upgrade
            settings["model"] = self.TARGET_MODEL
            self.SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
            old_label = current or "(not set)"
            return CheckResult(
                self.name, Status.FIXED,
                f"Set model to {self.TARGET_MODEL} (was: {old_label})"
            )

        if current == self.TARGET_MODEL:
            return CheckResult(self.name, Status.OK, "ok")

        # User has a non-default model — deliberate choice, don't touch
        return CheckResult(
            self.name, Status.OK,
            f"ok (user chose {current}, leaving it)"
        )
