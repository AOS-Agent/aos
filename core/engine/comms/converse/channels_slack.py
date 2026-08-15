"""Slack ConverseChannel (PLAN.md §3).

Wraps `core.engine.comms.channels.slack_session.SlackSessionClient` for the
converse per-conversation protocol: `poll()` via conversations.history
filtered to the counterpart's user id, `send()` via chat.postMessage,
`invalid_auth` -> ChannelAuthError -> supervisor pauses that channel
(paused_reason='reauth') and notifies the operator. Reauth itself
(converse/reauth.py) is operator-invoked only, never automatic.

Workspace URL is sourced from ~/.aos/config/converse.yaml
(`channels.slack.workspace_url`) — falling back to the shipped default
template only to keep imports/tests working before migration 100 has
written the instance file; the fallback's placeholder value
(`https://<workspace>.slack.com`) is rejected by SlackSessionClient itself,
so a genuinely unconfigured instance fails loudly and specifically rather
than silently hitting the wrong workspace. Never hardcoded here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..channels.slack_session import SlackAuthError, SlackSessionClient
from .channels import (
    ChannelAuthError,
    InboundMsg,
    SendResult,
    WatchSpec,
    resolve_contact,
)

log = logging.getLogger(__name__)

INSTANCE_CONFIG = Path.home() / ".aos" / "config" / "converse.yaml"
# repo root: converse -> comms -> engine -> core -> <repo root>
DEFAULT_CONFIG = Path(__file__).resolve().parents[4] / "config" / "defaults" / "converse.yaml"


def load_slack_config() -> dict:
    """channels.slack config dict: instance file takes precedence; falls
    back to the shipped default template (which still carries the
    placeholder workspace_url — SlackSessionClient rejects that with a
    clear error rather than this function pretending it's a real value)."""
    for path in (INSTANCE_CONFIG, DEFAULT_CONFIG):
        if not path.exists():
            continue
        try:
            cfg = yaml.safe_load(path.read_text()) or {}
        except Exception as e:
            log.warning("channels_slack: could not parse %s: %s", path, e)
            continue
        slack_cfg = (cfg.get("channels") or {}).get("slack") or {}
        if slack_cfg:
            return slack_cfg
    return {}


def _ts_to_iso(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")


class SlackChannel:
    """Implements converse.channels.ConverseChannel for Slack."""

    name = "slack"

    def __init__(
        self,
        workspace_url: str | None = None,
        *,
        poll_interval_s: int | None = None,
        client: SlackSessionClient | None = None,
    ):
        cfg = load_slack_config() if (workspace_url is None or poll_interval_s is None) else {}
        self.workspace_url = workspace_url or cfg.get("workspace_url") or ""
        self.poll_interval_s = poll_interval_s or cfg.get("poll_interval_s") or 25
        self._needs_reauth = False
        self._client = client
        if self._client is None:
            try:
                self._client = SlackSessionClient(self.workspace_url)
            except SlackAuthError as e:
                # Fresh install / no secrets yet / placeholder workspace_url
                # -> graceful skip, not a crash (PLAN.md §4): the channel
                # exists but reports needs_reauth() until the operator runs
                # `converse reauth slack` (or sets the secrets + workspace).
                log.warning("SlackChannel: not ready — %s", e)
                self._needs_reauth = True

    def poll(
        self, conversation_ref: str, counterpart_handle: str, cursor: str | None
    ) -> tuple[list[InboundMsg], str | None]:
        if self._client is None:
            raise ChannelAuthError("slack channel not configured (missing secrets/workspace_url)")

        oldest = cursor or "0"
        try:
            resp = self._client.history(conversation_ref, oldest=oldest, limit=30)
        except Exception as e:
            # Network/transport failure — not necessarily an auth problem;
            # report "nothing new" and let the supervisor retry next tick
            # rather than tripping the reauth path for a blip.
            log.warning("slack poll: request failed for %s: %s", conversation_ref, e)
            return [], None

        if not resp.get("ok"):
            err = resp.get("error", "unknown_error")
            if err == "invalid_auth":
                self._needs_reauth = True
                raise ChannelAuthError(err)
            log.warning("slack poll: channel=%s err=%s", conversation_ref, err)
            return [], None

        self._needs_reauth = False
        msgs = [
            m for m in resp.get("messages", [])
            if m.get("user") == counterpart_handle and float(m.get("ts", 0)) > float(oldest)
        ]
        msgs.sort(key=lambda m: float(m["ts"]))
        if not msgs:
            return [], None

        inbound = [
            InboundMsg(channel_message_id=m["ts"], text=m.get("text", ""), ts=_ts_to_iso(m["ts"]))
            for m in msgs
        ]
        new_cursor = msgs[-1]["ts"]
        return inbound, new_cursor

    def send(self, conversation_ref: str, text: str) -> SendResult:
        if self._client is None:
            return SendResult(ok=False, error="slack channel not configured (missing secrets/workspace_url)")

        try:
            resp = self._client.post_message(conversation_ref, text)
        except Exception as e:
            return SendResult(ok=False, error=str(e))

        if not resp.get("ok"):
            err = resp.get("error", "unknown_error")
            if err == "invalid_auth":
                self._needs_reauth = True
            return SendResult(ok=False, error=err)

        self._needs_reauth = False
        return SendResult(ok=True, channel_message_id=resp.get("ts"))

    def resolve_counterpart(self, handle: str) -> dict | None:
        display_name = None
        if self._client is not None:
            try:
                info = self._client.user_info(handle)
                if info.get("ok"):
                    display_name = info["user"].get("real_name") or info["user"].get("name")
            except Exception as e:
                log.debug("slack resolve_counterpart: user_info(%s) failed: %s", handle, e)

        result = resolve_contact(display_name or handle)
        if not result.get("resolved"):
            return None
        contact = result.get("contact") or {}
        return {
            "person_id": result.get("person_id"),
            "canonical_name": contact.get("canonical_name") or display_name,
            "importance": contact.get("importance"),
        }

    def needs_reauth(self) -> bool:
        return self._needs_reauth

    def watch_spec(self) -> WatchSpec:
        return WatchSpec(kind="poll", interval_s=self.poll_interval_s)
