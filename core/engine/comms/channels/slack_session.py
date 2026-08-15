"""Slack-as-user session client — shared low-level transport for converse.

Generalizes the proven `~/.aos/services/slack-lite/slack.py` prototype (xoxc/
xoxd session-token API, no bot install required) into a reusable client. Per
PLAN.md §3 (~/.aos/tmp/sessions-build/PLAN.md), this lives in `channels/`
(the package where a real Slack channel is otherwise missing today) rather
than under `converse/`, so a future full bus `ChannelAdapter` for Slack
(comms-bus ingest of all Slack DMs — explicitly out of scope for v1) can wrap
this same client rather than reimplementing auth.

Secrets: Keychain only, via the `agent-secret` CLI wrapper (SLACK_XOXC,
SLACK_XOXD — already present). Workspace URL is a required constructor
argument — this module never hardcodes a workspace; the caller (converse/
channels_slack.py) is responsible for sourcing it from
~/.aos/config/converse.yaml.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import requests

AGENT_SECRET = Path.home() / "aos" / "core" / "bin" / "cli" / "agent-secret"

# Matches the slack-lite prototype's UA — Slack's session-token API is
# sensitive to looking like a real browser request.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class SlackAuthError(Exception):
    """Raised when Slack secrets are missing/unreadable, or the session
    token is rejected (invalid_auth). Callers (converse/channels_slack.py)
    catch this and surface it as ChannelAuthError to the converse protocol."""


def secret_get(name: str) -> str:
    """Read a secret from Keychain via agent-secret. Raises SlackAuthError
    with a clear message if it's not set (fresh install / no reauth yet —
    PLAN.md §4 "no Slack secrets -> slack channel needs_reauth() true")."""
    try:
        return subprocess.check_output(
            [str(AGENT_SECRET), "get", name], text=True, stderr=subprocess.PIPE
        ).strip()
    except subprocess.CalledProcessError as e:
        raise SlackAuthError(
            f"secret {name!r} not available in Keychain "
            f"(run `converse reauth slack` or `agent-secret set {name}`): "
            f"{(e.stderr or '').strip()}"
        ) from e


def secret_set(name: str, value: str) -> None:
    """Write a secret to Keychain via agent-secret. Used only by the reauth
    flow (converse/reauth.py) — never called automatically by polling/send."""
    subprocess.run(
        [str(AGENT_SECRET), "set", name, value],
        check=True, capture_output=True, text=True,
    )


class SlackSessionClient:
    """Slack-as-user via session tokens (xoxc/xoxd), matching PLAN.md §3's
    interface exactly:

        __init__(workspace_url)
        history(channel, oldest, limit=30) -> dict
        post_message(channel, text) -> dict
        auth_test() -> dict
        user_info(uid) -> dict

    Every method returns Slack's raw JSON response (`{"ok": bool, ...}`) —
    callers (converse/channels_slack.py) interpret `ok`/`error` themselves so
    this class stays a thin, testable transport with no converse-specific
    policy (that includes NOT raising on `invalid_auth`; it's a normal-shaped
    response, `{"ok": false, "error": "invalid_auth"}` — the channel layer is
    what turns that into ChannelAuthError, per PLAN.md §3's "raises
    ChannelAuthError on auth failure" being a ConverseChannel.poll()
    contract, not a transport-client one).
    """

    def __init__(
        self,
        workspace_url: str,
        *,
        xoxc: str | None = None,
        xoxd: str | None = None,
        timeout: int = 30,
    ):
        if not workspace_url or "<workspace>" in workspace_url:
            raise SlackAuthError(
                f"invalid/placeholder workspace_url: {workspace_url!r} — "
                "set channels.slack.workspace_url in ~/.aos/config/converse.yaml"
            )
        self.workspace_url = workspace_url.rstrip("/")
        self._base = f"{self.workspace_url}/api"
        self._timeout = timeout
        self._xoxc = xoxc or secret_get("SLACK_XOXC")
        self._xoxd = xoxd or secret_get("SLACK_XOXD")
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._session.headers["Cookie"] = f"d={self._xoxd}"

    def _call(self, method: str, **params: Any) -> dict:
        resp = self._session.post(
            f"{self._base}/{method}",
            data={"token": self._xoxc, **params},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def auth_test(self) -> dict:
        return self._call("auth.test")

    def history(self, channel: str, oldest: str = "0", limit: int = 30) -> dict:
        return self._call("conversations.history", channel=channel, oldest=oldest, limit=limit)

    def post_message(self, channel: str, text: str) -> dict:
        return self._call("chat.postMessage", channel=channel, text=text)

    def user_info(self, uid: str) -> dict:
        return self._call("users.info", user=uid)
