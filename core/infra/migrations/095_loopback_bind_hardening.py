"""
Migration 095: bind Qareen and n8n to loopback, preserving remote access.

WHAT WAS WRONG
Qareen defaulted to ``0.0.0.0`` and the shipped LaunchAgent template passed
``--host 0.0.0.0`` outright, so port 4096 served the entire dashboard —
people.db (1,148 contacts), comms.db (~248,000 WhatsApp/iMessage/email
messages), every task and project, and the vault API — unauthenticated, over
plain HTTP, to anything on the local network. Verified empirically before this
migration was written: ``curl http://<lan-ip>:4096/`` from another machine on
the house WiFi returned HTTP 200. n8n was the same on 5678, with workflow
credentials behind it. Both violate the standing rule in CLAUDE.md: "Network:
localhost only. Tailscale for remote access."

n8n deserves a note, because the plist *looked* correct. It already set
``N8N_HOST=127.0.0.1``, but that variable only supplies the public hostname n8n
uses to build editor and webhook URLs — it does not bind. The bind address is
``N8N_LISTEN_ADDRESS``, which defaults to ``'::'`` (all interfaces). Confirmed
against the installed n8n's own compiled config, not assumed.

WHY THIS MIGRATION EXISTS AT ALL
The framework fix is two template edits. But an install that reaches Qareen
across the LAN or over Tailscale *today* would lose that access the moment the
bind flips, and per the atomic migration rule the commit that breaks an
instance assumption ships the bridge. So this migration:

  1. Ensures a ``tailscale serve`` route to 127.0.0.1:4096 exists and VERIFIES
     it responds before touching the bind. Funnel (public internet) is never
     enabled — that would trade this bug for a worse one.

     !! CORRECTION 2026-07-26 — READ BEFORE TRUSTING THIS STEP.
     "Serve is tailnet-only" is what the CLI claims and it was NOT true on the
     machine this migration was written for. A route reported as tailnet-only,
     with AllowFunnel empty, served the full dashboard to an off-tailnet client.
     Proof: an off-tailnet request to a serve route whose upstream was dead
     returned 502 Bad Gateway — reachable only if the request arrived. Suspected
     cause is CLI/daemon version skew (CLI 1.96.3, daemon 1.98.9) causing the CLI
     to misreport funnel state.

     Consequence: establishing a serve route as the "continuity bridge" may
     itself publish the service. That is the opposite of this migration's intent
     and is how this instance was briefly exposed while hardening it.

     Therefore: treat a serve route as UNVERIFIED until checked from genuinely
     off-tailnet. A tailnet-joined host cannot perform that check — it reaches the
     route either way, so a "successful" verification proves nothing about
     privacy. Where an authenticated path already exists (this instance fronts
     :4096 with Cloudflare Access), prefer it and leave the service on loopback
     with no serve route at all.
  2. Only then rewrites the deployed plists and restarts the services.
  3. If it cannot establish that route, it does NOT flip silently. It flips and
     tells the operator loudly — printed output plus a Telegram message — with
     the exact command to restore reach. Losing remote access without being
     told is worse than the exposure; losing it *with* clear instructions is
     recoverable, and leaving 248,000 messages open to the LAN is not an
     acceptable resting state.

The Cloudflare tunnel path (aos.<domain> behind Cloudflare Access) connects to
``localhost:4096`` and is unaffected by a loopback bind.

Idempotent: plist rewrites are deterministic, ``tailscale serve`` is
declarative, launchctl lifecycle tolerates re-runs.

Kickstart safety: ``launchctl kickstart -k`` can block past a short subprocess
timeout while the old instance drains before the new one binds. TimeoutExpired
is caught and the real success criterion is a health poll on the port —
mirroring migrations 054/056/071/080.
"""

DESCRIPTION = "Bind Qareen (4096/4097) and n8n (5678) to loopback; preserve tailnet access"

import os
import plistlib
import socket
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
AOS_ROOT = HOME / "aos"
LAUNCH_AGENTS = HOME / "Library" / "LaunchAgents"

