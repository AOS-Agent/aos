"""Vault contract reconcile check.

Runs on every update cycle. Re-scans the vault, writes the inventory
table, and reports aggregate drift (total, issues, orphans). Never
auto-fixes — contract violations surface in the Knowledge UI (Part 7)
for operator review, and the bootstrap flow (Part 8) offers a guided
one-shot upgrade.

This check is intentionally "notify only":
    - If the inventory is healthy (0 issues) → OK
    - If there are issues → NOTIFY with a summary

It is never destructive. Scanner is read-only.
"""

from __future__ import annotations

import logging

from ..base import CheckResult, ReconcileCheck, Status

logger = logging.getLogger(__name__)


class VaultContractCheck(ReconcileCheck):
    name = "vault_contract"
    description = (
        "Scans ~/vault/knowledge/ and writes the vault_inventory table. "
        "Reports aggregate drift: total docs, contract violations, orphans. "
        "Never auto-fixes — operator review via Knowledge UI."
    )

    def __init__(self) -> None:
        self._stats = None

    def check(self) -> bool:
        """Runs the scanner as the 'check' step.

        Returns True only if every doc passes the contract — which is
        almost never the case for a mature vault. Drift is expected;
        the point is to keep the inventory cache fresh.
        """
        try:
            # Try both import styles — reconcile runs from various cwds
            try:
                from engine.intelligence.inventory import scan_vault
            except ImportError:
                from core.engine.intelligence.inventory import scan_vault
            self._stats = scan_vault()
        except Exception as e:
            logger.exception("vault_contract scan failed: %s", e)
            return False

        return (
            self._stats is not None
            and self._stats.with_issues == 0
            and self._stats.missing_frontmatter == 0
        )

    def fix(self) -> CheckResult:
        """Report-only — never mutates the vault."""
        if self._stats is None:
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message="Scanner did not produce stats",
            )

        stats = self._stats
        if stats.total == 0:
            return CheckResult(
                name=self.name,
                status=Status.SKIP,
                message="Vault is empty or missing",
            )

        # Drift is present — notify the operator
        by_type_summary = ", ".join(
            f"{t}={n}" for t, n in sorted(
                stats.by_type.items(), key=lambda x: -x[1]
            )[:6]
        )

        return CheckResult(
            name=self.name,
            status=Status.NOTIFY,
            message=(
                f"Vault inventory refreshed: {stats.total} docs, "
                f"{stats.with_issues} with contract violations, "
                f"{stats.orphans} orphans, "
                f"{stats.missing_frontmatter} missing frontmatter"
            ),
            detail=f"types: {by_type_summary}",
            notify=False,  # silent — operator reviews in Knowledge UI
        )
