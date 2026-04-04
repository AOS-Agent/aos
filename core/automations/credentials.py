"""Credential bridge — syncs Qareen-managed OAuth tokens into n8n.

Users connect accounts through Qareen's Connectors page (one OAuth flow).
This bridge copies those tokens into n8n so automations can use them
without the user ever touching n8n.

Supported token sources:
- Google Workspace: ~/.google_workspace_mcp/credentials/{email}.json
- Telegram: macOS Keychain (TELEGRAM_BOT_TOKEN)

The bridge runs:
1. On Qareen startup (sync all)
2. After a user connects/reconnects an account
3. Periodically via cron to catch token refreshes
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GOOGLE_CREDS_DIR = Path.home() / ".google_workspace_mcp" / "credentials"


async def sync_google_credentials(n8n_client, email: str | None = None) -> list[str]:
    """Sync Google OAuth tokens from workspace-mcp into n8n.

    If email is specified, syncs only that account.
    Otherwise syncs all accounts found in the credentials directory.

    Returns list of synced credential names.
    """
    if not GOOGLE_CREDS_DIR.is_dir():
        logger.info("No Google credentials directory found")
        return []

    # Get existing n8n credentials to avoid duplicates
    existing = await n8n_client.list_credentials()
    existing_names = {c.get("name"): c.get("id") for c in existing}

    synced = []
    token_files = (
        [GOOGLE_CREDS_DIR / f"{email}.json"] if email
        else sorted(GOOGLE_CREDS_DIR.glob("*.json"))
    )

    for token_file in token_files:
        if not token_file.exists():
            continue

        account_email = token_file.stem
        try:
            token_data = json.loads(token_file.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read token file: %s", token_file)
            continue

        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            logger.warning("No refresh token for %s", account_email)
            continue

        # Read the OAuth app credentials from Keychain
        client_id, client_secret = _get_oauth_app_credentials()
        if not client_id:
            logger.error("Google OAuth app credentials not found in Keychain")
            return synced

        # Build the n8n OAuth token data structure
        oauth_token_data = {
            "access_token": token_data.get("token", ""),
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        # Create/update credentials for each Google service
        google_services = [
            ("googleSheetsOAuth2Api", f"Google Sheets ({account_email})"),
            ("gmailOAuth2", f"Gmail ({account_email})"),
            ("googleCalendarOAuth2Api", f"Google Calendar ({account_email})"),
            ("googleDriveOAuth2Api", f"Google Drive ({account_email})"),
        ]

        for cred_type, cred_name in google_services:
            try:
                cred_data = {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "oauthTokenData": json.dumps(oauth_token_data),
                }

                if cred_name in existing_names:
                    # Credential exists — could update token, but n8n API
                    # doesn't expose credential data updates via public API.
                    # The token refresh is handled by n8n internally once seeded.
                    logger.debug("Credential already exists: %s", cred_name)
                else:
                    # Create new credential via internal API
                    await _create_credential_internal(
                        n8n_client, cred_type, cred_name, cred_data,
                    )
                    synced.append(cred_name)
                    logger.info("Synced credential: %s", cred_name)
            except Exception as e:
                logger.warning("Failed to sync %s: %s", cred_name, e)

    return synced


async def sync_telegram_credential(n8n_client) -> str | None:
    """Sync Telegram bot token from Keychain into n8n."""
    import subprocess

    agent_secret = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"
    result = subprocess.run(
        [str(agent_secret), "get", "TELEGRAM_BOT_TOKEN"],
        capture_output=True, text=True, timeout=5,
    )
    bot_token = result.stdout.strip()
    if not bot_token:
        return None

    existing = await n8n_client.list_credentials()
    existing_names = {c.get("name") for c in existing}

    cred_name = "Telegram Bot"
    if cred_name in existing_names:
        logger.debug("Telegram credential already exists")
        return None

    try:
        await n8n_client.create_credential(
            credential_type="telegramApi",
            name=cred_name,
            data={"accessToken": bot_token},
        )
        logger.info("Synced Telegram credential")
        return cred_name
    except Exception as e:
        logger.warning("Failed to sync Telegram: %s", e)
        return None


async def sync_all(n8n_client) -> dict[str, list[str]]:
    """Sync all known credential sources into n8n.

    Called on Qareen startup and periodically.
    Returns dict of {source: [synced_credential_names]}.
    """
    results = {}

    # Google
    google = await sync_google_credentials(n8n_client)
    if google:
        results["google"] = google

    # Telegram
    telegram = await sync_telegram_credential(n8n_client)
    if telegram:
        results["telegram"] = [telegram]

    return results


def _get_oauth_app_credentials() -> tuple[str | None, str | None]:
    """Read Google OAuth app client ID and secret from Keychain."""
    import subprocess

    agent_secret = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

    def _get(key: str) -> str | None:
        # Try agent-secret first, then direct Keychain
        result = subprocess.run(
            [str(agent_secret), "get", key],
            capture_output=True, text=True, timeout=5,
        )
        val = result.stdout.strip()
        if val and not val.startswith("Error"):
            return val
        # Try the n8n-specific key
        result = subprocess.run(
            [str(agent_secret), "get", f"N8N_{key}"],
            capture_output=True, text=True, timeout=5,
        )
        val = result.stdout.strip()
        return val if val and not val.startswith("Error") else None

    client_id = _get("GOOGLE_CLIENT_ID") or _get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = _get("GOOGLE_CLIENT_SECRET") or _get("GOOGLE_OAUTH_CLIENT_SECRET")
    return client_id, client_secret


async def _create_credential_internal(
    n8n_client, cred_type: str, cred_name: str, cred_data: dict,
) -> dict | None:
    """Create a credential via n8n's internal REST API (not the public API).

    The public API requires strict schema validation that doesn't accept
    pre-authenticated OAuth tokens. The internal API (used by the UI) is
    more flexible.
    """
    import subprocess

    agent_secret = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

    # Get admin credentials for internal API auth
    email_result = subprocess.run(
        [str(agent_secret), "get", "N8N_ADMIN_EMAIL"],
        capture_output=True, text=True, timeout=5,
    )
    pwd_result = subprocess.run(
        [str(agent_secret), "get", "N8N_ADMIN_PASSWORD"],
        capture_output=True, text=True, timeout=5,
    )
    admin_email = email_result.stdout.strip()
    admin_pwd = pwd_result.stdout.strip()

    if not admin_email or not admin_pwd:
        logger.error("n8n admin credentials not found in Keychain")
        return None

    import httpx
    import uuid

    browser_id = str(uuid.uuid4())

    async with httpx.AsyncClient(base_url="http://127.0.0.1:5678") as client:
        # Login
        login_resp = await client.post("/rest/login", json={
            "emailOrLdapLoginId": admin_email,
            "password": admin_pwd,
        }, headers={"browser-id": browser_id})

        if login_resp.status_code != 200:
            logger.error("n8n login failed: %s", login_resp.status_code)
            return None

        cookies = login_resp.cookies

        # Create credential
        resp = await client.post("/rest/credentials", json={
            "name": cred_name,
            "type": cred_type,
            "data": cred_data,
        }, cookies=cookies, headers={"browser-id": browser_id})

        if resp.status_code in (200, 201):
            data = resp.json().get("data", {})
            logger.info("Created credential via internal API: %s (id=%s)", cred_name, data.get("id"))
            return data
        else:
            logger.warning("Failed to create credential %s: %s %s", cred_name, resp.status_code, resp.text[:200])
            return None
