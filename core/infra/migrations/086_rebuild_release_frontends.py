"""
Migration 086: Rebuild missing vite frontends in the active release.

Releases are cut with `git archive`, which extracts only tracked files —
gitignored build output (core/qareen/screen/dist) never landed in a release,
so every runtime since the release system took over :4096 served a UI-less
qareen backend. release-manager now builds vite frontends into each release
at create time; this migration repairs machines ALREADY sitting on a
UI-less active release, so they get the UI immediately instead of waiting
for the next release cut.

Atomic-migration rule: the framework change (release-manager build step)
ships in the same diff as this instance-layer repair.

Idempotent: a release whose vite frontends all have dist/index.html is left
untouched. Graceful: if npm is unavailable or a build fails, the release is
re-frozen and the migration still succeeds (backend runs without UI —
component-lifecycle graceful skip) with a loud warning.
"""

import json
import shutil
import subprocess
from pathlib import Path

DESCRIPTION = "Rebuild missing vite frontends in the active release"

AOS_LINK = Path.home() / "aos"


def _vite_frontends(root: Path):
    """Tracked package.json files whose build script invokes vite build."""
    out = []
    for base in ("core", "apps"):
        d = root / base
        if not d.is_dir():
            continue
        for pkg in d.rglob("package.json"):
            if "node_modules" in pkg.parts:
                continue
            try:
                build = json.loads(pkg.read_text()).get("scripts", {}).get("build", "")
            except (json.JSONDecodeError, OSError):
                continue
            if "vite build" in build:
                out.append(pkg.parent)
    return out


def _missing(root: Path):
    return [p for p in _vite_frontends(root) if not (p / "dist" / "index.html").is_file()]


def _dev_repo() -> Path | None:
    for cand in (Path.home() / "project" / "aos", Path.home() / ".aos" / "repo"):
        if (cand / ".git").is_dir():
            return cand
    return None


def check() -> bool:
    release = AOS_LINK.resolve()
    if not release.is_dir():
        return True  # nothing to repair
    return not _missing(release)


def up() -> bool:
    release = AOS_LINK.resolve()
    missing = _missing(release)
    if not missing:
        print("       All release frontends already built ✓")
        return True

    if shutil.which("npm") is None:
        print("       WARN: npm not found — cannot rebuild frontends; UI will be absent")
        return True  # graceful skip, not a failure

    dev = _dev_repo()
    for pkgdir in missing:
        rel = pkgdir.relative_to(release)
        print(f"       Building {rel} into active release...")
        subprocess.run(["chmod", "-R", "u+w", str(pkgdir)], check=False)
        mods = pkgdir / "node_modules"
        linked = False
        try:
            devmods = (dev / rel / "node_modules") if dev else None
            if devmods and devmods.is_dir():
                if not mods.exists():
                    mods.symlink_to(devmods)
                    linked = True
            else:
                r = subprocess.run(
                    ["npm", "ci", "--silent", "--no-audit", "--no-fund"],
                    cwd=pkgdir, capture_output=True, text=True,
                )
                if r.returncode != 0:
                    print(f"       WARN: npm ci failed for {rel} — skipping (UI absent)")
                    continue

            r = subprocess.run(
                ["npm", "run", "build", "--silent"],
                cwd=pkgdir, capture_output=True, text=True,
            )
            if r.returncode != 0:
                # tsc type errors shouldn't block — fall back to plain vite
                subprocess.run(
                    [str(mods / ".bin" / "vite"), "build", "--logLevel", "error"],
                    cwd=pkgdir, capture_output=True, text=True,
                )
        finally:
            if linked:
                mods.unlink(missing_ok=True)
            elif mods.is_dir() and not mods.is_symlink():
                shutil.rmtree(mods, ignore_errors=True)
            subprocess.run(["chmod", "-R", "a-w", str(pkgdir)], check=False)

        if (pkgdir / "dist" / "index.html").is_file():
            print(f"       ✓ {rel}/dist built")
        else:
            print(f"       WARN: {rel} build produced no dist/index.html — UI absent")

    return True


if __name__ == "__main__":
    print("already applied" if check() else ("done" if up() else "failed"))
