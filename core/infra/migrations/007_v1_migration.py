"""
Migration 007: DEPRECATED — v1 migration (no longer needed).

This migration was intended to import data from a separate AOS v1 installation,
but had a bug where V1_DIR and V2_DIR both pointed to ~/aos/ (same path).
The restructure.sh script handled the actual v1→v2 migration instead.

Kept for version history — check() always returns True so it's a no-op.
Cleaned up 2026-03-22.
"""

DESCRIPTION = "[deprecated] v1 migration — no-op"

from pathlib import Path

MIGRATION_RECORD = Path.home() / ".aos" / "logs" / "v1-migration.yaml"


def check() -> bool:
    """Always true — this migration is deprecated."""
    return True


def up() -> bool:
    """No-op."""
    print("       Deprecated — skipping")
    return True
