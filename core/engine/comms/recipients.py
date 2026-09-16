"""Recipient resolution for the operator-facing iMessage CLIs.

``comms-send`` and ``comms-thread`` both take one positional argument that is
either an allowlist *name* ("Hisham") or a raw *handle* (a phone number or an
email address). This module turns that argument into a handle and decides,
through ``scope``, whether the operator may act on it.

Resolution order:

1. Case-insensitive allowlist name from ``~/.aos/config/comms.yaml`` →
   the first handle registered under that name.
2. Anything else is taken as a raw handle and checked against the
   allowlist as-is.

The audit line for a send goes to the same ``~/.aos/logs/comms.log`` the
scope gate writes to. The message text is never logged — only its length.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import scope


@dataclass(frozen=True)
class Recipient:
    """The outcome of resolving a CLI argument."""

    arg: str
    handle: str
    name: str
    allowed: bool

    @property
    def label(self) -> str:
        """``Name (handle)`` when the name is known, else the handle alone."""
        if self.name and self.name != self.handle:
            return f"{self.name} ({self.handle})"
        return self.handle


def resolve(arg: str, policy: scope.Policy | None = None) -> Recipient:
    """Resolve an allowlist name or raw handle into a ``Recipient``."""
    policy = policy or scope.load_policy()
    arg = (arg or "").strip()

    handles = policy.handles_for(arg)
    if handles:
        handle = handles[0]
    else:
        handle = arg

    allowed = bool(handle) and policy.handle_allowed(handle)
    name = policy.name_for(handle) or handle
    return Recipient(arg=arg, handle=handle, name=name, allowed=allowed)


def all_handles(recipient: Recipient, policy: scope.Policy | None = None) -> list[str]:
    """Every allowlisted handle for the recipient's contact (phone + email)."""
    policy = policy or scope.load_policy()
    handles = policy.handles_for(recipient.name)
    return handles or [recipient.handle]


def log_send(recipient: Recipient, *, channel: str, chars: int, result: str) -> None:
    """Append one audit line for an outbound attempt. Never logs the text."""
    line = (
        f"{datetime.now().isoformat(timespec='seconds')} "
        f"send channel={channel} to={recipient.handle} name={recipient.name} "
        f"chars={chars} result={result}\n"
    )
    try:
        path = scope._log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(line)
    except OSError:
        pass  # a send must never fail because the log is unwritable
