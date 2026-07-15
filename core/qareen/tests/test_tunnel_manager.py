"""Unit tests for TunnelManager — orchestration core (Qareen Remote Access).

Per the blueprint test plan, this covers the parts of TunnelManager that are
pure logic / IO-shaping and can be exercised without launchctl, Cloudflare, or
the Keychain actually running:

  * ``_rebind_qareen`` rewrites BOTH the ``--host`` ProgramArguments value AND
    the ``AOS_QAREEN_HOST`` env string in a fixture plist,
  * ``disconnect`` stops the connector BEFORE deleting CF resources, deletes the
    Keychain keys, and ALWAYS rebinds to 0.0.0.0 (even on partial failure),
  * ``_emit`` pushes a ``RemoteAccessProgress`` event to the bus,
  * the agent-secret helpers shell out via a (mocked) subprocess.

Repo convention: plain pytest functions driving coroutines through a fresh
event loop (no pytest-asyncio in this project).
"""

from __future__ import annotations

import asyncio
import plistlib
import subprocess
import sys
from pathlib import Path
from unittest import mock

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.events.types import RemoteAccessProgress  # noqa: E402
from qareen.services import tunnel_manager as tm_mod  # noqa: E402
from qareen.services.tunnel_manager import TunnelManager  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeBus:
    """Collects emitted events for assertions."""

    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class FakeState:
    """In-memory stand-in for RemoteAccessState."""

    def __init__(self, initial=None):
        self.data = dict(initial or {"status": "disconnected"})
        self.cleared = False

    def get(self):
        return dict(self.data)

    def upsert(self, **fields):
        self.data.update(fields)
        return dict(self.data)

    def set_status(self, status, error=None):
        self.data["status"] = status
        self.data["error_message"] = error
        return dict(self.data)

    def clear(self):
        self.cleared = True
        self.data = {"status": "disconnected"}
        return dict(self.data)


# ---------------------------------------------------------------------------
# _rebind_qareen
# ---------------------------------------------------------------------------
_FIXTURE_PLIST = {
    "Label": "com.aos.qareen",
    "ProgramArguments": [
        "/python", "-m", "uvicorn", "qareen.main:app",
        "--host", "0.0.0.0", "--port", "4096",
    ],
    "EnvironmentVariables": {
        "PATH": "/opt/homebrew/bin",
        "AOS_QAREEN_HOST": "0.0.0.0",
    },
}


def _write_fixture_plist(path: Path) -> None:
    with open(path, "wb") as fh:
        plistlib.dump(dict(_FIXTURE_PLIST), fh)


