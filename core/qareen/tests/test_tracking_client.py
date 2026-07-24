"""Tests for the carrier HTTP client and the OAuth2 token lifecycle.

Everything runs on injected transports, secret getters, and clocks — no
network, no Keychain, no wall-clock dependence.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import client as client_mod  # noqa: E402
from qareen.tracking.client import (  # noqa: E402
    CarrierAuthError,
    CarrierClient,
    CarrierClientError,
    CarrierHTTPError,
    RateLimited,
    TransportResponse,
)
from qareen.tracking.oauth import TokenManager, auth_self_test  # noqa: E402
from qareen.tracking.packs import CarrierPack  # noqa: E402

# ── helpers ───────────────────────────────────────────────────────────────


def _pack(slug="ups", auth=None, endpoints=None, rate_limits=None):
    manifest = {
        "display_name": slug.title(),
        "auth": auth or {"model": "none"},
        "endpoints": endpoints
        or {"base": "https://api.test", "track": "https://api.test/track/{number}"},
        "tracking": {"patterns": [], "check_digit": None},
        "capabilities": {"edd": True, "pod": False, "push": False},
        "status_map": {},
        "response_map": {},
        "rate_limits": rate_limits or {"requests_per_day": 100, "min_interval_seconds": 0},
        "retention": {"delete_days_after_delivery": None},
    }
    return CarrierPack(slug=slug, path=Path("."), manifest=manifest)


class FakeTransport:
    """Records calls; replays queued responses or delegates to a handler."""

    def __init__(self, responses=None, handler=None):
        self.calls = []
        self._responses = list(responses or [])
        self._handler = handler

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if self._handler is not None:
            return self._handler(method, url, headers, body)
        assert self._responses, "no canned response left for %s %s" % (method, url)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _json_response(payload, status=200, headers=None):
    return TransportResponse(
        status=status,
        headers=headers or {},
        body=json.dumps(payload).encode(),
    )


def _secrets(mapping):
    return lambda name: mapping.get(name)


class FakeStore:
    def __init__(self):
        self.state = {}

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value


OAUTH_AUTH = {
    "model": "oauth2_client_credentials",
    "keychain_keys": ["UPS_CLIENT_ID_PROD", "UPS_CLIENT_SECRET_PROD"],
}
OAUTH_SECRETS = {"UPS_CLIENT_ID_PROD": "cid", "UPS_CLIENT_SECRET_PROD": "csec"}
OAUTH_ENDPOINTS = {
    "base": "https://api.ups.com",
    "token": "https://api.ups.com/oauth/token",
    "track": "https://api.ups.com/track/{number}",
}


# ── client: auth models ───────────────────────────────────────────────────


def test_api_key_auth_uses_manifest_header_and_canonical_number():
    pack = _pack(
        slug="dhl",
        auth={"model": "api_key", "header": "DHL-API-Key", "keychain_keys": ["DHL_KEY"]},
    )
    transport = FakeTransport([_json_response({"shipments": []})])
    c = CarrierClient(pack, secret_getter=_secrets({"DHL_KEY": "s3cret"}), transport=transport)

    result = c.track("ab c-123")

    assert result == {"shipments": []}
    call = transport.calls[0]
    assert call["headers"]["DHL-API-Key"] == "s3cret"
    assert call["url"].endswith("/track/ABC123")  # canonicalized


def test_basic_auth_header():
    pack = _pack(
        slug="canadapost",
        auth={"model": "basic", "keychain_keys": ["CP_USER", "CP_PASS"]},
    )
    transport = FakeTransport([_json_response({"ok": True})])
    c = CarrierClient(
        pack, secret_getter=_secrets({"CP_USER": "u", "CP_PASS": "p"}), transport=transport
    )
    c.track("123")
    import base64

    expected = "Basic " + base64.b64encode(b"u:p").decode()
    assert transport.calls[0]["headers"]["Authorization"] == expected


def test_api_key_missing_secret_raises_auth_error():
    pack = _pack(auth={"model": "api_key", "header": "X-Key", "keychain_keys": ["MISSING"]})
    c = CarrierClient(pack, secret_getter=_secrets({}), transport=FakeTransport())
    with pytest.raises(CarrierAuthError):
        c.track("123")


def test_oauth2_client_fetches_token_then_sends_bearer():
    pack = _pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS)
    transport = FakeTransport(
        [
            _json_response({"access_token": "tok-1", "expires_in": 3600}),
            _json_response({"events": []}),
            _json_response({"events": []}),
        ]
    )
    c = CarrierClient(pack, secret_getter=_secrets(OAUTH_SECRETS), transport=transport)

    c.track("1Z123")
    c.track("1Z123")

    # one token request, then two track calls with the cached token
    assert transport.calls[0]["url"] == OAUTH_ENDPOINTS["token"]
    assert transport.calls[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert b"grant_type=client_credentials" in transport.calls[0]["body"]
    assert [call["url"] for call in transport.calls[1:]] == [
        "https://api.ups.com/track/1Z123",
        "https://api.ups.com/track/1Z123",
    ]
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer tok-1"
    assert transport.calls[2]["headers"]["Authorization"] == "Bearer tok-1"


# ── client: typed errors ──────────────────────────────────────────────────


def test_429_raises_rate_limited_with_retry_after():
    pack = _pack()
    transport = FakeTransport(
        [TransportResponse(status=429, headers={"retry-after": "120"}, body=b"")]
    )
    c = CarrierClient(pack, transport=transport)
    with pytest.raises(RateLimited) as exc_info:
        c.track("123")
    assert exc_info.value.retry_after == 120.0


def test_429_without_retry_after():
    pack = _pack()
    transport = FakeTransport([TransportResponse(status=429, headers={}, body=b"")])
    c = CarrierClient(pack, transport=transport)
    with pytest.raises(RateLimited) as exc_info:
        c.track("123")
    assert exc_info.value.retry_after is None


def test_401_raises_auth_error():
    c = CarrierClient(_pack(), transport=FakeTransport([_json_response({}, status=401)]))
    with pytest.raises(CarrierAuthError):
        c.track("123")


def test_500_raises_http_error():
    c = CarrierClient(
        _pack(), transport=FakeTransport([TransportResponse(status=500, body=b"boom")])
    )
    with pytest.raises(CarrierHTTPError) as exc_info:
        c.track("123")
    assert exc_info.value.status == 500


def test_invalid_json_raises_client_error():
    c = CarrierClient(
        _pack(), transport=FakeTransport([TransportResponse(status=200, body=b"<html>")])
    )
    with pytest.raises(CarrierClientError):
        c.track("123")


def test_injected_transport_exception_propagates():
    # injected transports surface their own errors; the default urllib
    # transport wraps network failures in CarrierClientError (tested below)
    c = CarrierClient(_pack(), transport=FakeTransport([OSError("dns failed")]))
    with pytest.raises(OSError):
        c.track("123")


def test_urllib_transport_wraps_network_errors():
    def boom(req, timeout):
        import urllib.error

        raise urllib.error.URLError("no route")

    import unittest.mock as mock

    pack = _pack()
    c = CarrierClient(pack)  # default urllib transport
    with mock.patch.object(client_mod.urllib.request, "urlopen", side_effect=boom):
        with pytest.raises(CarrierClientError):
            c.track("123")


# ── oauth: cache, expiry margin, persistence ──────────────────────────────


def test_token_cache_reuses_valid_token():
    clock = [1000.0]
    transport = FakeTransport([_json_response({"access_token": "t1", "expires_in": 3600})])
    tm = TokenManager(
        _pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS),
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport,
        clock=lambda: clock[0],
    )
    assert tm.get_token() == "t1"
    clock[0] += 3000  # still valid (margin is 60s)
    assert tm.get_token() == "t1"
    assert len(transport.calls) == 1


def test_token_refreshes_within_expiry_margin():
    clock = [1000.0]
    transport = FakeTransport(
        [
            _json_response({"access_token": "t1", "expires_in": 3600}),
            _json_response({"access_token": "t2", "expires_in": 3600}),
        ]
    )
    tm = TokenManager(
        _pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS),
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport,
        clock=lambda: clock[0],
    )
    assert tm.get_token() == "t1"
    clock[0] += 3600 - 30  # inside the 60s margin → treated as expired
    assert tm.get_token() == "t2"
    assert len(transport.calls) == 2


def test_token_persisted_in_store_and_reused_by_new_manager():
    clock = [1000.0]
    store = FakeStore()
    transport = FakeTransport([_json_response({"access_token": "t1", "expires_in": 3600})])
    kwargs = dict(
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport,
        store=store,
        clock=lambda: clock[0],
    )
    tm1 = TokenManager(_pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS), **kwargs)
    assert tm1.get_token() == "t1"
    assert "oauth:ups" in store.state

    # "restart": a fresh manager with a transport that must NOT be called
    transport2 = FakeTransport()
    tm2 = TokenManager(
        _pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS),
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport2,
        store=store,
        clock=lambda: clock[0],
    )
    assert tm2.get_token() == "t1"
    assert transport2.calls == []


def test_expired_persisted_token_triggers_refresh():
    clock = [1000.0]
    store = FakeStore()
    store.set_state("oauth:ups", json.dumps({"access_token": "old", "expires_at": 1050.0}))
    transport = FakeTransport([_json_response({"access_token": "new", "expires_in": 3600})])
    tm = TokenManager(
        _pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS),
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport,
        store=store,
        clock=lambda: clock[0],
    )
    assert tm.get_token() == "new"
    assert len(transport.calls) == 1


def test_single_flight_refresh_under_thread_stampede():
    """8 threads racing an expired token must fire exactly ONE refresh."""
    transport = FakeTransport()

    def slow_token(method, url, headers, body):
        time.sleep(0.2)  # widen the race window
        return _json_response({"access_token": "stampede-token", "expires_in": 3600})

    transport._handler = slow_token
    tm = TokenManager(
        _pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS),
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport,
        clock=time.time,
    )
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(tm.get_token()))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["stampede-token"] * 8
    assert len(transport.calls) == 1


def test_invalidate_forces_refresh_not_persisted_resurrection():
    clock = [1000.0]
    store = FakeStore()
    transport = FakeTransport(
        [
            _json_response({"access_token": "t1", "expires_in": 3600}),
            _json_response({"access_token": "t2", "expires_in": 3600}),
        ]
    )
    tm = TokenManager(
        _pack(auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS),
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport,
        store=store,
        clock=lambda: clock[0],
    )
    assert tm.get_token() == "t1"
    tm.invalidate()  # e.g. after a 401 from the carrier
    assert tm.get_token() == "t2"


# ── oauth: startup auth self-test ─────────────────────────────────────────


def test_self_test_ok_and_unconfigured():
    packs = {
        "ups": _pack("ups", auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS),
        "dhl": _pack(
            "dhl",
            auth={"model": "api_key", "header": "DHL-API-Key", "keychain_keys": ["DHL_KEY"]},
        ),
        "fedex": _pack(
            "fedex",
            auth={
                "model": "oauth2_client_credentials",
                "keychain_keys": ["FDX_ID", "FDX_SECRET"],
            },
            endpoints={
                "base": "https://api.fedex.com",
                "token": "https://api.fedex.com/oauth/token",
                "track": "https://api.fedex.com/track/{number}",
            },
        ),
    }
    # fedex keys absent → unconfigured → skipped, not a failure
    secrets = _secrets(dict(OAUTH_SECRETS, DHL_KEY="k"))
    transport = FakeTransport([_json_response({"access_token": "t", "expires_in": 3600})])

    report = auth_self_test(packs, secret_getter=secrets, transport=transport)

    assert report["ups"] == {"configured": True, "ok": True, "error": None}
    assert report["dhl"] == {"configured": True, "ok": True, "error": None}
    assert report["fedex"]["configured"] is False
    assert report["fedex"]["ok"] is True
    assert len(transport.calls) == 1  # only ups fetched a token


def test_self_test_failure_reports_and_alerts():
    packs = {"ups": _pack("ups", auth=OAUTH_AUTH, endpoints=OAUTH_ENDPOINTS)}
    transport = FakeTransport([_json_response({"error": "invalid_client"}, status=401)])
    alerts = []

    class FakeNotifier:
        def alert(self, title, body):
            alerts.append((title, body))
            return True

    report = auth_self_test(
        packs,
        secret_getter=_secrets(OAUTH_SECRETS),
        transport=transport,
        notifier=FakeNotifier(),
    )

    assert report["ups"]["ok"] is False
    assert "401" in report["ups"]["error"] or "Auth" in report["ups"]["error"]
    assert len(alerts) == 1
    assert "ups" in alerts[0][0]
