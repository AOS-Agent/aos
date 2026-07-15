"""
Pattern gate: every `launchctl kickstart` call in a migration or a reconcile
check must be wrapped in a `try/except subprocess.TimeoutExpired`.

Why this gate exists: the drain-blocking kickstart-timeout bug shipped FOUR
separate times (migrations 054, 056, 071 and SentinelPlistDriftCheck). Each
instance was fixed in isolation, and the pattern kept coming back — a new
service migration or reconcile check would copy the naive
`subprocess.run([... "kickstart" ...], timeout=N)` and reintroduce it. The
failure mode is always the same: `kickstart -k` blocks past the short subprocess
timeout while the old instance drains, subprocess.TimeoutExpired propagates out,
and the migration/reconcile runner's generic `except Exception` mislabels a
service that actually came up healthy seconds later as a hard failure.

The canonical fix (see core/infra/reconcile/checks/sentinel_plist.py and
migrations 054/056/071) is:

    try:
        _run(["launchctl", "kickstart", "-k", service], timeout=10)
    except subprocess.TimeoutExpired:
        ...  # non-fatal — the health poll / next reconcile cycle owns it

This test walks the AST of every migration and reconcile module, finds each
call that runs `kickstart`, and fails if any of them is not lexically inside a
try-body whose except handler names TimeoutExpired. It is deliberately a
structural gate, not a code review — it catches the copy-paste before it ships.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCAN_DIRS = [
    REPO_ROOT / "core" / "infra" / "migrations",
    REPO_ROOT / "core" / "infra" / "reconcile",
]

CANONICAL_HINT = (
    "Wrap the kickstart call in `try: ... except subprocess.TimeoutExpired:` — "
    "kickstart -k can block past the subprocess timeout while the old instance "
    "drains, and an unguarded TimeoutExpired is misreported as a hard failure. "
    "See core/infra/reconcile/checks/sentinel_plist.py and migrations 054/056/071."
)


# ── AST helpers ──────────────────────────────────────────────────────────────


def _call_runs_kickstart(node: ast.Call) -> bool:
    """True if this call passes a command list/tuple containing 'kickstart'.

    Covers both `subprocess.run(["launchctl", "kickstart", ...])` and the
    migrations' `_run(["launchctl", "kickstart", ...])` helper.
    """
    for arg in list(node.args) + [kw.value for kw in node.keywords]:
        if isinstance(arg, (ast.List, ast.Tuple)):
            for el in arg.elts:
                if isinstance(el, ast.Constant) and el.value == "kickstart":
                    return True
    return False


def _name_of(node) -> str:
    """Dotted name for an exception type node (TimeoutExpired /
    subprocess.TimeoutExpired), or '' if it isn't a plain name/attribute."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}".lstrip(".")
    return ""


def _handler_catches_timeout(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:  # bare `except:` — not a deliberate timeout guard
        return False
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for t in types:
        if _name_of(t) in ("TimeoutExpired", "subprocess.TimeoutExpired"):
            return True
    return False


def find_unguarded_kickstarts(source: str, filename: str) -> list[tuple[str, int]]:
    """Return (filename, lineno) for every kickstart call NOT inside a
    try-body guarded by an except TimeoutExpired handler."""
    tree = ast.parse(source, filename=filename)

    # Every kickstart call in the module.
    all_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_runs_kickstart(n)
    ]

    # Kickstart calls that live in the body of a try guarded by TimeoutExpired.
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
            _handler_catches_timeout(h) for h in node.handlers
        ):
            for stmt in node.body:  # only the try body — not else/finally/handlers
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Call) and _call_runs_kickstart(n):
                        guarded.add(id(n))

    return [(filename, c.lineno) for c in all_calls if id(c) not in guarded]


def _iter_python_files():
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.rglob("*.py")):
            yield path


# ── The gate ─────────────────────────────────────────────────────────────────


def test_all_kickstarts_guarded_against_timeout():
    """No migration or reconcile check may ship an unguarded kickstart."""
    violations: list[tuple[str, int]] = []
    for path in _iter_python_files():
        rel = str(path.relative_to(REPO_ROOT))
        violations.extend(find_unguarded_kickstarts(path.read_text(), rel))

    assert not violations, (
        "Unguarded `launchctl kickstart` call(s) found:\n"
        + "\n".join(f"  {f}:{ln}" for f, ln in violations)
        + f"\n\n{CANONICAL_HINT}"
    )


# ── Tests for the gate itself (test the test) ────────────────────────────────

_GUARDED_FIXTURE = '''
import subprocess
def up():
    try:
        subprocess.run(["launchctl", "kickstart", "-k", "svc"], timeout=10)
    except subprocess.TimeoutExpired:
        pass
'''

_GUARDED_BARE_NAME_FIXTURE = '''
from subprocess import run, TimeoutExpired
def up():
    try:
        run(["launchctl", "kickstart", "-k", "svc"], timeout=10)
    except TimeoutExpired:
        pass
'''

_UNGUARDED_FIXTURE = '''
import subprocess
def up():
    subprocess.run(["launchctl", "kickstart", "-k", "svc"], timeout=10)
'''

_WRONG_EXCEPT_FIXTURE = '''
import subprocess
def up():
    try:
        subprocess.run(["launchctl", "kickstart", "-k", "svc"], timeout=10)
    except ValueError:
        pass
'''

_TIMEOUT_IN_FINALLY_FIXTURE = '''
import subprocess
def up():
    try:
        pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        subprocess.run(["launchctl", "kickstart", "-k", "svc"], timeout=10)
'''


def test_detector_passes_guarded_fixture():
    assert find_unguarded_kickstarts(_GUARDED_FIXTURE, "fixture.py") == []


def test_detector_passes_guarded_bare_name_fixture():
    # `except TimeoutExpired` (imported name, not subprocess.TimeoutExpired)
    assert find_unguarded_kickstarts(_GUARDED_BARE_NAME_FIXTURE, "fixture.py") == []


def test_detector_flags_unguarded_fixture():
    hits = find_unguarded_kickstarts(_UNGUARDED_FIXTURE, "fixture.py")
    assert len(hits) == 1, hits


def test_detector_flags_wrong_except_type():
    # A try that catches ValueError does NOT guard the timeout.
    hits = find_unguarded_kickstarts(_WRONG_EXCEPT_FIXTURE, "fixture.py")
    assert len(hits) == 1, hits


def test_detector_flags_kickstart_in_finally():
    # The kickstart lives in `finally`, not the guarded try-body.
    hits = find_unguarded_kickstarts(_TIMEOUT_IN_FINALLY_FIXTURE, "fixture.py")
    assert len(hits) == 1, hits
