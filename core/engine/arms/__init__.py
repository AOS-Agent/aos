"""Arm engine — the authority on what AOS modules exist and whether they work.

Three consumers read this package, and they must never drift apart:
  1. `aos arm` CLI            — operator-facing verbs
  2. the desktop app          — shells out to `aos arm status --json`
  3. reconcile / doctor       — same probes, same verdicts

Design note (2026-08-17): the Tauri app carries a lightweight copy of these
probes in Rust because it must also run on a machine where AOS is NOT yet
installed (it is the installer). Wherever `aos` IS on PATH, the app should
prefer shelling out to this engine so there is exactly one authority on what
"healthy" means. Two implementations of a truth is how the panel started lying.
"""

from .manifest import Manifest, Module, load_manifest
from .probe import ModuleState, probe_all, probe_module

__all__ = [
    "Manifest",
    "Module",
    "ModuleState",
    "load_manifest",
    "probe_all",
    "probe_module",
]
