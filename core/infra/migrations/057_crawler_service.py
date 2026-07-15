"""
Migration 057: Deploy crawler service (crawl4ai + MCP) for web research.

(Renumbered from 027 during release-train wave 3 promotion; see 056's
docstring for why the renumber is load-bearing, not cosmetic. Also
added a network preflight + one retry with backoff on the Playwright
Chromium download — see up().)

The crawler service gives all AOS agents the ability to crawl web pages,
extract structured data, and deep-crawl sites — headlessly, via MCP or CLI.

Steps:
1. Create venv at ~/.aos/services/crawler/.venv/
2. Install Python dependencies (crawl4ai, mcp, pyyaml)
3. Install Playwright Chromium browser
4. Create schema store directory
5. Copy seed schemas from framework
6. Run aos sync-mcp to register the MCP server
"""

DESCRIPTION = "Deploy crawler service (crawl4ai + MCP) for web research"

import shutil
import socket
import subprocess
import time
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
CRAWLER_DIR = AOS_ROOT / "core" / "services" / "crawler"
CRAWLER_VENV = HOME / ".aos" / "services" / "crawler" / ".venv"
CRAWLER_PYTHON = CRAWLER_VENV / "bin" / "python"
REQUIREMENTS = CRAWLER_DIR / "requirements.txt"
LOG_DIR = HOME / ".aos" / "logs"

SCHEMA_DIR = HOME / ".aos" / "data" / "crawler" / "schemas"
SEED_DIR = CRAWLER_DIR / "seed-schemas"


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _network_reachable(host: str = "pypi.org", port: int = 443, timeout: float = 5.0) -> bool:
    """Cheap reachability probe before starting a multi-minute download.

    pip install and the Playwright Chromium download both use a bare
    300s timeout with no retry — on a genuinely offline machine that's
    5 minutes wasted per step before failing. A quick preflight fails
    fast with a clear message instead.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run_with_retry(
    cmd: list[str], timeout: int = 300, retries: int = 1, backoff: float = 5.0
) -> subprocess.CompletedProcess:
    """Run cmd, retrying up to `retries` times with linear backoff on failure.

    Used for the Playwright Chromium download specifically — it's the
    largest download in this migration and the most likely to trip a
    transient network failure on a slow link.
    """
    attempt = 0
    while True:
        result = _run(cmd, timeout=timeout)
        if result.returncode == 0 or attempt >= retries:
            return result
        attempt += 1
        print(f"  Retrying in {backoff:.0f}s (attempt {attempt + 1}/{retries + 1})...")
        time.sleep(backoff)


def check() -> bool:
    """Applied if crawler venv exists and crawl4ai is importable."""
    if not CRAWLER_VENV.exists():
        return False
    if not CRAWLER_PYTHON.exists():
        return False
    # Check crawl4ai importable
    result = _run([str(CRAWLER_PYTHON), "-c", "import crawl4ai; print('ok')"])
    if result.returncode != 0:
        return False
    # Check schema dir exists
    if not SCHEMA_DIR.exists():
        return False
    return True


def up() -> bool:
    """Deploy the crawler service."""

    # 1. Create venv
    CRAWLER_VENV.parent.mkdir(parents=True, exist_ok=True)
    if not CRAWLER_PYTHON.exists():
        print("  Creating crawler venv...")
        result = _run(["python3", "-m", "venv", str(CRAWLER_VENV)])
        if result.returncode != 0:
            print(f"  ERROR: venv creation failed: {result.stderr}")
            return False
        print(f"  Created venv at {CRAWLER_VENV}")
    else:
        print(f"  Venv already exists at {CRAWLER_VENV}")

    # 2. Install dependencies
    if not _network_reachable():
        print("  ERROR: network unreachable (couldn't reach pypi.org:443).")
        print("  pip install and the Playwright download both need network access.")
        return False

    print("  Installing crawler dependencies...")
    if not REQUIREMENTS.exists():
        print(f"  ERROR: requirements.txt not found at {REQUIREMENTS}")
        return False

    result = _run(
        [str(CRAWLER_PYTHON), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
        timeout=300,
    )
    if result.returncode != 0:
        print(f"  ERROR: pip install failed: {result.stderr}")
        return False
    print("  Dependencies installed")

    # 3. Install Playwright Chromium — the largest download here, so it
    # gets one retry with backoff on top of the network preflight above.
    print("  Installing Playwright Chromium browser...")
    playwright_bin = CRAWLER_VENV / "bin" / "playwright"
    if playwright_bin.exists():
        result = _run_with_retry([str(playwright_bin), "install", "chromium"], timeout=300)
        if result.returncode != 0:
            print(f"  WARNING: Playwright install issue: {result.stderr}")
            # Try via python module
            result = _run_with_retry(
                [str(CRAWLER_PYTHON), "-m", "playwright", "install", "chromium"],
                timeout=300,
            )
            if result.returncode != 0:
                print(f"  ERROR: Playwright chromium install failed: {result.stderr}")
                return False
    else:
        result = _run_with_retry(
            [str(CRAWLER_PYTHON), "-m", "playwright", "install", "chromium"],
            timeout=300,
        )
        if result.returncode != 0:
            print(f"  ERROR: Playwright chromium install failed: {result.stderr}")
            return False
    print("  Playwright Chromium installed")

    # 4. Create schema directories
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Schema directory ready at {SCHEMA_DIR}")

    # 5. Copy seed schemas
    if SEED_DIR.exists():
        copied = 0
        for src in SEED_DIR.glob("*.yaml"):
            dst = SCHEMA_DIR / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                copied += 1
        if copied:
            print(f"  Copied {copied} seed schema(s)")
        else:
            print("  Seed schemas already present")
    else:
        print("  WARNING: No seed schemas found")

    # 6. Ensure log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 7. Sync MCP servers (registers crawler in ~/.claude/mcp.json)
    print("  Registering MCP server...")
    aos_cli = AOS_ROOT / "core" / "bin" / "cli" / "aos"
    if aos_cli.exists():
        result = _run(["bash", str(aos_cli), "sync-mcp"])
        if result.returncode == 0:
            print(f"  {result.stdout.strip()}")
        else:
            print(f"  WARNING: sync-mcp issue: {result.stderr}")

    print("  Crawler service deployed successfully")
    return True


def down() -> bool:
    """Remove the crawler service (preserves user schemas)."""
    # Remove venv
    if CRAWLER_VENV.exists():
        shutil.rmtree(CRAWLER_VENV)
        print(f"  Removed venv at {CRAWLER_VENV}")

    # Note: we preserve ~/.aos/data/crawler/schemas/ — that's user data
    print("  Schemas preserved at ~/.aos/data/crawler/schemas/")
    return True
