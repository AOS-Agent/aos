"""
Migration 107: Seed ~/.aos/config/qren-site-root from the existing publish root.

release.sh used to hardcode the qren.ai publish root — a machine-local path on
the operator's release machine that also embeds a personal domain. The privacy
gate rightly refuses to push that literal to the public repo, so the path moved
to instance config: ~/.aos/config/qren-site-root (one line, absolute path),
overridable with QREN_SITE_ROOT.

Framework ships the mechanism; this migration carries the state across without
naming the path in code: the publish root is discoverable on the release
machine as the unique <serving root>/sites/qren directory on a mounted volume.
Where exactly one match exists, seed the config with it. On every other
machine (which never releases) there is nothing to find and nothing to seed —
release.sh fails with a clear instruction if it is ever run there. Graceful
skip, not crash; ambiguity (several matches) also skips and leaves the choice
to the operator rather than guessing.

Idempotent: check() passes once the config file has content, or when no
unambiguous publish root exists on this machine.
"""

DESCRIPTION = "Move the app publish root out of release.sh into instance config"

import glob
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = HOME / ".aos" / "config" / "qren-site-root"


def _discover() -> Path | None:
    matches = sorted(glob.glob("/Volumes/*/*/sites/qren"))
    dirs = [Path(m) for m in matches if Path(m).is_dir()]
    return dirs[0] if len(dirs) == 1 else None


def check() -> bool:
    if CONFIG_PATH.is_file() and CONFIG_PATH.read_text().strip():
        return True
    return _discover() is None


def up():
    root = _discover()
    if root is None:
        return "no unambiguous publish root on this machine; nothing to seed"
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(str(root) + "\n")
    return f"seeded {CONFIG_PATH} from existing publish root"
