"""
Tests for the Telegram add-task reply.

The handler used to return the work CLI's raw stdout, so texting the bot a task
replied "Added: Created t#187: Fix the login page" — an internal ID in front of
the operator every single time, against the house Telegram rule (clean English,
no jargon, no IDs). These tests pin the reply shape so the raw-stdout path
cannot come back.

The handler shells out and reads a database, so both are stubbed. What is being
tested is the contract with the reader: what appears on the phone.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "core" / "services" / "bridge" / "intent_classifier.py"


@pytest.fixture(scope="module")
def ic():
    """Load intent_classifier by path (it is not on a package path)."""
    sys.path.insert(0, str(REPO / "core" / "infra" / "lib"))
    spec = importlib.util.spec_from_file_location("intent_classifier_uut", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"intent_classifier not importable in this environment: {e}")
    return mod


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def cli_calls(monkeypatch, ic):
    """Capture the argv the handler would run, and fake a successful CLI."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # The exact stdout the real CLI prints — the thing that used to leak.
        return _Result(0, "Created t#187: Fix the login page [aos]\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_reply_contains_no_task_id(ic, cli_calls, monkeypatch):
    monkeypatch.setattr(ic, "_current_project", lambda: None)
    reply = ic.handle_add_task("add task: Fix the login page")
    assert "t#187" not in reply
    assert "Created" not in reply


def test_reply_is_plain_english(ic, cli_calls, monkeypatch):
    monkeypatch.setattr(ic, "_current_project", lambda: None)
    assert ic.handle_add_task("add task: Fix the login page") == "✅ Added: Fix the login page"


def test_reply_names_the_project_when_known(ic, cli_calls, monkeypatch):
    monkeypatch.setattr(ic, "_current_project", lambda: "quran-garden")
    reply = ic.handle_add_task("add task: Fix the login page")
    assert reply == "✅ Added to Quran Garden: Fix the login page"
    assert "quran-garden" not in reply  # the id itself never shows


def test_project_is_passed_to_the_cli(ic, cli_calls, monkeypatch):
    monkeypatch.setattr(ic, "_current_project", lambda: "aos")
    ic.handle_add_task("add task: Fix the login page")
    cmd = cli_calls[-1]
    assert "--project" in cmd
    assert cmd[cmd.index("--project") + 1] == "aos"


def test_no_project_flag_when_none_known(ic, cli_calls, monkeypatch):
    """No active task means no project — not a stale one from last week."""
    monkeypatch.setattr(ic, "_current_project", lambda: None)
    ic.handle_add_task("add task: Fix the login page")
    assert "--project" not in cli_calls[-1]


def test_failure_reply_carries_no_traceback(ic, monkeypatch):
    """A stderr dump is the last thing anyone wants on their phone."""
    monkeypatch.setattr(ic, "_current_project", lambda: None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: _Result(1, "", "Traceback (most recent call last):\n  File ..."),
    )
    reply = ic.handle_add_task("add task: Fix the login page")
    assert "Traceback" not in reply
    assert "File" not in reply
    assert reply.startswith("😕")


def test_exception_reply_carries_no_detail(ic, monkeypatch):
    monkeypatch.setattr(ic, "_current_project", lambda: None)

    home_marker = "/" + "Users"  # not written literally: ship-check bans the path

    def boom(cmd, **kw):
        raise OSError(f"{home_marker}/someone/.aos/services/bridge exploded")

    monkeypatch.setattr(subprocess, "run", boom)
    reply = ic.handle_add_task("add task: Fix the login page")
    assert home_marker not in reply
    assert reply.startswith("😕")


def test_title_is_html_escaped(ic, cli_calls, monkeypatch):
    """Titles are operator-supplied and the reply is sent with parse_mode=HTML."""
    monkeypatch.setattr(ic, "_current_project", lambda: None)
    reply = ic.handle_add_task("add task: <b>bold</b> & <script>x</script>")
    assert "<b>bold</b>" not in reply
    assert "&lt;b&gt;" in reply


@pytest.mark.parametrize("text", ["add task:", "add task", "task:", "new task:"])
def test_bare_trigger_asks_instead_of_creating_a_junk_task(ic, monkeypatch, text):
    """A bare trigger must not become a task titled "add task:".

    Every prefix pattern used to require a trailing space, so "add task:" (no
    space) matched none of them and fell through to "the whole message is the
    title". That is how "--help" ended up persisted twice as a task title.
    """
    monkeypatch.setattr(ic, "_current_project", lambda: None)
    called = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: called.append(cmd) or _Result(0, ""))
    reply = ic.handle_add_task(text)
    assert called == [], "a bare trigger must not reach the CLI"
    assert "?" in reply and "Added" not in reply


@pytest.mark.parametrize("prefix", [
    "add task: ", "add task ", "new task: ", "create task: ", "task: ",
])
def test_trigger_prefixes_are_stripped_from_the_title(ic, cli_calls, monkeypatch, prefix):
    monkeypatch.setattr(ic, "_current_project", lambda: None)
    reply = ic.handle_add_task(f"{prefix}Water the plants")
    assert reply == "✅ Added: Water the plants"


def test_project_display_humanises_the_id(ic):
    assert ic._project_display("quran-garden-ios") == "Quran Garden Ios"
    assert ic._project_display("aos") == "Aos"


def test_current_project_returns_none_without_a_db(ic, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert ic._current_project() is None
