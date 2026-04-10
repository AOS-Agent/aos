"""intelligence.inventory — Non-destructive vault inventory scanner.

Walks ~/vault/knowledge/, parses frontmatter, validates against the
vault contract, detects orphans, and caches the result in the
vault_inventory table.

Nothing in this subpackage writes to vault files. Everything is read-only.
The bootstrap flow (Part 8) consumes the inventory to propose
non-destructive frontmatter upgrades for operator approval.
"""

from .contract import (
    CONTRACT,
    STAGE_BY_FOLDER,
    DocumentContract,
    infer_stage,
    infer_type,
    validate,
)
from .scanner import InventoryStats, scan_vault

__all__ = [
    "CONTRACT",
    "STAGE_BY_FOLDER",
    "DocumentContract",
    "InventoryStats",
    "infer_stage",
    "infer_type",
    "scan_vault",
    "validate",
]
