"""
Migration 009: Consolidate loose MCP servers and vendor tools.

Current state:
  ~/chitchats-mcp/      — ChitChats shipping MCP (48MB)
  ~/mcp-gsuite/         — Google Suite MCP (174MB)
  ~/wave_mcp/           — Wave accounting MCP (59MB)
  ~/vendor/             — Loose vendor dir with ayrshare-mcp + chitchats-mcp duplicate
  ~/aos/vendor/         — Framework vendor (Steer, iphone-mirror, clickup-mcp)

Target:
  ~/aos/vendor/         — All vendor deps, tracked in .gitignore
  Loose dirs removed, symlinks left if anything references old paths

Also cleans up:
  - clickup-mcp in vendor (deprecated, replaced by Plane)
"""

DESCRIPTION = "Consolidate MCP servers and vendor tools into ~/aos/vendor/"

import shutil
from pathlib import Path

HOME = Path.home()
VENDOR = HOME / "aos" / "vendor"

# MCP servers to consolidate
MCP_SERVERS = [
    ("chitchats-mcp", HOME / "chitchats-mcp"),
    ("mcp-gsuite", HOME / "mcp-gsuite"),
    ("wave-mcp", HOME / "wave_mcp"),
]

# Loose vendor dir
LOOSE_VENDOR = HOME / "vendor"


def check() -> bool:
    """Applied if no loose MCP dirs exist."""
    for name, path in MCP_SERVERS:
        if path.exists() and not path.is_symlink():
            return False
    if LOOSE_VENDOR.exists():
        return False
    return True


def up() -> bool:
    """Move MCP servers into vendor."""
    VENDOR.mkdir(parents=True, exist_ok=True)

    for name, src in MCP_SERVERS:
        dst = VENDOR / name
        if src.exists() and not src.is_symlink():
            if dst.exists():
                print(f"       {name} already in vendor, removing loose copy")
                shutil.rmtree(str(src))
            else:
                shutil.move(str(src), str(dst))
                print(f"       Moved ~/{src.name}/ → vendor/{name}/")

    # Consolidate ~/vendor/ into ~/aos/vendor/
    if LOOSE_VENDOR.exists():
        for item in LOOSE_VENDOR.iterdir():
            if item.name.startswith("."):
                continue
            dst = VENDOR / item.name
            if not dst.exists():
                shutil.move(str(item), str(dst))
                print(f"       Moved ~/vendor/{item.name} → aos/vendor/")
            else:
                print(f"       {item.name} already in aos/vendor/, skipping")
        # Remove loose vendor
        try:
            shutil.rmtree(str(LOOSE_VENDOR))
            print("       Removed ~/vendor/")
        except OSError:
            pass

    # Remove deprecated clickup-mcp (replaced by Plane)
    clickup = VENDOR / "clickup-mcp"
    if clickup.exists():
        shutil.rmtree(str(clickup))
        print("       Removed deprecated clickup-mcp (replaced by Plane)")

    return True
