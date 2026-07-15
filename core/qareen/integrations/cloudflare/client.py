"""Async Cloudflare API client for Qareen Remote Access (Phase-1).

A thin, pure API layer over the Cloudflare v4 REST API. It owns NOTHING but
HTTP: no Keychain, no SQLite, no launchctl. Secrets (the scoped API token) are
passed in by the caller; the connector run-token returned by ``create_tunnel``
is handed straight back for the caller to stash in the Keychain.

Every call goes through :meth:`CloudflareClient._request`, which enforces the
standard Cloudflare envelope ``{success, errors, messages, result}`` and raises
:class:`CloudflareError` (carrying ``errors[0].message``) on failure.

All ``create_*``/``upsert_*``/``ensure_*`` methods are idempotent — they GET and
match an existing resource before creating a new one — and :meth:`teardown`
deletes resources in reverse creation order while preserving the shared
``onetimepin`` identity provider.

Base URL: ``https://api.cloudflare.com/client/v4``.
Recon-confirmed corrections vs. the original spec:
  * Access apps MUST use ``destinations:[{type:'public',uri}]`` — the legacy
    ``domain``/``self_hosted_domains`` fields are deprecated and ignored.
  * The ``onetimepin`` IdP is no longer auto-created; GET-then-POST it.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

BASE_URL = "https://api.cloudflare.com/client/v4"
DEFAULT_TIMEOUT = 30.0

# Ingress origin Qareen listens on locally.
DEFAULT_SERVICE = "http://localhost:4096"

ACCESS_APP_NAME = "Qareen ({hostname})"
ACCESS_POLICY_NAME = "Qareen allowed users"
OTP_IDP_NAME = "One-time PIN login"


class CloudflareError(Exception):
    """Raised when the Cloudflare API returns ``success: false``.

    Carries the first error's message (used as the exception string), plus the
    raw ``errors`` list, the Cloudflare error ``code`` and the HTTP status for
    callers that want to branch on them.
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        errors: Optional[list] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.errors = errors or []
        self.status_code = status_code


