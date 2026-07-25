"""Pack manifest linter — test-time validation for carrier packs.

Manifests declare regexes that run over arbitrary message text inside the
Qareen process, JSONPath expressions that parse live API payloads, and
check-digit validator names resolved from a registry. A bad pack must fail
here, in tests, never in production. ``lint_manifest`` returns a list of
human-readable problems; an empty list means the manifest is valid.

ReDoS guard: tracking patterns are rejected if they contain an UNBOUNDED
quantifier (``*``, ``+``, ``{n,}``) or a NESTED quantifier (a quantified
group whose body itself contains a quantifier, e.g. ``(a{2,3})+``). Both
are classic catastrophic-backtracking shapes; bounded flat patterns
(``[0-9]{22}``, ``1Z[0-9A-Z]{16}``) are the pack idiom.
"""

import re
from typing import Any, Dict, List, Optional

from . import checkdigits, jsonpath
from .models import Milestone

# re's pattern parser moved names across versions; both expose parse() with
# the same opcodes. `re._parser` exists on 3.11+ (incl. 3.14), `sre_parse`
# is the public-ish name on 3.9/3.10.
try:  # Python >= 3.11
    from re import _parser as _re_parser
except ImportError:  # Python 3.9 / 3.10
    import sre_parse as _re_parser  # type: ignore[no-redef]

_MAXREPEAT = _re_parser.MAXREPEAT

_VALID_AUTH_MODELS = {"oauth2_client_credentials", "api_key", "basic", "none"}

# Manifest sections every pack must carry (values may be tuned per carrier,
# but the keys must exist so the engine can rely on them).
_REQUIRED_TOP_LEVEL = [
    "carrier",
    "display_name",
    "auth",
    "endpoints",
    "tracking",
    "capabilities",
    "status_map",
    "response_map",
    "rate_limits",
    "retention",
]


def _plain(node: Any) -> Any:
    """Normalize a parsed-regex tree to plain lists/tuples.

    ``re._parser.parse`` returns ``SubPattern`` objects which ARE lists on
    3.9 but plain classes (not list subclasses) on 3.12+ — isinstance checks
    against ``list`` silently skip them. Convert once, up front, so the
    walker below sees the same shape on every supported Python.
    """
    if isinstance(node, tuple):
        return tuple(_plain(x) for x in node)
    if isinstance(node, (str, bytes)) or node is None or isinstance(node, int):
        return node
    if isinstance(node, list) or hasattr(node, "data"):
        return [_plain(x) for x in node]
    return node


_REPEAT_OPS = tuple(
    op for op in (_re_parser.MAX_REPEAT, getattr(_re_parser, "POSSESSIVE_REPEAT", None)) if op
)


def _subpatterns(arg: Any) -> Any:
    """Yield every token list nested inside an opcode argument.

    Handles SUBPATTERN, BRANCH, ASSERT/ASSERT_NOT (lookarounds carry a
    subpattern), GROUPREF_EXISTS, ATOMIC_GROUP, etc. without enumerating
    opcodes per Python version.
    """
    if isinstance(arg, list):
        yield arg
    elif isinstance(arg, tuple):
        for item in arg:
            yield from _subpatterns(item)


def _find_bad_repeats(tokens: Any, inside_repeat: bool, problems: List[str], pattern: str) -> None:
    """Walk parsed regex tokens, flagging unbounded and nested quantifiers."""
    for op, arg in tokens:
        if op in _REPEAT_OPS:
            _min, _max, body = arg
            if _max is _MAXREPEAT:
                problems.append(
                    "pattern %r uses an unbounded quantifier (*, +, or {n,}) — "
                    "use bounded repeats like {n,m} (ReDoS guard)" % pattern
                )
            if inside_repeat:
                problems.append(
                    "pattern %r nests a quantifier inside a quantified group "
                    "(ReDoS guard)" % pattern
                )
            _find_bad_repeats(body, True, problems, pattern)
        else:
            for sub in _subpatterns(arg):
                _find_bad_repeats(sub, inside_repeat, problems, pattern)


def lint_pattern(pattern: Any) -> List[str]:
    """ReDoS-guard a single tracking pattern. Empty list = OK."""
    problems: List[str] = []
    if not isinstance(pattern, str) or not pattern:
        return ["tracking pattern must be a non-empty string, got %r" % (pattern,)]
    try:
        parsed = [_plain(token) for token in _re_parser.parse(pattern)]
    except re.error as exc:
        return ["pattern %r does not compile: %s" % (pattern, exc)]
    _find_bad_repeats(parsed, False, problems, pattern)
    return problems


def _lint_response_map(node: Any, where: str, problems: List[str]) -> None:
    """Every string leaf in a response_map must be a parseable JSONPath."""
    if isinstance(node, str):
        if not jsonpath.is_valid(node):
            problems.append("response_map %s is not a parseable path: %r" % (where, node))
    elif isinstance(node, dict):
        for key, value in node.items():
            _lint_response_map(value, "%s.%s" % (where, key), problems)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _lint_response_map(value, "%s[%d]" % (where, i), problems)
    else:
        problems.append("response_map %s must be a path string or mapping, got %r" % (where, node))


