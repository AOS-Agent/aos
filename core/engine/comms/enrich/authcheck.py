"""Auth-failure detection for the Haiku extraction path.

Yesterday's failure: the subscription `claude` session expired mid-backfill.
Every batch then failed identically, but the engine kept marching — burning the
whole run producing nothing, with no signal to the operator. This detects the
signature of an expired/invalid login in the CLI's output so the engine can
PAUSE cleanly (checkpointing what's done) and alert, instead of grinding on.

We match on a small, extendable set of case-insensitive substrings observed
from `claude --print` when the session is not authenticated. It is deliberately
conservative: matching a real auth failure pauses the run (safe — resumes after
`/login`); a false negative just falls back to the normal per-batch error path.
Add new signatures here as they are observed in the wild.
"""

from __future__ import annotations

# Case-insensitive substrings that indicate an auth/login problem rather than a
# transient per-batch error. Kept narrow to avoid pausing on ordinary failures.
AUTH_SIGNATURES = (
    "please run /login",
    "/login",
    "not logged in",
    "not authenticated",
    "authentication required",
    "authentication_error",
    "invalid api key",
    "invalid_api_key",
    "oauth token",
    "oauth error",
    "session expired",
    "credit balance is too low",
    "unauthorized",
    "401",
)


def is_auth_failure(*parts: str | None) -> bool:
    """True if any provided text (stderr, stdout, error string) carries an auth
    signature. None/empty parts are ignored."""
    blob = " ".join(p for p in parts if p).lower()
    if not blob:
        return False
    return any(sig in blob for sig in AUTH_SIGNATURES)
