"""TunnelManager — orchestration core for Qareen Remote Access (Phase-1).

Owns the connect/disconnect state machine that wires a user's local Qareen
(http://localhost:4096) to ``aos.<domain>`` through a Cloudflare-managed tunnel
gated by Cloudflare Access email-OTP. Responsibilities:

  * Provision/teardown Cloudflare resources via :class:`CloudflareClient`
    (tunnel, ingress, proxied DNS CNAME, OTP IdP, Access app, allow policy).
  * Persist non-secret metadata + CF resource IDs in :class:`RemoteAccessState`.
  * Read/write the macOS Keychain via the ``agent-secret`` subprocess
    (5s timeout). Secrets (CF API token + cloudflared run-token) live ONLY in
    the Keychain — never the DB, a YAML file, a log, or a committed plist.
  * Generate + boot the cloudflared LaunchAgent
    (``bootout -> sleep -> bootstrap -> kickstart`` with ``gui/{uid}/{label}``).
  * Switch Qareen's bind host by rewriting the DEPLOYED qareen plist
    (``--host`` arg + ``AOS_QAREEN_HOST`` env) and kickstarting it. Rebind to
    ``127.0.0.1`` happens LAST in :meth:`connect` (only after the connector is
    health-verified, so we never lock the operator out) and rebind to
    ``0.0.0.0`` happens in a ``finally`` in :meth:`disconnect` so a partial
    teardown still restores LAN/Tailscale reachability.

Live per-step progress streams over the EXISTING SSE bus as
``remote_access.progress`` events. Stored in ``app.state.tunnel_manager``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import plistlib
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, Optional

from qareen.events.types import RemoteAccessProgress
from qareen.integrations.cloudflare.client import CloudflareClient, CloudflareError
from qareen.services.remote_access_state import RemoteAccessState

logger = logging.getLogger(__name__)

# --- agent-secret (Keychain) ------------------------------------------------
AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"
SECRET_TIMEOUT = 5  # seconds — Keychain lookups must never block the loop

CF_TOKEN_KEY = "cloudflare_api_token"
CF_TUNNEL_TOKEN_KEY = "cloudflare_tunnel_token"

# --- LaunchAgents -----------------------------------------------------------
TUNNEL_LABEL = "com.aos.qareen-tunnel"
QAREEN_LABEL = "com.aos.qareen"

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
TUNNEL_PLIST_PATH = LAUNCH_AGENTS_DIR / f"{TUNNEL_LABEL}.plist"
QAREEN_PLIST_PATH = LAUNCH_AGENTS_DIR / f"{QAREEN_LABEL}.plist"

# Framework-managed template for the connector LaunchAgent. Resolve from the
# DEPLOYED runtime framework (~/aos, matching migration 049's AOS_ROOT), never
# the dev workspace on the external AOS-X SSD which can be unmounted.
REPO_ROOT = Path.home() / "aos"
TUNNEL_PLIST_TEMPLATE = (
    REPO_ROOT / "config" / "launchagents" / f"{TUNNEL_LABEL}.plist.template"
)

LOGS_DIR = Path.home() / ".aos" / "logs"

# TCC-safe locations to prefer / symlink cloudflared into.
CLOUDFLARED_SEARCH = [
    "/opt/homebrew/bin/cloudflared",
    "/usr/local/bin/cloudflared",
    "/Volumes/AOS-X/homebrew/bin/cloudflared",
]
TCC_SAFE_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"]

# Local origin Qareen listens on.
QAREEN_PORT = 4096

# Health-check tuning: tunnel takes ~10s to come up, retry 3x over ~30s.
HEALTH_ATTEMPTS = 3
HEALTH_DELAY = 10  # seconds between attempts


class TunnelManager:
    """Connect/disconnect orchestration + macOS service + host-bind control."""

    def __init__(self, bus: Any, state: RemoteAccessState) -> None:
        self._bus = bus
        self.state = state
        # Serialize connect/disconnect so overlapping runs never interleave the
        # plist / launchctl / rebind steps on the singleton state row.
        self._lock = asyncio.Lock()

    # ==================================================================
    # Public API
    # ==================================================================
    async def validate_token(self, token: str) -> dict:
        """Verify a pasted CF token and list its zones; infer missing scopes.

        Returns ``{ok, active, account_id, zones:[{name,id,account_id}],
        missing_scopes:[...], error?}``. Never stores the token.
        """
        missing_scopes: list[str] = []
        try:
            async with CloudflareClient(token) as cf:
                verify = await cf.verify_token()
                active = (verify or {}).get("status") == "active"
                zones = await cf.list_zones()

                zone_list = [
                    {
                        "name": z.get("name"),
                        "id": z.get("id"),
                        "account_id": (z.get("account") or {}).get("id"),
                    }
                    for z in (zones or [])
                ]
                # Zone/DNS visibility: an active token that returns no zones is
                # missing Zone:Read / DNS:Edit.
                if active and not zone_list:
                    missing_scopes.append("dns")
                    missing_scopes.append("zone")

                account_id = zone_list[0]["account_id"] if zone_list else None
                # Cloudflare exposes no scope-introspection API, so actively
                # probe the Tunnel + Access capabilities with a scoped read and
                # treat a 403 as a missing scope. The token is NEVER stored here.
                if active and account_id:
                    cf.account_id = account_id
                    try:
                        await cf.find_tunnel_by_name(self._tunnel_name())
                    except CloudflareError as exc:
                        if exc.status_code == 403:
                            missing_scopes.append("argotunnel")
                    try:
                        await cf.find_access_app("__scope_probe__.invalid")
                    except CloudflareError as exc:
                        if exc.status_code == 403:
                            missing_scopes.append("access")
        except CloudflareError as exc:
            return {
                "ok": False,
                "active": False,
                "account_id": None,
                "zones": [],
                "missing_scopes": [],
                "error": str(exc),
            }

        return {
            "ok": active and bool(zone_list) and not missing_scopes,
            "active": active,
            "account_id": zone_list[0]["account_id"] if zone_list else None,
            "zones": zone_list,
            "missing_scopes": missing_scopes,
        }

    async def connect(
        self,
        token: str,
        domain: str,
        hostname: str,
        zone_id: str,
        account_id: str,
        emails: list[str],
    ) -> None:
        """Idempotently provision the full remote-access stack.

        Emits ``remote_access.progress`` at every step. The host rebind to
        127.0.0.1 is the LAST step, performed only after the connector is
        health-verified — so a failure mid-provision never locks out the LAN.
        """
        async with self._lock:
            await self._connect_locked(
                token, domain, hostname, zone_id, account_id, emails
            )

    async def _connect_locked(
        self,
        token: str,
        domain: str,
        hostname: str,
        zone_id: str,
        account_id: str,
        emails: list[str],
    ) -> None:
        self.state.upsert(
            status="provisioning",
            domain=domain,
            hostname=hostname,
            zone_id=zone_id,
            account_id=account_id,
            allowed_emails=emails,
            error_message=None,
        )
        await self._emit("start", "in_progress", f"Provisioning {hostname}")

        try:
            async with CloudflareClient(token, account_id=account_id) as cf:
                # 1. Remotely-managed tunnel (idempotent).
                await self._emit("tunnel", "in_progress", "Creating tunnel")
                tunnel = await cf.create_tunnel(self._tunnel_name())
                tunnel_id = tunnel["id"]
                # Stash the CF API token ONLY after the first scoped write
                # succeeds, so an under-scoped/invalid token never persists.
                # Raise on Keychain failure to keep connect atomic/teardownable.
                if not await self._secret_set(CF_TOKEN_KEY, token):
                    raise RuntimeError(
                        "Failed to persist Cloudflare API token to Keychain"
                    )
                run_token = tunnel.get("token")
                if not run_token:
                    raise RuntimeError("Cloudflare did not return a tunnel run-token")
                if not await self._secret_set(CF_TUNNEL_TOKEN_KEY, run_token):
                    raise RuntimeError(
                        "Failed to persist Cloudflare tunnel run-token to Keychain"
                    )
                self.state.upsert(tunnel_id=tunnel_id)
                await self._emit("tunnel", "done", "Tunnel ready")

                # 2. Ingress -> localhost:4096.
                await self._emit("ingress", "in_progress", "Routing ingress")
                await cf.set_tunnel_config(tunnel_id, hostname)
                await self._emit("ingress", "done", "Ingress configured")

                # 3. Proxied DNS CNAME.
                await self._emit("dns", "in_progress", "Publishing DNS record")
                dns = await cf.upsert_dns_cname(zone_id, hostname, tunnel_id)
                self.state.upsert(dns_record_id=dns.get("id"))
                await self._emit("dns", "done", "DNS record published")

                # 4. Ensure One-time-PIN IdP (GET-then-POST).
                await self._emit("idp", "in_progress", "Ensuring email login")
                idp_id = await cf.ensure_otp_idp()
                self.state.upsert(idp_id=idp_id)
                await self._emit("idp", "done", "Email login ready")

                # 5. Self-hosted Access app.
                await self._emit("access_app", "in_progress", "Creating Access app")
                app = await cf.create_access_app(hostname, idp_id)
                app_id = app["id"]
                self.state.upsert(
                    access_app_id=app_id, access_aud=app.get("aud")
                )
                await self._emit("access_app", "done", "Access app created")

                # 6. Email allow policy.
                await self._emit("policy", "in_progress", "Restricting to your emails")
                policy = await cf.create_access_policy(app_id, emails)
                self.state.upsert(policy_id=policy.get("id"))
                await self._emit("policy", "done", "Access policy set")

            # 7. Deploy + boot the cloudflared LaunchAgent.
            await self._emit("connector", "in_progress", "Starting connector")
            uid = os.getuid()
            self._write_tunnel_plist(uid)
            self._boot_service(TUNNEL_LABEL)
            await self._emit("connector", "done", "Connector started")

            # 8. Health-verify the connector BEFORE locking the door.
            await self._emit("health", "in_progress", "Verifying tunnel health")
            health = await self._health_check(hostname)
            if not health.get("healthy"):
                raise RuntimeError(
                    f"Tunnel did not become healthy: {health}"
                )
            await self._emit("health", "done", "Tunnel healthy")

            # 9. Persist the terminal 'connected' state and notify the client
            # BEFORE the self-restart. _rebind_qareen kickstart -k's THIS very
            # process, so anything after it is not reliably reached. The rebind
            # to 127.0.0.1 is the ABSOLUTE FINAL action — nothing follows it.
            self.state.upsert(status="connected", error_message=None)
            await self._emit("complete", "done", f"Remote access live at {hostname}")
            await self._emit("rebind", "in_progress", "Securing local bind")
            self._rebind_qareen("127.0.0.1")
        except Exception as exc:  # noqa: BLE001 — surface to UI + state
            logger.exception("Remote access provisioning failed")
            self.state.set_status("error", error=str(exc))
            await self._emit("error", "error", "Provisioning failed", detail=str(exc))
            raise

    async def disconnect(self) -> None:
        """Tear down everything and ALWAYS restore the 0.0.0.0 bind.

        The connector LaunchAgent is booted out BEFORE deleting the tunnel
        (DELETE fails while it holds live connections). Keychain entries are
        removed. The rebind to 0.0.0.0 lives in ``finally`` so a partial
        teardown still restores LAN/Tailscale reachability.
        """
        async with self._lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        st = self.state.get()
        await self._emit("start", "in_progress", "Disconnecting remote access")
        try:
            # 1. Stop the connector FIRST so the tunnel has no live connections.
            await self._emit("connector", "in_progress", "Stopping connector")
            self._bootout_service(TUNNEL_LABEL)
            # Give Cloudflare a moment to register the connector as gone.
            await asyncio.sleep(2)
            await self._emit("connector", "done", "Connector stopped")

            # 2. CF teardown (reverse order; keeps the shared OTP IdP).
            token = await self._secret_get(CF_TOKEN_KEY)
            account_id = st.get("account_id")
            if token and account_id:
                await self._emit("cloudflare", "in_progress", "Removing Cloudflare resources")
                async with CloudflareClient(token, account_id=account_id) as cf:
                    await self._teardown_with_retry(
                        cf,
                        zone_id=st.get("zone_id"),
                        tunnel_id=st.get("tunnel_id"),
                        app_id=st.get("access_app_id"),
                        dns_record_id=st.get("dns_record_id"),
                        policy_id=st.get("policy_id"),
                    )
                await self._emit("cloudflare", "done", "Cloudflare resources removed")
            else:
                logger.warning(
                    "disconnect: missing CF token or account_id; skipping CF teardown"
                )

            # 3. Reset state metadata. (Keychain secrets are wiped in the
            # finally block so they go regardless of teardown outcome.)
            self.state.clear()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Remote access disconnect failed")
            self.state.set_status("error", error=str(exc))
            await self._emit("error", "error", "Disconnect failed", detail=str(exc))
            raise
        finally:
            # ALWAYS wipe BOTH Keychain secrets and restore LAN reachability,
            # even when CF teardown raised. Each guarded so one failure does
            # not mask the original error or skip the rebind.
            await self._emit("secrets", "in_progress", "Clearing stored secrets")
            try:
                await self._secret_delete(CF_TUNNEL_TOKEN_KEY)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to delete %s during disconnect", CF_TUNNEL_TOKEN_KEY)
            try:
                await self._secret_delete(CF_TOKEN_KEY)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to delete %s during disconnect", CF_TOKEN_KEY)
            try:
                self._rebind_qareen("0.0.0.0")
                await self._emit("rebind", "done", "Bound to 0.0.0.0")
            except Exception:  # noqa: BLE001
                logger.exception("Failed to rebind Qareen to 0.0.0.0 during disconnect")

    async def status(self) -> dict:
        """Return a FLAT status dict for the status endpoint.

        Shape: ``{status, hostname, domain, allowed_emails, error_message,
        connector_health}`` where ``connector_health`` is the STRING-valued
        ``{tunnel, dns, access, overall}`` shape the UI renders (never raw
        booleans). The internal ``_health_check`` booleans stay private to the
        connect/disconnect logic.
        """
        st = self.state.get()
        hostname = st.get("hostname")
        # Single, non-retrying probe — the UI polls; don't block 30s here.
        health = await self._health_check(hostname, attempts=1)
        return {**st, "connector_health": self._map_health(health)}

    @staticmethod
    def _map_health(h: dict) -> dict:
        """Map the internal boolean health dict to the string UI shape."""
        h = h or {}
        access_ok = h.get("http_code") in {"200", "301", "302", "401", "403"}
        return {
            "tunnel": "ok" if h.get("tunnel") else "down",
            "dns": "ok" if h.get("tunnel") else "down",
            "access": "ok" if access_ok else "down",
            "overall": "ok" if h.get("healthy") else "degraded",
        }

    # ==================================================================
    # SSE progress
    # ==================================================================
    async def _emit(
        self, step: str, status: str, message: str = "", detail: str = ""
    ) -> None:
        """Push a RemoteAccessProgress event onto the bus (best-effort)."""
        try:
            await self._bus.emit(
                RemoteAccessProgress(
                    source="remote_access",
                    step=step,
                    status=status,
                    message=message,
                    detail=detail,
                )
            )
        except Exception:  # noqa: BLE001 — progress must never break provisioning
            logger.exception("Failed to emit remote_access.progress (%s/%s)", step, status)

    # ==================================================================
    # Keychain (agent-secret subprocess, 5s timeout, never DB/file/log)
    # ==================================================================
    async def _run_secret(self, *args: str) -> subprocess.CompletedProcess:
        """Invoke agent-secret off the event loop with a hard 5s timeout."""
        return await asyncio.to_thread(
            subprocess.run,
            [str(AGENT_SECRET), *args],
            capture_output=True,
            text=True,
            timeout=SECRET_TIMEOUT,
        )

    async def _secret_set(self, key: str, val: str) -> bool:
        """Store a secret. The value is fed via stdin (agent-secret reads it
        when no value arg is given) so it never appears in process argv."""
        try:
            r = await asyncio.to_thread(
                subprocess.run,
                [str(AGENT_SECRET), "set", key],
                input=val,
                capture_output=True,
                text=True,
                timeout=SECRET_TIMEOUT,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("agent-secret set timed out/failed for %s", key)
            return False

    async def _secret_get(self, key: str) -> Optional[str]:
        try:
            r = await self._run_secret("get", key)
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("agent-secret get timed out/failed for %s", key)
            return None
        if r.returncode != 0:
            return None
        val = (r.stdout or "").strip()
        if not val or val.startswith("Error"):
            return None
        return val

    async def _secret_delete(self, key: str) -> bool:
        try:
            r = await self._run_secret("delete", key)
            return r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("agent-secret delete timed out/failed for %s", key)
            return False

    # ==================================================================
    # LaunchAgent — connector plist generation + lifecycle
    # ==================================================================
    @staticmethod
    def _tunnel_name() -> str:
        """Deterministic per-machine tunnel name (drives CF idempotency)."""
        host = socket.gethostname().split(".")[0]
        safe = "".join(c if (c.isalnum() or c == "-") else "-" for c in host).strip("-")
        return f"qareen-{safe or 'host'}"

    def _resolve_cloudflared(self) -> str:
        """Resolve the cloudflared binary, preferring TCC-safe locations.

        launchd-spawned processes cannot self-grant external-volume access, so
        if cloudflared is found only on /Volumes/AOS-X we symlink it into a
        TCC-safe bin dir (best-effort) and return that path.
        """
        found = shutil.which("cloudflared")
        if found and any(found.startswith(d) for d in TCC_SAFE_BIN_DIRS):
            return found
        for candidate in CLOUDFLARED_SEARCH:
            if os.path.exists(candidate):
                if any(candidate.startswith(d) for d in TCC_SAFE_BIN_DIRS):
                    return candidate
                # Found outside TCC-safe dirs — try to symlink into one.
                for bindir in TCC_SAFE_BIN_DIRS:
                    if os.path.isdir(bindir):
                        link = os.path.join(bindir, "cloudflared")
                        try:
                            if not os.path.exists(link):
                                os.symlink(candidate, link)
                            return link
                        except OSError:
                            continue
                return candidate
        if found:
            return found
        raise RuntimeError("cloudflared binary not found (install via Homebrew)")

    def _write_tunnel_plist(self, uid: int) -> None:
        """Render the connector plist from template and deploy it.

        Substitutes ``__HOME__`` / ``__CLOUDFLARED__`` / ``__TUNNEL_TOKEN__``.
        The run-token is read from the Keychain at generation time and is the
        ONLY place a secret touches disk (the deployed instance plist), which is
        why the file is written owner-read-only (0600).
        """
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Fail fast with a clear error if the framework template is missing,
        # rather than mid-provision after CF resources are already created.
        if not TUNNEL_PLIST_TEMPLATE.exists():
            raise RuntimeError(
                f"Connector plist template not found at {TUNNEL_PLIST_TEMPLATE}"
            )
        template = TUNNEL_PLIST_TEMPLATE.read_text()
        cloudflared = self._resolve_cloudflared()
        # Read the run-token synchronously off the loop is fine here — this is a
        # short, deploy-time call. Use the sync agent-secret invocation.
        run_token = self._secret_get_sync(CF_TUNNEL_TOKEN_KEY)
        if not run_token:
            raise RuntimeError("cloudflare_tunnel_token missing from Keychain")

        rendered = (
            template
            .replace("__HOME__", str(Path.home()))
            .replace("__CLOUDFLARED__", cloudflared)
            .replace("__TUNNEL_TOKEN__", run_token)
        )
        # Write owner-read-only (0600) in one shot — the plist carries the
        # run-token, so never leave a world/group-readable window.
        fd = os.open(
            str(TUNNEL_PLIST_PATH),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, rendered.encode("utf-8"))
        finally:
            os.close(fd)

    def _secret_get_sync(self, key: str) -> Optional[str]:
        """Synchronous Keychain read used at plist-generation time."""
        try:
            r = subprocess.run(
                [str(AGENT_SECRET), "get", key],
                capture_output=True,
                text=True,
                timeout=SECRET_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("agent-secret get (sync) failed for %s", key)
            return None
        if r.returncode != 0:
            return None
        val = (r.stdout or "").strip()
        if not val or val.startswith("Error"):
            return None
        return val

    def _boot_service(self, label: str) -> None:
        """bootout -> sleep -> bootstrap -> kickstart (gui/{uid}/{label})."""
        uid = os.getuid()
        plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        # bootout any stale instance (ignore failure — may not be loaded).
        self._launchctl("bootout", f"gui/{uid}/{label}", check=False)
        # Brief settle before re-bootstrapping.
        import time

        time.sleep(1)
        self._launchctl("bootstrap", f"gui/{uid}", str(plist_path), check=False)
        self._launchctl("kickstart", "-k", f"gui/{uid}/{label}", check=False)

    def _bootout_service(self, label: str) -> None:
        """bootout the LaunchAgent, then delete its deployed plist file."""
        uid = os.getuid()
        self._launchctl("bootout", f"gui/{uid}/{label}", check=False)
        plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        try:
            plist_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Failed to delete %s", plist_path)

    @staticmethod
    def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
        """Run a launchctl subcommand (capture, text, 10s timeout)."""
        try:
            return subprocess.run(
                ["launchctl", *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=check,
            )
        except subprocess.TimeoutExpired:
            logger.warning("launchctl %s timed out", " ".join(args))
            return subprocess.CompletedProcess(args, 1, "", "timeout")

    # ==================================================================
    # Host-bind control — rewrite the DEPLOYED qareen plist
    # ==================================================================
    def _rebind_qareen(self, host: str) -> None:
        """Rewrite BOTH the ``--host`` arg AND ``AOS_QAREEN_HOST`` then kickstart.

        Edits the DEPLOYED instance plist at
        ~/Library/LaunchAgents/com.aos.qareen.plist (instance state, allowed to
        be edited at runtime — the framework template defaults to 0.0.0.0).
        """
        if not QAREEN_PLIST_PATH.exists():
            logger.warning("qareen plist not found at %s; cannot rebind", QAREEN_PLIST_PATH)
            return

        with open(QAREEN_PLIST_PATH, "rb") as fh:
            data = plistlib.load(fh)

        # Rewrite the --host value in ProgramArguments.
        args = list(data.get("ProgramArguments") or [])
        for i, arg in enumerate(args):
            if arg == "--host" and i + 1 < len(args):
                args[i + 1] = host
                break
        data["ProgramArguments"] = args

        # Rewrite (or add) AOS_QAREEN_HOST in EnvironmentVariables.
        env = dict(data.get("EnvironmentVariables") or {})
        env["AOS_QAREEN_HOST"] = host
        data["EnvironmentVariables"] = env

        with open(QAREEN_PLIST_PATH, "wb") as fh:
            plistlib.dump(data, fh)

        uid = os.getuid()
        self._launchctl("kickstart", "-k", f"gui/{uid}/{QAREEN_LABEL}", check=False)

    # ==================================================================
    # Health check — launchctl PID + local port + remote TLS
    # ==================================================================
    async def _health_check(
        self,
        host: Optional[str] = None,
        attempts: int = HEALTH_ATTEMPTS,
        delay: int = HEALTH_DELAY,
    ) -> dict:
        """Probe connector + local port + remote endpoint, retrying 3x/30s.

        Returns ``{connector, qareen_local, tunnel, http_code, healthy}``.
        A remote ``401/403`` means the tunnel is up but unauthenticated — that
        is HEALTHY (Access is gating). ``502``/``000`` means the tunnel is down.
        """
        result: dict = {
            "connector": False,
            "qareen_local": False,
            "tunnel": False,
            "http_code": None,
            "healthy": False,
        }
        for attempt in range(max(1, attempts)):
            connector = await asyncio.to_thread(self._connector_pid_present)
            qareen_local = await asyncio.to_thread(self._port_open, "127.0.0.1", QAREEN_PORT)
            http_code = None
            tunnel = False
            if host:
                http_code = await asyncio.to_thread(self._curl_head, host)
                tunnel = http_code in {"200", "301", "302", "401", "403"}
            result = {
                "connector": connector,
                "qareen_local": qareen_local,
                "tunnel": tunnel,
                "http_code": http_code,
                "healthy": connector and (tunnel if host else True),
            }
            if result["healthy"]:
                return result
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
        return result

    @staticmethod
    def _connector_pid_present() -> bool:
        """True if launchctl lists the connector label with a live PID."""
        try:
            r = subprocess.run(
                ["launchctl", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        if r.returncode != 0:
            return False
        for line in r.stdout.splitlines():
            if TUNNEL_LABEL in line:
                pid = line.split("\t", 1)[0].strip()
                return pid.isdigit()
        return False

    @staticmethod
    def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _curl_head(host: str) -> str:
        """curl -I https://host — return the HTTP status code ('000' on fail)."""
        try:
            r = subprocess.run(
                [
                    "curl", "-I", "-s", "-o", "/dev/null",
                    "-w", "%{http_code}", "--max-time", "10",
                    f"https://{host}",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            return "000"
        return (r.stdout or "000").strip() or "000"

    # ==================================================================
    # Teardown with retry (connector may still be draining)
    # ==================================================================
    async def _teardown_with_retry(
        self,
        cf: CloudflareClient,
        *,
        zone_id: Optional[str],
        tunnel_id: Optional[str],
        app_id: Optional[str],
        dns_record_id: Optional[str],
        policy_id: Optional[str],
        attempts: int = 3,
        delay: int = 5,
    ) -> None:
        """Run CF teardown, retrying if the tunnel DELETE races the connector."""
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                await cf.teardown(
                    zone_id=zone_id,
                    tunnel_id=tunnel_id,
                    app_id=app_id,
                    dns_record_id=dns_record_id,
                    policy_id=policy_id,
                )
                return
            except CloudflareError as exc:
                last_exc = exc
                logger.warning(
                    "CF teardown attempt %d/%d failed: %s", attempt + 1, attempts, exc
                )
                if attempt < attempts - 1:
                    await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc
