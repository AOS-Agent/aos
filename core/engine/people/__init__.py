"""People Intelligence database package (framework-owned)."""

from .normalize import normalize_phone, normalize_email, normalize_name, phonetic_key
from .identity import IdentityResolver, ResolveResult

__all__ = [
    "normalize_phone", "normalize_email", "normalize_name", "phonetic_key",
    "IdentityResolver", "ResolveResult",
]
