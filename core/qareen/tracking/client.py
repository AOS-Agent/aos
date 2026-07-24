"""Manifest-driven carrier HTTP client (stdlib urllib only).

The client is generic: everything carrier-specific comes from the pack
manifest. Auth models supported:

- ``oauth2_client_credentials`` — token endpoint from ``endpoints.token``,
  client id/secret read from macOS Keychain via ``agent-secret`` (key
  NAMES declared in ``auth.keychain_keys``). Tokens are fetched/cached by
  ``oauth.TokenManager``.
- ``api_key`` — static header. Header name from ``auth.header`` (e.g.
  ``DHL-API-Key``), value from the first of ``auth.keychain_keys``.
- ``basic`` — ``Authorization: Basic base64(key0:key1)`` from the first two
  of ``auth.keychain_keys`` (Canada Post style).
- ``none`` — no auth (email-event pseudo-carriers).

Endpoint extras the manifests may declare (all honored by ``track``):

- ``endpoints.method: POST`` + ``endpoints.body`` — POST carriers (FedEx);
  ``{number}`` is substituted into the body template too.
- ``endpoints.accept`` — overrides the Accept header; XML media types run
  the body through the pack's ``mapper.py`` before returning (Canada Post).

The transport is injectable for tests: any callable with the signature
``transport(method, url, headers, body) -> TransportResponse``. The client
returns parsed JSON; callers map responses with ``engine.normalize_event``
+ the pack's ``response_map``.

Errors are typed so the scheduler can react precisely:

- ``RateLimited``    — HTTP 429; carries ``retry_after`` when the carrier
                       sends a Retry-After header.
- ``CarrierAuthError`` — 401/403 (bad/expired credentials).
- ``CarrierHTTPError`` — any other >= 400 response.
- ``CarrierClientError`` — transport-level failure (DNS, timeout, …).

Compatible with system Python 3.9.
"""

from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import engine, packs
from .packs import CarrierPack

DEFAULT_TIMEOUT = 20
AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "agent-secret"


# ── errors ────────────────────────────────────────────────────────────────


class CarrierClientError(Exception):
    """Transport-level failure (unreachable host, timeout, bad JSON)."""


class CarrierHTTPError(CarrierClientError):
    """Non-2xx response that is neither 429 nor an auth failure."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__("HTTP %d: %s" % (status, message))
        self.status = status


class CarrierAuthError(CarrierHTTPError):
    """401/403 — credentials rejected. Tracked separately so the scheduler
    can surface an auth alert instead of blindly backing off."""


class RateLimited(CarrierHTTPError):
    """HTTP 429. ``retry_after`` is seconds (float) when the carrier sent a
    Retry-After header, else None — the scheduler then applies its own
    exponential backoff."""

    def __init__(self, retry_after: Optional[float] = None, message: str = "") -> None:
        super().__init__(429, message or "rate limited")
        self.retry_after = retry_after


# ── transport ─────────────────────────────────────────────────────────────


@dataclass
class TransportResponse:
    """What a transport callable returns. ``body`` is raw bytes."""

    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""


Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], TransportResponse]


def urllib_transport(
    method: str, url: str, headers: Dict[str, str], body: Optional[bytes]
) -> TransportResponse:
    """Default transport: stdlib urllib. HTTP error statuses are returned
    (not raised) so the client can map them to typed errors; network-level
    failures raise CarrierClientError."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            return TransportResponse(
                status=resp.status,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=resp.read(),
            )
    except urllib.error.HTTPError as exc:
        return TransportResponse(
            status=exc.code,
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            body=exc.read() if hasattr(exc, "read") else b"",
        )
    except (urllib.error.URLError, OSError) as exc:
        raise CarrierClientError("transport failure for %s: %s" % (url, exc))


# ── secrets ───────────────────────────────────────────────────────────────