class CloudflareClient:
    """Async wrapper around the Cloudflare v4 API.

    Args:
        token: a scoped Cloudflare API token (Bearer auth). Never logged.
        account_id: the account the resources live under. Optional at
            construction — it can be set later (e.g. after resolving the zone)
            via the ``account_id`` property. Account-scoped calls raise
            :class:`CloudflareError` if it is still unset.
        timeout: per-request timeout in seconds.
        transport: optional ``httpx`` transport (used by tests to inject an
            ``httpx.MockTransport``); ``None`` uses the real network transport.

    Usable as an async context manager::

        async with CloudflareClient(token, account_id=aid) as cf:
            await cf.verify_token()
    """

    def __init__(
        self,
        token: str,
        *,
        account_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._token = token
        self._account_id = account_id
        self._timeout = timeout
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Lifecycle / plumbing
    # ------------------------------------------------------------------
    @property
    def account_id(self) -> Optional[str]:
        return self._account_id

    @account_id.setter
    def account_id(self, value: Optional[str]) -> None:
        self._account_id = value

    def _acct(self) -> str:
        if not self._account_id:
            raise CloudflareError("account_id is not set on the CloudflareClient")
        return self._account_id

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "CloudflareClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        with_info: bool = False,
    ) -> Any:
        """Issue a request and unwrap the Cloudflare envelope.

        Returns ``result`` (a dict, a list, a scalar, or ``None`` depending on
        the endpoint). Raises :class:`CloudflareError` when ``success`` is not
        true, surfacing ``errors[0].message``.

        When ``with_info`` is true, returns ``(result, result_info)`` instead so
        paginated list callers can read ``result_info.total_pages`` to walk every
        page (see :meth:`_paginate`).
        """
        resp = await self._http().request(method, path, params=params, json=json)
        try:
            data = resp.json()
        except ValueError:
            raise CloudflareError(
                f"Non-JSON response from Cloudflare (HTTP {resp.status_code})",
                status_code=resp.status_code,
            )

        if not isinstance(data, dict) or not data.get("success", False):
            errors = data.get("errors") if isinstance(data, dict) else None
            errors = errors or []
            if errors:
                first = errors[0]
                message = (
                    first.get("message")
                    if isinstance(first, dict)
                    else str(first)
                ) or "Unknown Cloudflare API error"
                code = first.get("code") if isinstance(first, dict) else None
            else:
                message = f"Cloudflare API error (HTTP {resp.status_code})"
                code = None
            raise CloudflareError(
                message,
                code=code,
                errors=errors,
                status_code=resp.status_code,
            )
        result = data.get("result")
        if with_info:
            return result, data.get("result_info")
        return result

    async def _paginate(
        self,
        path: str,
        *,
        params: Optional[dict] = None,
        per_page: int = 50,
    ) -> list:
        """GET every page of a paginated Cloudflare list endpoint.

        Cloudflare list responses are paginated; a single GET only returns the
        first page. Idempotency scans that conclude "absent" from one page can
        miss an existing resource that fell onto a later page (and then create a
        duplicate). This walks ``page=1,2,...`` at ``per_page`` (max 50),
        concatenating ``result`` until ``result_info.total_pages`` is reached, or
        — if the endpoint omits ``result_info`` — until a short/empty final page
        signals the end. Returns the full list across all pages.
        """
        items: list = []
        page = 1
        while True:
            page_params = dict(params or {})
            page_params["page"] = page
            page_params["per_page"] = per_page
            result, info = await self._request(
                "GET", path, params=page_params, with_info=True
            )
            batch = result or []
            items.extend(batch)
            total_pages = (info or {}).get("total_pages")
            if total_pages is not None:
                if page >= total_pages:
                    break
            elif len(batch) < per_page:
                break
            page += 1
        return items

    # ------------------------------------------------------------------
    # Token / account / zone discovery
    # ------------------------------------------------------------------
    async def verify_token(self) -> dict:
        """GET /user/tokens/verify — ``result.status`` is ``'active'`` when ok."""
        return await self._request("GET", "/user/tokens/verify")

    async def list_accounts(self) -> list:
        """GET /accounts — list of accounts the token can see."""
        return await self._request("GET", "/accounts") or []

    async def list_zones(self) -> list:
        """GET /zones — ``[{id, name, account: {id}}, ...]``."""
        return await self._request("GET", "/zones") or []

    async def get_zone_by_name(self, domain: str) -> Optional[dict]:
        """GET /zones?name=<domain> — first match or ``None``.

        Convenience: when found and no ``account_id`` is set yet, captures it
        from ``result[0].account.id`` so subsequent account-scoped calls work.
        """
        result = await self._request("GET", "/zones", params={"name": domain})
        result = result or []
        if not result:
            return None
        zone = result[0]
        if not self._account_id:
            acct = (zone.get("account") or {}).get("id")
            if acct:
                self._account_id = acct
        return zone

    # ------------------------------------------------------------------
    # Tunnel
    # ------------------------------------------------------------------
    async def find_tunnel_by_name(self, name: str) -> Optional[dict]:
        """GET /accounts/{a}/cfd_tunnel?name=<name> — first live REMOTELY-managed match or None.

        Only a remotely-managed tunnel (``config_src == 'cloudflare'``) is a
        valid reuse target: ``set_tunnel_config`` (PUT .../configurations) is
        rejected by Cloudflare for locally-managed tunnels. A live name-collision
        with a locally-managed tunnel (e.g. a leftover manual
        ``cloudflared tunnel create``) is therefore treated as not-found and
        raises :class:`CloudflareError` with a clear remediation message instead
        of being silently reused (which would fail opaquely at ingress setup).
        """
        result = await self._request(
            "GET",
            f"/accounts/{self._acct()}/cfd_tunnel",
            params={"name": name, "is_deleted": "false"},
        )
        local_collision = False
        for tunnel in result or []:
            if tunnel.get("deleted_at") is not None:
                continue
            if tunnel.get("config_src") == "cloudflare":
                return tunnel
            local_collision = True
        if local_collision:
            raise CloudflareError(
                f"A locally-managed Cloudflare tunnel named '{name}' already "
                "exists; delete it (`cloudflared tunnel delete "
                f"{name}`) so Qareen can create and remotely manage its own."
            )
        return None

    async def _get_tunnel_token(self, tunnel_id: str) -> str:
        """GET /accounts/{a}/cfd_tunnel/{id}/token — connector run-token string."""
        return await self._request(
            "GET", f"/accounts/{self._acct()}/cfd_tunnel/{tunnel_id}/token"
        )

    async def create_tunnel(self, name: str) -> dict:
        """POST /accounts/{a}/cfd_tunnel — remotely-managed tunnel.

        Idempotent: if a live tunnel with ``name`` already exists, reuse it and
        fetch its run-token (the token is only returned inline on create).
        Returns ``{id, token, ...}``; ``token`` is the connector run-token —
        the caller stores it in the Keychain, never in the DB/files.
        """
        existing = await self.find_tunnel_by_name(name)
        if existing is not None:
            if not existing.get("token"):
                token = await self._get_tunnel_token(existing["id"])
                existing = {**existing, "token": token}
            return existing
        result = await self._request(
            "POST",
            f"/accounts/{self._acct()}/cfd_tunnel",
            json={"name": name, "config_src": "cloudflare"},
        )
        if not result.get("token"):
            token = await self._get_tunnel_token(result["id"])
            result = {**result, "token": token}
        return result

    async def set_tunnel_config(
        self,
        tunnel_id: str,
        hostname: str,
        service: str = DEFAULT_SERVICE,
    ) -> None:
        """PUT /accounts/{a}/cfd_tunnel/{id}/configurations.

        Routes ``hostname`` -> ``service`` and appends the REQUIRED
        ``http_status:404`` catch-all (no hostname). PUT replaces the whole
        config, so this is naturally idempotent.
        """
        await self._request(
            "PUT",
            f"/accounts/{self._acct()}/cfd_tunnel/{tunnel_id}/configurations",
            json={
                "config": {
                    "ingress": [
                        {
                            "hostname": hostname,
                            "service": service,
                            "originRequest": {},
                        },
                        {"service": "http_status:404"},
                    ]
                }
            },
        )

    # ------------------------------------------------------------------
    # DNS
    # ------------------------------------------------------------------
    async def upsert_dns_cname(
        self, zone_id: str, hostname: str, tunnel_id: str
    ) -> dict:
        """Create/update a proxied CNAME ``hostname`` -> ``<tunnel>.cfargotunnel.com``.

        ``proxied:true`` is mandatory — a grey-clouded record bypasses Access.
        Idempotent: matches an existing CNAME by name and PUTs it, else POSTs.
        """
        content = f"{tunnel_id}.cfargotunnel.com"
        body = {
            "type": "CNAME",
            "name": hostname,
            "content": content,
            "proxied": True,
        }
        existing = await self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"type": "CNAME", "name": hostname},
        )
        existing = existing or []
        if existing:
            record_id = existing[0]["id"]
            return await self._request(
                "PUT",
                f"/zones/{zone_id}/dns_records/{record_id}",
                json=body,
            )
        return await self._request(
            "POST", f"/zones/{zone_id}/dns_records", json=body
        )

    # ------------------------------------------------------------------
    # Access — identity provider, application, policy
    # ------------------------------------------------------------------
    async def ensure_otp_idp(self) -> str:
        """Return the ``onetimepin`` IdP id, creating it if absent.

        New Zero Trust orgs no longer get OTP automatically, so GET the
        identity providers and reuse any ``type == 'onetimepin'`` entry, else
        POST one. Requires the ``access_acct`` scope.
        """
        providers = await self._paginate(
            f"/accounts/{self._acct()}/access/identity_providers"
        )
        for idp in providers or []:
            if idp.get("type") == "onetimepin":
                return idp["id"]
        result = await self._request(
            "POST",
            f"/accounts/{self._acct()}/access/identity_providers",
            json={"name": OTP_IDP_NAME, "type": "onetimepin", "config": {}},
        )
        return result["id"]

    @staticmethod
    def _app_matches_hostname(app: dict, hostname: str) -> bool:
        """True if an Access app targets ``hostname`` (destinations or legacy)."""
        for dest in app.get("destinations") or []:
            if dest.get("type") == "public" and dest.get("uri") == hostname:
                return True
        # Tolerate legacy-shaped apps that predate the destinations migration.
        if app.get("domain") == hostname:
            return True
        if hostname in (app.get("self_hosted_domains") or []):
            return True
        return False

    async def find_access_app(self, hostname: str) -> Optional[dict]:
        """GET /accounts/{a}/access/apps — first app targeting ``hostname``.

        Paginated to exhaustion: ``/access/apps`` has no server-side hostname
        filter, so an existing app on a later page must not be missed (a missed
        match makes ``create_access_app`` POST a duplicate app on the hostname).
        """
        apps = await self._paginate(
            f"/accounts/{self._acct()}/access/apps"
        )
        for app in apps or []:
            if self._app_matches_hostname(app, hostname):
                return app
        return None

    async def create_access_app(
        self,
        hostname: str,
        idp_id: str,
        session_duration: str = "24h",
    ) -> dict:
        """POST /accounts/{a}/access/apps — self-hosted app for ``hostname``.

        Uses ``destinations:[{type:'public',uri:hostname}]`` (NOT the deprecated
        ``domain``/``self_hosted_domains``). Restricts login to the OTP IdP.
        Idempotent: returns an existing app for ``hostname`` if present.
        Returns ``{id, aud, ...}``.
        """
        existing = await self.find_access_app(hostname)
        if existing is not None:
            return existing
        return await self._request(
            "POST",
            f"/accounts/{self._acct()}/access/apps",
            json={
                "type": "self_hosted",
                "name": ACCESS_APP_NAME.format(hostname=hostname),
                "destinations": [{"type": "public", "uri": hostname}],
                "session_duration": session_duration,
                "app_launcher_visible": False,
                "auto_redirect_to_identity": True,
                "allowed_idps": [idp_id],
            },
        )

    async def create_access_policy(self, app_id: str, emails: list) -> dict:
        """Create/update the app-scoped allow-by-email policy.

        ``include`` uses OR logic; each rule is exactly ``{email:{email:addr}}``.
        Idempotent: if a policy named ``Qareen allowed users`` already exists on
        the app, PUT it with the current email set; otherwise POST a new one.
        """
        body = {
            "name": ACCESS_POLICY_NAME,
            "decision": "allow",
            "include": [{"email": {"email": addr}} for addr in emails],
        }
        existing = await self._paginate(
            f"/accounts/{self._acct()}/access/apps/{app_id}/policies",
        )
        for policy in existing or []:
            if policy.get("name") == ACCESS_POLICY_NAME:
                return await self._request(
                    "PUT",
                    f"/accounts/{self._acct()}/access/apps/{app_id}/policies/{policy['id']}",
                    json=body,
                )
        return await self._request(
            "POST",
            f"/accounts/{self._acct()}/access/apps/{app_id}/policies",
            json=body,
        )

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    async def teardown(
        self,
        zone_id: Optional[str],
        tunnel_id: Optional[str],
        app_id: Optional[str],
        dns_record_id: Optional[str],
        policy_id: Optional[str] = None,
    ) -> None:
        """Delete provisioned resources in reverse creation order.

        Order: policy -> Access app (cascades any remaining nested policies) ->
        DNS record -> tunnel. The shared ``onetimepin`` IdP is intentionally
        NOT deleted. Each delete is best-effort and tolerates an
        already-removed resource (idempotent disconnect); other CF errors
        propagate after the first failure is surfaced.

        NOTE: the caller MUST stop the cloudflared connector (bootout the
        LaunchAgent) BEFORE calling this — deleting a tunnel with live
        connections fails.
        """
        acct = self._acct()
        if policy_id and app_id:
            await self._delete_ignoring_missing(
                f"/accounts/{acct}/access/apps/{app_id}/policies/{policy_id}"
            )
        if app_id:
            await self._delete_ignoring_missing(
                f"/accounts/{acct}/access/apps/{app_id}"
            )
        if dns_record_id and zone_id:
            await self._delete_ignoring_missing(
                f"/zones/{zone_id}/dns_records/{dns_record_id}"
            )
        if tunnel_id:
            await self._delete_ignoring_missing(
                f"/accounts/{acct}/cfd_tunnel/{tunnel_id}"
            )

    async def _delete_ignoring_missing(self, path: str) -> None:
        """DELETE ``path``; swallow a 404/"not found" so teardown is idempotent."""
        try:
            await self._request("DELETE", path)
        except CloudflareError as exc:
            if exc.status_code == 404:
                return
            raise
