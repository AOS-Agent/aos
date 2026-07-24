"""Carrier pack discovery and loading.

A pack is a directory under ``carriers/`` containing ``manifest.yaml``.
Packs are discovered from the filesystem — the AOS rule is that the
filesystem declares the list, so nothing here hardcodes carrier names.
Directories starting with ``_`` (e.g. ``_template``) are scaffolds, not
live carriers: ``discover_packs`` sees them, ``load_packs`` skips them by
default.

Loading runs the linter and refuses an invalid pack: a bad manifest fails
loudly at load/test time instead of misbehaving inside the Qareen process.
"""

import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from . import linter

CARRIERS_DIR = Path(__file__).resolve().parent / "carriers"
MANIFEST_NAME = "manifest.yaml"


class PackError(ValueError):
    """Raised when a pack manifest is missing, unparseable, or fails lint."""


class CarrierPack:
    """A loaded, lint-clean carrier manifest.

    Attributes mirror the manifest sections; ``raw`` keeps the full parsed
    YAML for sections the engine doesn't interpret yet (url_templates is
    consumed by the detection layer in a later task).
    """

    def __init__(self, slug: str, path: Path, manifest: Dict[str, Any]) -> None:
        self.slug = slug  # directory name, e.g. "ups"
        self.path = path
        self.raw = manifest
        self.display_name: str = manifest["display_name"]
        self.auth: Dict[str, Any] = manifest["auth"]
        self.endpoints: Dict[str, Any] = manifest["endpoints"]
        self.tracking: Dict[str, Any] = manifest["tracking"]
        self.url_templates: List[str] = list(manifest.get("url_templates") or [])
        self.capabilities: Dict[str, Any] = manifest["capabilities"]
        self.status_map: Dict[str, str] = manifest["status_map"]
        self.response_map: Dict[str, Any] = manifest["response_map"]
        self.rate_limits: Dict[str, Any] = manifest["rate_limits"]
        self.retention: Dict[str, Any] = manifest["retention"]

    @property
    def check_digit(self) -> Optional[str]:
        """Registered check-digit validator name, or None."""
        return self.tracking.get("check_digit")

    @property
    def patterns(self) -> List[str]:
        return list(self.tracking.get("patterns") or [])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "CarrierPack(%r)" % self.slug


def discover_packs(carriers_dir: Optional[Path] = None) -> List[Path]:
    """Return the directories of all packs under *carriers_dir*, sorted.

    Includes ``_``-prefixed scaffold dirs — filtering them out is the
    caller's choice (``load_packs`` does it by default).
    """
    root = Path(carriers_dir) if carriers_dir else CARRIERS_DIR
    if not root.is_dir():
        return []
    return sorted(
        (d for d in root.iterdir() if d.is_dir() and (d / MANIFEST_NAME).is_file()),
        key=lambda d: d.name,
    )


def load_pack(pack_dir: Path) -> CarrierPack:
    """Load and lint one pack directory. Raises PackError on any problem."""
    pack_dir = Path(pack_dir)
    manifest_path = pack_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PackError("no %s in %s" % (MANIFEST_NAME, pack_dir))
    try:
        manifest = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as exc:
        raise PackError("%s: invalid YAML: %s" % (manifest_path, exc))
    problems = linter.lint_manifest(manifest, source=str(manifest_path))
    if problems:
        raise PackError(
            "%s failed lint:\n  - %s" % (manifest_path, "\n  - ".join(problems))
        )
    declared = manifest.get("carrier")
    if declared != pack_dir.name:
        raise PackError(
            "%s: carrier %r does not match directory name %r"
            % (manifest_path, declared, pack_dir.name)
        )
    return CarrierPack(slug=pack_dir.name, path=pack_dir, manifest=manifest)


def load_mapper(pack: CarrierPack) -> Optional[Callable[[str], Any]]:
    """Load the pack's optional ``mapper.py`` (XML → dict, e.g. Canada Post).

    Returns the ``track_xml_to_dict`` callable or None. The engine client
    runs it on XML bodies before response_map; fixture validation runs the
    same mapper, so what validation proves is what polling executes.
    """
    mapper_path = pack.path / "mapper.py"
    if not mapper_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "qareen_pack_mapper_%s" % pack.slug, str(mapper_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "track_xml_to_dict", None)


def load_packs(
    carriers_dir: Optional[Path] = None, include_scaffolds: bool = False
) -> Dict[str, CarrierPack]:
    """Load every pack under *carriers_dir*, keyed by slug.

    ``_``-prefixed scaffold packs (the ``_template`` source for new-carrier
    onboarding) are skipped unless *include_scaffolds* is set.
    """
    packs: Dict[str, CarrierPack] = {}
    for pack_dir in discover_packs(carriers_dir):
        if pack_dir.name.startswith("_") and not include_scaffolds:
            continue
        pack = load_pack(pack_dir)
        packs[pack.slug] = pack
    return packs
