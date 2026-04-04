"""n8n REST API client.

Async Python wrapper for the n8n public API running on localhost:5678.
Used by Qareen to create, manage, and monitor automation workflows.

All methods are async (httpx) since Qareen's FastAPI backend is async.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

import httpx

from .errors import (
    N8nConnectionError,
    N8nCredentialError,
    N8nError,
    N8nNotFoundError,
    N8nValidationError,
)

logger = logging.getLogger(__name__)

AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"
BASE_URL = "http://127.0.0.1:5678"
API_BASE = f"{BASE_URL}/api/v1"
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0  # seconds


def _get_api_key() -> str:
    """Read the n8n API key from macOS Keychain."""
    try:
        result = subprocess.run(
            [str(AGENT_SECRET), "get", "N8N_API_KEY"],
            capture_output=True, text=True, timeout=5,
        )
        key = result.stdout.strip()
        if not key or result.returncode != 0:
            raise N8nError("N8N_API_KEY not found in Keychain")
        return key
    except subprocess.TimeoutExpired:
        raise N8nError("Timed out reading N8N_API_KEY from Keychain")


class N8nClient:
    """Async client for the n8n REST API."""

    def __init__(self, api_key: str | None = None, base_url: str = API_BASE):
        self._api_key = api_key or _get_api_key()
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "X-N8N-API-KEY": self._api_key,
                    "Accept": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # -- Internal request helpers --

    async def _request(
        self, method: str, path: str, json: dict | None = None, params: dict | None = None,
    ) -> dict[str, Any]:
        """Make an API request with retry logic for connection errors."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                client = self._get_client()
                resp = await client.request(method, path, json=json, params=params)
                return self._handle_response(resp, path)
            except httpx.ConnectError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
            except (N8nError, httpx.HTTPError):
                raise

        raise N8nConnectionError(detail=str(last_error))

    def _handle_response(self, resp: httpx.Response, path: str) -> dict[str, Any]:
        """Parse response and raise appropriate errors."""
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 201:
            return resp.json()
        if resp.status_code == 204:
            return {}

        # Error responses
        try:
            body = resp.json()
            message = body.get("message", str(body))
        except Exception:
            message = resp.text or f"HTTP {resp.status_code}"

        if resp.status_code == 401:
            raise N8nCredentialError(
                "API authentication failed. The n8n API key may be invalid.",
                detail=message,
            )
        if resp.status_code == 404:
            raise N8nNotFoundError("Resource", path)
        if resp.status_code == 400:
            raise N8nValidationError(message)

        raise N8nError(message, status_code=resp.status_code)

    # -- Health --

    async def health(self) -> dict[str, Any]:
        """Check if n8n is running and healthy."""
        try:
            client = self._get_client()
            resp = await client.get(f"{BASE_URL}/healthz")
            return resp.json()
        except httpx.ConnectError:
            raise N8nConnectionError()

    # -- Workflows --

    async def list_workflows(self, limit: int = 100, active: bool | None = None) -> list[dict]:
        """List all workflows."""
        params: dict[str, Any] = {"limit": limit}
        if active is not None:
            params["active"] = str(active).lower()
        result = await self._request("GET", "/workflows", params=params)
        return result.get("data", [])

    async def get_workflow(self, workflow_id: str) -> dict:
        """Get a single workflow by ID."""
        result = await self._request("GET", f"/workflows/{workflow_id}")
        return result

    async def create_workflow(
        self,
        name: str,
        nodes: list[dict],
        connections: dict,
        settings: dict | None = None,
        auto_map_credentials: bool = True,
    ) -> dict:
        """Create a new workflow. Returns the created workflow with its ID.

        If auto_map_credentials is True, resolves credential references
        (id: null) to actual n8n credential IDs by matching type and name.
        """
        if auto_map_credentials:
            nodes = await self._map_credentials(nodes)

        payload: dict[str, Any] = {
            "name": name,
            "nodes": nodes,
            "connections": connections,
            "settings": settings or {"executionOrder": "v1"},
        }
        return await self._request("POST", "/workflows", json=payload)

    async def _map_credentials(self, nodes: list[dict]) -> list[dict]:
        """Resolve credential references to actual n8n credential IDs."""
        # Build lookup: (type, name) -> id
        existing = await self.list_credentials()
        by_type: dict[str, list[dict]] = {}
        for cred in existing:
            cred_type = cred.get("type", "")
            by_type.setdefault(cred_type, []).append(cred)

        for node in nodes:
            creds = node.get("credentials", {})
            for cred_type, cred_ref in creds.items():
                if isinstance(cred_ref, dict) and cred_ref.get("id") is None:
                    # Find a matching credential
                    candidates = by_type.get(cred_type, [])
                    if candidates:
                        # Prefer name match, fall back to first available
                        match = None
                        wanted_name = cred_ref.get("name", "")
                        for c in candidates:
                            if c.get("name") == wanted_name:
                                match = c
                                break
                        if not match:
                            match = candidates[0]
                        cred_ref["id"] = match["id"]
                        cred_ref["name"] = match["name"]

        return nodes

    async def update_workflow(self, workflow_id: str, data: dict) -> dict:
        """Update an existing workflow."""
        return await self._request("PUT", f"/workflows/{workflow_id}", json=data)

    async def activate_workflow(self, workflow_id: str) -> dict:
        """Activate a workflow (start triggers, webhooks, crons)."""
        return await self._request("POST", f"/workflows/{workflow_id}/activate")

    async def deactivate_workflow(self, workflow_id: str) -> dict:
        """Deactivate a workflow (stop triggers)."""
        return await self._request("POST", f"/workflows/{workflow_id}/deactivate")

    async def delete_workflow(self, workflow_id: str) -> dict:
        """Delete a workflow permanently."""
        return await self._request("DELETE", f"/workflows/{workflow_id}")

    # -- Executions --

    async def list_executions(
        self,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        include_data: bool = False,
    ) -> list[dict]:
        """List workflow executions."""
        params: dict[str, Any] = {"limit": limit}
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status
        if include_data:
            params["includeData"] = "true"
        result = await self._request("GET", "/executions", params=params)
        return result.get("data", [])

    async def get_execution(self, execution_id: str, include_data: bool = True) -> dict:
        """Get a single execution with optional data."""
        params = {"includeData": "true"} if include_data else {}
        return await self._request("GET", f"/executions/{execution_id}", params=params)

    async def retry_execution(self, execution_id: str) -> dict:
        """Retry a failed execution."""
        return await self._request("POST", f"/executions/{execution_id}/retry")

    # -- Credentials --

    async def list_credentials(self) -> list[dict]:
        """List all credentials (secrets are not included)."""
        result = await self._request("GET", "/credentials")
        return result.get("data", [])

    async def create_credential(
        self, credential_type: str, name: str, data: dict,
    ) -> dict:
        """Create a new credential."""
        payload = {
            "name": name,
            "type": credential_type,
            "data": data,
        }
        return await self._request("POST", "/credentials", json=payload)

    async def delete_credential(self, credential_id: str) -> dict:
        """Delete a credential."""
        return await self._request("DELETE", f"/credentials/{credential_id}")

    # -- Tags --

    async def list_tags(self) -> list[dict]:
        """List all tags."""
        result = await self._request("GET", "/tags")
        return result.get("data", [])

    async def create_tag(self, name: str) -> dict:
        """Create a new tag."""
        return await self._request("POST", "/tags", json={"name": name})
