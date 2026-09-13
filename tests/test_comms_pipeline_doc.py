"""
docs/reference/comms-pipeline.md truthfulness (aos#236.9).

Dangling-wires audit (2026-09-13): "comms_bus :4099 / CommsStoreConsumer /
message-person — zero grep hits anywhere, doc describes fiction." The real
pipeline is core/engine/comms/extract/pipeline.py (scheduled via the
comms-extract cron through extract/lifecycle.py), the comms.db consumers
are core/engine/comms/consumers/pattern_update.py and people_intel.py (real
code, just never wired to a running bus), and the Sentinel spawner
(core/engine/comms/sentinel/spawner.py) is default-off since v0.8.0.

This test doesn't try to parse prose for "truthfulness" — it checks two
mechanical things that would have caught the original doc's problem:
  1. Frontmatter is untouched (the task said keep it, stale globs and all).
  2. The body is short (<=20 lines) and every core/... file path it
     name-checks as something that EXISTS actually exists on disk — the
     exact property the old doc violated for comms_bus/main.py,
     comms_store.py, and message-person.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOC = REPO_ROOT / "docs" / "reference" / "comms-pipeline.md"

EXPECTED_FRONTMATTER = """---
globs:
  - "core/comms/**"
  - "core/engine/comms/**"
  - "core/services/comms_bus/**"
  - "core/bin/cli/message-person"
  - "core/bin/crons/enrich-comms"
description: Comms pipeline architecture — unified message store, bus, trust cascade, messaging
---"""

# Paths this doc explicitly calls out as NOT existing (the old architecture) --
# excluded from the "every referenced path is real" check below.
_KNOWN_FICTIONAL = {
    "core/services/comms_bus",
    "core/bin/cli/message-person",
}


def _split_frontmatter(text: str) -> tuple[str, str]:
    m = re.match(r"(---\n.*?\n---)\n*(.*)", text, re.DOTALL)
    assert m, "doc has no frontmatter block"
    return m.group(1), m.group(2)


def test_frontmatter_is_unchanged():
    text = DOC.read_text()
    frontmatter, _ = _split_frontmatter(text)
    assert frontmatter == EXPECTED_FRONTMATTER


def test_body_is_at_most_20_lines():
    text = DOC.read_text()
    _, body = _split_frontmatter(text)
    lines = body.rstrip("\n").splitlines()
    assert len(lines) <= 20, f"body is {len(lines)} lines, want <=20:\n{body}"


def test_every_core_path_the_body_cites_as_real_actually_exists():
    text = DOC.read_text()
    _, body = _split_frontmatter(text)
    cited = set(re.findall(r"`(core/[\w./-]+)`", body))
    assert cited, "expected the rewritten doc to name at least one real core/ path"

    missing = []
    for rel in cited:
        if rel in _KNOWN_FICTIONAL:
            continue
        if not (REPO_ROOT / rel).exists():
            missing.append(rel)
    assert missing == [], f"doc cites paths that don't exist: {missing}"


def test_fictional_bus_daemon_is_named_only_as_history_not_current_architecture():
    """The old daemon/class/CLI names may appear (explaining what was
    removed), but the doc must say plainly that they never existed --
    not describe them as live components the way the old version did."""
    text = DOC.read_text()
    _, body = _split_frontmatter(text)
    assert "never existed" in body or "does not exist" in body


def test_real_replacement_components_are_named():
    text = DOC.read_text()
    _, body = _split_frontmatter(text)
    for must_mention in (
        "extract/pipeline.py",
        "pattern_update.py",
        "people_intel.py",
        "sentinel/spawner.py",
    ):
        assert must_mention in body, f"expected the doc to name {must_mention}"
