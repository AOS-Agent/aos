#!/usr/bin/env python3
"""List a service's third-party imports that are missing from its pyproject.toml.

Prints one bare module name per line for every genuine third-party import a
service uses but does not declare as a dependency — nothing else. Callers
(ship-check) count the lines and warn per module. Exit status is always 0; the
absence of output means "no gaps".

What is filtered out, so only real dependency gaps remain:
  - standard-library modules      (sys.stdlib_module_names)
  - the service's own local modules (sibling *.py files and packages)
  - other AOS-internal modules — any import name that exists as a module or
    package ANYWHERE in the AOS tree (services import shared code like `log`,
    `notify`, `core.*` via sys.path inserts, so a sibling-only filter isn't
    enough)
  - relative imports              (from . / .. imports are always local)
  - dunder/private top-levels

Distribution vs import name: pyproject declares distributions (python-telegram-bot,
pyyaml) but code imports modules (telegram, yaml). When run with the service's
own venv interpreter — where those distributions are installed — this maps each
declared distribution back to the import names it provides, so telegram/yaml are
correctly seen as declared. Without that interpreter it falls back to a
normalized name match: conservative (it may miss an alias-named dep) but it never
invents a gap that is not a real undeclared import.
"""

import ast
import re
import sys
from pathlib import Path

_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", "site-packages", ".pytest_cache",
}


def _local_modules(svc_dir: Path) -> set[str]:
    names = {p.stem for p in svc_dir.glob("*.py")}
    names |= {
        p.name for p in svc_dir.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    }
    return names


def _aos_internal_modules(aos_root: Path) -> set[str]:
    """Every import name that resolves to a module or package inside the AOS
    tree — module file stems plus the package/dir names along each path. This
    catches shared internal code a service reaches via sys.path inserts (log,
    notify, resolver, core.*), which a sibling-only filter would miss."""
    names: set[str] = set()
    for p in aos_root.rglob("*.py"):
        rel = p.relative_to(aos_root).parts
        if any(part in _SKIP_DIRS for part in rel):
            continue
        names.add(p.stem)              # the module file itself
        names.update(rel[:-1])         # package/dir names on the way to it
    return names


def _declared_dists(pyproject: Path) -> set[str]:
    """Normalized distribution names from [project].dependencies.

    Parses TOML properly (tomllib, 3.11+) so extras like `uvicorn[standard]`
    don't truncate the list — a naive `[...]` regex stops at the extras
    bracket. Falls back to a per-line regex only if tomllib is unavailable.
    """
    deps: list[str] = []
    try:
        import tomllib
        with open(pyproject, "rb") as f:
            deps = tomllib.load(f).get("project", {}).get("dependencies", [])
    except Exception:
        in_deps = False
        for line in pyproject.read_text().splitlines():
            s = line.strip()
            if s.startswith("dependencies") and "[" in s:
                in_deps = True
                continue
            if in_deps and s.startswith("]"):
                break
            if in_deps:
                m = re.search(r'["\']([A-Za-z0-9_.-]+)', s)
                if m:
                    deps.append(m.group(1))

    names: set[str] = set()
    for spec in deps:
        name = re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0].strip()
        if name:
            names.add(name.replace("-", "_").lower())
    return names


def _used_imports(svc_dir: Path) -> set[str]:
    used: set[str] = set()
    for p in svc_dir.glob("*.py"):
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    used.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if (node.level or 0) > 0:
                    continue  # relative import — always local to the service
                if node.module:
                    used.add(node.module.split(".")[0])
    return used


def _declared_import_names(declared: set[str]) -> set[str]:
    """Import names provided by the declared distributions, per THIS
    interpreter's installed metadata. Empty when run with an interpreter that
    doesn't have the service's deps installed."""
    names: set[str] = set()
    try:
        from importlib import metadata
        for imp, dists in metadata.packages_distributions().items():
            if any(d.replace("-", "_").lower() in declared for d in dists):
                names.add(imp)
    except Exception:
        pass
    return names


def find_import_gaps(svc_dir: Path) -> list[str]:
    svc_dir = svc_dir.resolve()
    pyproject = svc_dir / "pyproject.toml"
    if not pyproject.exists():
        return []

    # core/services/<svc> → the AOS repo root is three levels up.
    aos_root = svc_dir.parents[2]

    local = _local_modules(svc_dir)
    internal = _aos_internal_modules(aos_root)
    declared = _declared_dists(pyproject)
    declared_imports = _declared_import_names(declared)
    stdlib = set(sys.stdlib_module_names)

    gaps = []
    for mod in sorted(_used_imports(svc_dir)):
        if mod in stdlib or mod in local or mod in internal or mod.startswith("_"):
            continue
        if mod.replace("-", "_").lower() in declared or mod in declared_imports:
            continue
        gaps.append(mod)
    return gaps


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: service-import-check.py <service-dir>", file=sys.stderr)
        return 2
    for mod in find_import_gaps(Path(argv[1])):
        print(mod)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
