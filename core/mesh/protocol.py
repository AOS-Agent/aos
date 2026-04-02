"""Mesh Protocol — message format and types for AOS Mesh.

All messages between nodes follow this format. This is the shared
contract that all mesh components depend on.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """Types of messages that can be sent between nodes/agents."""
    TEXT = "text"             # Simple text message
    QUERY = "query"           # Ask a question, expect a response
    INFORM = "inform"         # Share information, no response expected
    DELEGATE = "delegate"     # Request another agent to perform a task
    RESULT = "result"         # Response to a query or delegation
    SOCIAL = "social"         # Post to the mesh feed
    HEALTH = "health"         # Health/status report
    ERROR = "error"           # Error report


@dataclass
class MeshMessage:
    """A message sent between mesh nodes or agents."""
    from_: str                          # sender: "node_name" or "agent@node"
    to: str                             # recipient: "node_name" or "agent@node"
    type: MessageType                   # message type
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    in_reply_to: str | None = None      # id of message this replies to

    def to_dict(self) -> dict:
        d = asdict(self)
        d["from"] = d.pop("from_")
        d["type"] = d["type"].value if isinstance(d["type"], MessageType) else d["type"]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> MeshMessage:
        data = data.copy()
        data["from_"] = data.pop("from", data.pop("from_", "unknown"))
        if isinstance(data.get("type"), str):
            try:
                data["type"] = MessageType(data["type"])
            except ValueError:
                data["type"] = MessageType.TEXT
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class NodeInfo:
    """Identity and metadata for a mesh node."""
    name: str
    role: str = "node"          # "admin" or "node"
    version: str = "unknown"
    ip: str = ""
    mesh_port: int = 4100
    status: str = "unknown"     # online, offline
    health: str = "unknown"     # healthy, warning, error
    uptime: int = 0
    last_seen: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
