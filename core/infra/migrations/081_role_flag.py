"""
Migration 081: Role flag — mark each machine developer or operator.

AOS ships one set of session rules to every machine via the AOS:MANAGED blocks
in ~/.claude/CLAUDE.md (see core/infra/reconcile/checks/claude_md.py). Those
rules told EVERY machine "NEVER edit ~/aos — all framework changes go in
~/project/aos (dev workspace)". That is correct for the developer's machine,
but friends' installs have no dev workspace: they run AOS as managed software.
Lecturing an operator about a production/development split they can't act on is
noise, and worse, it tells their agent to look for a workspace that isn't there.

This migration stamps a ``role`` key into ~/.aos/config/operator.yaml so the
role-aware reconcile check (claude_md.GlobalClaudeMdCheck) can pick the right
rules block:

    role: developer   — has the ~/project/aos dev workspace; keeps dev rules
    role: operator    — no dev workspace; gets "fix your instance directly,
                        report framework bugs upstream" instead

Inference: a machine with ~/project/aos is a developer machine; everything else
is an operator machine. This is instance-impacting state that the framework
(the role-aware claude_md check) now depends on, so per the atomic-migration
rule it ships in the same release as that check.

Idempotent: once a valid role is present, check() passes and up() is a no-op.
It never overwrites an existing role, so a machine that later flips role (e.g.
an operator who clones the dev workspace) can set role: developer by hand and
this migration will respect it. The claude_md resolver also infers the role at
runtime when the key is still absent, so a machine is never lectured wrongly
even in the window before this migration runs.
"""

DESCRIPTION = "Stamp operator.yaml role: developer (has ~/project/aos) | operator"

from pathlib import Path

HOME = Path.home()
OPERATOR_YAML = HOME / ".aos" / "config" / "operator.yaml"
DEV_WORKSPACE = HOME / "project" / "aos"


def _infer_role() -> str:
    return "developer" if DEV_WORKSPACE.exists() else "operator"


def _current_role() -> str:
    if not OPERATOR_YAML.exists():
        return ""
    try:
        import yaml

        data = yaml.safe_load(OPERATOR_YAML.read_text()) or {}
    except Exception:
        return ""
    return str(data.get("role", "")).strip().lower()


def check() -> bool:
    """Applied once operator.yaml carries a valid role key.

    If there is no operator.yaml yet, treat the migration as satisfied: the
    profile is created by onboarding, and the claude_md resolver infers the
    role from the dev workspace until a profile with a role exists.
    """
    if not OPERATOR_YAML.exists():
        return True
    return _current_role() in ("developer", "operator")


def up() -> bool:
    role = _infer_role()

    if not OPERATOR_YAML.exists():
        print(f"       {OPERATOR_YAML} not present — role inferred as '{role}' at runtime")
        return True

    existing = _current_role()
    if existing in ("developer", "operator"):
        print(f"       role already set to '{existing}' — skipped")
        return True

    import yaml

    data = yaml.safe_load(OPERATOR_YAML.read_text()) or {}
    data["role"] = role
    OPERATOR_YAML.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    print(f"       Set role: {role} in {OPERATOR_YAML}")
    return True
