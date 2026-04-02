"""Mesh Identity — agent addressing on the mesh.

Agents are addressed as: agent@node (e.g., chief@hisham, advisor@khalid).
Nodes are addressed by name (e.g., hisham, khalid).

This module handles parsing, validation, and resolution of mesh addresses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MeshAddress:
    """A mesh address: either a node or an agent on a node."""
    agent: str | None  # None = addressing the node itself
    node: str

    @classmethod
    def parse(cls, address: str) -> MeshAddress:
        """Parse 'agent@node' or 'node' into a MeshAddress."""
        if "@" in address:
            agent, node = address.split("@", 1)
            return cls(agent=agent, node=node)
        return cls(agent=None, node=address)

    def __str__(self) -> str:
        if self.agent:
            return f"{self.agent}@{self.node}"
        return self.node

    @property
    def is_agent(self) -> bool:
        return self.agent is not None

    @property
    def is_node(self) -> bool:
        return self.agent is None
