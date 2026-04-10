"""Automation action dispatcher.

Reads action rules from ~/.aos/config/feeds.yaml (operator config) or
falls back to config/feeds.example.yaml (shipped defaults). Each event
emitter (emit_brief_created, emit_brief_compiled, emit_proposal_pending)
picks the matching rules and runs the configured actions.

Safety:
    - All actions are wrapped in try/except — never raises to the caller
    - Missing config file = no actions fire (silent)
    - Missing optional deps (Telegram bridge, work CLI) = skip, log warning
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

USER_CONFIG = Path.home() / ".aos" / "config" / "feeds.yaml"
DEFAULT_CONFIG_RUNTIME = Path.home() / "aos" / "config" / "feeds.example.yaml"
DEFAULT_CONFIG_DEV = Path.home() / "project" / "aos" / "config" / "feeds.example.yaml"


@dataclass
class ActionRule:
    name: str
    threshold: float | None = None
    description: str = ""
    notify_telegram: bool = False
    notify_event: bool = False
    create_task: bool = False
    task_prefix: str = ""
    task_priority: int = 3
    raw: dict[str, Any] = field(default_factory=dict)

    def matches_brief_relevance(self, relevance: float) -> bool:
        if self.threshold is None:
            return False
        return relevance >= self.threshold


def load_action_rules() -> dict[str, ActionRule]:
    """Load action rules from user or example config.

    Returns a dict keyed by rule name. Empty dict on failure.
    """
    try:
        import yaml
    except ImportError:
        return {}

    config_path = None
    if USER_CONFIG.is_file():
        config_path = USER_CONFIG
    elif DEFAULT_CONFIG_RUNTIME.is_file():
        config_path = DEFAULT_CONFIG_RUNTIME
    elif DEFAULT_CONFIG_DEV.is_file():
        config_path = DEFAULT_CONFIG_DEV

    if config_path is None:
        return {}

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse %s: %s", config_path, e)
        return {}

    if not isinstance(raw, dict):
        return {}

    actions_raw = raw.get("actions") or {}
    if not isinstance(actions_raw, dict):
        return {}

    rules: dict[str, ActionRule] = {}
    for name, spec in actions_raw.items():
        if not isinstance(spec, dict):
            continue
        notify = spec.get("notify") or {}
        if not isinstance(notify, dict):
            notify = {}
        rules[name] = ActionRule(
            name=name,
            threshold=spec.get("threshold"),
            description=spec.get("description", ""),
            notify_telegram=bool(notify.get("telegram", False)),
            notify_event=bool(notify.get("event", False)),
            create_task=bool(spec.get("create_task", False)),
            task_prefix=spec.get("task_prefix", ""),
            task_priority=int(spec.get("task_priority", 3)),
            raw=spec,
        )
    return rules


# ---------------------------------------------------------------------------
# Event emitters
# ---------------------------------------------------------------------------

async def emit_brief_created(brief: dict[str, Any]) -> None:
    """Called by the ingest runner or save endpoint when a new brief lands.

    Applies any matching relevance rules and runs their actions.
    """
    rules = load_action_rules()
    if not rules:
        return

    relevance = float(brief.get("relevance_score") or 0.0)
    matches = [r for r in rules.values()
               if r.threshold is not None and r.matches_brief_relevance(relevance)]

    for rule in matches:
        logger.info(
            "brief %s matched rule '%s' (relevance=%.2f >= %.2f)",
            brief.get("id", "?"), rule.name, relevance, rule.threshold,
        )
        await _run_actions(rule, event="brief_created", payload=brief)


async def emit_brief_compiled(brief: dict[str, Any], compilation: dict[str, Any]) -> None:
    """Called after a brief has been compiled and written to the vault."""
    rules = load_action_rules()
    rule = rules.get("brief_compiled")
    if rule is None:
        return
    payload = {"brief": brief, "compilation": compilation}
    await _run_actions(rule, event="brief_compiled", payload=payload)


async def emit_proposal_pending(proposal: dict[str, Any]) -> None:
    """Called when a compilation proposal lands in the review queue."""
    rules = load_action_rules()
    rule = rules.get("proposal_pending")
    if rule is None:
        return
    await _run_actions(rule, event="proposal_pending", payload=proposal)


# ---------------------------------------------------------------------------
# Action runners (each wrapped in try/except — never raises)
# ---------------------------------------------------------------------------

async def _run_actions(rule: ActionRule, *, event: str, payload: dict[str, Any]) -> None:
    if rule.notify_event:
        await _emit_event_bus(event, rule, payload)
    if rule.notify_telegram:
        await _notify_telegram(event, rule, payload)
    if rule.create_task:
        _create_task(rule, payload)


async def _emit_event_bus(event: str, rule: ActionRule, payload: dict[str, Any]) -> None:
    """Publish to the AOS event bus if available."""
    try:
        # The bus client lives in the qareen service; use a thin subprocess
        # shim to avoid dragging the fastapi app state into engine code.
        try:
            from qareen.events.bus import get_bus  # type: ignore
        except ImportError:
            return
        bus = get_bus()
        if bus is None:
            return
        try:
            from qareen.events.types import Event  # type: ignore
        except ImportError:
            return
        e = Event(
            event_type=f"intelligence.{event}",
            source="intelligence.hooks",
            payload={
                "rule": rule.name,
                **{k: v for k, v in payload.items() if k not in ("extraction_json", "compilation_json")},
            },
        )
        await bus.emit(e)
    except Exception as e:
        logger.debug("event bus emit failed: %s", e)


async def _notify_telegram(event: str, rule: ActionRule, payload: dict[str, Any]) -> None:
    """Send a brief Telegram notification via the bridge."""
    try:
        # Compose the notification text
        title = payload.get("title") or payload.get("brief", {}).get("title") or "(untitled)"
        url = payload.get("url") or payload.get("brief", {}).get("url") or ""
        relevance = payload.get("relevance_score") or payload.get("brief", {}).get("relevance_score")
        rel_str = f" [{relevance:.2f}]" if isinstance(relevance, (int, float)) else ""

        label_map = {
            "brief_created": "📥 New",
            "brief_compiled": "📚 Compiled",
            "proposal_pending": "⏸ Review",
        }
        prefix = label_map.get(event, "ℹ")
        message = f"{prefix}{rel_str} {title}"
        if url:
            message += f"\n{url}"

        # Try the bridge via its HTTP interface; it's a localhost service
        import httpx
        bridge_url = "http://127.0.0.1:4098/notify"
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(bridge_url, json={"text": message, "channel": "intelligence"})
    except Exception as e:
        logger.debug("telegram notify failed: %s", e)


def _create_task(rule: ActionRule, payload: dict[str, Any]) -> None:
    """Create a work-system task via the CLI."""
    try:
        title_raw = (
            payload.get("title")
            or payload.get("brief", {}).get("title")
            or "(untitled)"
        )
        title = f"{rule.task_prefix} {title_raw}".strip()
        work_cli = Path.home() / "aos" / "core" / "work" / "cli.py"
        if not work_cli.is_file():
            work_cli = Path.home() / "project" / "aos" / "core" / "work" / "cli.py"
        if not work_cli.is_file():
            return
        subprocess.run(
            ["python3", str(work_cli), "add", title, "--priority", str(rule.task_priority)],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        logger.debug("task creation failed: %s", e)
