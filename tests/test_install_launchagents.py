"""
Guards on which LaunchAgents the installer is allowed to deploy.

The installer used to glob `config/launchagents/*` and `launchctl load`
everything it found. A glob sees filenames but not the service registry's
`status`, so every fresh install force-loaded:

  - `com.aos.listen`        — status: retired ("must NOT be loaded")
  - `com.aos.crawler/memory/mesh` — status: optional (opt-in only)
  - `com.aos.qareen-tunnel` — a Cloudflare tunnel nobody enabled, rendered with
    its `__CLOUDFLARED__` placeholder unsubstituted (the installer only replaces
    `__HOME__`), producing a plist whose program path is the literal string
    "__CLOUDFLARED__" — loaded with KeepAlive, i.e. a permanent crash loop.

These tests pin the fix: deployment is derived from the registry, and a plist
carrying an unresolved placeholder is never loaded.
"""

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / "install.sh"
LAUNCHAGENTS = REPO / "config" / "launchagents"
REGISTRY_PATH = REPO / "core" / "infra" / "lib" / "service_registry.py"

# The harness itself: a launchd presence that is not a service, allowlisted in
# install.sh as INFRA_PLISTS.
INFRA = {
    "com.aos.scheduler.plist",
    "com.aos.sentinel.plist",
    "com.aos.claude-remote.plist",
}


def _registry():
    spec = importlib.util.spec_from_file_location("svc_registry_install_test", REGISTRY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["svc_registry_install_test"] = mod
    spec.loader.exec_module(mod)
    return mod.load_registry()


def _deployable() -> set[str]:
    """Plist basenames the installer may deploy — active services + infra."""
    allowed = {
        m.plist_template
        for m in _registry()
        if m.is_active and m.plist_template and m.plist_template != "generated"
    }
    return allowed | INFRA


def _would_deploy(basename: str) -> bool:
    """Mirror install.sh's match: allowlist entries match with or without .template."""
    name = basename[: -len(".template")] if basename.endswith(".template") else basename
    return any(a in (basename, name) for a in _deployable())


def test_installer_consults_the_registry():
    """The deploy set must be derived from the registry, not from a directory glob."""
    src = INSTALL_SH.read_text()
    assert "_deployable_plists" in src, (
        "install.sh must gate LaunchAgent deployment on the service registry"
    )
    assert "service_registry" in src, "install.sh must import the service registry"


def test_retired_services_are_never_deployed():
    """A retired service must not be loaded — core/services/README.md is explicit."""
    for manifest in _registry():
        if not manifest.is_retired or not manifest.plist_template:
            continue
        assert not _would_deploy(manifest.plist_template), (
            f"{manifest.name} is retired but its plist would still be deployed"
        )


def test_optional_services_are_not_auto_deployed():
    """Optional services are opt-in; the installer must not force them on."""
    for manifest in _registry():
        if manifest.status != "optional" or not manifest.plist_template:
            continue
        if manifest.plist_template == "generated":
            continue
        assert not _would_deploy(manifest.plist_template), (
            f"{manifest.name} is optional but would be auto-deployed"
        )


def test_active_services_with_a_plist_are_deployed():
    """The flip side: nothing active silently stops shipping."""
    for manifest in _registry():
        if not manifest.is_active or not manifest.plist_template:
            continue
        if manifest.plist_template == "generated":
            continue
        assert _would_deploy(manifest.plist_template), (
            f"{manifest.name} is active but would not be deployed"
        )


def test_cloudflare_tunnel_is_not_installer_owned():
    """The tunnel is opt-in, rendered by tunnel_manager — never by the installer."""
    assert not _would_deploy("com.aos.qareen-tunnel.plist.template")


def test_installer_guards_unsubstituted_placeholders():
    """A plist with an unresolved __PLACEHOLDER__ must never be loaded."""
    src = INSTALL_SH.read_text()
    assert re.search(r"__\[A-Z_\]\\\{2,\\\}__", src), (
        "install.sh must refuse to load plists containing __PLACEHOLDER__ values"
    )


def test_only_home_placeholder_in_deployable_plists():
    """
    Every plist the installer deploys must be fully renderable with the single
    substitution the installer performs (__HOME__). Anything else belongs to a
    different renderer and must not be in the deploy set.
    """
    for path in LAUNCHAGENTS.iterdir():
        if not path.name.startswith("com.aos."):
            continue
        if not _would_deploy(path.name):
            continue
        leftover = set(re.findall(r"__[A-Z_]{2,}__", path.read_text())) - {"__HOME__"}
        assert not leftover, (
            f"{path.name} is deployed by the installer but carries {sorted(leftover)}, "
            "which the installer does not substitute"
        )


def test_vscode_is_gone():
    """cmux is the only supported terminal surface."""
    for path in (INSTALL_SH, REPO / "core" / "bin" / "cli" / "aos"):
        text = path.read_text()
        assert "visual-studio-code" not in text, f"{path.name} still installs VS Code"
        assert "Visual Studio Code" not in text, f"{path.name} still references VS Code"
