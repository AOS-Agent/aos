"""People Intelligence database package (framework-owned)."""

from .identity import IdentityResolver, ResolveResult
from .normalize import normalize_email, normalize_name, normalize_phone, phonetic_key

__all__ = [
    "normalize_phone", "normalize_email", "normalize_name", "phonetic_key",
    "IdentityResolver", "ResolveResult",
]