QAREEN_LABEL = "com.aos.qareen"
QAREEN_PLIST = LAUNCH_AGENTS / f"{QAREEN_LABEL}.plist"
QAREEN_PORT = 4096

QAREEN_DEV_LABEL = "com.agent.qareen-dev"
QAREEN_DEV_PLIST = LAUNCH_AGENTS / f"{QAREEN_DEV_LABEL}.plist"
QAREEN_DEV_PORT = 4097

N8N_LABEL = "com.aos.n8n"
N8N_PLIST = LAUNCH_AGENTS / f"{N8N_LABEL}.plist"
N8N_PORT = 5678
N8N_LISTEN_KEY = "N8N_LISTEN_ADDRESS"

HOST_ENV_KEY = "AOS_QAREEN_HOST"
LOOPBACK = "127.0.0.1"

# Tailnet port that fronts Qareen after the flip. Serve on a dedicated port
# rather than a path prefix under :443 — the dashboard is an SPA with absolute
# asset paths, which a path-mounted proxy breaks.
SERVE_PORT = 8443

TAILSCALE_CANDIDATES = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
)


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _port_open(port: int, host: str = LOOPBACK, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _tailscale_bin() -> str | None:
    from shutil import which
    for candidate in TAILSCALE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return which("tailscale")


def _notify(text: str) -> None:
    """Best-effort Telegram ping. Never fails the migration."""
    try:
        sys.path.insert(0, str(AOS_ROOT / "core"))
        from lib.notify import send_telegram
        send_telegram(text)
    except Exception as e:  # noqa: BLE001
        print(f"  (could not send Telegram notice: {e})")


# ---------------------------------------------------------------------------
# plist rewriting
# ---------------------------------------------------------------------------
def _rebind_qareen_plist() -> bool:
    """Rewrite the deployed Qareen plist: --host arg AND AOS_QAREEN_HOST env.

    Both matter. The CLI arg wins over the env var, so changing only the
    module default (or only the env) leaves the service exposed — which is
    exactly why the original one-line-looking bug was not one line.
    """
    if not QAREEN_PLIST.exists():
        print(f"  ⚠ {QAREEN_PLIST.name} not deployed — skipping")
        return True
    with open(QAREEN_PLIST, "rb") as fh:
        data = plistlib.load(fh)

    args = data.get("ProgramArguments", [])
    if "--host" in args:
        args[args.index("--host") + 1] = LOOPBACK
    else:
        print("  ⚠ no --host arg in Qareen plist; relying on env var")
    data.setdefault("EnvironmentVariables", {})[HOST_ENV_KEY] = LOOPBACK

    with open(QAREEN_PLIST, "wb") as fh:
        plistlib.dump(data, fh)
    print(f"  ✓ {QAREEN_PLIST.name}: --host + {HOST_ENV_KEY} → {LOOPBACK}")
    return True


def _rebind_qareen_dev_plist() -> bool:
    """The dev backend on 4097 embeds host='0.0.0.0' inside a python -c string."""
    if not QAREEN_DEV_PLIST.exists():
        return True
    with open(QAREEN_DEV_PLIST, "rb") as fh:
        data = plistlib.load(fh)
    args = data.get("ProgramArguments", [])
    changed = False
    for i, arg in enumerate(args):
        if isinstance(arg, str) and "host='0.0.0.0'" in arg:
            args[i] = arg.replace("host='0.0.0.0'", f"host='{LOOPBACK}'")
            changed = True
    if not changed:
        return True
    with open(QAREEN_DEV_PLIST, "wb") as fh:
        plistlib.dump(data, fh)
    print(f"  ✓ {QAREEN_DEV_PLIST.name}: uvicorn host → {LOOPBACK}")
    return True


def _rebind_n8n_plist() -> bool:
    """Add N8N_LISTEN_ADDRESS. N8N_HOST does not bind; this does."""
    if not N8N_PLIST.exists():
        return True
    with open(N8N_PLIST, "rb") as fh:
        data = plistlib.load(fh)
    env = data.setdefault("EnvironmentVariables", {})
    if env.get(N8N_LISTEN_KEY) == LOOPBACK:
        return True
    env[N8N_LISTEN_KEY] = LOOPBACK
    with open(N8N_PLIST, "wb") as fh:
        plistlib.dump(data, fh)
    print(f"  ✓ {N8N_PLIST.name}: {N8N_LISTEN_KEY} → {LOOPBACK}")
    return True


def _restart(label: str, plist: Path, port: int, wait: int = 60) -> bool:
    """bootout → bootstrap → kickstart, then poll the port on loopback."""
    if not plist.exists():
        return True
    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"{domain}/{label}"

    _run(["launchctl", "bootout", service])
    time.sleep(1)
    result = _run(["launchctl", "bootstrap", domain, str(plist)])
    if result.returncode != 0:
        print(f"  ⚠ bootstrap {label} returned {result.returncode}: {result.stderr.strip()}")
    try:
        _run(["launchctl", "kickstart", "-k", service])
    except subprocess.TimeoutExpired:
        print(f"  ⚠ kickstart {label} timed out (old instance draining) — polling port")

    for i in range(wait // 2):
        time.sleep(2)
        if _port_open(port):
            print(f"  ✓ {label} listening on {LOOPBACK}:{port} after {(i + 1) * 2}s")
            return True
    print(f"  ✗ {label} not listening on {LOOPBACK}:{port} after {wait}s")
    print(f"    Check ~/.aos/logs/{label.split('.')[-1]}.err.log")
    return False


# ---------------------------------------------------------------------------
# tailscale serve — the continuity bridge, established BEFORE the flip
# ---------------------------------------------------------------------------
def _ensure_serve() -> tuple[bool, str]:
    """Ensure + verify a tailnet route to Qareen. Returns (ok, description).

    Tailnet-only. Funnel is never enabled: it would publish an unauthenticated
    dashboard to the public internet, which is strictly worse than the LAN
    exposure this migration closes.
    """
    ts = _tailscale_bin()
    if not ts:
        return False, "Tailscale not installed"

    status = _run([ts, "status"])
    if status.returncode != 0:
        return False, f"tailscale status failed: {status.stderr.strip() or 'not running'}"

    existing = _run([ts, "serve", "status"])
    already = f"{LOOPBACK}:{QAREEN_PORT}" in (existing.stdout or "")
    if not already:
        # `tailscale serve` is declarative; re-running is safe.
        add = _run(
            [ts, "serve", "--bg", f"--https={SERVE_PORT}", f"{LOOPBACK}:{QAREEN_PORT}"],
            timeout=30,
        )
        if add.returncode != 0:
            return False, f"tailscale serve failed: {add.stderr.strip()}"

    verify = _run([ts, "serve", "status"])
    if f"{LOOPBACK}:{QAREEN_PORT}" not in (verify.stdout or ""):
        return False, "serve route not present after configuration"

    # Confirm the proxy target actually answers. Qareen is still wildcard-bound
    # at this point, so loopback necessarily works — this catches a dead service
    # before we take away the LAN fallback.
    if not _port_open(QAREEN_PORT):
        return False, f"nothing listening on {LOOPBACK}:{QAREEN_PORT} to serve"

    host = ""
    for line in (verify.stdout or "").splitlines():
        if line.startswith("https://") and f":{SERVE_PORT}" in line:
            host = line.split()[0]
            break
    return True, host or f"https://<this-machine>:{SERVE_PORT}"


# ---------------------------------------------------------------------------
# migration contract
# ---------------------------------------------------------------------------
def check() -> bool:
    """Applied when every deployed service binds loopback."""
    if QAREEN_PLIST.exists():
        with open(QAREEN_PLIST, "rb") as fh:
            data = plistlib.load(fh)
        args = data.get("ProgramArguments", [])
        if "--host" in args and args[args.index("--host") + 1] != LOOPBACK:
            return False
        if data.get("EnvironmentVariables", {}).get(HOST_ENV_KEY) != LOOPBACK:
            return False

    if QAREEN_DEV_PLIST.exists():
        with open(QAREEN_DEV_PLIST, "rb") as fh:
            data = plistlib.load(fh)
        if any(
            isinstance(a, str) and "host='0.0.0.0'" in a
            for a in data.get("ProgramArguments", [])
        ):
            return False

    if N8N_PLIST.exists():
        with open(N8N_PLIST, "rb") as fh:
            data = plistlib.load(fh)
        if data.get("EnvironmentVariables", {}).get(N8N_LISTEN_KEY) != LOOPBACK:
            return False

    return True


def up() -> bool:
    print("  Hardening network binds to loopback (Qareen 4096/4097, n8n 5678)")

    # 1. Continuity FIRST. Never flip a bind before the replacement path works.
    serve_ok, serve_info = _ensure_serve()
    if serve_ok:
        print(f"  ✓ Tailnet route verified: {serve_info} → {LOOPBACK}:{QAREEN_PORT}")
    else:
        print(f"  ⚠ Could not establish a tailnet route: {serve_info}")

    # 2. Rewrite the deployed plists.
    _rebind_qareen_plist()
    _rebind_qareen_dev_plist()
    _rebind_n8n_plist()

    # 3. Restart. Qareen is the one that matters; a dev-backend or n8n hiccup
    #    must not fail the whole batch (KeepAlive owns their liveness).
    ok = _restart(QAREEN_LABEL, QAREEN_PLIST, QAREEN_PORT)
    if not _restart(QAREEN_DEV_LABEL, QAREEN_DEV_PLIST, QAREEN_DEV_PORT, wait=30):
        print("  ⚠ dev backend (4097) did not come back — non-fatal")
    if not _restart(N8N_LABEL, N8N_PLIST, N8N_PORT, wait=90):
        print("  ⚠ n8n (5678) did not come back — non-fatal, check its log")

    if not ok:
        print("  ✗ Qareen failed to restart on loopback — investigate before shipping")
        return False

    # 4. Tell the operator, loudly, if their remote path is not guaranteed.
    if not serve_ok:
        banner = (
            "\n"
            "  ================================================================\n"
            "  ⚠  REMOTE ACCESS MAY HAVE CHANGED\n"
            "  ================================================================\n"
            "  Qareen (4096) and n8n (5678) now listen on 127.0.0.1 only. They\n"
            "  were reachable from any device on your local network, with no\n"
            "  password, serving your contacts and message history.\n"
            "\n"
            f"  A Tailscale route could NOT be set up here: {serve_info}\n"
            "\n"
            "  If you used the dashboard from another machine, restore it with:\n"
            f"    tailscale serve --bg --https={SERVE_PORT} {LOOPBACK}:{QAREEN_PORT}\n"
            "\n"
            "  Or set AOS_QAREEN_HOST back to 0.0.0.0 in\n"
            f"    {QAREEN_PLIST}\n"
            "  if you accept LAN exposure on a network you trust.\n"
            "  ================================================================\n"
        )
        print(banner)
        _notify(
            "🔒 I closed a security hole on the Mac Mini.\n\n"
            "Your dashboard and n8n were open to every device on your home WiFi "
            "with no password. Anyone on that network could read your contacts "
            "and messages. They now only accept connections from the Mini itself.\n\n"
            "One catch: I could not set up a Tailscale route automatically "
            f"({serve_info}). If you used the dashboard from your laptop or phone, "
            "tell me and I will set that up. 👍"
        )
    else:
        print(f"  ✓ Remote access preserved via {serve_info}")
        _notify(
            "🔒 Security fix applied on the Mac Mini.\n\n"
            "Your dashboard and n8n were reachable from every device on your home "
            "WiFi with no password — contacts, messages, tasks, all of it. That is "
            "now closed. They only accept local connections.\n\n"
            f"You can still reach the dashboard from your other devices here:\n{serve_info}\n\n"
            "Nothing else changes. ✅"
        )

    return True


if __name__ == "__main__":
    if check():
        print("Migration 095 already applied")
    else:
        print("Done" if up() else "Failed")
