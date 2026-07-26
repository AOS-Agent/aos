"""
Invariant: no AOS-managed service listens on a wildcard address.

``~/.claude/CLAUDE.md`` states the network rule plainly: *"Network: localhost
only. Tailscale for remote access."* Most services honour it — bridge (7610),
transcriber (7602) and whatsmeow (7601) all bind 127.0.0.1. Qareen did not: it
defaulted to ``0.0.0.0`` and sat on port 4096 serving the full dashboard,
unauthenticated, over plain HTTP to anything on the LAN — people.db (1,148
contacts), comms.db (~248,000 messages), every task and project, and the vault
API. A guest phone or a compromised IoT device on the same WiFi could read all
of it. n8n was the same story on 5678 with workflow credentials behind it.

Fixing those two instances is a one-line change each. This check is the part
that matters: it makes the *class* of bug visible, so the next service author
who types ``host="0.0.0.0"`` gets told on the next reconcile cycle instead of
shipping a silent data leak.

WHAT COUNTS AS AOS-MANAGED
Discovered from the filesystem, never hardcoded (lists drift, directories
don't). A deployed LaunchAgent in ~/Library/LaunchAgents is AOS-managed if
either:
  * its label starts with ``com.aos.`` — the framework's own namespace, or
  * any string in the plist points into the AOS tree (``/aos/`` or ``/.aos/``)
    — which catches dev/side-loaded jobs like ``com.agent.qareen-dev`` that run
    out of an AOS venv under a different label.
Everything else on the machine is deliberately out of scope. The operator's
caddy sites (8088/8092) are *supposed* to be public, and their personal
projects are not ours to police — flagging them would train the operator to
ignore this check, which is how a check dies.

TWO SIGNALS, REPORTED SEPARATELY
  1. RUNTIME — a live wildcard listener held by an AOS-managed job (or any of
     its child processes; uvicorn's reload mode holds the socket in a child).
     This is ground truth: something is exposed right now.
  2. DECLARED — a deployed AOS plist whose text still contains ``0.0.0.0``.
     Catches a service that is currently stopped but will expose itself the
     moment it starts, and drift from a hand-edited plist.

REPORT-ONLY, BY DESIGN
This check never auto-fixes. Rebinding a service means restarting it, and if
the operator reaches that service over the LAN today, an unattended "fix"
would cut their access with no warning — which is a worse failure than the
exposure it closes (see migration 095, which sets up ``tailscale serve`` and
verifies it *before* flipping a bind). So: NOTIFY, with the evidence, and let
a human choose. Same posture as StorageLayoutCheck and TrackerHealthCheck.

OPT-OUT
A genuinely-intentional wildcard bind is declared in ``~/.aos/config/network.yaml``:

    allow_wildcard_bind:
      - label: com.aos.listen
        reason: Voice endpoint reached from the operator's iPhone over Tailscale

Missing file means no allowances — absent config degrades to "report
everything", never to "allow everything".
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from base import CheckResult, ReconcileCheck, Status

logger = logging.getLogger(__name__)

HOME = Path.home()
LAUNCH_AGENTS = HOME / "Library" / "LaunchAgents"
ALLOW_CONFIG = HOME / ".aos" / "config" / "network.yaml"

# Addresses that mean "every interface on this machine".
WILDCARD_PREFIXES = ("*:", "0.0.0.0:", "[::]:", "[*]:")

# The literal that should never appear in a shipped AOS plist.
WILDCARD_LITERAL = "0.0.0.0"


def _plist_mentions_aos_tree(data: object) -> bool:
    """True if any string anywhere in the plist points into the AOS tree."""
    if isinstance(data, str):
        return "/aos/" in data or "/.aos/" in data
    if isinstance(data, dict):
        return any(_plist_mentions_aos_tree(v) for v in data.values())
    if isinstance(data, (list, tuple)):
        return any(_plist_mentions_aos_tree(v) for v in data)
    return False


def _aos_managed_plists() -> dict[str, Path]:
    """Discover AOS-managed LaunchAgents. Returns {label: plist_path}."""
    found: dict[str, Path] = {}
    if not LAUNCH_AGENTS.is_dir():
        return found
    for path in sorted(LAUNCH_AGENTS.glob("*.plist")):
        try:
            with open(path, "rb") as fh:
                data = plistlib.load(fh)
        except Exception:  # noqa: BLE001 — a corrupt plist is not our problem
            continue
        label = data.get("Label") or path.stem
        if label.startswith("com.aos.") or _plist_mentions_aos_tree(data):
            found[label] = path
    return found


def _job_pid(label: str) -> int | None:
    """Resolve a launchd label to its current PID, or None if not running."""
    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if '"PID"' in line:
            digits = "".join(c for c in line.split("=")[-1] if c.isdigit())
            return int(digits) if digits else None
    return None


def _child_map() -> dict[int, list[int]]:
    """Build {ppid: [pid, ...]} for the whole process table."""
    kids: dict[int, list[int]] = {}
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return kids
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        kids.setdefault(ppid, []).append(pid)
    return kids


def _with_descendants(root: int, kids: dict[int, list[int]]) -> set[int]:
    """A PID plus every process beneath it.

    uvicorn in reload mode binds the socket in a worker child, so checking the
    launchd job PID alone would report a clean bill of health on an exposed
    service.
    """
    seen = {root}
    stack = [root]
    while stack:
        for child in kids.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _wildcard_listeners() -> dict[int, list[str]]:
    """Live wildcard listening sockets as {pid: [address, ...]}."""
    listeners: dict[int, list[str]] = {}
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pn"],
            capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return listeners
    pid: int | None = None
    for line in result.stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            pid = int(value) if value.isdigit() else None
        elif tag == "n" and pid is not None:
            if value.startswith(WILDCARD_PREFIXES):
                listeners.setdefault(pid, [])
                if value not in listeners[pid]:
                    listeners[pid].append(value)
    return listeners


def _allowed_labels() -> set[str]:
    """Labels the operator has explicitly excused, from instance config."""
    if not ALLOW_CONFIG.exists():
        return set()
    try:
        import yaml
        data = yaml.safe_load(ALLOW_CONFIG.read_text()) or {}
    except Exception:  # noqa: BLE001
        logger.warning("Could not parse %s — treating as no allowances", ALLOW_CONFIG)
        return set()
    entries = data.get("allow_wildcard_bind") or []
    allowed = set()
    for entry in entries:
        if isinstance(entry, str):
            allowed.add(entry)
        elif isinstance(entry, dict) and entry.get("label"):
            allowed.add(str(entry["label"]))
    return allowed


class NetworkBindingCheck(ReconcileCheck):
    name = "network_binding"
    description = (
        "No AOS-managed service listens on a wildcard address (0.0.0.0/::/*). "
        "Loopback only; remote access goes through tailscale serve or the "
        "Cloudflare tunnel. Report-only — never rebinds a live service."
    )

    def __init__(self) -> None:
        self._runtime: list[str] = []
        self._declared: list[str] = []
        self._skip_reason: str | None = None

    def check(self) -> bool:
        self._runtime = []
        self._declared = []
        self._skip_reason = None

        plists = _aos_managed_plists()
        if not plists:
            self._skip_reason = f"no AOS LaunchAgents found in {LAUNCH_AGENTS}"
            return True

        allowed = _allowed_labels()

        # Signal 1 — live wildcard listeners.
        listeners = _wildcard_listeners()
        if listeners:
            kids = _child_map()
            for label, _path in plists.items():
                if label in allowed:
                    continue
                pid = _job_pid(label)
                if pid is None:
                    continue
                family = _with_descendants(pid, kids)
                # Deduped across PIDs: a uvicorn parent and its reload worker
                # both hold the same socket, which would otherwise read as two
                # separate exposures.
                hits = sorted({
                    addr
                    for owner, addrs in listeners.items()
                    if owner in family
                    for addr in addrs
                })
                if hits:
                    self._runtime.append(f"{label} listening on {', '.join(hits)}")
        else:
            # lsof unavailable or nothing wildcard-bound at all. Don't claim a
            # clean runtime when the tool simply did not run — the declared
            # scan below still applies either way.
            pass

        # Signal 2 — declared wildcard in a deployed plist (exposed on next start).
        for label, path in plists.items():
            if label in allowed:
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if WILDCARD_LITERAL in text:
                self._declared.append(f"{label} declares {WILDCARD_LITERAL} in {path.name}")

        return not (self._runtime or self._declared)

    def fix(self) -> CheckResult:
        """Never repairs — reports with evidence.

        Rebinding requires restarting the service, and doing that unattended
        could sever remote access the operator depends on. Migration 095 is the
        supervised path: it provisions and verifies tailscale serve first, then
        flips the bind.
        """
        if self._skip_reason:
            return CheckResult(self.name, Status.SKIP, self._skip_reason)

        lines: list[str] = []
        if self._runtime:
            lines.append("EXPOSED NOW — reachable from the local network:")
            lines.extend(f"  • {item}" for item in self._runtime)
        if self._declared:
            lines.append("Declared 0.0.0.0 in a deployed plist (exposes on next start):")
            lines.extend(f"  • {item}" for item in self._declared)

        lines.append("")
        lines.append(
            "These services have no authentication of their own. Bind them to "
            "127.0.0.1 and reach them over Tailscale:"
        )
        lines.append("  tailscale serve --bg --https=<port> 127.0.0.1:<port>")
        lines.append("")
        lines.append(
            "!! DO NOT TRUST 'tailnet only' IN `tailscale serve status`. "
            "Verified 2026-07-26 on this machine: routes reported as tailnet-only, "
            "with AllowFunnel empty, were reachable from the public internet. An "
            "off-tailnet request to a route proxying a dead upstream returned 502 "
            "Bad Gateway -- a status only reachable if the request arrived. The "
            "likely cause is a CLI/daemon version skew (CLI 1.96.3 vs daemon "
            "1.98.9) making the CLI misreport funnel state."
        )
        lines.append(
            "So after adding ANY serve route, verify from genuinely off-tailnet "
            "before believing it is private. A tailnet-joined host cannot test "
            "this -- it reaches the route either way. If you cannot verify, prefer "
            "an authenticated path (this instance uses Cloudflare Access) and "
            "leave the service on loopback only."
        )
        lines.append(
            f"If a wildcard bind is genuinely intended, declare it in {ALLOW_CONFIG} "
            "under allow_wildcard_bind with a reason."
        )

        count = len(self._runtime) + len(self._declared)
        summary = (
            f"{count} AOS service binding(s) on a wildcard address — "
            f"{len(self._runtime)} live, {len(self._declared)} declared"
        )
        return CheckResult(
            self.name,
            Status.NOTIFY,
            summary,
            detail="\n".join(lines),
            notify=True,
        )
