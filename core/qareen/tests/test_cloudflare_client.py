"""Unit tests for the Cloudflare API client (Qareen Remote Access, Phase-1).

Pure-HTTP layer tested with an injected ``httpx.MockTransport`` — no network,
no Keychain, no DB. Coverage per the blueprint test plan:

  * success envelope unwraps ``result`` and sends the Bearer token,
  * ``errors[]`` -> :class:`CloudflareError`,
  * idempotent ``find_tunnel_by_name`` / ``create_tunnel`` (no duplicate POST),
  * ``set_tunnel_config`` body carries the hostname ingress + the required
    ``http_status:404`` catch-all,
  * ``create_access_app`` body uses ``destinations:[{type:'public',uri}]`` and
    never the deprecated ``domain``/``self_hosted_domains`` fields.

Follows the repo convention: plain pytest functions driving coroutines through
a fresh event loop (no pytest-asyncio in this project).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core.qareen.integrations.cloudflare.client import (
    CloudflareClient,
    CloudflareError,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _envelope(result, *, success=True, errors=None, messages=None):
    """Build a standard Cloudflare response envelope."""
    return {
        "success": success,
        "errors": errors or [],
        "messages": messages or [],
        "result": result,
    }


def _client(handler, *, account_id="acct-1", token="tok-abc"):
    return CloudflareClient(
        token,
        account_id=account_id,
        transport=httpx.MockTransport(handler),
    )


def test_success_envelope_unwraps_result_and_sends_bearer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(
            200, json=_envelope({"id": "tok", "status": "active"})
        )

    cf = _client(handler)
    result = _run(cf.verify_token())

    assert result == {"id": "tok", "status": "active"}
    # Bearer auth header is set globally on the client.
    assert seen["auth"] == "Bearer tok-abc"
    # base_url is honored (path includes the /client/v4 prefix + endpoint).
    assert seen["path"].endswith("/client/v4/user/tokens/verify")


def test_error_envelope_raises_cloudflare_error_with_first_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json=_envelope(
                None,
                success=False,
                errors=[
                    {"code": 1000, "message": "Invalid API Token"},
                    {"code": 9999, "message": "secondary"},
                ],
            ),
        )

    cf = _client(handler)
    with pytest.raises(CloudflareError) as excinfo:
        _run(cf.verify_token())

    err = excinfo.value
    # Surfaces errors[0].message verbatim.
    assert str(err) == "Invalid API Token"
    assert err.code == 1000
    assert err.status_code == 403
    assert len(err.errors) == 2


def test_find_tunnel_by_name_match_and_idempotent_create():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))

        if request.method == "GET" and path.endswith("/cfd_tunnel"):
            # Listing is filtered by name and excludes deleted tunnels.
            assert request.url.params.get("name") == "qareen-mac"
            assert request.url.params.get("is_deleted") == "false"
            return httpx.Response(
                200,
                json=_envelope(
                    [
                        {
                            "id": "tun-1",
                            "name": "qareen-mac",
                            "deleted_at": None,
                            # remotely-managed tunnel → eligible for idempotent reuse
                            "config_src": "cloudflare",
                        }
                    ]
                ),
            )
        if request.method == "GET" and path.endswith("/cfd_tunnel/tun-1/token"):
            return httpx.Response(200, json=_envelope("run-token-xyz"))

        raise AssertionError(f"unexpected request: {request.method} {path}")

    cf = _client(handler)

    found = _run(cf.find_tunnel_by_name("qareen-mac"))
    assert found is not None
    assert found["id"] == "tun-1"

    # Idempotent: an existing tunnel is reused and its run-token fetched —
    # never a second POST that would create a duplicate.
    created = _run(cf.create_tunnel("qareen-mac"))
    assert created["id"] == "tun-1"
    assert created["token"] == "run-token-xyz"
    assert all(method != "POST" for method, _ in calls)


def test_set_tunnel_config_body_has_ingress_and_404_catchall():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_envelope({"config": {}}))

    cf = _client(handler)
    _run(cf.set_tunnel_config("tun-1", "aos.example.com"))

    assert captured["method"] == "PUT"
    assert captured["path"].endswith("/cfd_tunnel/tun-1/configurations")

    ingress = captured["body"]["config"]["ingress"]
    assert ingress[0]["hostname"] == "aos.example.com"
    assert ingress[0]["service"] == "http://localhost:4096"
    # camelCase originRequest present on the hostname rule.
    assert "originRequest" in ingress[0]
    # Required catch-all with NO hostname.
    assert ingress[-1] == {"service": "http_status:404"}
    assert "hostname" not in ingress[-1]


def test_create_access_app_body_uses_destinations_public_uri():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/access/apps"):
            # No existing app -> falls through to create.
            return httpx.Response(200, json=_envelope([]))
        if request.method == "POST" and path.endswith("/access/apps"):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200, json=_envelope({"id": "app-1", "aud": "aud-xyz"})
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    cf = _client(handler)
    app = _run(cf.create_access_app("aos.example.com", "idp-1"))
    assert app == {"id": "app-1", "aud": "aud-xyz"}

    body = captured["body"]
    assert body["type"] == "self_hosted"
    # The corrected, non-deprecated hostname field.
    assert body["destinations"] == [{"type": "public", "uri": "aos.example.com"}]
    assert "domain" not in body
    assert "self_hosted_domains" not in body
    assert body["allowed_idps"] == ["idp-1"]
    assert body["app_launcher_visible"] is False
    assert body["auto_redirect_to_identity"] is True
    assert body["session_duration"] == "24h"