def agent_secret_get(name: str) -> Optional[str]:
    """Read one secret from macOS Keychain via the agent-secret CLI.

    Returns None when the key is unset or the CLI fails — callers decide
    whether "not configured" is an error (it isn't, for unconfigured packs).
    """
    try:
        result = subprocess.run(
            [str(AGENT_SECRET), "get", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


# ── client ────────────────────────────────────────────────────────────────


class CarrierClient:
    """One carrier, driven entirely by its pack manifest.

    Parameters
    ----------
    pack:
        Loaded CarrierPack.
    secret_getter:
        ``(keychain key name) -> value or None``. Defaults to agent-secret.
    transport:
        Injectable HTTP transport; defaults to urllib.
    token_manager:
        Pre-built ``oauth.TokenManager`` for oauth2 packs (so the scheduler
        can share one cache/persistence across clients). When omitted and
        the pack is oauth2, one is built lazily on first request — without
        a store, tokens then live only for this client's lifetime.
    """

    def __init__(
        self,
        pack: CarrierPack,
        secret_getter: Optional[Callable[[str], Optional[str]]] = None,
        transport: Optional[Transport] = None,
        token_manager: Optional[Any] = None,
    ) -> None:
        self.pack = pack
        self._secret_getter = secret_getter or agent_secret_get
        self._transport = transport or urllib_transport
        self._token_manager = token_manager

    # ── public API ────────────────────────────────────────────────────

    def track(self, number: str) -> Dict[str, Any]:
        """Fetch the raw track response for *number* as a parsed dict.

        The number is canonicalized before substitution into the manifest's
        ``endpoints.track`` template. Manifest extras honored here:

        - ``endpoints.method`` — HTTP verb (default GET; FedEx declares
          POST with a JSON ``endpoints.body`` template, ``{number}``
          substituted the same way).
        - ``endpoints.accept`` — the Accept header. XML media types
          (Canada Post) route the body through the pack's ``mapper.py``
          (``track_xml_to_dict``) so callers always get the response_map
          dict shape, never raw XML.

        Raises the typed errors above.
        """
        canonical = engine.canonicalize(number)
        endpoints = self.pack.endpoints or {}
        url = endpoints["track"].replace("{number}", canonical)
        method = str(endpoints.get("method") or "GET").upper()
        accept = endpoints.get("accept")
        headers = {"Accept": accept or "application/json"}
        headers.update(self._auth_headers())
        body: Optional[bytes] = None
        if method != "GET":
            template = endpoints.get("body")
            if template:
                body = str(template).replace("{number}", canonical).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
        resp = self._transport(method, url, headers, body)
        self._check_status(resp)
        if accept and "xml" in str(accept).lower():
            mapper = packs.load_mapper(self.pack)
            if mapper is None:
                raise CarrierClientError(
                    "pack %s: endpoints.accept is XML but the pack has no mapper.py"
                    % self.pack.slug
                )
            try:
                data = mapper(resp.body.decode("utf-8"))
            except CarrierClientError:
                raise
            except Exception as exc:
                raise CarrierClientError(
                    "pack %s mapper failed on carrier response: %s"
                    % (self.pack.slug, exc)
                )
            if not isinstance(data, dict):
                raise CarrierClientError(
                    "pack %s mapper returned %r, expected a dict"
                    % (self.pack.slug, type(data))
                )
            return data
        return self._parse_json(resp)

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Authenticated raw request against any carrier endpoint (used by
        oauth token fetches and future push-registration calls)."""
        all_headers = dict(headers or {})
        resp = self._transport(method, url, all_headers, body)
        return self._handle(resp)

    # ── auth ──────────────────────────────────────────────────────────

    def _auth_headers(self) -> Dict[str, str]:
        model = (self.pack.auth or {}).get("model", "none")
        if model == "none":
            return {}
        if model == "api_key":
            header = self.pack.auth.get("header")
            if not header:
                raise CarrierClientError(
                    "pack %s: auth.model api_key requires auth.header in the manifest"
                    % self.pack.slug
                )
            key = self._first_secret()
            if not key:
                raise CarrierAuthError(
                    0, "pack %s: API key not in Keychain" % self.pack.slug
                )
            return {header: key}
        if model == "basic":
            keys = self._secrets()
            if len(keys) < 2 or not all(keys[:2]):
                raise CarrierAuthError(
                    0, "pack %s: basic-auth keys not in Keychain" % self.pack.slug
                )
            token = base64.b64encode(
                ("%s:%s" % (keys[0], keys[1])).encode()
            ).decode()
            return {"Authorization": "Basic %s" % token}
        if model == "oauth2_client_credentials":
            manager = self._get_token_manager()
            return {"Authorization": "Bearer %s" % manager.get_token()}
        raise CarrierClientError(
            "pack %s: unsupported auth model %r" % (self.pack.slug, model)
        )

    def _get_token_manager(self) -> Any:
        if self._token_manager is None:
            from . import oauth

            self._token_manager = oauth.TokenManager(
                self.pack,
                secret_getter=self._secret_getter,
                transport=self._transport,
            )
        return self._token_manager

    def _secrets(self) -> list:
        names = list((self.pack.auth or {}).get("keychain_keys") or [])
        return [self._secret_getter(name) for name in names]

    def _first_secret(self) -> Optional[str]:
        values = self._secrets()
        return values[0] if values else None

    # ── response handling ─────────────────────────────────────────────

    @staticmethod
    def _check_status(resp: TransportResponse) -> None:
        """Map HTTP error statuses to the typed errors."""
        if resp.status == 429:
            raise RateLimited(
                retry_after=_parse_retry_after(resp.headers.get("retry-after"))
            )
        if resp.status in (401, 403):
            raise CarrierAuthError(resp.status, "credentials rejected")
        if resp.status >= 400:
            raise CarrierHTTPError(resp.status, _snippet(resp.body))

    @staticmethod
    def _parse_json(resp: TransportResponse) -> Dict[str, Any]:
        try:
            data = json.loads(resp.body.decode("utf-8")) if resp.body else {}
        except (ValueError, UnicodeDecodeError) as exc:
            raise CarrierClientError("invalid JSON in carrier response: %s" % exc)
        if not isinstance(data, dict):
            raise CarrierClientError(
                "carrier response is not a JSON object: %r" % type(data)
            )
        return data

    @classmethod
    def _handle(cls, resp: TransportResponse) -> Dict[str, Any]:
        cls._check_status(resp)
        return cls._parse_json(resp)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Retry-After header → seconds. Only the integer-seconds form is
    parsed; HTTP-date forms return None (scheduler falls back to its own
    backoff)."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


def _snippet(body: bytes, limit: int = 200) -> str:
    try:
        return body[:limit].decode("utf-8", "replace")
    except Exception:  # pragma: no cover - defensive
        return "<unreadable body>"
