"""OAuth2 client-credentials lifecycle for carrier packs.

Per-carrier token cache with:

- **expiry−60s margin** — a token is treated as expired 60s before its
  stated expiry, so an in-flight request never carries a dying token.
- **single-flight refresh** — a threading.Lock per manager means a launchd
  restart stampede (or concurrent scheduler threads) fires exactly one
  token request; waiters reuse the refreshed token (double-checked inside
  the lock).
- **persistence in the tracking store** — tokens are written to the
  store's ``tracking_state`` key-value table (key ``oauth:<carrier>``), so
  a process restart reuses a still-valid token instead of hammering the
  carrier's token endpoint.
- **startup auth self-test** — ``auth_self_test`` checks every CONFIGURED
  carrier (configured = all of its manifest's Keychain key names resolve)
  and returns a per-carrier ok/fail report; failures raise an alert via
  ``notify`` so a 4am auto-update can't silently brick the tracker.

The store is duck-typed (see TokenManager docstring) so this module works
against the real ``qareen.tracking.store`` or a test fake.

Compatible with system Python 3.9.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, Optional

from .client import (
    CarrierAuthError,
    CarrierClientError,
    CarrierHTTPError,
    Transport,
    TransportResponse,
    agent_secret_get,
    urllib_transport,
)
from .packs import CarrierPack

logger = logging.getLogger(__name__)

EXPIRY_MARGIN_SECONDS = 60
STATE_KEY_PREFIX = "oauth:"


class OAuthError(CarrierClientError):
    """Token endpoint rejected us or returned an unusable payload."""


class TokenManager:
    """Caches and refreshes one carrier's OAuth2 access token.

    Parameters
    ----------
    pack:
        Loaded CarrierPack with ``auth.model == oauth2_client_credentials``.
    secret_getter:
        ``(keychain key name) -> value or None``; defaults to agent-secret.
    transport:
        Injectable HTTP transport (same contract as ``client.Transport``).
    store:
        Duck-typed tracking store; needs ``get_state(key) -> Optional[str]``
        and ``set_state(key, value)``. None = in-memory cache only.
    clock:
        ``() -> float`` epoch seconds; injectable for tests.
    """

    def __init__(
        self,
        pack: CarrierPack,
        secret_getter: Optional[Callable[[str], Optional[str]]] = None,
        transport: Optional[Transport] = None,
        store: Optional[Any] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.pack = pack
        self._secret_getter = secret_getter or agent_secret_get
        self._transport = transport or urllib_transport
        self._store = store
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._loaded = False  # persisted state read at most once per instance

    # ── public API ────────────────────────────────────────────────────

    def get_token(self) -> str:
        """Return a valid access token, refreshing when necessary.

        Fast path is lock-free; the refresh path takes the lock and
        re-checks validity so only one refresh ever fires (single-flight).
        """
        token = self._valid_token()
        if token is not None:
            return token
        with self._lock:
            token = self._valid_token()
            if token is not None:
                return token
            return self._refresh()

    def invalidate(self) -> None:
        """Drop the cached token (e.g. after a 401) so the next get_token
        refreshes instead of resurrecting the persisted copy."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0
            self._loaded = True  # do not reload the same token from the store

    # ── internals ─────────────────────────────────────────────────────

    def _valid_token(self) -> Optional[str]:
        self._load_persisted()
        if self._token and self._clock() < self._expires_at - EXPIRY_MARGIN_SECONDS:
            return self._token
        return None

    def _refresh(self) -> str:
        client_id, client_secret = self._credentials()
        token_url = self.pack.endpoints.get("token")
        if not token_url:
            raise OAuthError(
                "pack %s: endpoints.token missing for oauth2 pack" % self.pack.slug
            )
        basic = base64.b64encode(
            ("%s:%s" % (client_id, client_secret)).encode()
        ).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        headers = {
            "Authorization": "Basic %s" % basic,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        resp = self._transport("POST", token_url, headers, body)
        data = self._handle_token_response(resp)
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError(
                "pack %s: token response missing access_token" % self.pack.slug
            )
        try:
            expires_in = float(data.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600.0
        self._token = access_token
        self._expires_at = self._clock() + expires_in
        self._persist()
        return access_token

    def _handle_token_response(self, resp: TransportResponse) -> Dict[str, Any]:
        if resp.status == 429:
            from .client import RateLimited, _parse_retry_after

            raise RateLimited(
                retry_after=_parse_retry_after(resp.headers.get("retry-after"))
            )
        if resp.status in (400, 401, 403):
            raise CarrierAuthError(resp.status, "token endpoint rejected credentials")
        if resp.status >= 400:
            raise CarrierHTTPError(resp.status, "token endpoint error")
        try:
            data = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise OAuthError("invalid JSON from token endpoint: %s" % exc)
        if not isinstance(data, dict):
            raise OAuthError("token endpoint returned non-object JSON")
        return data

    def _credentials(self) -> tuple:
        """Resolve (client_id, client_secret) from Keychain key NAMES.

        The manifest may declare explicit ``auth.client_id_key`` /
        ``auth.client_secret_key``; otherwise the names in
        ``auth.keychain_keys`` are split by convention: the name containing
        "SECRET" is the secret, the other is the id.
        """
        auth = self.pack.auth or {}
        names = list(auth.get("keychain_keys") or [])
        id_name = auth.get("client_id_key")
        secret_name = auth.get("client_secret_key")
        if not id_name or not secret_name:
            for name in names:
                if "SECRET" in name.upper() and not secret_name:
                    secret_name = name
                elif not id_name:
                    id_name = name
        if not id_name or not secret_name:
            raise OAuthError(
                "pack %s: cannot determine client id/secret Keychain key names"
                % self.pack.slug
            )
        client_id = self._secret_getter(id_name)
        client_secret = self._secret_getter(secret_name)
        if not client_id or not client_secret:
            raise CarrierAuthError(
                0, "pack %s: OAuth credentials not in Keychain" % self.pack.slug
            )
        return client_id, client_secret

    # ── persistence (store tracking_state) ────────────────────────────

    @property
    def _state_key(self) -> str:
        return STATE_KEY_PREFIX + self.pack.slug

    def _load_persisted(self) -> None:
        if self._loaded or self._store is None:
            return
        self._loaded = True
        try:
            raw = self._store.get_state(self._state_key)
        except Exception:
            logger.debug("oauth state read failed for %s", self.pack.slug)
            return
        if not raw:
            return
        try:
            data = json.loads(raw)
            token = data["access_token"]
            expires_at = float(data["expires_at"])
        except (ValueError, KeyError, TypeError):
            logger.debug("corrupt persisted token for %s ignored", self.pack.slug)
            return
        if self._clock() < expires_at - EXPIRY_MARGIN_SECONDS:
            self._token = token
            self._expires_at = expires_at

    def _persist(self) -> None:
        if self._store is None:
            return
        try:
            self._store.set_state(
                self._state_key,
                json.dumps(
                    {"access_token": self._token, "expires_at": self._expires_at}
                ),
            )
        except Exception:
            logger.warning("oauth state persist failed for %s", self.pack.slug)


# ── startup auth self-test ────────────────────────────────────────────────


def is_configured(
    pack: CarrierPack, secret_getter: Optional[Callable[[str], Optional[str]]] = None
) -> bool:
    """True iff every Keychain key the manifest names resolves to a value.

    Unconfigured carriers are not errors — packs ship before credentials
    exist; the self-test only checks configured ones.
    """
    getter = secret_getter or agent_secret_get
    names = list((pack.auth or {}).get("keychain_keys") or [])
    if not names:
        # Models with no declared keys (e.g. "none") need nothing.
        return (pack.auth or {}).get("model", "none") == "none"
    return all(getter(name) for name in names)


def auth_self_test(
    packs: Dict[str, CarrierPack],
    secret_getter: Optional[Callable[[str], Optional[str]]] = None,
    transport: Optional[Transport] = None,
    store: Optional[Any] = None,
    notifier: Optional[Any] = None,
    clock: Optional[Callable[[], float]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Verify auth for every configured carrier. Returns a report:

        {slug: {"configured": bool, "ok": bool, "error": str | None}}

    - Unconfigured carriers (Keychain keys missing) are reported with
      ``configured: False`` and skipped — never failures.
    - oauth2 packs fetch a real token; api_key/basic packs verify their
      credentials resolve (there is no free "whoami" call to validate
      them against — the first real track call would surface a 401).
    - Every failure raises an alert via *notifier* (``notify.Notifier``)
      when one is provided.
    """
    getter = secret_getter or agent_secret_get
    report: Dict[str, Dict[str, Any]] = {}
    for slug, pack in sorted(packs.items()):
        model = (pack.auth or {}).get("model", "none")
        if not is_configured(pack, getter):
            report[slug] = {"configured": False, "ok": True, "error": None}
            continue
        error: Optional[str] = None
        if model == "oauth2_client_credentials":
            manager = TokenManager(
                pack,
                secret_getter=getter,
                transport=transport,
                store=store,
                clock=clock,
            )
            try:
                manager.get_token()
            except Exception as exc:  # report, never crash startup
                error = "%s: %s" % (type(exc).__name__, exc)
        # api_key / basic / none: key resolution already verified above.
        ok = error is None
        report[slug] = {"configured": True, "ok": ok, "error": error}
        if not ok:
            logger.error("tracking auth self-test FAILED for %s: %s", slug, error)
            if notifier is not None:
                try:
                    notifier.alert(
                        "Tracker auth failed: %s" % slug,
                        "Carrier %s failed its startup auth self-test: %s. "
                        "Tracking for this carrier is down until credentials "
                        "are fixed." % (slug, error),
                    )
                except Exception:
                    logger.exception("auth self-test alert failed for %s", slug)
        else:
            logger.info("tracking auth self-test ok: %s", slug)
    return report
