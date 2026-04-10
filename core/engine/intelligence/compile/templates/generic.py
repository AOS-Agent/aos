"""Generic fallback template — used when no platform-specific template fits."""

from .base import CaptureTemplate


class GenericTemplate(CaptureTemplate):
    name = "generic"
    # Inherits everything from base. Explicit class exists so the registry
    # can return a concrete type without special-casing None.