def lint_manifest(manifest: Dict[str, Any], source: Optional[str] = None) -> List[str]:
    """Validate a parsed manifest dict. Returns a list of problems ([] = valid).

    *source* is only used to make error messages point at a file.
    """
    where = source or "<manifest>"
    problems: List[str] = []
    if not isinstance(manifest, dict):
        return ["%s: manifest must be a mapping" % where]

    for key in _REQUIRED_TOP_LEVEL:
        if key not in manifest:
            problems.append("%s: missing required section %r" % (where, key))

    # ── auth ──────────────────────────────────────────────────────────
    auth = manifest.get("auth")
    if isinstance(auth, dict):
        model = auth.get("model")
        if model not in _VALID_AUTH_MODELS:
            problems.append(
                "%s: auth.model %r not one of %s" % (where, model, sorted(_VALID_AUTH_MODELS))
            )
        if model != "none" and not auth.get("keychain_keys"):
            problems.append(
                "%s: auth.keychain_keys must list the Keychain key NAMES the pack "
                "needs (values never appear in the manifest)" % where
            )
    elif "auth" in manifest:
        problems.append("%s: auth must be a mapping" % where)

    # ── tracking: patterns + check digit ──────────────────────────────
    tracking = manifest.get("tracking")
    if isinstance(tracking, dict):
        patterns = tracking.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            problems.append("%s: tracking.patterns must be a non-empty list" % where)
        else:
            for pattern in patterns:
                problems.extend(lint_pattern(pattern))

        # body_scan_exclude filters `patterns`; it is never a second source
        # of truth. An entry that matches nothing in `patterns` is almost
        # always a typo, and it fails OPEN — the pattern keeps scanning raw
        # message text while the manifest claims it was excluded.
        excluded = tracking.get("body_scan_exclude")
        if excluded is not None:
            if not isinstance(excluded, list):
                problems.append(
                    "%s: tracking.body_scan_exclude must be a list" % where
                )
            elif isinstance(patterns, list):
                for pattern in excluded:
                    if pattern not in patterns:
                        problems.append(
                            "%s: tracking.body_scan_exclude entry %r is not in "
                            "tracking.patterns — it excludes nothing"
                            % (where, pattern)
                        )
                if len(set(excluded)) == len(patterns) and patterns:
                    problems.append(
                        "%s: tracking.body_scan_exclude excludes every pattern "
                        "— this carrier could never be detected in text" % where
                    )

        validator = tracking.get("check_digit")
        if validator is not None and validator not in checkdigits.names():
            problems.append(
                "%s: tracking.check_digit %r is not a registered validator "
                "(registered: %s)" % (where, validator, checkdigits.names())
            )
    elif "tracking" in manifest:
        problems.append("%s: tracking must be a mapping" % where)

    # ── status_map: values must be canonical milestones ───────────────
    status_map = manifest.get("status_map")
    if isinstance(status_map, dict):
        valid = {m.value for m in Milestone}
        for code, milestone in status_map.items():
            if milestone not in valid:
                problems.append(
                    "%s: status_map[%r] → %r is not a canonical milestone (%s)"
                    % (where, code, milestone, sorted(valid))
                )
    elif "status_map" in manifest:
        problems.append("%s: status_map must be a mapping of carrier code → milestone" % where)

    # ── response_map: every leaf path must parse ──────────────────────
    response_map = manifest.get("response_map")
    if isinstance(response_map, dict):
        _lint_response_map(response_map, "response_map", problems)
    elif "response_map" in manifest:
        problems.append("%s: response_map must be a mapping" % where)

    # ── capabilities / rate_limits / retention sanity ─────────────────
    capabilities = manifest.get("capabilities")
    if isinstance(capabilities, dict):
        for key in ("edd", "pod", "push"):
            if key in capabilities and not isinstance(capabilities[key], bool):
                problems.append("%s: capabilities.%s must be a boolean" % (where, key))
    elif "capabilities" in manifest:
        problems.append("%s: capabilities must be a mapping" % where)

    rate_limits = manifest.get("rate_limits")
    if isinstance(rate_limits, dict):
        for key in ("requests_per_day", "min_interval_seconds"):
            value = rate_limits.get(key)
            if value is not None and (not isinstance(value, (int, float)) or value <= 0):
                problems.append("%s: rate_limits.%s must be a positive number" % (where, key))
    elif "rate_limits" in manifest:
        problems.append("%s: rate_limits must be a mapping" % where)

    retention = manifest.get("retention")
    if isinstance(retention, dict):
        days = retention.get("delete_days_after_delivery")
        if days is not None and (not isinstance(days, int) or days <= 0):
            problems.append(
                "%s: retention.delete_days_after_delivery must be a positive int "
                "or null" % where
            )
    elif "retention" in manifest:
        problems.append("%s: retention must be a mapping" % where)

    return problems
