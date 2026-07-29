"""
Guards against reconcile checks that report health without verifying anything.

Reconcile is the immune system: it runs every ~30 minutes and on every update.
Its failure mode is not a crash — it is a green tick nobody earned.

Measured when this file was written, by running the whole suite against a
machine with nothing on it: **25 of 34 checks reported "invariant holds"**. They
were not evaluating an empty machine; they were failing to notice there was
nothing to evaluate. That single property is why several real regressions ran
unobserved:

  * the installer force-loaded a `retired` service — no check asserts what
    should NOT exist;
  * the `people` package moved and broke three crons for three months — no
    check asserted the import still resolved;
  * two ship-check guards grepped a file that had moved, and `grep -q` on a
    missing file falls through to the else branch and prints ✓;
  * `tracker_health` shipped green across 23/23 cron failures because it
    checked structure and never execution.

A bug hides one bug. A blind monitor hides every bug it will ever be
responsible for catching.

Two tests, two different jobs:

  test_no_check_claims_health_on_an_empty_machine — an absolute invariant. With
  no AOS installed there is nothing to make statements about, so nothing may
  claim OK. This must stay at zero forever.

  test_partial_install_blindness_does_not_regress — a RATCHET. On a
  half-provisioned machine 21 checks still claim OK; each needs its own
  judgement to fix (some need a precondition, some need stricter logic — a
  check that is vacuously true over an empty set should usually fail, not
  skip). The number is allowed to go down and never up.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).parent.parent

# Checks that still report OK on a half-provisioned machine. Every name here is
# a known blind spot, not an accepted one. Shrink this list; never grow it.
#
# `service_loaded` is the clearest illustration: with no LaunchAgents at all,
# "no active resident service is unloaded" is vacuously true, so it reports
# health on a machine running nothing.
KNOWN_PARTIAL_BLIND = 21


def _run_probe(body: str) -> str:
    """Run a probe in a subprocess so HOME is set before any module caches it."""
    script = textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", script, str(REPO)],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "HOME": tempfile.gettempdir()},
    )
    assert result.returncode == 0, f"probe failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout.strip().splitlines()[-1]


PROBE = """
    import os, sys, tempfile
    from pathlib import Path
    REPO = Path(sys.argv[1])
    fake = Path(tempfile.mkdtemp(prefix="probe."))
    {setup}
    os.environ["HOME"] = str(fake)
    sys.path.insert(0, str(REPO / "core" / "infra" / "reconcile"))
    sys.path.insert(0, str(REPO / "core" / "infra"))
    from checks import ALL_CHECKS
    claiming_ok = []
    for cls in ALL_CHECKS:
        try:
            c = cls()
            # Exactly what runner.py does: precondition gates everything.
            if not c.precondition():
                continue
            if c.check():
                claiming_ok.append(c.name)
        except Exception:
            pass  # a crash is loud; this test is about quiet false health
    print("RESULT:" + ",".join(sorted(claiming_ok)))
"""


def _claiming_ok(setup: str) -> list[str]:
    line = _run_probe(PROBE.format(setup=setup))
    payload = line.split("RESULT:", 1)[1]
    return [n for n in payload.split(",") if n]


def test_no_check_claims_health_on_an_empty_machine():
    """
    Nothing installed → nothing may report OK.

    This is the invariant that was worth 25 false green ticks. It has no
    tolerance: a check that answers "fine" here is answering about a machine it
    never looked at.
    """
    claiming = _claiming_ok(setup="pass  # nothing provisioned at all")
    assert claiming == [], (
        "These checks report health on a machine with no AOS installed:\n  "
        + "\n  ".join(claiming)
        + "\n\nGive each a precondition() that returns False when its inputs "
          "are absent, so the runner records SKIP instead of OK."
    )


def test_partial_install_blindness_does_not_regress():
    """
    Ratchet: AOS present, nothing else provisioned.

    Every name still reporting OK here is a check that cannot see its own
    subject missing. The count may shrink; it must never grow.
    """
    setup = (
        '(fake / "aos").symlink_to(REPO)\n'
        '    (fake / ".aos").mkdir()\n'
        '    (fake / ".aos" / "config").mkdir()'
    )
    claiming = _claiming_ok(setup=setup)
    assert len(claiming) <= KNOWN_PARTIAL_BLIND, (
        f"Reconcile blindness regressed: {len(claiming)} checks report health on a "
        f"half-provisioned machine, was {KNOWN_PARTIAL_BLIND}.\n  "
        + "\n  ".join(claiming)
        + "\n\nA new check must be able to tell 'verified fine' from "
          "'could not verify'."
    )


def test_base_class_offers_the_precondition_hook():
    """The mechanism itself must not be quietly removed."""
    sys.path.insert(0, str(REPO / "core" / "infra" / "reconcile"))
    import base

    assert hasattr(base.ReconcileCheck, "precondition")
    assert hasattr(base, "aos_installed")
    assert base.Status.SKIP is not None


def test_runner_gates_on_precondition_before_checking():
    """
    The gate must live in the runner, ahead of check().

    If it moved inside individual checks, every future check would have to
    remember to call it — and the one that forgets is the next blind spot.
    """
    runner = (REPO / "core" / "infra" / "reconcile" / "runner.py").read_text()
    assert "c.precondition()" in runner
    gate = runner.index("c.precondition()")
    evaluate = runner.index("if c.check():")
    assert gate < evaluate, "precondition must be evaluated before check()"
