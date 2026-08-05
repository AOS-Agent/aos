"""The narrow per-conversation channel protocol (PLAN.md §3).

The comms-bus `ChannelAdapter` (bulk sync across all conversations) is the
wrong shape for a live, single-conversation supervised loop — converse
defines its own protocol here: two real implementations
(channels_imessage.py, channels_slack.py), no plugin registry, per
~/.aos/tmp/sessions-build/PLAN.md §3.

Everything in this module is data/typing only — no I/O. The concrete
channels import from here; the supervisor (core/services/converse, T3, a
later wave) imports `ConverseChannel` as its type and dispatches on
`watch_spec().kind`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class InboundMsg:
    channel_message_id: str   # slack ts | imessage guid
    text: str
    ts: str                   # ISO8601


@dataclass
class SendResult:
    ok: bool
    channel_message_id: str | None = None
    error: str | None = None          # 'invalid_auth' triggers the reauth path


@dataclass
class WatchSpec:
    kind: str                 # 'kqueue' | 'poll'
    paths: list[Path] = field(default_factory=list)   # kqueue targets
    interval_s: int = 25                              # poll interval


class ChannelAuthError(Exception):
    """Raised by poll()/send() when the channel's credentials are no longer
    valid (PLAN.md §3). The supervisor catches this, sets a sticky reauth
    flag, pauses that channel's sessions (paused_reason='reauth'), and
    notifies the operator — never raised for a channel simply having
    'nothing new' to report."""


@runtime_checkable
class ConverseChannel(Protocol):
    name: str

    def poll(
        self, conversation_ref: str, counterpart_handle: str, cursor: str | None
    ) -> tuple[list[InboundMsg], str | None]:
        """New inbound messages FROM the counterpart after cursor, plus new
        cursor. Never raises for 'nothing new'; raises ChannelAuthError on
        auth failure."""
        ...

    def send(self, conversation_ref: str, text: str) -> SendResult: ...

    def resolve_counterpart(self, handle: str) -> dict | None:
        """-> {person_id, canonical_name, importance} via the 5-tier people
        resolver, or None if unresolved."""
        ...

    def needs_reauth(self) -> bool: ...

    def watch_spec(self) -> WatchSpec: ...


# ---------------------------------------------------------------------------
# Shared helper: repo-root sys.path bootstrap + people resolver loader.
#
# Both concrete channels need cross-package absolute imports (iMessage needs
# core.engine.comms.sentinel.attributedbody; both need the people resolver
# for resolve_counterpart). `core` and `core/engine` are implicit namespace
# packages (no __init__.py — verified against this repo), so this only needs
# the repo root on sys.path, matching the existing convention in
# core/engine/comms/sentinel/service.py.
# ---------------------------------------------------------------------------

def ensure_repo_root_on_path() -> None:
    """Idempotent: insert the repo root (the directory containing `core/`)
    onto sys.path if it isn't already importable. Tries the dev workspace
    first, then the runtime copy — same precedence as sentinel/service.py."""
    try:
        import core  # noqa: F401
        return  # already importable
    except ImportError:
        pass
    for candidate in (Path.home() / "project" / "aos", Path.home() / "aos"):
        if (candidate / "core").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return


def resolve_contact(reference: str) -> dict:
    """Load and call people/resolver.py's resolve_contact by explicit file
    path — the people package uses sibling imports (`from db import
    connect`), so its directory must be on sys.path for the module to load
    at all (same approach as core/engine/comms/recall.py's
    `_default_resolver()`, duplicated here rather than imported since
    recall.py's version is private (`_default_resolver`) and this needs to
    be shared by both channels_imessage.py and channels_slack.py)."""
    ensure_repo_root_on_path()
    import importlib.util

    people_dir = Path(__file__).resolve().parents[2] / "people"  # converse -> comms -> engine -> people
    if str(people_dir) not in sys.path:
        sys.path.insert(0, str(people_dir))

    spec = importlib.util.spec_from_file_location("aos_people_resolver", people_dir / "resolver.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load resolver from {people_dir}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.resolve_contact(reference)
