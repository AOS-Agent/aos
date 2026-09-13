"""
Every declared service.yaml plist_template must resolve to a real file
(aos#236.7).

Dangling-wires audit (2026-09-13): "crawler/mesh/converse/listen/memory
plist_template — file absent (6 of 10 services)". service_registry.py's
schema doc says plist_template is "a basename under config/launchagents/",
but that has drifted from reality: converse and work_runner's actual
activation code (migration 101, core/engine/work/cli.py's `work runner
enable`) both hardcode `core/services/<name>/<basename>` instead — the
template genuinely lives next to the service, not in config/launchagents/.
So a plist_template resolves if it exists under EITHER location; this test
encodes that as the real contract, not the stale docstring.

The literal sentinel "generated" (whatsmeow — installed by
core/infra/integrations/whatsapp/setup.sh, no template file to check) and a
null/absent field (no framework plist) are not paths and are excluded.

Two services failed this before aos#236.7:
  - crawler: MCP stdio server spawned by an MCP client — never a LaunchAgent
    at all. The field was stale from before that shape existed; removed.
  - mesh: genuinely a LaunchAgent-shaped resident service (keepalive,
    liveness: http, port 4100) that the Mesh initiative hasn't finished
    rolling out — core/bin/internal/meshd, the wrapper its own main.py
    docstring names, does not exist either. Rather than fabricate a plist
    for a daemon whose deployment shape isn't settled yet, plist_template is
    now explicitly null (schema-legal for "no framework plist"), with a
    comment. Inventing a template here would be exactly the kind of made-up
    wiring this audit is trying to eliminate, not add.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVICES_DIR = REPO_ROOT / "core" / "services"
LAUNCHAGENTS_DIR = REPO_ROOT / "config" / "launchagents"

sys.path.insert(0, str(REPO_ROOT / "core" / "infra" / "lib"))
from service_registry import load_registry  # noqa: E402


def _resolve(name: str, plist_template: str) -> Path | None:
    """A plist_template resolves under config/launchagents/ OR the service's
    own directory — see module docstring for why both are legitimate.

    `name` is the manifest's `name` field, which is not always the directory
    name (work-runner's service.yaml says `name: work-runner`, its directory
    is core/services/work_runner) — normalize the same way
    _core_services_manifests() does.
    """
    for candidate in (
        LAUNCHAGENTS_DIR / plist_template,
        SERVICES_DIR / name.replace("-", "_") / plist_template,
    ):
        if candidate.exists():
            return candidate
    return None


def _core_services_manifests():
    """Only core/services/*/service.yaml — config/services.d/*.yaml
    (tombstones for retired services with no code dir, e.g. `listen`) are out
    of scope: nothing renders a plist for a service whose directory is
    already gone."""
    for manifest in load_registry():
        svc_yaml = SERVICES_DIR / manifest.name.replace("-", "_") / "service.yaml"
        if svc_yaml.exists():
            yield manifest


def test_every_core_services_plist_template_resolves_or_is_a_known_sentinel():
    unresolved = []
    for manifest in _core_services_manifests():
        template = manifest.plist_template
        if not template or template == "generated":
            continue  # null ("no framework plist") and the installer sentinel are valid
        if _resolve(manifest.name, template) is None:
            unresolved.append(f"{manifest.name}: plist_template={template!r}")

    assert unresolved == [], (
        "service.yaml declares a plist_template that resolves under neither "
        f"config/launchagents/ nor its own service dir: {unresolved}"
    )


def test_crawler_has_no_plist_template_field():
    """crawler is an MCP stdio server spawned by a client, never a
    LaunchAgent — the field should be absent, not pointing at a phantom."""
    manifest = next(m for m in load_registry() if m.name == "crawler")
    assert manifest.plist_template is None, (
        f"crawler is not a LaunchAgent; plist_template should be removed, "
        f"got {manifest.plist_template!r}"
    )


def test_mesh_plist_template_is_explicitly_null_not_a_dangling_basename():
    """mesh IS meant to become a LaunchAgent (keepalive/http-liveness/port
    are all declared) but the initiative hasn't shipped its plist or
    wrapper yet — null is the honest, schema-legal value; a fabricated
    template would be worse than the gap it's supposed to close."""
    manifest = next(m for m in load_registry() if m.name == "mesh")
    assert manifest.plist_template is None, (
        f"mesh has no real plist template yet — expected null, got "
        f"{manifest.plist_template!r}"
    )


def test_converse_template_resolves_under_its_own_service_dir():
    """Pin the service-dir-local case explicitly, so a future change that moves
    it (or breaks the dual-location resolver) is loud.

    `work-runner` was the other one, and it is deliberately gone: the same
    release deleted the service, its manifest and its plist template outright
    (0 rows in `task_runs`, ever). Asserting its template still resolves would
    contradict tests/engine/work/test_retired_surfaces.py, which asserts the
    template must NOT come back — an installable template for code that does
    not exist is the dangerous half of that retirement.
    """
    for name, dirname in (("converse", "converse"),):
        manifest = next(m for m in load_registry() if m.name == name)
        assert manifest.plist_template, f"{name} should still declare a plist_template"
        expected = SERVICES_DIR / dirname / manifest.plist_template
        assert expected.exists(), f"{name}'s plist_template should live at {expected}"
