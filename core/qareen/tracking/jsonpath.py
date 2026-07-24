"""Minimal JSONPath subset for manifest response_maps.

Manifests map carrier API responses onto normalized fields with expressions
like ``$.trackDetails[0].scanEvents[*].date``. We deliberately support only
a small, dependency-free subset — enough for real carrier payloads, small
enough to parse and lint safely:

    $            root
    .name        object key (letters, digits, '_', '-')
    [N]          array index (negative allowed)
    [*]          array/object wildcard — flattens one level

``parse`` raises JSONPathError on anything outside the subset (the linter
turns that into a manifest rejection). ``extract`` returns a single value,
a list when a wildcard was used, or None when the path doesn't match.
"""

from typing import Any, List, Optional, Union

_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class JSONPathError(ValueError):
    """Raised when a response_map path is outside the supported subset."""


# A parsed path is a list of steps: str = key, int = index, None = wildcard.
Step = Union[str, int, None]


def parse(path: str) -> List[Step]:
    """Parse *path* into steps, raising JSONPathError on invalid syntax."""
    if not isinstance(path, str) or not path.startswith("$"):
        raise JSONPathError("path must be a string starting with '$'")
    steps: List[Step] = []
    i = 1
    n = len(path)
    while i < n:
        c = path[i]
        if c == ".":
            i += 1
            start = i
            while i < n and path[i] in _NAME_CHARS:
                i += 1
            if i == start:
                raise JSONPathError("expected a key name after '.' in %r" % path)
            steps.append(path[start:i])
        elif c == "[":
            end = path.find("]", i)
            if end == -1:
                raise JSONPathError("unclosed '[' in %r" % path)
            inner = path[i + 1 : end].strip()
            if inner == "*":
                steps.append(None)
            else:
                try:
                    steps.append(int(inner))
                except ValueError:
                    raise JSONPathError(
                        "unsupported bracket segment [%s] in %r (only [N] and [*])"
                        % (inner, path)
                    )
            i = end + 1
        else:
            raise JSONPathError("unexpected character %r in %r" % (c, path))
    return steps


def is_valid(path: Any) -> bool:
    """True iff *path* parses — the linter's response_map check."""
    try:
        parse(path)
        return True
    except JSONPathError:
        return False


def _walk(node: Any, steps: List[Step]) -> List[Any]:
    """Return every node reachable via *steps* from *node*."""
    if not steps:
        return [node]
    step, rest = steps[0], steps[1:]
    if step is None:  # wildcard
        if isinstance(node, dict):
            children = list(node.values())
        elif isinstance(node, list):
            children = list(node)
        else:
            return []
        out: List[Any] = []
        for child in children:
            out.extend(_walk(child, rest))
        return out
    if isinstance(step, int):  # array index
        if not isinstance(node, list):
            return []
        try:
            return _walk(node[step], rest)
        except IndexError:
            return []
    # key
    if not isinstance(node, dict) or step not in node:
        return []
    return _walk(node[step], rest)


def extract(data: Any, path: str) -> Optional[Any]:
    """Evaluate *path* against parsed JSON *data*.

    Returns None when nothing matches, the single match otherwise, or a list
    of matches when the path contains a wildcard.
    """
    steps = parse(path)
    matches = _walk(data, steps)
    if None in steps:
        return matches
    return matches[0] if matches else None
