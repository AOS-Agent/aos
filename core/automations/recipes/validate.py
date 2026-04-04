"""Workflow JSON validation.

Catches structural errors before sending to n8n, since n8n's API
validates structure but NOT logical correctness.
"""

from __future__ import annotations

from typing import Any


def validate_workflow(workflow: dict[str, Any]) -> list[str]:
    """Validate an n8n workflow JSON. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    # Required top-level fields
    if not workflow.get("name"):
        errors.append("Missing workflow name")
    nodes = workflow.get("nodes", [])
    if not nodes:
        errors.append("Workflow has no nodes")
        return errors

    connections = workflow.get("connections", {})

    # Build node name set for connection validation
    node_names = set()
    node_ids = set()
    has_trigger = False

    for node in nodes:
        name = node.get("name")
        node_id = node.get("id")

        if not name:
            errors.append(f"Node missing 'name' field: {node}")
            continue
        if not node.get("type"):
            errors.append(f"Node '{name}' missing 'type' field")
        if node.get("position") is None:
            errors.append(f"Node '{name}' missing 'position' field")

        if name in node_names:
            errors.append(f"Duplicate node name: '{name}'")
        node_names.add(name)

        if node_id:
            if node_id in node_ids:
                errors.append(f"Duplicate node id: '{node_id}'")
            node_ids.add(node_id)

        # Check for trigger nodes
        node_type = node.get("type", "")
        if any(t in node_type.lower() for t in ["trigger", "webhook", "schedule", "cron"]):
            has_trigger = True

    if not has_trigger:
        errors.append("Workflow has no trigger node (schedule, webhook, or event trigger required)")

    # Validate connections reference existing nodes
    for source_name, conn_data in connections.items():
        if source_name not in node_names:
            errors.append(f"Connection references non-existent source node: '{source_name}'")

        if not isinstance(conn_data, dict):
            continue

        for conn_type, outputs in conn_data.items():
            if not isinstance(outputs, list):
                continue
            for output_idx, targets in enumerate(outputs):
                if targets is None:
                    continue
                if not isinstance(targets, list):
                    errors.append(f"Connection output {output_idx} of '{source_name}' should be a list")
                    continue
                for target in targets:
                    target_name = target.get("node")
                    if target_name and target_name not in node_names:
                        errors.append(
                            f"Connection from '{source_name}' references non-existent target: '{target_name}'"
                        )

    # Check for disconnected nodes (no incoming or outgoing connections)
    connected_nodes = set()
    for source_name, conn_data in connections.items():
        connected_nodes.add(source_name)
        if isinstance(conn_data, dict):
            for conn_type, outputs in conn_data.items():
                if isinstance(outputs, list):
                    for targets in outputs:
                        if isinstance(targets, list):
                            for target in targets:
                                if isinstance(target, dict):
                                    connected_nodes.add(target.get("node", ""))

    for node in nodes:
        name = node.get("name", "")
        node_type = node.get("type", "")
        # Skip trigger nodes (they're sources, not targets)
        if any(t in node_type.lower() for t in ["trigger", "webhook"]):
            continue
        if name and name not in connected_nodes and len(nodes) > 1:
            errors.append(f"Node '{name}' is disconnected from the workflow")

    return errors
