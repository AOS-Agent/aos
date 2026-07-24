"""Qareen shipment tracking — carrier packs, normalization, validation.

Core principle (auto-tracker initiative): carriers are data, not code. Each
carrier is a *pack* — a directory under ``carriers/`` with a ``manifest.yaml``
— discovered from the filesystem, never hardcoded. This package holds the
generic machinery every pack shares:

- ``models``       — Shipment / TrackingEvent dataclasses + Milestone enum
- ``checkdigits``  — check-digit validators (mod10) + registry
- ``jsonpath``     — minimal JSONPath subset used by manifest response_maps
- ``linter``       — test-time manifest validation (ReDoS guard et al.)
- ``packs``        — filesystem discovery + manifest loading
- ``engine``       — canonicalization, number validation, milestone mapping

Deliberately NOT here yet (later auto-tracker tasks): HTTP carrier calls,
scheduler, detection consumer, API router, storage migration.
"""
