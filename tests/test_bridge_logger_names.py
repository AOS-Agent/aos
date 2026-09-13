"""Every bridge module must log as a child of `aos.bridge`.

`main.py` attaches the rotating file handler for `~/.aos/logs/bridge.log` to
exactly one logger: `aos.bridge` (via `lib.log.get_logger("bridge", ...)`).
Python's logging only walks *up* the dotted name, so a module that calls
`logging.getLogger(__name__)` ("telegram_channel") or an ad hoc name
("bridge.session_manager") is a root-level logger with no handler attached —
its records go nowhere.

That is not theoretical. The 2026-09-13 review found bridge.log carried only
main.py's own lifecycle lines across 19,572 lines / five months: zero
"Message:", zero "Response:", zero "Quick command:", and a 0-byte
bridge.err.log. The bridge ran completely unobserved.

This is a structural gate so the regression cannot come back by copy-paste:
a new bridge module that reaches for `logging.getLogger(__name__)` fails here
before it ships.
"""

from __future__ import annotations

import ast
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent / "core" / "services" / "bridge"

# Third-party loggers the bridge deliberately reaches for by their own name,
# only ever to *raise their level* (silence polling noise). These are not
# bridge loggers and must not be renamed.
THIRD_PARTY_ALLOWED = {"httpx", "httpcore", "telegram", "telegram.ext", "apscheduler"}

HINT = (
    'Use logging.getLogger("aos.bridge.<module>") — only the "aos.bridge" '
    "logger has the bridge.log file handler, and logging resolves handlers by "
    "walking the dotted name upwards."
)


def _bridge_modules() -> list[Path]:
    return sorted(p for p in BRIDGE.glob("*.py") if p.name != "__init__.py")


def _getlogger_literals(tree: ast.AST) -> list[tuple[int, str | None]]:
    """(lineno, literal-name-or-None) for every logging.getLogger(...) call.

    A None name means the argument was not a plain string literal (e.g.
    `__name__`), which is exactly the bug this gate blocks.
    """
    found: list[tuple[int, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "getLogger"):
            continue
        if not node.args:
            found.append((node.lineno, None))
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.append((node.lineno, arg.value))
        else:
            found.append((node.lineno, None))
    return found


def test_every_bridge_logger_is_a_child_of_aos_bridge():
    offenders: list[str] = []
    for path in _bridge_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno, name in _getlogger_literals(tree):
            if name is None:
                offenders.append(f"{path.name}:{lineno} — non-literal logger name (__name__?)")
                continue
            if name in THIRD_PARTY_ALLOWED:
                continue
            if name == "aos.bridge" or name.startswith("aos.bridge."):
                continue
            offenders.append(f"{path.name}:{lineno} — logger {name!r}")

    assert not offenders, "Bridge loggers outside the aos.bridge tree:\n  " + \
        "\n  ".join(offenders) + f"\n{HINT}"


def test_no_bridge_module_uses_dunder_name_for_its_logger():
    """The specific shape that caused five months of silence."""
    offenders = []
    for path in _bridge_modules():
        text = path.read_text()
        if "logging.getLogger(__name__)" in text:
            offenders.append(path.name)
    assert not offenders, (
        "getLogger(__name__) gives a root-level logger with no handler: "
        + ", ".join(offenders) + f"\n{HINT}"
    )


def test_main_still_configures_the_parent_logger():
    """The gate above is only meaningful while `aos.bridge` owns the handler."""
    main = (BRIDGE / "main.py").read_text()
    assert 'get_logger(' in main and '"bridge"' in main, \
        "main.py must keep configuring the 'bridge' logger (→ aos.bridge) with a file handler"
    assert "~/.aos/logs/bridge.log" in main
