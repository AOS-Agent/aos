"""The `channel-update` cron is gone, and the idea is written down (aos#235).

The 2026-09-13 review: it has been disabled in `crons.yaml` since "v1 script,
needs v2 rewrite"; its Bridge check probes `apps/bridge/main.py`, a path that has
not existed since the service moved, so it reported the bridge DOWN on every
single run; and its output is a raw dump of percentages, file paths and `<code>`
tracebacks — the exact opposite of the style everything else moved to. "Don't
resurrect it as-is."

Deleted rather than left commented out. A 500-line script nobody can run is not
a head start on the rewrite, and the one thing worth keeping — what an hourly
status should say, if anything — is a paragraph, now in docs/specs/.

The legacy filename `channel-update.yaml` is a different artifact entirely: the
update-freeze flag written by migration 117. These tests are careful to
distinguish them, because a careless cleanup of "everything matching
channel-update" would take out the freeze policy.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "docs" / "specs" / "channel-update-v2.md"

# The freeze flag's legacy filename — a real, live reference that must survive.
_FREEZE_FILE_REF = re.compile(r"channel-update\.yaml")


def test_the_cron_script_is_gone():
    assert not (REPO / "core" / "bin" / "crons" / "channel-update").exists()


def test_crons_yaml_has_no_reference_at_all():
    """Not even the commented-out placeholder: the spec is the placeholder now."""
    text = (REPO / "config" / "crons.yaml").read_text()
    assert "channel-update" not in text


def test_nothing_under_core_references_the_cron():
    """Only the freeze flag's filename and the prose that explains it remain."""
    offenders = []
    for path in (REPO / "core").rglob("*"):
        if not path.is_file() or path.suffix in (".pyc",):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "channel-update" not in line:
                continue
            if _FREEZE_FILE_REF.search(line):
                continue  # the update-freeze flag, a different thing
            # Prose naming the retired cron as retired is fine.
            if "since removed" in line or "and `channel-update`" in line:
                continue
            offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()[:90]}")
    assert not offenders, "live references to the removed cron:\n  " + "\n  ".join(offenders)


def test_the_v2_spec_exists_and_is_a_stub():
    assert SPEC.exists(), "the idea was kept, so it has to be written down somewhere"
    body = [ln for ln in SPEC.read_text().splitlines() if ln.strip()]
    assert len(body) <= 25, f"a stub, not a design doc: {len(body)} non-blank lines"


def test_the_v2_spec_says_what_it_replaces_and_why_the_v1_died():
    text = SPEC.read_text().lower()
    assert "digest" in text, "the v2 has to be placed against the digest model"
    assert "down" in text, "record the always-DOWN bug, or someone rebuilds it"
    assert "hourly" in text