def test_rebind_rewrites_both_host_and_env(tmp_path):
    """_rebind_qareen rewrites the --host arg AND AOS_QAREEN_HOST."""
    plist = tmp_path / "com.aos.qareen.plist"
    _write_fixture_plist(plist)

    tm = TunnelManager(FakeBus(), FakeState())
    with mock.patch.object(tm_mod, "QAREEN_PLIST_PATH", plist), \
         mock.patch.object(TunnelManager, "_launchctl", return_value=None) as kick:
        tm._rebind_qareen("127.0.0.1")

    with open(plist, "rb") as fh:
        data = plistlib.load(fh)

    args = data["ProgramArguments"]
    host_idx = args.index("--host")
    assert args[host_idx + 1] == "127.0.0.1"
    assert data["EnvironmentVariables"]["AOS_QAREEN_HOST"] == "127.0.0.1"
    # Unrelated args/env preserved.
    assert "--port" in args and args[args.index("--port") + 1] == "4096"
    assert data["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin"
    # Applied via kickstart.
    assert kick.called


def test_rebind_adds_env_when_missing(tmp_path):
    """If AOS_QAREEN_HOST is absent it is added (template predates migration)."""
    fixture = dict(_FIXTURE_PLIST)
    fixture["EnvironmentVariables"] = {"PATH": "/opt/homebrew/bin"}
    plist = tmp_path / "com.aos.qareen.plist"
    with open(plist, "wb") as fh:
        plistlib.dump(fixture, fh)

    tm = TunnelManager(FakeBus(), FakeState())
    with mock.patch.object(tm_mod, "QAREEN_PLIST_PATH", plist), \
         mock.patch.object(TunnelManager, "_launchctl", return_value=None):
        tm._rebind_qareen("0.0.0.0")

    with open(plist, "rb") as fh:
        data = plistlib.load(fh)
    assert data["EnvironmentVariables"]["AOS_QAREEN_HOST"] == "0.0.0.0"


# ---------------------------------------------------------------------------
# disconnect — teardown order + secret deletes + finally-rebind
# ---------------------------------------------------------------------------
class _FakeCF:
    """Records teardown so we can assert it ran after the connector stopped."""

    def __init__(self, order, *args, **kwargs):
        self._order = order

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def teardown(self, **kwargs):
        self._order.append(("cf_teardown", kwargs))


def test_disconnect_stops_connector_before_cf_teardown_and_rebinds(tmp_path):
    """Connector booted out BEFORE CF delete; secrets deleted; rebind 0.0.0.0 last."""
    order: list = []

    state = FakeState({
        "status": "connected",
        "account_id": "acct123",
        "zone_id": "zone123",
        "tunnel_id": "tun123",
        "access_app_id": "app123",
        "dns_record_id": "dns123",
        "policy_id": "pol123",
        "hostname": "aos.example.com",
    })
    tm = TunnelManager(FakeBus(), state)

    def fake_bootout(label):
        order.append(("bootout", label))

    def fake_rebind(host):
        order.append(("rebind", host))

    async def fake_secret_get(key):
        order.append(("secret_get", key))
        return "fake-token"

    async def fake_secret_delete(key):
        order.append(("secret_delete", key))
        return True

    def fake_cf_factory(*a, **k):
        return _FakeCF(order, *a, **k)

    with mock.patch.object(tm, "_bootout_service", side_effect=fake_bootout), \
         mock.patch.object(tm, "_rebind_qareen", side_effect=fake_rebind), \
         mock.patch.object(tm, "_secret_get", side_effect=fake_secret_get), \
         mock.patch.object(tm, "_secret_delete", side_effect=fake_secret_delete), \
         mock.patch.object(tm_mod, "CloudflareClient", side_effect=fake_cf_factory), \
         mock.patch.object(tm_mod.asyncio, "sleep", new=mock.AsyncMock()):
        _run(tm.disconnect())

    kinds = [o[0] for o in order]
    # Connector stopped before CF teardown.
    assert kinds.index("bootout") < kinds.index("cf_teardown")
    # Both Keychain secrets deleted (tunnel-token then api-token).
    deleted = [o[1] for o in order if o[0] == "secret_delete"]
    assert tm_mod.CF_TUNNEL_TOKEN_KEY in deleted
    assert tm_mod.CF_TOKEN_KEY in deleted
    # Teardown got the reverse-order resource IDs.
    teardown_kwargs = next(o[1] for o in order if o[0] == "cf_teardown")
    assert teardown_kwargs["tunnel_id"] == "tun123"
    assert teardown_kwargs["policy_id"] == "pol123"
    # State cleared and bind restored to 0.0.0.0 as the final action.
    assert state.cleared
    assert ("rebind", "0.0.0.0") in order
    assert kinds[-1] == "rebind"


def test_disconnect_rebinds_even_on_cf_failure(tmp_path):
    """A CF teardown blow-up must still restore the 0.0.0.0 bind (finally)."""
    order: list = []
    state = FakeState({"status": "connected", "account_id": "a", "hostname": "h"})
    tm = TunnelManager(FakeBus(), state)

    class _BoomCF(_FakeCF):
        async def teardown(self, **kwargs):
            raise RuntimeError("boom")

    with mock.patch.object(tm, "_bootout_service", side_effect=lambda l: order.append("bootout")), \
         mock.patch.object(tm, "_rebind_qareen", side_effect=lambda h: order.append(("rebind", h))), \
         mock.patch.object(tm, "_secret_get", new=mock.AsyncMock(return_value="tok")), \
         mock.patch.object(tm, "_secret_delete", new=mock.AsyncMock(return_value=True)), \
         mock.patch.object(tm_mod, "CloudflareClient", side_effect=lambda *a, **k: _BoomCF(order)), \
         mock.patch.object(tm_mod.asyncio, "sleep", new=mock.AsyncMock()):
        try:
            _run(tm.disconnect())
        except RuntimeError:
            pass

    assert ("rebind", "0.0.0.0") in order


# ---------------------------------------------------------------------------
# _emit
# ---------------------------------------------------------------------------
def test_emit_pushes_remote_access_progress():
    """_emit enqueues a RemoteAccessProgress carrying step/status/message."""
    bus = FakeBus()
    tm = TunnelManager(bus, FakeState())
    _run(tm._emit("tunnel", "done", "Tunnel ready", detail="x"))

    assert len(bus.events) == 1
    ev = bus.events[0]
    assert isinstance(ev, RemoteAccessProgress)
    assert ev.event_type == "remote_access.progress"
    assert ev.step == "tunnel"
    assert ev.status == "done"
    assert ev.message == "Tunnel ready"
    assert ev.detail == "x"


# ---------------------------------------------------------------------------
# agent-secret helpers (mocked subprocess)
# ---------------------------------------------------------------------------
def test_secret_set_get_delete_mocked():
    """_secret_set/get/delete shell out to agent-secret (subprocess mocked)."""
    tm = TunnelManager(FakeBus(), FakeState())

    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="my-token\n", stderr="")
    with mock.patch.object(tm_mod.subprocess, "run", return_value=ok) as run:
        assert _run(tm._secret_set("k", "v")) is True
        assert _run(tm._secret_get("k")) == "my-token"
        assert _run(tm._secret_delete("k")) is True

    # Verify the agent-secret binary path + verbs were used.
    calls = [c.args[0] for c in run.call_args_list]
    assert calls[0][:2] == [str(tm_mod.AGENT_SECRET), "set"]
    assert calls[1][:2] == [str(tm_mod.AGENT_SECRET), "get"]
    assert calls[2][:2] == [str(tm_mod.AGENT_SECRET), "delete"]
    # 5s timeout enforced on every call.
    for c in run.call_args_list:
        assert c.kwargs.get("timeout") == tm_mod.SECRET_TIMEOUT


def test_secret_get_returns_none_on_error_output():
    """A non-zero exit or 'Error...' stdout yields None (not a fake secret)."""
    tm = TunnelManager(FakeBus(), FakeState())
    bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="nope")
    with mock.patch.object(tm_mod.subprocess, "run", return_value=bad):
        assert _run(tm._secret_get("missing")) is None
